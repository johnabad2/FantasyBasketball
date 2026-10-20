"""The league chat (ESPN's message board for all members), read-only.

ESPN keeps it in the league "communication" feed as CHAT_ALL_MEMBERS. Private direct messages
(CHAT_DIRECT_MESSAGE) are deliberately not read. System notices posted by "LM" carry only an event
code and no text, so they're skipped."""
import json
from datetime import datetime


def league_chat(league, limit=100):
    """[{when (datetime), author, team, text}] newest first, or [] if ESPN can't be reached."""
    filters = {"topics": {"filterType": {"value": ["CHAT_ALL_MEMBERS"]}, "limit": limit,
                          "limitPerMessageSet": {"value": limit},
                          "sortMessageDate": {"sortPriority": 1, "sortAsc": False}}}
    try:
        data = league.espn_request.league_get(extend="/communication/", params={"view": "kona_league_communication"},
                                              headers={"x-fantasy-filter": json.dumps(filters)})
        members = league.espn_request.league_get(params={"view": "mTeam"}).get("members", [])
    except Exception:
        return []
    names = {m["id"]: (f"{m.get('firstName', '')} {m.get('lastName', '')}".strip() or m.get("displayName", "?"))
             for m in members}
    teams = {}
    for t in league.teams:
        for o in t.owners or []:
            if isinstance(o, dict) and o.get("id"):
                teams[o["id"]] = t.team_name
    out = []
    for topic in data.get("topics", []):
        for m in topic.get("messages", []):
            text = (m.get("content") or "").strip()
            if not text or m.get("isDeleted") or m.get("author") == "LM":
                continue
            out.append({"when": datetime.fromtimestamp(m["date"] / 1000), "author": names.get(m["author"], "someone"),
                        "team": teams.get(m["author"], ""), "text": " ".join(text.split())})
    return sorted(out, key=lambda x: x["when"], reverse=True)
