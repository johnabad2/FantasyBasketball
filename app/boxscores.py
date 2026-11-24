"""This season's finished NBA box scores from ESPN's public game summary, one file per game in
data/boxscores_fp/ (finished games never change, so each is downloaded once).

Used by defense.py (points allowed to each position), usage.py (how players do when a teammate sits)
and backtest.py (replaying the season to check the projections)."""
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from stats import DATA

CACHE_DIR = DATA / "boxscores_fp"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gid}"
BUCKET = {"PG": "G", "SG": "G", "G": "G", "SF": "F", "PF": "F", "F": "F", "C": "C"}
VERSION = 2  # rows carry the player's id


def _parse(data):
    out = {}
    for team in data.get("boxscore", {}).get("players", []):
        rows = []
        for block in team.get("statistics", []):
            keys = block.get("keys", [])
            for a in block.get("athletes", []):
                if a.get("didNotPlay") or not a.get("stats"):
                    continue
                v = dict(zip(keys, a["stats"]))

                def split(k):
                    return [float(x or 0) for x in (v.get(k) or "0-0").split("-")]
                try:
                    fgm, fga = split("fieldGoalsMade-fieldGoalsAttempted")
                    tpm, tpa = split("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
                    ftm, fta = split("freeThrowsMade-freeThrowsAttempted")
                    s = {"PTS": float(v.get("points") or 0), "FGM": fgm, "FGA": fga, "3PM": tpm, "3PA": tpa,
                         "FTM": ftm, "FTA": fta, "REB": float(v.get("rebounds") or 0),
                         "OREB": float(v.get("offensiveRebounds") or 0), "DREB": float(v.get("defensiveRebounds") or 0),
                         "AST": float(v.get("assists") or 0), "TO": float(v.get("turnovers") or 0),
                         "STL": float(v.get("steals") or 0), "BLK": float(v.get("blocks") or 0),
                         "PF": float(v.get("fouls") or 0)}
                    minutes = float(v.get("minutes") or 0)
                except ValueError:
                    continue
                ath = a.get("athlete", {})
                pos = BUCKET.get((ath.get("position") or {}).get("abbreviation", ""), None)
                if pos and minutes > 0:
                    rows.append([pos, minutes, s, int(ath.get("id") or 0)])
        out[str(team["team"]["id"])] = rows
    return out


def box(gid):
    """{NBA team id (str): [[G/F/C, minutes, {stat: value}, player id]]} for one finished game, or None
    if it hasn't finished. Players who didn't play aren't listed."""
    path = CACHE_DIR / f"{gid}.json"
    if path.exists():
        cached = json.loads(path.read_text())
        if cached.get("v") == VERSION:
            return cached["teams"]
    with urllib.request.urlopen(SUMMARY.format(gid=gid), timeout=15) as r:
        data = json.load(r)
    if not (data.get("header", {}).get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed")):
        return None
    out = _parse(data)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"v": VERSION, "teams": out}))
    return out


def season(league):
    """[(date, game id, home id, away id, box)] for every finished game this season, oldest first.
    Cached on the league object for the session; [] without a schedule or if ESPN can't be reached."""
    if getattr(league, "_fh_boxes", None) is not None:
        return league._fh_boxes
    schedule = getattr(league, "pro_schedule", None) or {}
    today = getattr(league, "current_week", 1) or 1
    games = {}
    for days in schedule.values():
        for day, gs in days.items():
            if int(day) < today:
                for g in gs:
                    games[g["id"]] = g
    try:
        with ThreadPoolExecutor(8) as pool:
            boxes = dict(zip(games, pool.map(box, games)))
    except Exception:
        boxes = {}
    from matchups import EASTERN
    out = [(datetime.fromtimestamp(g["date"] / 1000, EASTERN).date(), gid, g["homeProTeamId"], g["awayProTeamId"],
            boxes[gid]) for gid, g in games.items() if boxes.get(gid)]
    league._fh_boxes = sorted(out, key=lambda r: (r[0], str(r[1])))
    return league._fh_boxes


def fantasy_points(stats, rules):
    return sum(rules.get(k, 0) * v for k, v in stats.items())
