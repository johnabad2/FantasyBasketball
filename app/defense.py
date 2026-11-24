"""Defence vs position: how many fantasy points each NBA team gives up to guards, forwards and centres.

ESPN doesn't publish this for basketball, so it's worked out from ESPN's public box scores (which list
every player's position, G / F / C, and full stat line): for each team, the fantasy points per minute
its opponents' guards, forwards and centres scored against it, using your league's scoring.

Only the position *tilt* is kept: a team's rate against one position divided by its rate against
everyone. Overall defence and pace are already in matchups.factor, so they aren't counted twice. Early
in the season the tilt is shrunk toward none (games / (games + TRUST_GAMES)) and it's capped at
LIMIT either way, since a few games against one position are mostly noise.

Box scores come from boxscores.py (downloaded once each)."""
import json
from datetime import date

import boxscores
from boxscores import BUCKET
from stats import DATA, scoring_rules

TABLE = DATA / "defense_vs_position.json"
# Kept small on purpose: replaying the season (backtest.py) found no reliable signal in position
# defence yet - the well-known problem with "defence vs position" numbers is that they're mostly noise
# from who each team happened to play. Re-check with `fantasy.py backtest` as the season goes on.
TRUST_GAMES = 60   # games before a team's position tilt counts fully
LIMIT = 0.03       # never move a projection more than 3% for position
VERSION = 2


def bucket(player):
    """G / F / C for a fantasy player (his main position)."""
    return BUCKET.get(getattr(player, "position", ""), None)


def tally(games, rules):
    """{defending team: {"games": n, pos: [fantasy points allowed, minutes]}} from boxscores.season rows."""
    allowed = {}
    for _, _, home, away, box in games:
        for offense, defense in ((home, away), (away, home)):
            d = allowed.setdefault(defense, {"games": 0})
            d["games"] += 1
            for pos, minutes, s, *_ in box.get(str(offense), []):
                a = d.setdefault(pos, [0.0, 0.0])
                a[0] += boxscores.fantasy_points(s, rules)
                a[1] += minutes
    return allowed


def tilts(allowed, trust=None, limit=None):
    """{team: {G/F/C: factor}} from tally(): each position's rate allowed vs the league, beyond the
    team's overall rate, shrunk by games / (games + trust) and capped at limit."""
    trust = TRUST_GAMES if trust is None else trust
    limit = LIMIT if limit is None else limit
    league_rate = {}
    for pos in ("G", "F", "C"):
        f = sum(d.get(pos, [0, 0])[0] for d in allowed.values())
        m = sum(d.get(pos, [0, 0])[1] for d in allowed.values())
        league_rate[pos] = f / m if m else 0
    league_all = (sum(sum(d.get(p, [0, 0])[0] for p in "GFC") for d in allowed.values())
                  / max(sum(sum(d.get(p, [0, 0])[1] for p in "GFC") for d in allowed.values()), 1))
    out = {}
    for tid, d in allowed.items():
        f_all = sum(d.get(p, [0, 0])[0] for p in "GFC")
        m_all = sum(d.get(p, [0, 0])[1] for p in "GFC")
        overall = (f_all / m_all) / league_all if m_all and league_all else 1.0
        w = d["games"] / (d["games"] + trust)
        out[tid] = {}
        for pos in ("G", "F", "C"):
            f, m = d.get(pos, [0, 0])
            if not m or not league_rate[pos] or not overall:
                out[tid][pos] = 1.0
                continue
            tilt = (f / m) / league_rate[pos] / overall  # vs this position, beyond the team's overall defence
            out[tid][pos] = round(min(max(1 + w * (tilt - 1), 1 - limit), 1 + limit), 4)
    return out


def ratings(league):
    """{NBA team id: {"G": factor, "F": factor, "C": factor}} for this season so far (1.0 = no tilt).
    Rebuilt at most once a day; {} if there's no schedule or ESPN can't be reached."""
    if not getattr(league, "pro_schedule", None):
        return {}
    try:
        cached = json.loads(TABLE.read_text())
        if cached.get("date") == date.today().isoformat() and cached.get("v") == VERSION and cached.get("year") == league.year:
            return {int(k): v for k, v in cached["teams"].items()}
    except (OSError, ValueError):
        pass
    games = boxscores.season(league)
    allowed = tally(games, scoring_rules(league)) if games else {}
    if not allowed:
        return {}
    out = tilts(allowed)
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
