"""How good are the app's projections? Once a day it saves what it expects from every rostered player
(and the best free agents) and every team, then later compares that with what actually happened.

    .venv\\Scripts\\python app\\fantasy.py accuracy

Players: projected points per game on a day vs what he actually averaged over his next games
(from the change in his season totals between two saved days). Compared with two simple guesses:
his season average so far, and ESPN's projection alone, so you can see if the app beats them.
Teams: projected points for the next 7 days vs the points actually scored in those 7 days.
Saved in data/accuracy.db (stays on your PC)."""
import sqlite3
from datetime import date, timedelta

import fantasy
import stats

DB = stats.DATA / "accuracy.db"


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS players (day TEXT, player_id INTEGER, name TEXT, base REAL,
                   season_avg REAL, espn REAL, fp_total REAL, gp INTEGER, PRIMARY KEY (day, player_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS teams (day TEXT, team_id INTEGER, name TEXT, points REAL,
                   next7 REAL, final REAL, PRIMARY KEY (day, team_id))""")
    return con


def log(d):
    """Save today's projections (once a day; later calls the same day are ignored)."""
    league = d["league"]
    if d.get("demo") or not getattr(league, "pro_schedule", None):
        return
    import planner
    today = date.today().isoformat()
    con = _db()
    if con.execute("SELECT 1 FROM teams WHERE day = ? LIMIT 1", (today,)).fetchone():
        con.close()
        return
    year = league.year
    players = {p.playerId: p for t in league.teams for p in t.roster}
    for f in d.get("fas") or []:
        players.setdefault(f.player.playerId, f.player)
    rows = []
    for p in players.values():
        f = fantasy.form_of(p, year)
        season = stats.split_of(p, year, "total")
        rows.append((today, p.playerId, p.name, f.baseline, fantasy.split_value(season) if f.games else None,
                     fantasy.split_value(stats.split_of(p, year, "projected")), season.get("applied_total", 0) or 0,
                     f.games))
    con.executemany("INSERT OR REPLACE INTO players VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    proj = d.get("projected") or {}
    injuries = d.get("injuries") or {}
    teams = []
    for t in league.teams:
        vals = {p.playerId: fantasy.form_of(p, year).baseline for p in t.roster}
        next7 = sum(day["points"] for day in planner.week(league, t.roster, vals, injuries, 7))
        teams.append((today, t.team_id, t.team_name.strip(), (d.get("live") or {}).get(t.team_id, t.points_for),
                      next7, proj[t.team_id][2] if t.team_id in proj else None))
    con.executemany("INSERT OR REPLACE INTO teams VALUES (?, ?, ?, ?, ?, ?)", teams)
    con.commit()
    con.close()


def report(min_gap=7, min_games=2):
    """Text report: how far off the projections were, once there's enough history."""
    con = _db()
    days = [r[0] for r in con.execute("SELECT DISTINCT day FROM teams ORDER BY day")]
    lines = [f"Saved projections for {len(days)} day(s)" + (f" ({days[0]} to {days[-1]})" if days else "") + "."]
    pairs = [(a, b) for a in days for b in days if date.fromisoformat(b) - date.fromisoformat(a) >= timedelta(min_gap)]
    if not pairs:
        need = min_gap - (len(days) and (date.today() - date.fromisoformat(days[0])).days)
        lines.append(f"Not enough history yet: the first check is possible about {max(need, 1)} day(s) from now.")
        con.close()
        return "\n".join(lines)
    # players: projection on day a vs what he actually averaged in his games between a and the first day b >= a + gap
    errs = {"app": [], "season average": [], "ESPN projection": []}
    seen = set()
    for a, b in pairs:
        if a in seen:
            continue
        seen.add(a)
        rows = con.execute("""SELECT p.base, p.season_avg, p.espn, q.fp_total - p.fp_total, q.gp - p.gp
                              FROM players p JOIN players q ON q.player_id = p.player_id AND q.day = ?
                              WHERE p.day = ?""", (b, a)).fetchall()
        for base, season_avg, espn, fp, gp in rows:
            if gp is None or gp < min_games:
                continue
            actual = fp / gp
            errs["app"].append(abs(base - actual))
            errs["ESPN projection"].append(abs(espn - actual))
            errs["season average"].append(abs((season_avg if season_avg is not None else espn) - actual))
    if errs["app"]:
        lines.append(f"\nPlayers ({len(errs['app'])} checks): average miss in fantasy points per game")
        for name, e in sorted(errs.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
            lines.append(f"  {name:16} {sum(e) / len(e):5.2f}" + ("   <- the app" if name == "app" else ""))
    # teams: projected next 7 days vs points actually scored in those 7 days
    team_errs = []
    for a, b in pairs:
        if (date.fromisoformat(b) - date.fromisoformat(a)).days != min_gap:
            continue
        for next7, start, end in con.execute("""SELECT p.next7, p.points, q.points FROM teams p JOIN teams q
                                                ON q.team_id = p.team_id AND q.day = ? WHERE p.day = ?""", (b, a)):
            team_errs.append((next7, end - start))
    if team_errs:
        miss = sum(abs(p - a) for p, a in team_errs) / len(team_errs)
        bias = sum(p - a for p, a in team_errs) / len(team_errs)
        lines.append(f"\nTeams ({len(team_errs)} weeks): projected 7-day points were off by {miss:.0f} on average "
                     f"({'too high' if bias > 0 else 'too low'} by {abs(bias):.0f} on average).")
    con.close()
    return "\n".join(lines)
