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
from dataclasses import dataclass
from pathlib import Path

import stats
from stats import owner_names, table

ROOT = Path(__file__).resolve().parent.parent  # project folder (this file is in app/)
CONFIG = ROOT / "config" / "config.json"
SEEN = ROOT / "data" / "seen_alerts.json"
KEEP = ROOT / "config" / "keep.json"

OUT_STATUSES = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
SHAKY_STATUSES = {"DAY_TO_DAY", "QUESTIONABLE", "DOUBTFUL"}
BENCH_SLOTS = {"BE", "IR"}
POSITIONS = ("PG", "SG", "SF", "PF", "C")
MIN_AT_POSITION, MAX_AT_POSITION = 2, 3  # aim for 2-3 players at each main position

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
    baseline: float  # what the player "really" is: season avg, blended with projection early on
    recent: float    # last 15 days (falls back to last 7, then baseline)
    games: int

    @property
    def trend(self):
        return (self.recent - self.baseline) / self.baseline if self.baseline else 0.0

    @property
    def worth(self):
        return (self.baseline + self.recent) / 2

    @property
    def status(self):
        return self.player.injuryStatus or "ACTIVE"

    @property
    def healthy(self):
        return self.status not in OUT_STATUSES


def form_of(player, year):
    stats = player.stats
    season = stats.get(f"{year}_total", {})
    games = int((season.get("total") or {}).get("GP", 0) or 0)
    season_v = split_value(season)
    proj_v = split_value(stats.get(f"{year}_projected", {}))
    # Lean on projections until there's a real sample; fully trust the season after 10 games.
    weight = min(games, 10) / 10
    baseline = weight * season_v + (1 - weight) * proj_v if proj_v else season_v
    recent = next(
        (v for v in (split_value(stats.get(f"{year}_last_15", {})),
                     split_value(stats.get(f"{year}_last_7", {}))) if v),
        baseline,
    )
    return Form(player, round(baseline, 2), round(recent, 2), games)


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
    """The balance problems a move would create that your roster doesn't already have, as text.
    [] means the move is fine (it may still leave an existing problem as it is)."""
    drop_ids = {p.playerId for p in drop}
    after = [p for p in players if p.playerId not in drop_ids] + list(add)
    before = balance_problems(players)
    return [describe_problem(k, pos, names) for (k, pos), names in balance_problems(after).items()
            if (k, pos) not in before]


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


def form_alerts(mine, t):
    alerts = []
    for f in mine:
        if f.games < t["min_games"] or not f.healthy:
            continue
        if f.trend <= -t["trend_pct"]:
            alerts.append(Alert(f"slump:{f.player.playerId}", 3,
                                f"{f.player.name} is slumping ({f.trend:+.0%})",
                                f"{fmt(f)}: {nums(f)}. If the baseline is solid, hold: "
                                "selling now gets you the least back."))
        elif f.trend >= t["trend_pct"]:
            alerts.append(Alert(f"hot:{f.player.playerId}", 2,
                                f"Sell-high window: {f.player.name} ({f.trend:+.0%})",
                                f"{fmt(f)}: {nums(f)}. Other managers see the hot streak, "
                                "so this is when they're worth the most in a trade."))
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


def waiver_alerts(mine, free_agents, t, leaving=()):
    keep = load_keep() | set(leaving)  # never drop: kept, or in a trade that's going through
    droppable = sorted((f for f in mine if f.player.lineupSlot != "IR" and f.player.playerId not in keep),
                       key=lambda f: f.worth)
    roster = [f.player for f in mine]
    alerts, used = [], set()
    # Best free agent vs your worst player, next best vs your second worst, and so on - skipping
    # swaps that would unbalance your positions (e.g. a 4th PG, or no backup C).
    for fa in sorted(free_agents, key=lambda f: f.worth, reverse=True):
        if len(alerts) >= min(t["max_waiver_alerts"], len(droppable)):
            break
        worst = next((d for d in droppable if d.player.playerId not in used
                      and not unbalances(roster, [fa.player], [d.player])), None)
        if worst and fa.healthy and fa.worth > worst.worth * (1 + t["waiver_margin"]):
            used.add(worst.player.playerId)
            alerts.append(Alert(f"fa:{fa.player.playerId}:{worst.player.playerId}", 1,
                                f"Pick up {fa.player.name}, drop {worst.player.name}",
                                f"{fmt(fa)}: {nums(fa)}\n      vs {fmt(worst)}: {nums(worst)}"))
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
                  f"Add {fmt(s['fa'])} and {how}. He plays {s['games']} games this week, "
                  f"{s['light_games']} of them on days you have empty lineup slots.")]


def analyze(league, my_team, fa_count=75):
    """Form for your roster, every other roster, and the free-agent pool."""
    year = league.year
    mine = [form_of(p, year) for p in my_team.roster]
    others = [(tm, [form_of(p, year) for p in tm.roster])
              for tm in league.teams if tm.team_id != my_team.team_id]
    # proTeam "FA" means unsigned in the NBA: no games, no points.
    fas = [form_of(p, year) for p in league.free_agents(size=fa_count) if p.proTeam != "FA"]
    return mine, others, fas


def build_alerts(league, my_team, thresholds, analysis=None, injuries=None):
    mine, others, fas = analysis or analyze(league, my_team)
    real = bool(getattr(league, "pro_schedule", None))  # the demo league has no schedule or ESPN ids
    if injuries is None:
        import news
        injuries = news.injuries() if real else {}
    import feed
    leaving = feed.leaving(league, my_team.team_id) if real else set()
    alerts = injury_alerts(mine, injuries) + waiver_alerts(mine, fas, thresholds, leaving) + balance_alerts(my_team)
    if real:
        alerts += (status_alerts(mine, injuries) + lineup_alerts(league, my_team, injuries)
                   + ir_alerts(league, my_team, injuries) + streamer_alerts(league, my_team, fas, injuries))
    alerts += trade_alerts(mine, others, thresholds, leaving) + form_alerts(mine, thresholds)
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
    alerts = build_alerts(league, my_team(league, cfg), cfg["thresholds"])
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
