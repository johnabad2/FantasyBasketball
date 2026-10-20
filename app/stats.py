"""League-wide stats from ESPN: every roster's numbers, any player's full stat line, the
league's scoring rules, recent moves, CSV/JSON exports, and a daily history of points."""
import csv
import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # project folder (this file is in app/)
DATA = ROOT / "data"
HISTORY = DATA / "history.db"
NICKNAMES = ROOT / "config" / "nicknames.json"

BOX = ["PTS", "REB", "AST", "STL", "BLK", "TO", "FG", "3P", "FT", "MPG"]
# shooting columns show volume and efficiency together: made-attempted per game, then the %
SHOOTING = {"FG": ("FGM", "FGA"), "3P": ("3PM", "3PA"), "FT": ("FTM", "FTA")}
SHOOTING_WIDTHS = {"FG": 135, "3P": 130, "FT": 120}  # pixel widths for these columns in the window
SPLITS = ["total", "last_7", "last_15", "last_30", "projected"]
SPLIT_LABELS = {"total": "season", "last_7": "last 7 days", "last_15": "last 15 days",
                "last_30": "last 30 days", "projected": "projected"}


# ---------------------------------------------------------------- helpers

def table(rows, headers):
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, widths))
    print(line(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(line(r))


def live_points(league):
    """{fantasy team id: season points including games still in progress}. ESPN only adds a day's
    points to the standings once the day is over; the matchup's live total has them as they happen.
    Falls back to the standings points if ESPN can't be reached (or in the demo)."""
    live = {t.team_id: t.points_for for t in league.teams}
    if not getattr(league, "pro_schedule", None):
        return live
    try:
        data = league.espn_request.league_get(params={"view": ["mMatchupScore", "mLiveScoring"]})
    except Exception:
        return live
    for matchup in data.get("schedule", []):
        for side in matchup.get("teams", []):
            pts = side.get("totalPointsLive")
            if side.get("teamId") in live and pts is not None:
                live[side["teamId"]] = max(live[side["teamId"]], pts)
    return live


def owner_names(team):
    return ", ".join(
        f"{o.get('firstName', '')} {o.get('lastName', '')}".strip() if isinstance(o, dict) else str(o)
        for o in (team.owners or []))


def scoring_rules(league):
    """{stat name: fantasy points per unit}, read from the league's own settings."""
    from espn_api.basketball.constant import STATS_MAP
    settings = getattr(league, "settings", None)
    items = (getattr(settings, "_raw_scoring_settings", None) or {}).get("scoringItems", [])
    return {STATS_MAP.get(str(i["statId"]), str(i["statId"])): i["points"] for i in items}


def split_of(player, year, key):
    return player.stats.get(f"{year}_{key}", {})


def games(split):
    return int(((split.get("total") or {}).get("GP", 0)) or 0)


def fp_per_game(split, rules):
    if split.get("applied_avg"):
        return float(split["applied_avg"])
    avg = split.get("avg") or {}
    return sum(avg.get(stat, 0) * pts for stat, pts in rules.items())


def shooting(split, stat):
    """(made, attempted, pct) per game for FG / 3P / FT, or None if ESPN has no numbers."""
    made_key, att_key = SHOOTING[stat]
    avg = split.get("avg") or {}
    made, att = avg.get(made_key), avg.get(att_key)
    if made is None or att is None:
        return None
    return made, att, (made / att * 100 if att else 0.0)


def box(split, stat):
    if stat in SHOOTING:
        s = shooting(split, stat)
        return "-" if s is None else f"{s[0]:.1f}-{s[1]:.1f} {s[2]:.0f}%"
    v = (split.get("avg") or {}).get(stat)
    if v is None:
        return "-"
    if stat in ("FG%", "FT%"):
        return f"{v * 100:.0f}" if v <= 1 else f"{v:.0f}"
    return f"{v:.1f}"


def season_or_projection(player, year):
    """The season split once the player has played, else ESPN's projection."""
    season = split_of(player, year, "total")
    if games(season):
        return season, False
    return split_of(player, year, "projected"), True


# ---------------------------------------------------------------- history

def _db():
    DATA.mkdir(exist_ok=True)
    db = sqlite3.connect(HISTORY)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS team_daily (
            day TEXT, team_id INTEGER, team_name TEXT, points REAL,
            PRIMARY KEY (day, team_id));
        CREATE TABLE IF NOT EXISTS player_daily (
            day TEXT, player_id INTEGER, name TEXT, fantasy_team_id INTEGER,
            games INTEGER, fp_total REAL, fp_avg REAL,
            PRIMARY KEY (day, player_id));
    """)
    return db


def snapshot(league, rules):
    """Record today's team totals and player numbers. Re-running on the same day overwrites."""
    today = date.today().isoformat()
    with _db() as db:
        for tm in league.teams:
            db.execute("INSERT OR REPLACE INTO team_daily VALUES (?,?,?,?)",
                       (today, tm.team_id, tm.team_name, tm.points_for))
            for p in tm.roster:
                s = split_of(p, league.year, "total")
                db.execute("INSERT OR REPLACE INTO player_daily VALUES (?,?,?,?,?,?,?)",
                           (today, p.playerId, p.name, tm.team_id, games(s),
                            s.get("applied_total", 0), fp_per_game(s, rules)))
    return today


def previous_points():
    """({team_id: points}, day) from the latest snapshot before today, or ({}, None)."""
    if not HISTORY.exists():
        return {}, None
    with _db() as db:
        row = db.execute("SELECT MAX(day) FROM team_daily WHERE day < ?",
                         (date.today().isoformat(),)).fetchone()
        if not row[0]:
            return {}, None
        pts = dict(db.execute("SELECT team_id, points FROM team_daily WHERE day = ?", (row[0],)))
        return pts, row[0]


# ---------------------------------------------------------------- commands

def cmd_league(league, my_id, rules, split_key=None):
    year = league.year
    teams = sorted(league.teams, key=lambda t: -t.points_for)
    leader = teams[0].points_for if teams else 0
    prev, prev_day = previous_points()
    used_projection = False
    print(f"Split: {SPLIT_LABELS[split_key] if split_key else 'season (projected if no games yet)'}\n")
    for rank, tm in enumerate(teams, 1):
        head = f"{rank}. {tm.team_name}{'  <- you' if tm.team_id == my_id else ''}  ({owner_names(tm)})"
        head += f"   {tm.points_for:.1f} pts"
        if tm.team_id in prev:
            head += f"   {tm.points_for - prev[tm.team_id]:+.1f} since {prev_day}"
        if rank > 1:
            head += f"   {leader - tm.points_for:.1f} behind 1st"
        print(head)
        rows = []
        for p in tm.roster:
            if split_key:
                s, proj = split_of(p, year, split_key), split_key == "projected"
            else:
                s, proj = season_or_projection(p, year)
            used_projection |= proj and not split_key
            season = split_of(p, year, "total")
            rows.append((
                p.name + (" *" if proj and not split_key else ""), p.position, p.proTeam,
                p.lineupSlot, (p.injuryStatus or "ACTIVE").replace("_", " ").lower(),
                games(s if split_key else season),  # games actually played, not projected
                f"{season.get('applied_total', 0):.0f}", f"{fp_per_game(s, rules):.1f}",
                *(box(s, stat) for stat in BOX)))
        rows.sort(key=lambda r: -float(r[7]))
        table(rows, ("player", "pos", "nba", "slot", "status", "gp", "fp tot", "fp/g", *BOX))
        print()
    if used_projection:
        print("* no games yet: per-game numbers are ESPN's projection.")


def norm(text):
    """Lowercase, no accents or punctuation: 'Jokić' -> 'jokic', 'P.J. Washington' -> 'pj washington'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[.'’]", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _nickname_file():
    try:
        data = json.loads(NICKNAMES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: nicks for name, nicks in data.items() if not name.startswith("_")}


def nickname_lookup():
    """{normalized nickname: player name}"""
    return {norm(nick): name for name, nicks in _nickname_file().items() for nick in nicks}


def nicknames_for(player_name):
    target = norm(player_name)
    return next((nicks for name, nicks in _nickname_file().items()
                 if norm(name) == target or target.startswith(norm(name) + " ")), [])


def find_player(league, query):
    """(player, fantasy team or None). Accepts nicknames from config/nicknames.json.
    Searches rosters first, then every NBA player."""
    q = norm(query)
    q = norm(nickname_lookup().get(q, query))
    rostered = [(p, tm) for tm in league.teams for p in tm.roster if q in norm(p.name)]
    exact = [x for x in rostered if norm(x[0].name) == q]
    if len(exact) == 1 or len(rostered) == 1:
        return (exact or rostered)[0]
    if len(rostered) > 1:
        raise SystemExit("Several rostered players match: "
                         + ", ".join(p.name for p, _ in rostered) + ". Be more specific.")
    names = {n for n in getattr(league, "player_map", {}).values()
             if isinstance(n, str) and q in norm(n)}
    if not names:
        raise SystemExit(f"No player matches '{query}'.")
    exact_names = [n for n in names if norm(n) == q]
    if len(names) > 1 and not exact_names:
        raise SystemExit("Several players match: " + ", ".join(sorted(names)[:15]) + ". Be more specific.")
    return league.player_info(name=(exact_names or sorted(names))[0]), None


def cmd_player(league, query, rules):
    year = league.year
    p, tm = find_player(league, query)
    status = (p.injuryStatus or "ACTIVE").replace("_", " ").lower()
    owner = f"{tm.team_name} ({owner_names(tm)})" if tm else "free agent"
    print(f"{p.name}  {p.position}, {p.proTeam}  |  {status}  |  fantasy team: {owner}")
    nicks = nicknames_for(p.name)
    print(f"aka {', '.join(nicks)}\n" if nicks else "")
    rows = [(SPLIT_LABELS[k], games(split_of(p, year, k)), f"{fp_per_game(split_of(p, year, k), rules):.1f}",
             *(box(split_of(p, year, k), stat) for stat in BOX))
            for k in SPLITS if split_of(p, year, k)]
    table(rows, ("split", "gp", "fp/g", *BOX))

    s, proj = season_or_projection(p, year)
    avg = s.get("avg") or {}
    parts = sorted(((stat, avg.get(stat, 0), pts, avg.get(stat, 0) * pts) for stat, pts in rules.items()),
                   key=lambda x: -abs(x[3]))
    print(f"\nWhere the points come from ({'projected' if proj else 'season'}, per game)")
    table([(stat, f"{per:.1f}", f"{pts:+g}", f"{fp:+.1f}") for stat, per, pts, fp in parts if per],
          ("stat", "per game", "pts each", "fantasy pts"))


def cmd_scoring(league, rules):
    s = league.settings
    print(f"{s.name}  |  {s.team_count} teams  |  scoring: {(s.scoring_type or '').replace('_', ' ').lower()}")
    if s.trade_deadline:
        print(f"Trade deadline: {datetime.fromtimestamp(s.trade_deadline / 1000):%d %b %Y}")
    if s.acquisition_limit not in (None, -1):
        print(f"Acquisition limit: {s.acquisition_limit}")
    print()
    table([(stat, f"{pts:+g}") for stat, pts in sorted(rules.items(), key=lambda x: -x[1])],
          ("stat", "points"))


def cmd_activity(league, size):
    import chat
    items = []
    for act in league.recent_activity(size=size):
        when = datetime.fromtimestamp(act.date / 1000)
        for team, action, player, *_ in act.actions:
            items.append((when, getattr(team, "team_name", team) or "-", action.lower(),
                          getattr(player, "name", player) or "-"))
    if getattr(league, "pro_schedule", None):
        items += [(m["when"], m["team"] or "-", "chat", f'{m["author"]}: {m["text"]}') for m in chat.league_chat(league)]
    items.sort(key=lambda x: x[0], reverse=True)
    rows = [(when.strftime("%d %b %H:%M"), *rest) for when, *rest in items[:size]]
    if rows:
        table(rows, ("when", "team", "move", "player / message"))
    else:
        print("No recent activity.")


def cmd_history(league, team):
    if not HISTORY.exists():
        raise SystemExit("No history yet. Run `python app\fantasy.py export` (or let alerts run) to start one.")
    with _db() as db:
        days = db.execute("SELECT day, points FROM team_daily WHERE team_id = ? ORDER BY day",
                          (team.team_id,)).fetchall()
        ranks = {day: 1 + db.execute("SELECT COUNT(*) FROM team_daily WHERE day = ? AND points > ?",
                                     (day, pts)).fetchone()[0] for day, pts in days}
    print(f"{team.team_name}\n")
    rows, last = [], None
    for day, pts in days:
        rows.append((day, f"{pts:.1f}", f"{pts - last:+.1f}" if last is not None else "-", ranks[day]))
        last = pts
    table(rows, ("day", "points", "change", "rank"))


def player_row(p, year, rules, team_name, owner):
    row = {"fantasy_team": team_name, "owner": owner, "player": p.name, "player_id": p.playerId,
           "pos": p.position, "nba_team": p.proTeam, "slot": getattr(p, "lineupSlot", ""),
           "status": p.injuryStatus or "ACTIVE",
           "season_fp_total": split_of(p, year, "total").get("applied_total", 0)}
    for k in SPLITS:
        s = split_of(p, year, k)
        row[f"{k}_gp"] = games(s)
        row[f"{k}_fp_per_game"] = round(fp_per_game(s, rules), 2)
    s, _ = season_or_projection(p, year)
    for stat in BOX:
        if stat in SHOOTING:  # separate columns so the CSV stays numeric in Excel
            made, att, pct = shooting(s, stat) or ("", "", "")
            row[f"{SHOOTING[stat][0]}_per_game"] = round(made, 2) if made != "" else ""
            row[f"{SHOOTING[stat][1]}_per_game"] = round(att, 2) if att != "" else ""
            row[f"{stat}%"] = round(pct, 1) if pct != "" else ""
        else:
            row[f"{stat}_per_game"] = box(s, stat)
    return row


def cmd_export(league, rules):
    DATA.mkdir(exist_ok=True)
    year = league.year
    players = [player_row(p, year, rules, tm.team_name, owner_names(tm))
               for tm in league.teams for p in tm.roster]
    players += [player_row(p, year, rules, "FREE AGENT", "") for p in league.free_agents(size=100)]
    teams = [{"team_id": tm.team_id, "team": tm.team_name, "owner": owner_names(tm),
              "points": tm.points_for, "acquisitions": getattr(tm, "acquisitions", 0),
              "trades": getattr(tm, "trades", 0)} for tm in league.teams]
    for name, rows in (("players.csv", players), ("teams.csv", teams)):
        with open(DATA / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    (DATA / "league.json").write_text(json.dumps(
        {"exported": datetime.now().isoformat(timespec="seconds"), "scoring": rules,
         "teams": teams, "players": players}, indent=1), encoding="utf-8")
    day = snapshot(league, rules)
    print(f"Wrote data/players.csv ({len(players)} players), data/teams.csv, data/league.json")
    print(f"Saved today's snapshot ({day}) to data/history.db")
