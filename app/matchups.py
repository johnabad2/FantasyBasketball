"""Matchup boost: how good a game is for fantasy, from the opponent's pace and defence.

The total-points method: a game's possessions = pace A x pace B / league pace, and a team scores
(its offence x the opponent's defence / league average) per 100 of them. A player's own team's
pace and offence are already in his per-game average, so only the opponent changes his outlook:

    factor = (opponent pace / league pace) x (1 + DEF_WEIGHT x (opponent DRtg / league rating - 1))

Pace counts fully (more possessions = more of every stat); defence counts half, because a leaky
defence gives up points and shots but not more rebounds, steals or blocks.

Ratings come from ESPN's public team stats: last season in full, blended toward this season's
results as games are played (this season's possessions use last season's pace until ESPN posts
this season's team stats). Cached in data/team_ratings.json and refreshed once a day.

game_factor adds the schedule effects on top of the opponent (see README for sources):
- Back-to-back, second night: teams shoot worse and play slower (Aicardi & Zitzewitz, 20,000+
  games), and stars/veterans are the ones rested -> B2B.
- Blowout risk: a lopsided game (expected margin past ~8 points) costs starters 4th-quarter
  minutes -> 1% per point past BLOWOUT_FROM, at most BLOWOUT_MAX.
- End of season: the last LATE_DAYS days, teams rest players; losing teams shut them down."""
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from stats import DATA

CACHE = DATA / "team_ratings.json"
SITE = "https://site.api.espn.com/apis"
DEF_WEIGHT = 0.5
LIMITS = (0.9, 1.1)  # never move a projection more than 10% either way
TRUST_GAMES = 20     # this season's games before it counts as much as last season
B2B = 0.97           # second night of a back-to-back
BLOWOUT_FROM, BLOWOUT_MAX = 8.0, 0.06  # expected margin (points) where blowout risk starts; biggest cut
HOME = 2.5           # home-court points
LATE_DAYS = 14       # end-of-season window
LATE, TANKING = 0.95, 0.85  # in that window: everyone / teams winning under 40%
MARGIN_CARRY = 0.6   # share of last season's scoring margin expected to carry over (teams regress)
VERSION = 2          # bump when the cached fields change
EASTERN = timezone(timedelta(hours=-5))  # NBA game dates are US dates


def _get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def _season(season):
    """{team id: {pace, ortg, drtg, gp}} for one NBA season (season = the year it ends)."""
    standings = _get(f"{SITE}/v2/sports/basketball/nba/standings?season={season}")
    teams = {}
    for conf in standings.get("children", []):
        for e in conf["standings"]["entries"]:
            st = {s["name"]: s.get("value") for s in e["stats"]}
            gp = (st.get("wins") or 0) + (st.get("losses") or 0)
            teams[int(e["team"]["id"])] = {"gp": gp, "wins": st.get("wins") or 0, "ppg": st.get("avgPointsFor") or 0,
                                           "opp_ppg": st.get("avgPointsAgainst") or 0}

    def pace(tid):  # possessions per game = FGA - offensive rebounds + turnovers + 0.44 x FTA
        try:
            cats = _get(f"{SITE}/site/v2/sports/basketball/nba/teams/{tid}/statistics?season={season}")
            off = {s["name"]: s["value"] for c in cats["results"]["stats"]["categories"] if c["name"] == "offensive"
                   for s in c["stats"]}
            return off["avgFieldGoalsAttempted"] - off["avgOffensiveRebounds"] + off["avgTurnovers"] + \
                0.44 * off["avgFreeThrowsAttempted"]
        except Exception:
            return None  # not posted yet (early in a season)
    with ThreadPoolExecutor(8) as pool:
        paces = dict(zip(teams, pool.map(pace, teams)))
    for tid, t in teams.items():
        t["pace"] = paces[tid]
    return teams


def ratings(year):
    """{team id: {pace, drtg}} plus league averages under key 'league'. year = the fantasy season
    (e.g. 2027 for 2026-27). Fetched at most once a day; {} if ESPN can't be reached."""
    try:
        cached = json.loads(CACHE.read_text())
        if cached.get("date") == date.today().isoformat() and cached.get("year") == year                 and cached.get("v") == VERSION:
            return {int(k) if k.isdigit() else k: v for k, v in cached["teams"].items()}
    except (OSError, ValueError, KeyError):
        pass
    try:
        last, now = _season(year - 1), _season(year)
    except Exception:
        return {}
    out = {}
    for tid, prev in last.items():
        cur = now.get(tid, {"gp": 0})
        if not prev.get("pace"):
            continue
        w = cur["gp"] / (cur["gp"] + TRUST_GAMES)
        pace = w * (cur.get("pace") or prev["pace"]) + (1 - w) * prev["pace"]
        drtg_prev = prev["opp_ppg"] / prev["pace"] * 100
        drtg_cur = cur["opp_ppg"] / (cur.get("pace") or prev["pace"]) * 100 if cur["gp"] else drtg_prev
        margin_prev = MARGIN_CARRY * (prev["ppg"] - prev["opp_ppg"])
        margin_cur = cur["ppg"] - cur["opp_ppg"] if cur["gp"] else margin_prev
        out[tid] = {"pace": pace, "drtg": w * drtg_cur + (1 - w) * drtg_prev,
                    "margin": w * margin_cur + (1 - w) * margin_prev,  # points per game vs opponents
                    "win_pct": cur["wins"] / cur["gp"] if cur["gp"] >= TRUST_GAMES else None}
    if out:
        out["league"] = {"pace": sum(t["pace"] for t in out.values()) / len(out),
                         "drtg": sum(t["drtg"] for t in out.values()) / len(out)}
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"date": date.today().isoformat(), "year": year, "v": VERSION,
                                     "teams": {str(k): v for k, v in out.items()}}, indent=1))
    return out


def opponent(game, team_id):
    """The other team's id in a fantasy pro_schedule game."""
    if not game:
        return None
    return game["awayProTeamId"] if game["homeProTeamId"] == team_id else game["homeProTeamId"]


def factor(table, opp_id):
    """Multiplier on a player's per-game value when facing opp_id (1.0 if unknown)."""
    opp, lg = table.get(opp_id), table.get("league")
    if not opp or not lg:
        return 1.0
    f = opp["pace"] / lg["pace"] * (1 + DEF_WEIGHT * (opp["drtg"] / lg["drtg"] - 1))
    return min(max(f, LIMITS[0]), LIMITS[1])


def for_league(league):
    """The ratings table for this league's season, cached on the league object for the session."""
    if getattr(league, "_fh_matchups", None) is None:
        league._fh_matchups = ratings(league.year) if getattr(league, "pro_schedule", None) else {}
    return league._fh_matchups


def _dates(league):
    """{NBA team id: set of game dates}, and the last date of the regular season. Cached."""
    if getattr(league, "_fh_game_dates", None) is None:
        out, last = {}, None
        for tid, days in (getattr(league, "pro_schedule", None) or {}).items():
            for games in days.values():
                for g in games:
                    d = datetime.fromtimestamp(g["date"] / 1000, EASTERN).date()
                    out.setdefault(tid, set()).add(d)
                    last = d if last is None or d > last else last
        league._fh_game_dates = (out, last)
    return league._fh_game_dates


def schedule_factor(league, table, game, team_id):
    """Multiplier for this game beyond the opponent: back-to-back, blowout risk, end of season."""
    if not game:
        return 1.0
    f = 1.0
    day = datetime.fromtimestamp(game["date"] / 1000, EASTERN).date()
    dates, last = _dates(league)
    if day - timedelta(days=1) in dates.get(team_id, ()):
        f *= B2B
    opp_id = opponent(game, team_id)
    me, opp = table.get(team_id), table.get(opp_id)
    if me and opp and me.get("margin") is not None and opp.get("margin") is not None:
        margin = me["margin"] - opp["margin"] + (HOME if game["homeProTeamId"] == team_id else -HOME)
        f *= 1 - min(max(abs(margin) - BLOWOUT_FROM, 0) * 0.01, BLOWOUT_MAX)
    if last and (last - day).days < LATE_DAYS:
        win = (me or {}).get("win_pct")
        f *= TANKING if win is not None and win < 0.4 else LATE
    return f


def game_factor(league, game, team_id):
    """Everything about this game that moves a player's value: opponent x schedule."""
    if not game:
        return 1.0
    table = for_league(league)
    return factor(table, opponent(game, team_id)) * schedule_factor(league, table, game, team_id)
