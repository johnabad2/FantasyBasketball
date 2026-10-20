"""Who's playing today, and who actually starts in the NBA.

- Today's games come from ESPN's NBA schedule in the fantasy league data (one scoring period = one day).
- NBA role comes from ESPN box scores, which flag the five starters of every game. Finished games
  never change, so each box score is cached in data/boxscores once downloaded."""
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import gamelog
from stats import DATA

BOXSCORES = DATA / "boxscores"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gid}"
RECENT = 5  # games used to judge whether a player starts


def _pro_team_abbrev(team_id):
    from espn_api.basketball.constant import PRO_TEAM_MAP
    return PRO_TEAM_MAP.get(team_id, "?")


def todays_games(league):
    """{NBA team id: 'vs NYK 19:30' / '@ NYK 01:00'} for teams playing in today's scoring period."""
    schedule = getattr(league, "pro_schedule", None) or {}
    today = str(getattr(league, "current_week", ""))
    out = {}
    for team_id, periods in schedule.items():
        for game in periods.get(today, []):
            home = game["homeProTeamId"] == team_id
            opp = game["awayProTeamId"] if home else game["homeProTeamId"]
            tip = datetime.fromtimestamp(game["date"] / 1000).strftime("%a %H:%M")  # your local time
            out[team_id] = f"{'vs' if home else '@'} {_pro_team_abbrev(opp)} {tip}"
    return out


def game_today(league, player, games=None):
    games = todays_games(league) if games is None else games
    team_id = getattr(player, "proTeamId", None)
    if team_id is None:  # espn_api keeps the abbreviation; map it back to the id
        from espn_api.basketball.constant import PRO_TEAM_MAP
        team_id = next((k for k, v in PRO_TEAM_MAP.items() if v == player.proTeam), None)
    return games.get(team_id, "")


# ---------------------------------------------------------------- NBA role

def _starters(gid):
    """{athlete id: started?} for one game, from cache when we have it."""
    path = BOXSCORES / f"{gid}.json"
    if path.exists():
        return {int(k): v for k, v in json.loads(path.read_text()).items()}
    with urllib.request.urlopen(SUMMARY.format(gid=gid), timeout=15) as r:
        data = json.load(r)
    out = {}
    for team in data.get("boxscore", {}).get("players", []):
        for block in team.get("statistics", []):
            for a in block.get("athletes", []):
                if not a.get("didNotPlay"):
                    out[int(a["athlete"]["id"])] = bool(a.get("starter"))
    completed = (data.get("header", {}).get("competitions", [{}])[0]
                 .get("status", {}).get("type", {}).get("completed"))
    if completed:
        BOXSCORES.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out


def _recent_game_ids(pid, year):
    """(game ids newest first, note) for this season; last season if none yet. note is
    '' / ' pre' (preseason) / ' last season'."""
    for season in (year, year - 1):
        data = gamelog._fetch(pid, season)
        events = data.get("events", {})
        regular, pre = [], []
        for st in data.get("seasonTypes", []):
            bucket = pre if "preseason" in st.get("displayName", "").lower() else regular
            for cat in st.get("categories", []):
                bucket += [ev["eventId"] for ev in cat.get("events", []) if ev.get("eventId") in events]
        ids = regular or pre
        if ids:
            ids = sorted(set(ids), key=lambda g: events[g]["gameDate"], reverse=True)
            note = " last season" if season != year else " pre" if not regular else ""
            return ids[:RECENT], note
    return [], ""


def nba_role(pid, year):
    """'starter 5/5', 'bench 0/5', 'split 2/5' (+ ' pre' / ' last season'), or '-'."""
    try:
        ids, note = _recent_game_ids(pid, year)
        played = [s[int(pid)] for s in map(_starters, ids) if int(pid) in s]
    except OSError:
        return "?"
    if not played:
        return "-"
    starts = sum(played)
    kind = "starter" if starts == len(played) else "bench" if starts == 0 else "split"
    return f"{kind} {starts}/{len(played)}{note}"


def nba_roles(player_ids, year):
    """{player id: role} for many players at once (downloads run in parallel)."""
    ids = list(player_ids)
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(ids, pool.map(lambda pid: nba_role(pid, year), ids)))
