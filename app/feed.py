"""Trades from the league's activity feed (what ESPN's League Assistant shows), read-only.

ESPN's "communication" feed tags each player in a trade with a message type:
224 = trade accepted (waiting out the review period), 244 = trade processed (players moved),
245 = a player dropped to make room when the trade went through. Offers (230) are left out."""
import json
from datetime import datetime, timedelta

from stats import owner_names

ACCEPTED, PROCESSED, TRADE_DROP = 224, 244, 245


def trades(league, limit=100):
    """Cached per league object (one ESPN request per refresh). See _trades."""
    if getattr(league, "_fh_trades", None) is None:
        league._fh_trades = _trades(league, limit)
    return league._fh_trades


def _trades(league, limit=100):
    """[{when, kind ("accepted"/"completed"), process_at, sides: [{team, manager, receives, drops}]}]
    newest first. Players are {id, name, pro, pos}. [] if ESPN can't be reached."""
    filters = {"topics": {"filterType": {"value": ["ACTIVITY_TRANSACTIONS"]}, "limit": limit,
                          "limitPerMessageSet": {"value": 25},
                          "sortMessageDate": {"sortPriority": 1, "sortAsc": False}}}
    try:
        data = league.espn_request.league_get(extend="/communication/", params={"view": "kona_league_communication"},
                                              headers={"x-fantasy-filter": json.dumps(filters)})
    except Exception:
        return []
    teams = {t.team_id: t for t in league.teams}
    rostered = {p.playerId: p for t in league.teams for p in t.roster}
    review = timedelta(hours=getattr(league.settings, "trade_revision_hours", None) or 24)

    def player(pid):
        p = rostered.get(pid)
        return {"id": pid, "name": p.name if p else league.player_map.get(pid, "?"),
                "pro": p.proTeam if p else "", "pos": p.position if p else ""}

    out = []
    for topic in data.get("topics", []):
        msgs = [m for m in topic.get("messages", []) if m.get("messageTypeId") in (ACCEPTED, PROCESSED, TRADE_DROP)]
        if not msgs:
            continue
        sides = {}  # team id -> side, in the order teams first appear

        def side(team_id):
            if team_id not in sides:
                t = teams.get(team_id)
                sides[team_id] = {"team_id": team_id, "team": t.team_name if t else "?",
                                  "manager": owner_names(t) if t else "",
                                  "receives": [], "drops": []}
            return sides[team_id]

        moved = set()
        for m in msgs:
            pid = m.get("targetId")
            if m["messageTypeId"] == TRADE_DROP:
                side(m.get("from"))["drops"].append(player(pid))
            else:
                side(m.get("from"))
                side(m.get("to"))["receives"].append({**player(pid), "from": m.get("from")})
                moved.add((pid, m.get("to")))
        when = datetime.fromtimestamp(topic["date"] / 1000)
        done = any(m["messageTypeId"] == PROCESSED for m in msgs)
        out.append({"when": when, "kind": "completed" if done else "accepted",
                    "process_at": None if done else when + review, "sides": list(sides.values()),
                    "key": frozenset(moved)})
    # once a trade goes through, ESPN keeps its "accepted" entry too: show it once
    completed = {t["key"] for t in out if t["kind"] == "completed"}
    out = [t for t in out if t["kind"] == "completed" or t["key"] not in completed]
    return sorted(out, key=lambda t: t["when"], reverse=True)


VETO_GRACE = timedelta(hours=6)  # accepted, review over, still not processed this long after: vetoed


def pending(league):
    """Accepted trades still waiting to go through (oldest first)."""
    if not getattr(league, "pro_schedule", None):
        return []
    now = datetime.now()
    return sorted((t for t in trades(league) if t["kind"] == "accepted" and t["process_at"] + VETO_GRACE > now),
                  key=lambda t: t["when"])


def leaving(league, team_id):
    """Ids of this team's players in accepted trades that haven't gone through yet: never suggest
    dropping or trading them (that would break the deal)."""
    return {p["id"] for t in pending(league) for side in t["sides"] for p in side["receives"]
            if p["from"] == team_id}


def after_trades(league):
    """Every team as it will be once the pending trades go through: {team id: team-like object
    with .roster swapped} (same name, id, owners...). Teams with no pending trade are returned as is.
    Cached on the league object."""
    if getattr(league, "_fh_after", None) is None:
        rosters = {t.team_id: list(t.roster) for t in league.teams}
        players = {p.playerId: p for roster in rosters.values() for p in roster}
        changed = set()
        for t in pending(league):
            for side in t["sides"]:
                for got in side["receives"]:
                    p = players.get(got["id"])
                    if p is None or got["from"] not in rosters:
                        continue
                    rosters[got["from"]] = [x for x in rosters[got["from"]] if x.playerId != p.playerId]
                    rosters[side["team_id"]].append(p)
                    changed |= {got["from"], side["team_id"]}
        league._fh_after = {t.team_id: (_Planned(t, rosters[t.team_id]) if t.team_id in changed else t)
                            for t in league.teams}
    return league._fh_after


class _Planned:
    """A fantasy team with its roster as it will be after pending trades; everything else is the real team."""

    def __init__(self, team, roster):
        self._team, self.roster = team, roster

    def __getattr__(self, name):
        return getattr(self._team, name)
