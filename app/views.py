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


# ---------------------------------------------------------------- Week Ahead

def _matchup(league, p, day):
    """('+6%', 6) when his opponent that day is fast and/or bad on defence; ('', None) without a game."""
    import planner
    game = planner._game(league, p, day)
    if not game:
        return "", None
    pct = (planner._matchup(league, p, game) - 1) * 100
    return (f"{pct:+.0f}%" if abs(pct) >= 0.5 else "even"), round(pct, 1)


def week_ahead(d):
    import planner
    from datetime import date, timedelta
    from loader import short_name
    league, plan = d["league"], d.get("plan") or {}
    if not getattr(league, "pro_schedule", None):
        return {"demo": True}
    today_day = getattr(league, "current_week", 1) or 1
    team_of = projections.nba_team_id

    def row(slot, p, game, pts, change, kind="", day=today_day):
        text, pct = _matchup(league, p, day) if p else ("", None)
        return {"slot": slot, "player": p.name if p else "", "id": p.playerId if p else None, "game": game,
                "matchup": text, "matchup_pct": pct, "pts": round(pts, 1) if pts else None, "change": change,
                "kind": kind}

    # today: best legal lineup for the rest of today vs the one that's set
    moves, today_rows, summary = plan.get("moves"), [], None
    if moves:
        games = lineup.todays_games(league)
        starting = {p.playerId for p in d["me"].roster if p.lineupSlot not in ("BE", "IR")}
        for p in moves["locked"]:
            pts = fantasy.form_of(p, league.year).baseline * planner._matchup(league, p, planner._game(league, p, today_day))
            today_rows.append(row(p.lineupSlot, p, lineup.game_today(league, p, games), pts, "locked (game started)"))
        for si, slot in enumerate(moves["slots"]):
            c = moves["lineup"].get(si)
            if not c:
                today_rows.append(row(slot, None, "", None, "empty (no game)", "empty"))
                continue
            pl = c[1]
            change, kind = (("start (from bench)", "start") if pl.playerId not in starting else
                            (f"move from {pl.lineupSlot}", "move") if pl.lineupSlot != slot else ("keep", ""))
            today_rows.append(row(slot, pl, lineup.game_today(league, pl, games), c[0], change, kind))
        for pl in moves["bench"]:
            today_rows.append(row("BE", pl, lineup.game_today(league, pl, games) or "no game", None,
                                  "bench to make room", "bench"))
        summary = {"current": round(moves["current"], 1), "best": round(moves["best"], 1)}

    # next 7 days
    first = date.today() - timedelta(days=today_day - 1)
    week_no = lambda dt: ((dt - timedelta(days=dt.weekday())) - (first - timedelta(days=first.weekday()))).days // 7 + 1
    days = []
    for i, day in enumerate(plan.get("week") or []):
        games = lineup.games_on(league, day["day"])
        starters = sorted(day["lineup"].values(), key=lambda c: -c[0])
        used = {c[1].playerId for c in starters}
        rows = []
        for si, slot in enumerate(day["slot_names"]):
            c = day["lineup"].get(si)
            rows.append(row(slot, c[1], games.get(team_of(c[1]), ""), c[0], "start", "", day["day"]) if c else
                        row(slot, None, "", None, "empty (no game)", "empty", day["day"]))
        for value, p, _ in sorted(day["cands"], key=lambda c: -c[0]):
            if p.playerId not in used:
                rows.append(row("BE", p, games.get(team_of(p), ""), value, "plays, but no open slot fits", "bench",
                                day["day"]))
        days.append({"index": i, "date": day["date"].isoformat(), "label": day["date"].strftime("%a %d %b"),
                     "long": day["date"].strftime("%A %d %b"), "filled": day["filled"], "slots": day["slots"],
                     "points": round(day["points"]), "week": week_no(day["date"]),
                     "new_week": bool(i and day["date"].weekday() == 0),
                     "who": [short_name(c[1].name) for c in starters],
                     "spare": [short_name(c[1].name) for c in day["cands"] if c[1].playerId not in used],
                     "lineup": rows})

    # streamers
    waivers = getattr(league, "_fh_waivers", None) or {}
    streamers = []
    for s in plan.get("streamers") or []:
        fa = s["fa"]
        drop = f"drop {s['drop'].name} ({fantasy.form_of(s['drop'], league.year).baseline:.1f})" if s["drop"] else ""
        room = ("open roster spot: just add him" if s["open_spot"] else
                f"IR: {s['ir'].name} (INJ)" if s["ir"] and s["ir_certain"] else
                f"IR: {s['ir'].name} if IR-eligible" + (f", else {drop}" if drop else "") if s["ir"] else drop)
        claim = waivers.get(fa.player.playerId)
        streamers.append({"id": fa.player.playerId, "name": fa.player.name, "pos": fa.player.position,
                          "nba": fa.player.proTeam, "base": round(fa.baseline, 1), "games": s["games"],
                          "light_games": s["light_games"], "gain": round(s["gain"]), "later": round(s["later"]),
                          "room": room, "waivers": claim.strftime("%a %H:%M") if claim else None})
    return {"today": today_rows, "summary": summary, "days": days, "streamers": streamers,
            "empty": sum(x["slots"] - x["filled"] for x in days)}


# ---------------------------------------------------------------- Schedule

def schedule_view(d):
    import schedule
    from espn_api.basketball.constant import PRO_TEAM_MAP
    from loader import short_name
    league = d["league"]
    if not getattr(league, "pro_schedule", None):
        return {"demo": True}
    team_of = projections.nba_team_id
    mine, fas = {}, {}
    for p in d["me"].roster:
        if p.lineupSlot != "IR":
            mine.setdefault(team_of(p), []).append(short_name(p.name))
    for f in sorted(d["fas"], key=lambda f: -f.worth):
        if f.healthy and len(fas.setdefault(team_of(f.player), [])) < 2:
            fas[team_of(f.player)].append(f"{short_name(f.player.name)} {f.worth:.0f}")
    plan = schedule.plan(league)
    teams = []
    for tid, weeks in plan["teams"].items():
        teams.append({"team": PRO_TEAM_MAP.get(tid, "?"), "mine": mine.get(tid, []), "fas": fas.get(tid, []),
                      "weeks": [[{"date": g.strftime("%a %d %b"), "opp": PRO_TEAM_MAP.get(opp, "?"), "home": home}
                                 for g, opp, home in games] for games in weeks]})
    teams.sort(key=lambda t: (-sum(len(w) for w in t["weeks"][:2]), t["team"]))
    return {"days": [{"label": dt.strftime("%a"), "date": dt.strftime("%d %b"), "games": n} for dt, n in plan["days"]],
            "weeks": [{"label": "this week" if i == 0 else f"wk of {a:%d %b}", "range": f"{a:%d %b} - {b:%d %b}"}
                      for i, (a, b) in enumerate(plan["weeks"])],
            "teams": teams}
