"""ESPN NBA fantasy tracker: standings, rosters, and trade / waiver / injury alerts.

Usage:
    python app\fantasy.py teams               list every team and its id (to set my_team_id)
    python app\fantasy.py standings           league table + this week's matchups
    python app\fantasy.py roster [TEAM]       one roster with form (default: your team)
    python app\fantasy.py alerts [--notify]   what you should do now; --notify pops Windows toasts

  League stats:
    python app\fantasy.py league [--split S]  every team's roster with points and box-score averages
    python app\fantasy.py player NAME         any NBA player's full stat line + where their points come from
    python app\fantasy.py scoring             the league's scoring rules
    python app\fantasy.py activity [-n N]     recent adds, drops and trades
    python app\fantasy.py export              write data/*.csv + league.json and save today's snapshot
    python app\fantasy.py history [TEAM]      a team's points day by day (from saved snapshots)
Add --demo before the command to run against made-up data without an ESPN league.
"""
import argparse
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import stats
from stats import owner_names, split_of, table

ROOT = Path(__file__).resolve().parent.parent  # project folder (this file is in app/)
CONFIG = ROOT / "config" / "config.json"
SEEN = ROOT / "data" / "seen_alerts.json"
KEEP = ROOT / "config" / "keep.json"

OUT_STATUSES = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
SHAKY_STATUSES = {"DAY_TO_DAY", "QUESTIONABLE", "DOUBTFUL"}
BENCH_SLOTS = {"BE", "IR"}
POSITIONS = ("PG", "SG", "SF", "PF", "C")
MIN_AT_POSITION, MAX_AT_POSITION = 2, 3  # aim for 2-3 players at each main position

PRIOR_GAMES = 20    # ESPN's projection counts like this many games against a player's own (shrinkage)
MINUTES_PRIOR = 8   # projected minutes count like this many games; last week's games count double
RECENT_PRIOR = 5    # a hot/cold stretch is shrunk toward the baseline like this many games
MARKET_GAMES = 5    # buy-low / sell-high: games before a player's season average means anything to his manager
MARKET_GAP = 0.15   # season average this far off ESPN's projection (either way)
MARKET_EDGE = 4.0   # and at least this many points a game between what his manager sees and his real value
NO_PROJECTION_VALUE = 18.0  # what a player ESPN doesn't even project is assumed to be worth, until proven

DEFAULT_THRESHOLDS = {
    "trend_pct": 0.25,       # recent form this far above/below baseline counts as hot/slumping
    "min_games": 5,          # games played before trends are trusted
    "waiver_margin": 0.10,   # free agent must beat your worst player by this fraction
    "trade_margin": 0.10,    # trade target's baseline must beat yours by this fraction
    "max_waiver_alerts": 3,
    "max_trade_alerts": 3,
}


# ---------------------------------------------------------------- player value

def split_value(split):
    """Fantasy value per game for one stat split (season, last 15, projected...).

    Points leagues: ESPN's own fantasy points average.
    Category leagues (no fantasy points): a rough per-game composite of the counting cats.
    """
    if not split:
        return 0.0
    if split.get("applied_avg"):
        return float(split["applied_avg"])
    avg = split.get("avg") or {}
    if not avg:
        return 0.0
    return round(
        avg.get("PTS", 0) + 1.2 * avg.get("REB", 0) + 1.5 * avg.get("AST", 0)
        + 3 * avg.get("STL", 0) + 3 * avg.get("BLK", 0) + avg.get("3PM", 0)
        - avg.get("TO", 0),
        2,
    )


@dataclass
class Form:
    player: object
    baseline: float  # what the player "really" is per game: expected minutes x fantasy points per minute
    recent: float    # last 15 days as played (falls back to last 7, then baseline)
    games: int
    recent_games: int = 0  # games behind `recent`
    minutes: float = 0.0   # expected minutes per game
    rate: float = 0.0      # expected fantasy points per minute

    @property
    def trend(self):
        return (self.recent - self.baseline) / self.baseline if self.baseline else 0.0

    @property
    def worth(self):
        """Value for pickups and trades: baseline, nudged by recent form. A short hot or cold
        stretch only counts a little (it's shrunk toward the baseline like everything else)."""
        g = self.recent_games
        recent = (g * self.recent + RECENT_PRIOR * self.baseline) / (g + RECENT_PRIOR) if g else self.baseline
        return (self.baseline + recent) / 2

    @property
    def status(self):
        return self.player.injuryStatus or "ACTIVE"

    @property
    def healthy(self):
        return self.status not in OUT_STATUSES


def _avg(split, key):
    return float(((split or {}).get("avg") or {}).get(key) or 0)


def _gp(split):
    return int(((split or {}).get("total") or {}).get("GP") or ((split or {}).get("avg") or {}).get("GP") or 0)


def form_of(player, year):
    """A player's expected fantasy points per game = expected minutes x fantasy points per minute.

    - Points per minute is a skill and changes slowly: his own season, shrunk toward ESPN's
      projection by games / (games + PRIOR_GAMES), so one huge (or awful) night barely moves it.
    - Minutes follow his role and change fast: ESPN's projected minutes, his season minutes and
      his last week's minutes (counted double), so a new starting job or a benching shows quickly.
    Falls back to a plain shrunk average when minutes aren't available (e.g. no projection)."""
    stats = player.stats
    season, proj = stats.get(f"{year}_total", {}), stats.get(f"{year}_projected", {})
    last7 = stats.get(f"{year}_last_7", {})
    games = _gp(season)
    season_v, proj_v = split_value(season), split_value(proj)
    s_min, p_min = _avg(season, "MPG") or _avg(season, "MIN"), _avg(proj, "MPG") or _avg(proj, "MIN")
    r_min, r_gp = _avg(last7, "MPG") or _avg(last7, "MIN"), _gp(last7)
    w = games / (games + PRIOR_GAMES)
    minutes = rate = 0.0
    if proj_v and p_min and (not games or s_min):
        rate = w * (season_v / s_min if games else 0) + (1 - w) * (proj_v / p_min)
        minutes = ((MINUTES_PRIOR * p_min + games * s_min + 2 * r_gp * r_min)
                   / (MINUTES_PRIOR + games + 2 * r_gp))
        baseline = rate * minutes
    elif proj_v:
        baseline = w * season_v + (1 - w) * proj_v
    else:  # no ESPN projection (a fringe player or late signing): pull him toward a bench-level value
        baseline = (games * season_v + PRIOR_GAMES * NO_PROJECTION_VALUE) / (games + PRIOR_GAMES) if games else 0.0
    recent, recent_games = baseline, 0
    for key in ("last_15", "last_7"):
        split = stats.get(f"{year}_{key}", {})
        if split_value(split):
            recent, recent_games = split_value(split), _gp(split)
            break
    return Form(player, round(baseline, 2), round(recent, 2), games, recent_games, round(minutes, 1), round(rate, 3))


# ---------------------------------------------------------------- position balance

def balance_problems(players):
    """{(kind, pos): [names]} by main position. 'many' = more than MAX_AT_POSITION (IR included:
    they come back); 'few' = fewer than MIN_AT_POSITION off IR (an IR'd player can't fill in)."""
    problems = {}
    for pos in POSITIONS:
        main = [p.name for p in players if p.position == pos]
        if len(main) > MAX_AT_POSITION:
            problems[("many", pos)] = main
        active = [p.name for p in players if p.position == pos and p.lineupSlot != "IR"]
        if len(active) < MIN_AT_POSITION:
            problems[("few", pos)] = active
    return problems


def describe_problem(kind, pos, names):
    if kind == "many":
        return f"{len(names)} {pos}s ({', '.join(names)})"
    return f"only {len(names)} {pos}" + (f" ({', '.join(names)})" if names else "")


def unbalances(players, add=(), drop=()):
    """The balance problems a move would create or make worse, as text.
    [] means the move is fine (it may still leave an existing problem as it is)."""
    drop_ids = {p.playerId for p in drop}
    after = [p for p in players if p.playerId not in drop_ids] + list(add)
    before = balance_problems(players)

    def worse(kind, pos, names):  # new, or an existing problem made worse (a 5th PG, or 0 C instead of 1)
        if (kind, pos) not in before:
            return True
        return len(names) > len(before[(kind, pos)]) if kind == "many" else len(names) < len(before[(kind, pos)])
    return [describe_problem(k, pos, names) for (k, pos), names in balance_problems(after).items()
            if worse(k, pos, names)]


def balance_alerts(my_team):
    alerts = []
    for (kind, pos), names in balance_problems(my_team.roster).items():
        if kind == "many":
            detail = f"{', '.join(names)}. Consider swapping one for a position you're short on."
        else:
            detail = ((", ".join(names) + ". ") if names else "") + \
                f"You're aiming for {MIN_AT_POSITION}-{MAX_AT_POSITION} at every position: consider adding a {pos}."
        detail = detail.replace(".. ", ". ")  # names like "Scotty Pippen Jr." already end in a dot
        alerts.append(Alert(f"balance:{kind}:{pos}:{','.join(sorted(names))}", 2,
                            f"Roster balance: {describe_problem(kind, pos, names).split(' (')[0]}", detail))
    return alerts


# ---------------------------------------------------------------- alerts

@dataclass
class Alert:
    key: str       # stable id so the same alert only notifies once
    priority: int  # 1 = act now, 2 = worth a look, 3 = fyi
    title: str
    detail: str


def fmt(f):
    return f"{f.player.name} ({f.player.position}, {f.player.proTeam})"


def nums(f):
    return f"season {f.baseline:.1f}, last 15 {f.recent:.1f}"


def _injury_note(f, injuries):
    """ESPN's latest injury update and expected return, if any."""
    info = (injuries or {}).get(f.player.playerId)
    if not info:
        return ""
    back = f" Expected back {info['return_date']:%a %d %b}." if info["return_date"] else ""
    return f"\n      ESPN: {info['comment']}{back}"


def injury_alerts(mine, injuries=None):
    alerts = []
    for f in mine:
        slot = f.player.lineupSlot
        if f.status in OUT_STATUSES and slot not in BENCH_SLOTS:
            alerts.append(Alert(f"inj:{f.player.playerId}:{f.status}", 1,
                                f"Bench {f.player.name}: {f.status.replace('_', ' ').lower()}",
                                f"{fmt(f)} is in your {slot} slot but listed {f.status}. "
                                "Swap in a healthy bench player"
                                + (" or move them to IR." if f.status == "INJURY_RESERVE" else ".")
                                + _injury_note(f, injuries)))
        elif f.status in SHAKY_STATUSES and slot not in BENCH_SLOTS:
            alerts.append(Alert(f"inj:{f.player.playerId}:{f.status}", 2,
                                f"Check on {f.player.name}: {f.status.replace('_', ' ').lower()}",
                                f"{fmt(f)} is starting at {slot}. Check news before lineups lock."
                                + _injury_note(f, injuries)))
    return alerts


def load_keep():
    """Player ids you never want suggested as a drop or as trade bait (config/keep.json)."""
    try:
        return {int(k) for k in json.loads(KEEP.read_text(encoding="utf-8")).get("players", {})}
    except (OSError, ValueError):
        return set()


def set_keep(player, keep=True):
    """Add or remove a player from the keep list. Returns the new list as {id: name}."""
    try:
        data = json.loads(KEEP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    players = data.get("players", {})
    if keep:
        players[str(player.playerId)] = player.name
    else:
        players.pop(str(player.playerId), None)
    KEEP.write_text(json.dumps({"_note": "players the app never suggests dropping or trading away",
                                "players": players}, indent=2), encoding="utf-8")
    return players


def upgrade_over(fa, droppable, roster, t, ahead=None):
    """The weakest of your droppable players this free agent is a clear upgrade on, or None:
    better per game by waiver_margin, no new position imbalance, and (when ahead(form) gives
    (points, games) for the next 30 days) more expected points over those days AND over the rest
    of the season (ahead.season) - a drop is permanent, so it has to pay off for the whole season,
    not just this month or on a per-game number."""
    if not fa.healthy:
        return None
    season = getattr(ahead, "season", None)
    for d in droppable:  # weakest first
        if fa.worth <= d.worth * (1 + t["waiver_margin"]):
            return None  # the rest are even better per game
        if unbalances(roster, [fa.player], [d.player]):
            continue
        if ahead and ahead(fa) and ahead(fa)[0] <= ahead(d)[0]:
            continue
        if season and season(fa) <= season(d):
            continue  # better this month, worse over the season: not worth a permanent drop
        ros_gain = getattr(ahead, "ros_gain", None)
        if ros_gain:
            import valuation
            if ros_gain(fa.player, d.player, roster) < valuation.MARGIN:
                continue  # your lineup wouldn't actually score more over the season (he'd sit, or it's noise)
        return d
    return None


def opportunity_alerts(league, mine, injuries, t, leaving=(), ahead=None, limit=2):
    """A teammate's injury (or a bigger role) makes a free agent better than one of your players.
    Uses the same upgrade rule as pickups, at his boosted value."""
    import opportunity
    keep = load_keep() | set(leaving)
    droppable = sorted((f for f in mine if f.player.lineupSlot != "IR" and f.player.playerId not in keep),
                       key=lambda f: f.worth)
    roster = [f.player for f in mine]
    alerts = []
    import planner
    today = getattr(league, "current_week", 1) or 1
    final = getattr(league, "finalScoringPeriod", None) or 174

    def season_points(form, value_until=None, until_day=None):
        """Rest-of-season points; at value_until up to until_day, then his normal worth."""
        if value_until is None or until_day is None:
            return form.worth * planner.games_after(league, form.player, today, injuries)
        boosted = value_until * planner.expected_games(league, form.player, today, min(until_day, final), injuries)
        return boosted + form.worth * planner.games_after(league, form.player, until_day + 1, injuries)

    for o in opportunity.opportunities(league, injuries):
        boosted = replace(o["form"], baseline=o["boosted"], recent=o["boosted"], recent_games=0)
        drop = upgrade_over(boosted, droppable, roster, t, ahead=None)  # 30 days at the boost overstates a short absence
        if not drop:
            continue
        # a drop is permanent: he must also beat that player over the whole season, at the boosted
        # value only until the injured teammate is due back (no return date: assume two weeks)
        back = o.get("back")
        until = today + ((back - __import__("datetime").date.today()).days if back else 14)
        if season_points(o["form"], o["boosted"], until) <= season_points(drop):
            continue
        droppable.remove(drop)
        p = o["player"]
        alerts.append(Alert(f"opp:{p.playerId}:{drop.player.playerId}", 2,
                            f"Opportunity: {p.name} ({p.proTeam}) - {o['reason']}",
                            f"{fmt(o['form'])} usually {o['form'].worth:.1f} a game; with about {o['extra_min']:.0f} "
                            f"more minutes, more like {o['boosted']:.1f}.\n      That beats {fmt(drop)} "
                            f"({drop.worth:.1f}). Free agents like this get picked up fast"
                            + (f": {get_text(league, p)}." if p.playerId in waivers(league) else ".")))
        if len(alerts) >= limit:
            break
    return alerts


def minutes_alerts(mine, year, drop=5):
    """One of your players is playing a lot less this week than usual: often a lost role."""
    import opportunity
    alerts = []
    for f in mine:
        trend = opportunity.minutes_trend(f.player, year)
        if trend is not None and trend <= -drop and f.healthy:
            usual = opportunity.minutes(f.player, year)
            alerts.append(Alert(f"minutes:{f.player.playerId}:{round(trend)}", 3,
                                f"{f.player.name}'s minutes are down ({trend:+.0f} a game this week)",
                                f"{fmt(f)} usually plays about {usual:.0f} minutes. Check the news: a smaller "
                                "role costs fantasy points every game until it changes."))
    return alerts


def next_30_days(league, injuries):
    """A cached form -> (expected points, expected games) over the next 30 days. Also has
    .season(form): expected points over the rest of the season (worth x expected games)."""
    import planner
    cache, season_cache = {}, {}
    today = getattr(league, "current_week", 1) or 1

    def ahead(form):
        if form.player.playerId not in cache:
            cache[form.player.playerId] = planner.next_days(league, form, injuries, 30)
        return cache[form.player.playerId]

    def season(form):
        if form.player.playerId not in season_cache:
            season_cache[form.player.playerId] = form.worth * planner.games_after(league, form.player, today, injuries)
        return season_cache[form.player.playerId]
    def ros_gain(add, drop, roster):
        """Rest-of-season lineup points from the swap (lineup-aware: a player who'd sit adds little)."""
        import valuation
        return valuation.pickup_gain(league, roster, add, drop, injuries)
    ahead.season = season
    ahead.ros_gain = ros_gain
    return ahead


def waivers(league):
    """{player id: when his waiver period ends} for players on waivers: they must be claimed, and
    only join your team once the claim processes. Also tells the planner not to count their games
    before then. Cached on the league object; {} without ESPN (demo)."""
    if getattr(league, "_fh_waivers", None) is None:
        out = {}
        if getattr(league, "pro_schedule", None):
            from datetime import date, datetime
            filters = {"players": {"filterStatus": {"value": ["WAIVERS"]}, "limit": 300,
                                   "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
            try:
                data = league.espn_request.league_get(
                    params={"view": "kona_player_info", "scoringPeriodId": league.current_week},
                    headers={"x-fantasy-filter": json.dumps(filters)})
                for pe in data.get("players", []):
                    if pe.get("status") == "WAIVERS" and pe.get("waiverProcessDate"):
                        out[pe["id"]] = datetime.fromtimestamp(pe["waiverProcessDate"] / 1000)
            except Exception:
                pass
            today = date.today()
            league._fh_from = {pid: league.current_week + (when.date() - today).days for pid, when in out.items()}
        league._fh_waivers = out
    return league._fh_waivers


def committed(league, team):
    """Ids of your players tied up in your own pending moves: the drop in a waiver claim (dropping
    him first makes the claim fail) or a player in a trade offer you've made. Never suggest
    dropping them. Cached on the league object; empty without ESPN (demo)."""
    if getattr(league, "_fh_committed", None) is None:
        out = set()
        if getattr(league, "pro_schedule", None):
            mine = {p.playerId for p in team.roster}
            try:
                data = league.espn_request.league_get(
                    params={"view": "mTransactions2", "scoringPeriodId": league.current_week})
                for t in data.get("transactions", []):
                    if t.get("teamId") != team.team_id or t.get("status") != "PENDING":
                        continue
                    if t.get("type") in ("WAIVER", "TRADE_PROPOSAL"):
                        out |= {i.get("playerId") for i in t.get("items", [])
                                if i.get("type") in ("DROP", "TRADE") and i.get("playerId") in mine}
            except Exception:
                pass
        league._fh_committed = out
    return league._fh_committed


def _cap(text):
    return text[:1].upper() + text[1:]


def get_text(league, player):
    """'add Rollins' or 'claim Rollins off waivers (clears Thu 08:00)'."""
    when = (getattr(league, "_fh_waivers", None) or {}).get(player.playerId)
    return f"claim {player.name} off waivers (clears {when:%a %H:%M})" if when else f"add {player.name}"


def waiver_alerts(mine, free_agents, t, leaving=(), ahead=None, league=None):
    """ahead(form) -> (points, games) over the next 30 days, or None (see planner.next_days)."""
    keep = load_keep() | set(leaving)  # never drop: kept, or in a trade that's going through
    droppable = sorted((f for f in mine if f.player.lineupSlot != "IR" and f.player.playerId not in keep),
                       key=lambda f: f.worth)
    roster = [f.player for f in mine]
    alerts = []
    # Best free agent vs your worst player, next best vs your second worst, and so on.
    for fa in sorted(free_agents, key=lambda f: f.worth, reverse=True):
        if len(alerts) >= min(t["max_waiver_alerts"], len(droppable)):
            break
        worst = upgrade_over(fa, droppable, roster, t, ahead)
        if worst:
            droppable.remove(worst)
            detail = f"{fmt(fa)}: {nums(fa)}\n      vs {fmt(worst)}: {nums(worst)}"
            if ahead and ahead(fa):
                (fa_pts, fa_g), (d_pts, d_g) = ahead(fa), ahead(worst)
                detail += (f"\n      Next 30 days: ~{fa_pts:.0f} pts ({fa_g:.0f} games expected) vs "
                           f"~{d_pts:.0f} ({d_g:.0f}): {fa_pts - d_pts:+.0f}")
            alerts.append(Alert(f"fa:{fa.player.playerId}:{worst.player.playerId}", 2,
                                _cap(get_text(league, fa.player) if league else f"pick up {fa.player.name}")
                                + f", drop {worst.player.name}", detail))
    return alerts


def trade_ideas(mine, others, t, per_team=1, leaving=()):
    """[(team, gain, give, get)], best first. Pairs one of your players with one of theirs where:
    - their player is better on baseline (you gain real value), and
    - their player's recent form is no better than yours (the deal looks fair to them).
    The classic version: sell your hot player, buy their slumping star.
    """
    keep = load_keep() | set(leaving)
    ideas = []
    for team, roster in others:
        found = []
        for give in mine:
            if give.games < t["min_games"] or not give.healthy or give.player.playerId in keep:
                continue  # kept players are never offered
            for get in roster:
                if get.games < t["min_games"] or not get.healthy:
                    continue
                gain = get.baseline - give.baseline
                if gain < give.baseline * t["trade_margin"]:
                    continue
                if get.recent > give.recent * 1.05:
                    continue  # they'd be giving up the hotter player; unlikely to accept
                if unbalances([f.player for f in mine], [get.player], [give.player]):
                    continue  # would leave you with too many at one position or too few at another
                found.append((team, gain, give, get))
        ideas += sorted(found, key=lambda x: -x[1])[:per_team]
    return sorted(ideas, key=lambda x: -x[1])


def market_read(form, year):
    """How a player's manager probably sees him vs what he's really worth. Managers judge by the season
    average; early on, scoring per minute is mostly luck (shooting streaks) and drifts back, while
    minutes reflect a real role. So a player whose minutes are intact but who is scoring well under
    his projection is a buy-low (worth more than he looks), and one scoring well over it on the same
    minutes is a sell-high. More minutes than projected = a real role change: neither.
    Returns ("buy" | "sell", season average, real value) or None."""
    p = form.player
    season, proj = split_of(p, year, "total"), split_of(p, year, "projected")
    if form.games < MARKET_GAMES or not split_value(proj):
        return None
    avg, projected = split_value(season), split_value(proj)
    s_min, p_min = _avg(season, "MPG") or _avg(season, "MIN"), _avg(proj, "MPG") or _avg(proj, "MIN")
    if not (s_min and p_min):
        return None
    if avg <= projected * (1 - MARKET_GAP) and s_min >= 0.95 * p_min and form.baseline - avg >= MARKET_EDGE:
        return "buy", avg, form.baseline
    if avg >= projected * (1 + MARKET_GAP) and s_min <= 1.05 * p_min and avg - form.baseline >= MARKET_EDGE:
        return "sell", avg, form.baseline
    return None


def market_alerts(mine, others, year, leaving=(), limit=3):
    """Sell-high players on your team and buy-low targets on others' (market_read)."""
    out = []
    for f in mine:
        m = market_read(f, year)
        if m and m[0] == "sell" and f.player.playerId not in leaving:
            out.append(Alert(f"sell:{f.player.playerId}", 2,
                             f"Sell high: {f.player.name} is averaging {m[1]:.1f}, likely more like {m[2]:.1f}",
                             f"He's playing his usual minutes, so the jump is efficiency (often hot shooting), which "
                             f"usually cools off. His manager-facing number is high right now: shop him for someone "
                             f"worth {m[1]:.0f}+ for real (check it in Trade Lab)."))
    buys = []
    for team, forms in others:
        for f in forms:
            m = market_read(f, year)
            if m and m[0] == "buy" and f.healthy:
                buys.append((m[2] - m[1], team, f, m))
    for gap, team, f, m in sorted(buys, key=lambda b: -b[0])[:limit]:
        out.append(Alert(f"buy:{f.player.playerId}", 2,
                         f"Buy low: {f.player.name} ({team.team_name.strip()}) is averaging {m[1]:.1f}, really more like {m[2]:.1f}",
                         f"He's still getting his minutes, so the slump is efficiency (often cold shooting), which "
                         f"usually bounces back. His manager may sell at his current average: try Trade Lab with "
                         f"{team.team_name.strip()}."))
    return out


def trade_alerts(mine, others, t, leaving=()):
    ideas = trade_ideas(mine, others, t, leaving=leaving)
    return [
        Alert(f"trade:{team.team_id}:{give.player.playerId}:{get.player.playerId}", 2,
              f"Trade idea with {team.team_name}: {give.player.name} for {get.player.name}",
              f"Send {fmt(give)}: {nums(give)}\n      Get  {fmt(get)}: {nums(get)}\n"
              f"      +{gain:.1f} baseline/game for you; their recent form looks no better than "
              "yours, so the offer looks fair to them.")
        for team, gain, give, get in ideas[: t["max_trade_alerts"]]
    ]


def lineup_alerts(league, my_team, injuries):
    """Today's best legal lineup vs the one you've set (games already started stay locked)."""
    import planner
    moves = planner.today_moves(league, my_team, injuries)
    if not moves or moves["best"] - moves["current"] < 0.5 or not moves["start"]:
        return []
    import lineup
    games = lineup.todays_games(league)
    starts = ", ".join(f"{p.name} at {slot} ({lineup.game_today(league, p, games)})" for p, slot in moves["start"])
    benches = ", ".join(p.name for p in moves["bench"]) or "nobody"
    moved = "".join(f"\n      Move {p.name} from {old} to {new}." for p, old, new in moves.get("moves", []))
    day = getattr(league, "current_week", "")
    key = "lineup:%s:%s" % (day, ",".join(sorted(str(p.playerId) for p, _ in moves["start"])))
    return [Alert(key, 1, f"Set your lineup: +{moves['best'] - moves['current']:.1f} pts today",
                  f"Start {starts}.\n      Bench {benches}.{moved}\n      (Full lineup on the Week Ahead tab.)")]


def status_alerts(mine, injuries):
    """A player's ESPN status changed in the last day (e.g. ACTIVE -> OUT, or back from injury)."""
    import news
    alerts = []
    for p, old, new, _ in news.track_statuses([f.player for f in mine]):
        f = next(x for x in mine if x.player is p)
        pretty = lambda s: s.replace("_", " ").lower()
        if new == "ACTIVE":
            alerts.append(Alert(f"status:{p.playerId}:{old}:{new}", 2, f"{p.name} is back ({pretty(old)} -> active)",
                                f"{fmt(f)} is cleared. Put him back in your lineup if he's on your bench or IR."))
        else:
            prio = 1 if new in OUT_STATUSES and p.lineupSlot not in BENCH_SLOTS else 2
            alerts.append(Alert(f"status:{p.playerId}:{old}:{new}", prio,
                                f"{p.name}: {pretty(old)} -> {pretty(new)}",
                                f"{fmt(f)} has a new injury status." + _injury_note(f, injuries)))
    return alerts


def ir_alerts(league, my_team, injuries):
    """Out for a while and your IR slot is empty: park him there and use the roster spot."""
    import planner
    room = planner.open_ir_slots(league, my_team)
    alerts = []
    for p, back, certain in planner.ir_candidates(league, my_team, injuries)[:room]:
        when = f"back ~{back:%d %b}" if back else "no return date yet"
        if certain:
            alerts.append(Alert(f"ir:{p.playerId}:{back}", 2, f"Move {p.name} to IR ({when})",
                                f"He's listed INJ, so he's IR-eligible and your IR slot is empty. Moving him "
                                "there frees a roster spot to add a streamer without dropping anyone (see Week Ahead)."))
        else:
            alerts.append(Alert(f"ir?:{p.playerId}:{back}", 3, f"Check if {p.name} is IR-eligible ({when})",
                                f"He's listed O (out). ESPN only lets an O player into IR once it marks him "
                                "IR-eligible - check his player card on ESPN.\n      If he is: move him to your "
                                "empty IR slot and add a streamer without dropping anyone.\n      If not: keep him "
                                "on your bench, or drop him for a streamer only if the Week Ahead tab says it's fair."))
    return alerts


def streamer_alerts(league, my_team, fas, injuries, min_gain=15):
    import planner
    picks = planner.streamers(league, my_team, fas, injuries, top=1)
    if not picks or picks[0]["gain"] < min_gain:
        return []
    s = picks[0]
    if s["open_spot"]:
        how = "use your open roster spot (no drop needed)"
    elif s["ir"] and s["ir_certain"]:
        how = f"move {s['ir'].name} (INJ) to IR"
    else:
        drop = (f"drop {s['drop'].name} (base {form_of(s['drop'], league.year).baseline:.1f} vs his "
                f"{s['fa'].baseline:.1f}; {s['later']:+,.0f} pts over the rest of the season)") if s["drop"] else None
        if s["ir"]:
            how = f"move {s['ir'].name} to IR if ESPN lists him IR-eligible" + (f"; otherwise {drop}" if drop else
                                                                                 "; otherwise skip it")
        else:
            how = drop
    room = "open" if s["open_spot"] else (s["ir"] or s["drop"]).playerId
    return [Alert(f"stream:{s['fa'].player.playerId}:{room}", 2,
                  f"Stream {s['fa'].player.name}: +{s['gain']:.0f} pts over the next 7 days",
                  f"{_cap(get_text(league, s['fa'].player))}"
                  f" ({s['fa'].player.position}, {s['fa'].player.proTeam}) and {how}. "
                  f"He plays {s['games']} games this week{' after clearing waivers' if s['fa'].player.playerId in waivers(league) else ''}, "
                  f"{s['light_games']} of them on days you have empty lineup slots.")]


def daily_stream_alerts(league, my_team, fas, injuries):
    """One alert per daily streaming move (planner.daily_streams)."""
    import planner
    plan = planner.daily_streams(league, my_team, fas, injuries)
    if not plan or not plan["moves"]:
        return []
    out = []
    for f, drop, gain in plan["moves"]:
        p = f.player
        out.append(Alert(f"daily:{plan['day']}:{p.playerId}:{drop.playerId}", 1 if plan["label"] == "today" else 2,
                         f"Stream {plan['label']}: add {p.name}, drop {drop.name} (+{gain:.0f} pts {plan['label']})",
                         f"{p.name} ({p.position}, {p.proTeam}, base {f.baseline:.1f}) plays {plan['label']} in a slot that "
                         f"would otherwise be empty. {drop.name} (base {form_of(drop, league.year).baseline:.1f}) has no "
                         f"game {plan['label']} and is no better than the free agents, so someone like him can be "
                         f"picked up again. Players you drop go on waivers for 2 days."))
    return out


def analyze(league, my_team, fa_count=75, teams=None):
    """Form for your roster, every other roster, and the free-agent pool. teams: the teams to use
    (e.g. with pending trades applied); defaults to the league's."""
    year = league.year
    mine = [form_of(p, year) for p in my_team.roster]
    others = [(tm, [form_of(p, year) for p in tm.roster])
              for tm in (teams or league.teams) if tm.team_id != my_team.team_id]
    # proTeam "FA" means unsigned in the NBA: no games, no points.
    fas = [form_of(p, year) for p in league.free_agents(size=fa_count) if p.proTeam != "FA"]
    return mine, others, fas


def build_alerts(league, my_team, thresholds, analysis=None, injuries=None):
    """my_team can be the planned team (pending trades applied, see feed.after_trades); today's
    lineup alert always uses the roster you actually have on ESPN right now."""
    today_team = getattr(my_team, "_team", my_team)
    mine, others, fas = analysis or analyze(league, my_team)
    real = bool(getattr(league, "pro_schedule", None))  # the demo league has no schedule or ESPN ids
    if injuries is None:
        import news
        injuries = news.injuries() if real else {}
    import feed
    leaving = feed.leaving(league, my_team.team_id) if real else set()
    ahead = next_30_days(league, injuries) if real else None
    if real:
        waivers(league)  # waiver players: claim wording, and no games counted before they clear
    alerts = injury_alerts(mine, injuries) + waiver_alerts(mine, fas, thresholds, leaving, ahead, league) + \
        balance_alerts(my_team)
    if real:
        alerts += opportunity_alerts(league, mine, injuries, thresholds, leaving) + minutes_alerts(mine, league.year)
        alerts += (status_alerts(mine, injuries) + lineup_alerts(league, today_team, injuries)
                   + ir_alerts(league, my_team, injuries) + streamer_alerts(league, my_team, fas, injuries)
                   + daily_stream_alerts(league, my_team, fas, injuries))
    alerts += trade_alerts(mine, others, thresholds, leaving)
    alerts += market_alerts(mine, others, league.year, leaving)
    return sorted(alerts, key=lambda a: a.priority)


# ---------------------------------------------------------------- notifications

def load_seen():
    try:
        return set(json.loads(SEEN.read_text()))
    except (OSError, ValueError):
        return set()


def notify(alerts):
    """Toast only alerts not seen on the previous run. Alerts that clear and come back re-notify."""
    seen = load_seen()
    fresh = [a for a in alerts if a.key not in seen]
    SEEN.parent.mkdir(exist_ok=True)
    SEEN.write_text(json.dumps(sorted(a.key for a in alerts)))
    if not fresh:
        return 0
    try:
        from winotify import Notification
    except ImportError:
        print("(winotify not installed; skipping desktop notifications)")
        return 0
    # Windows stacks toasts badly, so show the top 3 and summarize the rest.
    for a in fresh[:3]:
        Notification(app_id="Fantasy Hoops", title=a.title,
                     msg=a.detail.replace("\n      ", "\n")[:250]).show()
    if len(fresh) > 3:
        Notification(app_id="Fantasy Hoops", title=f"+{len(fresh) - 3} more alerts",
                     msg="Run: python app\fantasy.py alerts").show()
    return len(fresh)


# ---------------------------------------------------------------- output

def find_team(league, query):
    for tm in league.teams:
        if str(tm.team_id) == str(query):
            return tm
    matches = [tm for tm in league.teams if str(query).lower() in tm.team_name.lower()]
    if len(matches) == 1:
        return matches[0]
    sys.exit(f"No single team matches '{query}'. Run `python app\fantasy.py teams` to see names and ids.")


def cmd_teams(league, cfg):
    rows = [(tm.team_id, tm.team_name + (" <- you" if tm.team_id == cfg.get("my_team_id") else ""),
             owner_names(tm)) for tm in league.teams]
    table(rows, ("id", "team", "owner(s)"))


def season_points_league(league):
    settings = getattr(league, "settings", None)
    return getattr(settings, "scoring_type", "") == "TOTAL_SEASON_POINTS"


def cmd_standings(league, cfg):
    me = cfg.get("my_team_id")
    if season_points_league(league):
        # Whole season is one scoring period: total points is the standings.
        import projections
        from stats import live_points
        live = live_points(league)  # includes games in progress
        teams = sorted(league.teams, key=lambda t: -live[t.team_id])
        lead = live[teams[0].team_id]
        proj = projections.projected_final(league) or {}
        rows = [(i, tm.team_name + (" *" if tm.team_id == me else ""), owner_names(tm),
                 f"{live[tm.team_id]:.1f}", f"{lead - live[tm.team_id]:.1f}" if i > 1 else "-",
                 f"{proj[tm.team_id][2]:,.0f}" if tm.team_id in proj else "-")
                for i, tm in enumerate(teams, 1)]
        table(rows, ("#", "team", "owner", "points", "behind", "proj final"))
        return
    rows = [(i, tm.team_name + (" *" if tm.team_id == me else ""), f"{tm.wins}-{tm.losses}",
             f"{tm.points_for:.1f}")
            for i, tm in enumerate(league.standings(), 1)]
    table(rows, ("#", "team", "W-L", "PF"))
    try:
        matchups = league.scoreboard()
    except Exception:
        return
    if matchups:
        print("\nThis week")
        for m in matchups:
            home, away = m.home_team, m.away_team
            if not hasattr(away, "team_name"):
                print(f"  {home.team_name}: bye")
                continue
            hs = m.home_team_live_score if m.home_team_live_score is not None else m.home_final_score
            as_ = m.away_team_live_score if m.away_team_live_score is not None else m.away_final_score
            print(f"  {home.team_name} {hs:.1f}  vs  {as_:.1f} {away.team_name}")


def cmd_roster(league, cfg, team_query):
    tm = find_team(league, team_query) if team_query else my_team(league, cfg)
    print(f"{tm.team_name}  ({tm.wins}-{tm.losses})\n")
    forms = sorted((form_of(p, league.year) for p in tm.roster), key=lambda f: -f.worth)
    rows = [(f.player.name, f.player.position, f.player.lineupSlot, f.player.proTeam,
             f.status.replace("_", " ").lower(), f.games, f"{f.baseline:.1f}", f"{f.recent:.1f}",
             f"{f.trend:+.0%}" if f.games >= cfg["thresholds"]["min_games"] else "-")
            for f in forms]
    table(rows, ("player", "pos", "slot", "nba", "status", "gp", "base", "L15", "trend"))


def cmd_alerts(league, cfg, do_notify, save_snapshot):
    if save_snapshot:
        stats.snapshot(league, stats.scoring_rules(league))  # so scheduled checks build the history
    import feed
    me = my_team(league, cfg)
    after = feed.after_trades(league)
    plan = after.get(me.team_id, me)
    analysis = analyze(league, plan, teams=list(after.values()))
    alerts = build_alerts(league, plan, cfg["thresholds"], analysis)
    if save_snapshot:  # the scheduled check also saves today's projections, to measure their accuracy later
        try:
            import accuracy
            import news
            import projections
            live = stats.live_points(league)
            inj = news.injuries()
            accuracy.log({"league": league, "fas": analysis[2], "live": live, "injuries": inj,
                          "projected": projections.projected_final(league, live, list(after.values()), inj)})
        except Exception as e:  # never let bookkeeping break the alerts
            print(f"(couldn't save today's projections: {e})")
    if not alerts:
        print("Nothing to do right now.")
    labels = {1: "DO NOW", 2: "CONSIDER", 3: "FYI"}
    for a in alerts:
        print(f"[{labels[a.priority]}] {a.title}\n      {a.detail}\n")
    if do_notify:
        n = notify(alerts)
        print(f"{n} new notification(s) sent." if n else "No new alerts since last run.")


# ---------------------------------------------------------------- setup

def load_config():
    if not CONFIG.exists():
        sys.exit("No config/config.json yet. Copy config/config.example.json to config/config.json "
                 "and fill it in (see README.md).")
    cfg = json.loads(CONFIG.read_text())
    cfg["thresholds"] = {**DEFAULT_THRESHOLDS, **cfg.get("thresholds", {})}
    return cfg


def connect(cfg):
    from espn_api.basketball import League
    try:
        return League(league_id=int(cfg["league_id"]), year=int(cfg["year"]),
                      espn_s2=cfg.get("espn_s2") or None, swid=cfg.get("swid") or None)
    except Exception as e:
        sys.exit(f"Couldn't load the ESPN league: {e}\n"
                 "Check league_id and year. For a private league, espn_s2 and swid must be set.")


def my_team(league, cfg):
    if cfg.get("my_team_id") is None:
        sys.exit("Set my_team_id in config.json. Run `python app\fantasy.py teams` to find it.")
    return find_team(league, cfg["my_team_id"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="use made-up data instead of ESPN")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("teams")
    sub.add_parser("standings")
    sub.add_parser("accuracy", help="how close the app's projections have been to what actually happened")
    r = sub.add_parser("roster")
    r.add_argument("team", nargs="?", help="team id or part of its name (default: yours)")
    a = sub.add_parser("alerts")
    a.add_argument("--notify", action="store_true", help="send Windows notifications for new alerts")
    lg = sub.add_parser("league")
    lg.add_argument("--split", choices=stats.SPLITS,
                    help="stat window to show (default: season, or projection before games)")
    pl = sub.add_parser("player")
    pl.add_argument("name", nargs="+")
    sub.add_parser("scoring")
    ac = sub.add_parser("activity")
    ac.add_argument("-n", type=int, default=25, help="how many recent transactions")
    sub.add_parser("export")
    kp = sub.add_parser("keep", help="never suggest dropping/trading these players")
    kp.add_argument("name", nargs="*", help="player name or nickname (none: list the keep list)")
    up = sub.add_parser("unkeep")
    up.add_argument("name", nargs="+")
    hi = sub.add_parser("history")
    hi.add_argument("team", nargs="?", help="team id or part of its name (default: yours)")
    args = ap.parse_args()

    if args.demo:
        from demo import demo_league
        league = demo_league()
        cfg = {"my_team_id": 1, "thresholds": dict(DEFAULT_THRESHOLDS)}
    else:
        cfg = load_config()
        league = connect(cfg)

    if args.cmd == "teams":
        cmd_teams(league, cfg)
    elif args.cmd == "accuracy":
        import accuracy
        print(accuracy.report())
    elif args.cmd == "standings":
        cmd_standings(league, cfg)
    elif args.cmd == "roster":
        cmd_roster(league, cfg, args.team)
    elif args.cmd == "alerts":
        cmd_alerts(league, cfg, args.notify, save_snapshot=not args.demo)
    elif args.cmd == "league":
        stats.cmd_league(league, cfg.get("my_team_id"), stats.scoring_rules(league), args.split)
    elif args.cmd == "player":
        stats.cmd_player(league, " ".join(args.name), stats.scoring_rules(league))
    elif args.cmd == "scoring":
        stats.cmd_scoring(league, stats.scoring_rules(league))
    elif args.cmd == "activity":
        stats.cmd_activity(league, args.n)
    elif args.cmd in ("keep", "unkeep"):
        if args.cmd == "keep" and not args.name:
            names = sorted(json.loads(KEEP.read_text(encoding="utf-8")).get("players", {}).values()) \
                if KEEP.exists() else []
            print("Keep list: " + (", ".join(names) if names else "empty"))
        else:
            p, _ = stats.find_player(league, " ".join(args.name))
            names = set_keep(p, args.cmd == "keep").values()
            print(f"{'Kept' if args.cmd == 'keep' else 'Removed'} {p.name}. Keep list: {', '.join(sorted(names)) or 'empty'}")
    elif args.cmd == "export":
        if args.demo:
            sys.exit("export is disabled in --demo so made-up data never lands in data/.")
        stats.cmd_export(league, stats.scoring_rules(league))
    elif args.cmd == "history":
        stats.cmd_history(league, find_team(league, args.team) if args.team else my_team(league, cfg))


if __name__ == "__main__":
    main()
