"""Page data for the rest of the web app's tabs (Free Agents & Trades, League, Player, Compare, News,
Activity, Trade Lab), as JSON-ready dicts. Same idea as views.py, kept separate to stay readable."""
from datetime import datetime

import fantasy
import projections
from loader import points, status_tag, status_text
from stats import owner_names, split_of


# ---------------------------------------------------------------- Free Agents & Trades

def free_agents_trades(d):
    import feed
    import trade
    league, t = d["league"], d["cfg"]["thresholds"]
    real = bool(getattr(league, "pro_schedule", None))
    keep = fantasy.load_keep() | (feed.leaving(league, d["me"].team_id) if real else set())
    droppable = sorted((f for f in d["mine"] if f.player.lineupSlot != "IR" and f.player.playerId not in keep),
                       key=lambda f: f.worth)
    roster = [f.player for f in d["mine"]]
    ahead = fantasy.next_30_days(league, d["injuries"]) if real else None
    waivers = getattr(league, "_fh_waivers", None) or {}
    fas = []
    for fa in sorted(d["fas"], key=lambda f: -f.worth):
        p = fa.player
        beats = fantasy.upgrade_over(fa, droppable, roster, t, ahead)  # same rule as the pickup alert
        nxt = ahead(fa) if ahead else None
        claim = waivers.get(p.playerId)
        fas.append({"id": p.playerId, "name": p.name, "pos": p.position, "nba": p.proTeam, "status": status_text(p),
                    "status_tag": status_tag(p), "waivers": claim.strftime("%a") if claim else None,
                    "worth": round(fa.worth, 1), "next30": round(nxt[0]) if nxt else None,
                    "drop": beats.player.name if beats else None,
                    "gain": round(fa.worth - beats.worth, 1) if beats else None})
    ideas = [{"team": team.team_name.strip(), "team_id": team.team_id, "send": give.player.name,
              "send_id": give.player.playerId, "send_base": round(give.baseline, 1), "get": get.player.name,
              "get_id": get.player.playerId, "get_base": round(get.baseline, 1), "gain": round(gain, 1)}
             for team, gain, give, get in d["trades"]]
    partners = []
    for x in d.get("partners") or []:
        fixes = [trade.describe_fix(x["fixes_mine"])] if x["fixes_mine"] else []
        if x["fixes_theirs"]:
            fixes.append(trade.describe_fix(x["fixes_theirs"], "their"))
        partners.append({"team": x["team"].team_name.strip(), "team_id": x["team"].team_id,
                         "send": x["give"].name, "send_id": x["give"].playerId,
                         "get": x["get"].name, "get_id": x["get"].playerId, "fixes": ", ".join(fixes),
                         "both": bool(x["fixes_mine"] and x["fixes_theirs"]),
                         "me": round(x["me"], 1) + 0, "them": round(x["them"], 1) + 0})
    return {"free_agents": fas, "upgrades": sum(1 for f in fas if f["drop"]), "ideas": ideas, "partners": partners,
            "min_games": t["min_games"]}


# ---------------------------------------------------------------- League

def league_teams(d):
    return [{"id": t.team_id, "name": t.team_name.strip(), "owner": owner_names(t), "me": t.team_id == d["me"].team_id}
            for t in sorted(d["league"].teams, key=lambda t: -points(d, t))]


def league_roster(d, team_id=None, split=None):
    import stats
    from stats import BOX, box, fp_per_game, games
    league, rules, year = d["league"], d["rules"], d["league"].year
    team = next((t for t in league.teams if t.team_id == team_id), d["me"])
    split = split if split in stats.SPLITS else None
    rank = 1 + sum(points(d, t) > points(d, team) for t in league.teams)
    rows = []
    for p in team.roster:
        season = split_of(p, year, "total")
        if split:
            s, proj = split_of(p, year, split), False
        else:
            s, proj = stats.season_or_projection(p, year)
        vp = projections.vs_projection(p, year, rules)
        rows.append({"id": p.playerId, "name": p.name, "projected": proj, "pos": p.position, "nba": p.proTeam,
                     "slot": p.lineupSlot, "status": status_text(p), "status_tag": status_tag(p),
                     "gp": games(s if split else season), "fp_total": round(season.get("applied_total", 0)),
                     "fpg": round(fp_per_game(s, rules), 1),
                     "proj": round(fp_per_game(split_of(p, year, "projected"), rules), 1),
                     "vs_proj": round(vp, 1) if vp is not None else None,
                     "box": [box(s, stat) for stat in BOX]})
    rows.sort(key=lambda r: -r["fpg"])
    return {"team": team.team_name.strip(), "team_id": team.team_id, "owner": owner_names(team),
            "points": round(points(d, team), 1), "rank": rank, "box_cols": BOX, "rows": rows,
            "splits": [{"key": k, "label": stats.SPLIT_LABELS[k]} for k in stats.SPLITS]}


# ---------------------------------------------------------------- Player

def player(d, query):
    import gamelog
    import stats
    from stats import BOX, box, fp_per_game, games
    league, rules, year = d["league"], d["rules"], d["league"].year
    try:
        p, team = stats.find_player(league, query)
    except SystemExit as e:
        return {"error": str(e)}
    if p is None:
        return {"error": "Player not found."}
    splits = [{"label": stats.SPLIT_LABELS[k], "gp": games(split_of(p, year, k)),
               "fpg": round(fp_per_game(split_of(p, year, k), rules), 1),
               "box": [box(split_of(p, year, k), stat) for stat in BOX]} for k in stats.SPLITS if split_of(p, year, k)]
    s, proj = stats.season_or_projection(p, year)
    avg = s.get("avg") or {}
    parts = sorted(((stat, avg.get(stat, 0), pts) for stat, pts in rules.items() if avg.get(stat)),
                   key=lambda x: -abs(x[1] * x[2]))
    log, label = None, "No game logs in demo mode."
    if not d.get("demo"):
        try:
            log, label = gamelog.latest_games(p.playerId, year, rules)
        except OSError:
            log, label = None, "Couldn't load game logs from ESPN. Try again in a minute."
    return {"id": p.playerId, "name": p.name, "nicknames": stats.nicknames_for(p.name)[:3], "pos": p.position,
            "nba": p.proTeam, "status": status_text(p), "status_tag": status_tag(p),
            "owner": f"{team.team_name.strip()} ({owner_names(team)})" if team else "free agent",
            "box_cols": BOX, "splits": splits, "breakdown_label": "projected" if proj else "season",
            "breakdown": [{"stat": stat, "per_game": round(per, 1), "pts": pts, "fp": round(per * pts, 1)}
                          for stat, per, pts in parts],
            "games": log, "games_label": label, "chart_stats": gamelog.CHART_STATS, "stat_names": gamelog.STAT_NAMES,
            "rules": rules}


# ---------------------------------------------------------------- Compare

def compare(d, a, b, split=None, mode="fantasy"):
    import stats
    from compare import axes_for, profile
    league, rules, year = d["league"], d["rules"], d["league"].year
    found = []
    for q in (a, b):
        try:
            p, team = stats.find_player(league, q)
        except SystemExit as e:
            return {"error": str(e)}
        if p is None:
            return {"error": f"No player matches {q!r}."}
        found.append((p, team))
    split = split if split in stats.SPLITS else None
    mode = "raw" if mode == "raw" else "fantasy"
    axes = axes_for(mode, rules)
    players = [profile(p, year, rules, split, mode) for p, _ in found]
    pool = [profile(p, year, rules, split, mode) for tm in league.teams for p in tm.roster] + players
    ranges = {}
    for key, _ in axes:  # each axis spans every rostered player in the league
        vals = [v.get(key, 0) for v in pool]
        lo, hi = min(vals), max(vals)
        ranges[key] = [lo, hi if hi > lo else lo + 1]
    rows = []
    for key, label in axes:
        x, y = players[0].get(key, 0), players[1].get(key, 0)
        gap = (x - y if mode == "fantasy" or key == "FP" else (x - y) * rules[key] if key in rules else None)
        rows.append({"key": key, "label": label, "a": round(x, 1), "b": round(y, 1),
                     "edge": None if gap is None else round(gap, 1)})
    return {"mode": mode, "split": split, "axes": [{"key": k, "label": lab} for k, lab in axes], "ranges": ranges,
            "inverted": ["TO"] if mode == "raw" else [],
            "players": [{"id": p.playerId, "name": p.name, "pos": p.position, "nba": p.proTeam, "status": status_text(p),
                         "owner": team.team_name.strip() if team else "free agent",
                         "values": {k: round(v, 2) for k, v in prof.items()}}
                        for (p, team), prof in zip(found, players)],
            "rows": rows}


# ---------------------------------------------------------------- News

def news_view(d):
    injuries, league = d.get("injuries") or {}, d["league"]
    owner = {p.playerId: (p, t) for t in league.teams for p in t.roster}
    mine = {p.playerId for p in d["me"].roster}
    rows = []
    for pid in set(injuries) | mine:
        info = injuries.get(pid)
        pl, team = owner.get(pid, (None, None))
        status = (info or {}).get("status") or (status_text(pl) if pl else "")
        rows.append({"id": pid, "name": pl.name if pl else (info or {}).get("name", "?"),
                     "team": team.team_name.strip() if team else "free agent", "mine": pid in mine,
                     "rostered": team is not None, "status": status, "injury": (info or {}).get("injury") or "",
                     "back": info["return_date"].strftime("%a %d %b") if info and info["return_date"] else None,
                     "comment": (info or {}).get("comment") or "healthy: no injury report",
                     "details": (info or {}).get("details") or "", "injured": bool(info),
                     "tag": "out" if status.lower() == "out" else "shaky" if info else ""})
    rows.sort(key=lambda r: (not r["mine"], not r["injured"], r["name"]))
    opps = [{"id": o["player"].playerId, "name": o["player"].name, "nba": o["player"].proTeam,
             "usual": round(o["form"].worth, 1), "now": round(o["boosted"], 1), "why": o["reason"],
             "big": o["boosted"] - o["form"].worth >= 5} for o in (d.get("opportunities") or [])[:25]]
    return {"rows": rows, "opportunities": opps}


def player_news(pid):
    import news
    return [{"when": it["published"].strftime("%a %d %b %H:%M") if it["published"] else "",
             "headline": it["headline"], "story": it["story"]} for it in news.player_news(pid)]


# ---------------------------------------------------------------- Activity

def activity_view(d):
    items = []
    for t in d.get("trade_feed") or []:
        items.append({"when": t["when"].isoformat(), "kind": "trade", "status": t["kind"],
                      "process_at": t["process_at"].isoformat() if t["process_at"] else None,
                      "sides": [{"team": s["team"].strip(), "manager": s["manager"],
                                 "receives": [{"id": p["id"], "name": p["name"], "pro": p["pro"], "pos": p["pos"]}
                                              for p in s["receives"]],
                                 "drops": [p["name"] for p in s["drops"]]} for s in t["sides"]]})
    for act in d.get("activity") or []:
        when = datetime.fromtimestamp(act.date / 1000).isoformat()
        for team, action, pl, *_ in act.actions:
            if action.upper() == "TRADED":
                continue  # shown as a trade card
            items.append({"when": when, "kind": "move", "team": (getattr(team, "team_name", team) or "-").strip(),
                          "action": action.lower(), "player": getattr(pl, "name", pl) or "-"})
    for m in d.get("chat") or []:
        items.append({"when": m["when"].isoformat(), "kind": "chat", "author": m["author"],
                      "team": (m["team"] or "").strip(), "text": m["text"]})
    items.sort(key=lambda x: x["when"], reverse=True)
    return items[:150]


# ---------------------------------------------------------------- Trade Lab

def trade_rosters(d):
    """Every team's players for the Trade Lab pickers."""
    league, year = d["league"], d["league"].year
    out = []
    for t in sorted(league.teams, key=lambda t: t.team_name):
        forms = sorted((fantasy.form_of(p, year) for p in t.roster), key=lambda f: -f.worth)
        out.append({"id": t.team_id, "name": t.team_name.strip(), "me": t.team_id == d["me"].team_id,
                    "players": [{"id": f.player.playerId, "name": f.player.name, "pos": f.player.position,
                                 "nba": f.player.proTeam, "status": status_text(f.player),
                                 "status_tag": status_tag(f.player), "base": round(f.baseline, 1),
                                 "l15": round(f.recent, 1)} for f in forms]})
    return out


def trade_lab(d, a_id, b_id, send=(), get=()):
    import trade
    league = d["league"]
    teams = {t.team_id: t for t in league.teams}
    team_a = teams.get(a_id, d["me"])
    team_b = teams.get(b_id)
    if team_b is None or team_b.team_id == team_a.team_id:
        team_b = next(t for t in league.teams if t.team_id != team_a.team_id)
    send = {p for p in send if any(x.playerId == p for x in team_a.roster)}
    get = {p for p in get if any(x.playerId == p for x in team_b.roster)}
    r = trade.simulate(league, team_a, team_b, send, get)
    gl = r["games_left"]
    sides = []
    for key in ("a", "b"):
        s = r[key]
        gives = r["sent"] if key == "a" else r["got"]
        gets = r["got"] if key == "a" else r["sent"]
        look = (trade.outlook(league, s["team"], {f.player.playerId for f in gives}, [f.player for f in gets],
                              d.get("injuries") or {}) if (gives or gets) else None)
        sides.append({"team": s["team"].team_name.strip(), "before": round(s["before"], 1),
                      "after": round(s["after"], 1), "delta": round(s["delta"], 1),
                      "rank_before": s["rank_before"], "rank_after": s["rank_after"], "season": round(s["delta"] * gl),
                      "next30": ([round(look["before"]), round(look["after"])]
                                 if look and look["before"] is not None else None),
                      "fixed": trade.describe_fix(look["fixed"], "its") if look and look["fixed"] else "",
                      "created": ", ".join(look["created"]) if look else "",
                      "gives": [f"{f.player.name} ({f.baseline:.1f})" for f in gives],
                      "gets": [f"{f.player.name} ({f.baseline:.1f})" for f in gets],
                      "positions": [f"{pos} {s['pos_before'][pos]}→{s['pos_after'][pos]}" for pos in trade.POSITIONS
                                    if s["pos_before"][pos] != s["pos_after"][pos]],
                      "roster": [s["size_before"], s["size_after"]], "drop": s["drop"]})
    options = trade.balance(league, team_a, team_b, send, get)
    names = {t.team_id: t.team_name.strip() for t in league.teams}
    return {"a": team_a.team_id, "b": team_b.team_id, "send": sorted(send), "get": sorted(get),
            "verdict": trade.verdict(r), "sides": sides,
            "strength": [{"team": names[tid], "before": round(r["before"][tid], 1), "after": round(r["after"][tid], 1),
                          "side": 0 if tid == team_a.team_id else 1 if tid == team_b.team_id else None}
                         for tid in sorted(r["after"], key=lambda k: -r["after"][k])],
            "suggestions": [{"send": sorted(o["send"]), "get": sorted(o["get"]), "send_names": o["send_names"],
                             "get_names": o["get_names"], "da": round(o["da"], 1), "db": round(o["db"], 1)}
                            for o in options],
            "suggest_title": ("Ways to even out this trade (adds up to 2 players)" if (send or get)
                              else f"Fair trades with {names[team_b.team_id]} (1-2 players each way)")}
