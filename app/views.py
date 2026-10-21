"""What each page of the web app shows, as plain JSON-ready dicts built from loader.load().
Formatting decisions (what's 'hot', what's out) are made here so the page only lays things out."""
import fantasy
import lineup
import projections
from loader import minutes_text, points, status_tag, status_text
from stats import owner_names, split_of

PRIORITY = {1: "do-now", 2: "consider", 3: "fyi"}
PRIORITY_LABEL = {1: "Do now", 2: "Consider", 3: "FYI"}


def header(d):
    league = d["league"]
    return {"league": getattr(getattr(league, "settings", None), "name", "Fantasy Hoops"), "demo": d.get("demo", False)}


def standings(d):
    league, me = d["league"], d["me"]
    teams = sorted(league.teams, key=lambda t: -points(d, t))
    lead = points(d, teams[0]) if teams else 0
    proj = d.get("projected") or {}
    proj_rank = {tid: r for r, tid in enumerate(sorted(proj, key=lambda k: -proj[k][2]), 1)}
    today = d.get("today_pts") or {}
    rows = [{"id": t.team_id, "team": t.team_name.strip(), "owner": owner_names(t), "points": round(points(d, t), 1),
             "today": today.get(t.team_id), "behind": round(lead - points(d, t), 1),
             "proj_final": round(proj[t.team_id][2]) if t.team_id in proj else None,
             "proj_rank": proj_rank.get(t.team_id), "me": t.team_id == me.team_id} for t in teams]
    names = {t.team_id: t.team_name.strip() for t in league.teams}
    weeks = [{"week": w["week"], "start": w["start"].isoformat(), "end": w["end"].isoformat(),
              "totals": {names[k]: round(v, 1) for k, v in w["totals"].items()},
              "gained": {names[k]: round(v, 1) for k, v in w["gained"].items()},
              "ranks": {names[k]: v for k, v in w["ranks"].items()}} for w in d.get("weekly") or []]
    return {"teams": rows, "weeks": weeks, "me": names[me.team_id]}


def my_team(d, roles=None):
    league, me, year = d["league"], d["me"], d["league"].year
    t = d["cfg"]["thresholds"]
    forms = [fantasy.form_of(p, year) for p in me.roster]
    today_pts = projections.today_points(league, forms)
    lineup_today = sum(today_pts[f.player.playerId] for f in forms if f.player.lineupSlot not in ("BE", "IR"))
    proj = (d.get("projected") or {}).get(me.team_id)
    rank = 1 + sum(points(d, x) > points(d, me) for x in league.teams)

    pending = []
    for tr in d.get("pending") or []:
        mine = next(s for s in tr["sides"] if s["team_id"] == me.team_id)
        out = [p["name"] for s in tr["sides"] if s is not mine for p in s["receives"]]
        pending.append({"out": out, "in": [p["name"] for p in mine["receives"]],
                        "when": tr["process_at"].strftime("%a %H:%M")})

    alerts = [{"priority": PRIORITY[a.priority], "label": PRIORITY_LABEL[a.priority], "title": a.title,
               "detail": [line.strip() for line in a.detail.split("\n") if line.strip()]} for a in d["alerts"]]

    games = lineup.todays_games(league)
    kept = fantasy.load_keep()
    roles = roles or {}
    roster = []
    for f in sorted(forms, key=lambda f: -f.worth):
        p = f.player
        trusted = f.games >= t["min_games"]
        vp = projections.vs_projection(p, year, d["rules"])
        slot = p.lineupSlot
        form = ("hot" if trusted and f.trend >= t["trend_pct"] else
                "cold" if trusted and f.trend <= -t["trend_pct"] else "")
        roster.append({
            "id": p.playerId, "name": p.name, "kept": p.playerId in kept, "pos": p.position, "nba": p.proTeam,
            "lineup": "IR" if slot == "IR" else "bench" if slot == "BE" else slot,
            "starting": slot not in ("BE", "IR"),
            "game": lineup.game_today(league, p, games) or "", "proj_today": round(today_pts[p.playerId], 1) or None,
            "role": roles.get(p.playerId, "" if d.get("demo") else None),
            "status": status_text(p), "status_tag": status_tag(p), "gp": f.games, "minutes": minutes_text(p, year),
            "base": round(f.baseline, 1), "l15": round(f.recent, 1),
            "trend": round(f.trend * 100) if trusted else None, "form": form,
            "vs_proj": round(vp, 1) if vp is not None else None,
            "fp_total": round(split_of(p, year, "total").get("applied_total", 0)),
        })
    return {"team": me.team_name, "points": round(points(d, me), 1), "rank": rank, "of": len(league.teams),
            "lineup_today": round(lineup_today, 1), "proj_final": round(proj[2]) if proj else None,
            "pending": pending, "alerts": alerts, "roster": roster}
