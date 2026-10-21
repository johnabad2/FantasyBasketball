"""Injuries and player news from ESPN's public feeds, plus memory of each player's status so a
change (healthy -> out, out -> back) can be alerted once.

- injury report: every injured NBA player, with ESPN's latest update and expected return date
- player news: the latest fantasy news items for one player (Rotowire blurbs via ESPN)"""
import json
import re
import time
import urllib.request
from datetime import date, datetime

from stats import DATA

INJURIES = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
NEWS = "https://site.api.espn.com/apis/fantasy/v2/games/fba/news/players?playerId={pid}&limit={limit}"
STATUSES = DATA / "statuses.json"
CHANGE_WINDOW = 24 * 3600  # a status change keeps alerting for a day, so the scheduled check sees it too


def _get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def injuries():
    """{player id: {status, return_date (date or None), comment, details, updated}} for every
    injured NBA player. Empty dict if ESPN can't be reached."""
    try:
        data = _get(INJURIES)
    except OSError:
        return {}
    out = {}
    for team in data.get("injuries", []):
        for i in team.get("injuries", []):
            ath = i.get("athlete", {})
            pid = ath.get("id")
            if pid is None:  # the id is only in the profile link: .../player/_/id/4278067/name
                href = next((l.get("href", "") for l in ath.get("links", [])), "")
                m = re.search(r"/id/(\d+)", href)
                pid = m.group(1) if m else None
            if pid is None:
                continue
            details = i.get("details", {})
            ret = details.get("returnDate")
            out[int(pid)] = {
                "name": ath.get("displayName", ""),
                "team": team.get("displayName", ""),
                "status": i.get("status", ""),
                "return_date": date.fromisoformat(ret) if ret else None,
                "comment": i.get("shortComment", ""),
                "details": i.get("longComment", ""),
                "injury": details.get("type", ""),
                "updated": i.get("date", ""),
            }
    return out


def player_news(pid, limit=3):
    """[{published (datetime), headline, story}] newest first, or [] if unavailable."""
    try:
        feed = _get(NEWS.format(pid=int(pid), limit=limit)).get("feed", [])
    except OSError:
        return []
    out = []
    for item in feed[:limit]:
        when = item.get("published", "")
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone()
        except ValueError:
            when = None
        out.append({"published": when, "headline": plain_text(item.get("headline", ""), 200),
                    "story": plain_text(item.get("story", ""))})
    return out


def plain_text(text, limit=600):
    """ESPN stories sometimes come as HTML: keep just the words, and not too many of them."""
    import html
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def track_statuses(players):
    """Remember each player's ESPN status. Returns [(player, old status, new status, seconds ago)]
    for changes in the last day (including ones seen by an earlier run)."""
    try:
        memory = json.loads(STATUSES.read_text())
    except (OSError, ValueError):
        memory = {}
    now = time.time()
    first_run = not memory
    changes = []
    for p in players:
        key = str(p.playerId)
        status = p.injuryStatus or "ACTIVE"
        seen = memory.get(key)
        if seen is None:
            memory[key] = {"status": status, "prev": status, "since": 0 if first_run else now}
        elif seen["status"] != status:
            memory[key] = {"status": status, "prev": seen["status"], "since": now}
        entry = memory[key]
        if entry["prev"] != entry["status"] and now - entry["since"] < CHANGE_WINDOW:
            changes.append((p, entry["prev"], entry["status"], now - entry["since"]))
    DATA.mkdir(exist_ok=True)
    STATUSES.write_text(json.dumps(memory))
    return changes
