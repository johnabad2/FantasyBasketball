"""ESPN NBA fantasy tracker: standings, rosters, and trade / waiver / injury alerts.

Usage:
    python fantasy.py teams               list every team and its id (to set my_team_id)
    python fantasy.py standings           league table + this week's matchups
    python fantasy.py roster [TEAM]       one roster with form (default: your team)
    python fantasy.py alerts [--notify]   what you should do now; --notify pops Windows toasts
Add --demo before the command to run against made-up data without an ESPN league.
"""
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
SEEN = ROOT / "seen_alerts.json"

OUT_STATUSES = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
SHAKY_STATUSES = {"DAY_TO_DAY", "QUESTIONABLE", "DOUBTFUL"}
BENCH_SLOTS = {"BE", "IR"}

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


def injury_alerts(mine):
    alerts = []
    for f in mine:
        slot = f.player.lineupSlot
        if f.status in OUT_STATUSES and slot not in BENCH_SLOTS:
            alerts.append(Alert(f"inj:{f.player.playerId}:{f.status}", 1,
                                f"Bench {f.player.name}: {f.status.replace('_', ' ').lower()}",
                                f"{fmt(f)} is in your {slot} slot but listed {f.status}. "
                                "Swap in a healthy bench player"
                                + (" or move them to IR." if f.status == "INJURY_RESERVE" else ".")))
        elif f.status in SHAKY_STATUSES and slot not in BENCH_SLOTS:
            alerts.append(Alert(f"inj:{f.player.playerId}:{f.status}", 2,
                                f"Check on {f.player.name}: {f.status.replace('_', ' ').lower()}",
                                f"{fmt(f)} is starting at {slot}. Check news before lineups lock."))
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


def waiver_alerts(mine, free_agents, t):
    droppable = sorted((f for f in mine if f.player.lineupSlot != "IR"), key=lambda f: f.worth)
    alerts = []
    # Best free agent vs your worst player, next best vs your second worst, and so on.
    for fa in sorted(free_agents, key=lambda f: f.worth, reverse=True):
        if len(alerts) >= min(t["max_waiver_alerts"], len(droppable)):
            break
        worst = droppable[len(alerts)]
        if fa.healthy and fa.worth > worst.worth * (1 + t["waiver_margin"]):
            alerts.append(Alert(f"fa:{fa.player.playerId}:{worst.player.playerId}", 1,
                                f"Pick up {fa.player.name}, drop {worst.player.name}",
                                f"{fmt(fa)}: {nums(fa)}\n      vs {fmt(worst)}: {nums(worst)}"))
    return alerts


def trade_alerts(mine, others, t):
    """Pair one of your players with one of theirs where:
    - their player is better on baseline (you gain real value), and
    - their player's recent form is no better than yours (the deal looks fair to them).
    The classic version: sell your hot player, buy their slumping star.
    """
    ideas = []
    for team, roster in others:
        best = None
        for give in mine:
            if give.games < t["min_games"] or not give.healthy:
                continue
            for get in roster:
                if get.games < t["min_games"] or not get.healthy:
                    continue
                gain = get.baseline - give.baseline
                if gain < give.baseline * t["trade_margin"]:
                    continue
                if get.recent > give.recent * 1.05:
                    continue  # they'd be giving up the hotter player; unlikely to accept
                if best is None or gain > best[0]:
                    best = (gain, give, get)
        if best:
            ideas.append((team, *best))
    ideas.sort(key=lambda x: x[1], reverse=True)
    return [
        Alert(f"trade:{team.team_id}:{give.player.playerId}:{get.player.playerId}", 2,
              f"Trade idea with {team.team_name}: {give.player.name} for {get.player.name}",
              f"Send {fmt(give)}: {nums(give)}\n      Get  {fmt(get)}: {nums(get)}\n"
              f"      +{gain:.1f} baseline/game for you; their recent form looks no better than "
              "yours, so the offer looks fair to them.")
        for team, gain, give, get in ideas[: t["max_trade_alerts"]]
    ]


def build_alerts(league, my_team, thresholds):
    year = league.year
    mine = [form_of(p, year) for p in my_team.roster]
    others = [(tm, [form_of(p, year) for p in tm.roster])
              for tm in league.teams if tm.team_id != my_team.team_id]
    # proTeam "FA" means unsigned in the NBA: no games, no points.
    fas = [form_of(p, year) for p in league.free_agents(size=75) if p.proTeam != "FA"]
    alerts = (injury_alerts(mine) + waiver_alerts(mine, fas, thresholds)
              + trade_alerts(mine, others, thresholds) + form_alerts(mine, thresholds))
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
                     msg="Run: python fantasy.py alerts").show()
    return len(fresh)


# ---------------------------------------------------------------- output

def table(rows, headers):
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, widths))
    print(line(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(line(r))


def find_team(league, query):
    for tm in league.teams:
        if str(tm.team_id) == str(query):
            return tm
    matches = [tm for tm in league.teams if str(query).lower() in tm.team_name.lower()]
    if len(matches) == 1:
        return matches[0]
    sys.exit(f"No single team matches '{query}'. Run `python fantasy.py teams` to see names and ids.")


def cmd_teams(league, cfg):
    rows = []
    for tm in league.teams:
        owners = ", ".join(
            f"{o.get('firstName', '')} {o.get('lastName', '')}".strip() if isinstance(o, dict) else str(o)
            for o in (tm.owners or []))
        mark = " <- you" if tm.team_id == cfg.get("my_team_id") else ""
        rows.append((tm.team_id, tm.team_name + mark, owners))
    table(rows, ("id", "team", "owner(s)"))


def cmd_standings(league, cfg):
    me = cfg.get("my_team_id")
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


def cmd_alerts(league, cfg, do_notify):
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
        sys.exit("No config.json yet. Copy config.example.json to config.json and fill it in "
                 "(see README.md).")
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
        sys.exit("Set my_team_id in config.json. Run `python fantasy.py teams` to find it.")
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
        cmd_alerts(league, cfg, args.notify)


if __name__ == "__main__":
    main()
