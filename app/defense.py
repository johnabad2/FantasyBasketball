"""Defence vs position: how many fantasy points each NBA team gives up to guards, forwards and centres.

ESPN doesn't publish this for basketball, so it's worked out from ESPN's public box scores (which list
every player's position, G / F / C, and full stat line): for each team, the fantasy points per minute
its opponents' guards, forwards and centres scored against it, using your league's scoring.

Only the position *tilt* is kept: a team's rate against one position divided by its rate against
everyone. Overall defence and pace are already in matchups.factor, so they aren't counted twice. Early
in the season the tilt is shrunk toward none (games / (games + TRUST_GAMES)) and it's capped at
LIMIT either way, since a few games against one position are mostly noise.

Finished box scores never change, so each is cached in data/boxscores_fp/ and downloaded once."""
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from stats import DATA, scoring_rules

CACHE_DIR = DATA / "boxscores_fp"
TABLE = DATA / "defense_vs_position.json"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gid}"
TRUST_GAMES = 20   # games before a team's position tilt counts fully
LIMIT = 0.06       # never move a projection more than 6% for position
VERSION = 1
BUCKET = {"PG": "G", "SG": "G", "G": "G", "SF": "F", "PF": "F", "F": "F", "C": "C"}


def bucket(player):
    """G / F / C for a fantasy player (his main position)."""
    return BUCKET.get(getattr(player, "position", ""), None)


def _box(gid):
    """{team id: [[position, minutes, {stat: value}]]} for one finished game, or None if not finished."""
    path = CACHE_DIR / f"{gid}.json"
    if path.exists():
        return json.loads(path.read_text())
    with urllib.request.urlopen(SUMMARY.format(gid=gid), timeout=15) as r:
        data = json.load(r)
    if not (data.get("header", {}).get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed")):
        return None
    out = {}
    for team in data.get("boxscore", {}).get("players", []):
        rows = []
        for block in team.get("statistics", []):
            keys = block.get("keys", [])
            for a in block.get("athletes", []):
                if a.get("didNotPlay") or not a.get("stats"):
                    continue
                v = dict(zip(keys, a["stats"]))
                split = lambda k: [float(x or 0) for x in (v.get(k) or "0-0").split("-")]
                try:
                    fgm, fga = split("fieldGoalsMade-fieldGoalsAttempted")
                    tpm, _ = split("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
                    ftm, fta = split("freeThrowsMade-freeThrowsAttempted")
                    s = {"PTS": float(v.get("points") or 0), "FGM": fgm, "FGA": fga, "3PM": tpm, "FTM": ftm, "FTA": fta,
                         "REB": float(v.get("rebounds") or 0), "AST": float(v.get("assists") or 0),
                         "TO": float(v.get("turnovers") or 0), "STL": float(v.get("steals") or 0),
                         "BLK": float(v.get("blocks") or 0)}
                    minutes = float(v.get("minutes") or 0)
                except ValueError:
                    continue
                pos = BUCKET.get((a.get("athlete", {}).get("position") or {}).get("abbreviation", ""), None)
                if pos and minutes > 0:
                    rows.append([pos, minutes, s])
        out[str(team["team"]["id"])] = rows
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def ratings(league):
    """{NBA team id: {"G": factor, "F": factor, "C": factor}} for this season so far (1.0 = no tilt).
    Rebuilt at most once a day; {} if there's no schedule or ESPN can't be reached."""
    schedule = getattr(league, "pro_schedule", None)
    if not schedule:
        return {}
    try:
        cached = json.loads(TABLE.read_text())
        if cached.get("date") == date.today().isoformat() and cached.get("v") == VERSION and cached.get("year") == league.year:
            return {int(k): v for k, v in cached["teams"].items()}
    except (OSError, ValueError):
        pass
    today = getattr(league, "current_week", 1) or 1
    games = {}  # game id -> (home, away)
    for tid, days in schedule.items():
        for day, gs in days.items():
            if int(day) < today:
                for g in gs:
                    games[g["id"]] = (g["homeProTeamId"], g["awayProTeamId"])
    try:
        with ThreadPoolExecutor(8) as pool:
            boxes = dict(zip(games, pool.map(_box, games)))
    except Exception:
        return {}
    rules = scoring_rules(league)
    fp = lambda s: sum(rules.get(k, 0) * v for k, v in s.items())
    allowed = {}  # defending team -> {pos: [fp, minutes]}, plus "games"
    for gid, box in boxes.items():
        if not box:
            continue
        home, away = games[gid]
        for offense, defense in ((home, away), (away, home)):
            d = allowed.setdefault(defense, {"games": 0})
            d["games"] += 1
            for pos, minutes, s in box.get(str(offense), []):
                a = d.setdefault(pos, [0.0, 0.0])
                a[0] += fp(s)
                a[1] += minutes
    if not allowed:
        return {}
    league_rate = {}
    for pos in ("G", "F", "C"):
        f = sum(d.get(pos, [0, 0])[0] for d in allowed.values())
        m = sum(d.get(pos, [0, 0])[1] for d in allowed.values())
        league_rate[pos] = f / m if m else 0
    league_all = sum(sum(d.get(p, [0, 0])[0] for p in "GFC") for d in allowed.values()) / \
        max(sum(sum(d.get(p, [0, 0])[1] for p in "GFC") for d in allowed.values()), 1)
    out = {}
    for tid, d in allowed.items():
        f_all = sum(d.get(p, [0, 0])[0] for p in "GFC")
        m_all = sum(d.get(p, [0, 0])[1] for p in "GFC")
        overall = (f_all / m_all) / league_all if m_all and league_all else 1.0
        w = d["games"] / (d["games"] + TRUST_GAMES)
        out[tid] = {}
        for pos in ("G", "F", "C"):
            f, m = d.get(pos, [0, 0])
            if not m or not league_rate[pos] or not overall:
                out[tid][pos] = 1.0
                continue
            tilt = (f / m) / league_rate[pos] / overall  # vs this position, beyond the team's overall defence
            out[tid][pos] = round(min(max(1 + w * (tilt - 1), 1 - LIMIT), 1 + LIMIT), 4)
    DATA.mkdir(parents=True, exist_ok=True)
    TABLE.write_text(json.dumps({"date": date.today().isoformat(), "v": VERSION, "year": league.year,
                                 "teams": {str(k): v for k, v in out.items()}}, indent=1))
    return out


def for_league(league):
    """The table for this league, cached on the league object for the session."""
    if getattr(league, "_fh_defense", None) is None:
        league._fh_defense = ratings(league)
    return league._fh_defense


def factor(league, opp_id, pos):
    """Multiplier for a player at `pos` (G / F / C) facing opp_id: 1.0 if unknown."""
    if not pos:
        return 1.0
    return for_league(league).get(opp_id, {}).get(pos, 1.0)
