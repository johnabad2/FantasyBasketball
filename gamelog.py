"""Game-by-game box scores from ESPN's public game log, scored with your league's rules."""
import json
import urllib.request
from datetime import datetime

URL = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{pid}/gamelog?season={season}"

# Stats the chart can show, in button order. "FP" is fantasy points under the league's scoring.
CHART_STATS = ["FP", "PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FGM", "FGA", "FTM", "FTA", "MIN"]
STAT_NAMES = {"FP": "Fantasy points", "PTS": "Points", "REB": "Rebounds", "AST": "Assists", "STL": "Steals",
              "BLK": "Blocks", "3PM": "3-pointers made", "TO": "Turnovers", "FGM": "Field goals made",
              "FGA": "Field goals attempted", "FTM": "Free throws made", "FTA": "Free throws attempted",
              "MIN": "Minutes"}

_cache = {}  # (player id, season) -> raw JSON, for this run of the app


def _fetch(pid, season):
    key = (int(pid), int(season))
    if key not in _cache:
        with urllib.request.urlopen(URL.format(pid=int(pid), season=int(season)), timeout=15) as r:
            _cache[key] = json.load(r)
    return _cache[key]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def games(pid, season, rules):
    """Regular season + playoff games (no preseason), oldest first. Each game is a dict with
    date, opp ('vs DAL' / '@ DAL'), result, every box-score stat, and FP."""
    data = _fetch(pid, season)
    labels = data.get("labels", [])
    info = data.get("events", {})
    out, seen = [], set()
    for st in data.get("seasonTypes", []):
        if "preseason" in st.get("displayName", "").lower():
            continue  # fantasy doesn't count preseason
        for cat in st.get("categories", []):
            for ev in cat.get("events", []):
                gid = ev.get("eventId")
                if not gid or gid in seen or gid not in info:
                    continue
                seen.add(gid)
                row = dict(zip(labels, ev.get("stats", [])))
                g = {"MIN": _num(row.get("MIN")), "PTS": _num(row.get("PTS")), "REB": _num(row.get("REB")),
                     "AST": _num(row.get("AST")), "STL": _num(row.get("STL")), "BLK": _num(row.get("BLK")),
                     "TO": _num(row.get("TO")), "PF": _num(row.get("PF"))}
                for label, made, att in (("FG", "FGM", "FGA"), ("3PT", "3PM", "3PA"), ("FT", "FTM", "FTA")):
                    m, _, a = str(row.get(label, "0-0")).partition("-")
                    g[made], g[att] = _num(m), _num(a)
                g["FP"] = round(sum(g.get(stat, 0) * pts for stat, pts in rules.items()), 1)
                e = info[gid]
                g["date"] = datetime.fromisoformat(e["gameDate"].replace("Z", "+00:00")).astimezone()
                g["opp"] = f"{e.get('atVs', 'vs')} {e.get('opponent', {}).get('abbreviation', '?')}"
                g["result"] = e.get("gameResult", "")
                g["playoffs"] = "post" in st.get("displayName", "").lower()
                out.append(g)
    return sorted(out, key=lambda g: g["date"])


def season_label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def latest_games(pid, year, rules):
    """(games, label): this season's games, or last season's until this one has started."""
    this = games(pid, year, rules)
    if this:
        return this, f"{season_label(year)} season"
    last = games(pid, year - 1, rules)
    return last, f"{season_label(year - 1)} season (no {season_label(year)} games yet)"
