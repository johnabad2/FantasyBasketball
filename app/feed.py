"""Trades from the league's activity feed (what ESPN's League Assistant shows), read-only.

ESPN's "communication" feed tags each player in a trade with a message type:
224 = trade accepted (waiting out the review period), 244 = trade processed (players moved),
245 = a player dropped to make room when the trade went through. Offers (230) are left out."""
import json
from datetime import datetime, timedelta

from stats import owner_names

ACCEPTED, PROCESSED, TRADE_DROP = 224, 244, 245


def trades(league, limit=100):
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
                sides[team_id] = {"team": t.team_name if t else "?", "manager": owner_names(t) if t else "",
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


def leaving(league, team_id):
    """Ids of this team's players in accepted trades that haven't gone through yet: never suggest
    dropping or trading them (that would break the deal). Cached on the league object."""
    cache = getattr(league, "_fh_leaving", None)
    if cache is None:
        cache = {}
        for t in trades(league) if getattr(league, "pro_schedule", None) else []:
            if t["kind"] == "accepted":
                for team in t["sides"]:
                    for p in team["receives"]:
                        cache.setdefault(p["from"], set()).add(p["id"])
        league._fh_leaving = cache
    return cache.get(team_id, set())
