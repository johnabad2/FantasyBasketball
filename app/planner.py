"""Daily-lineup planning for a points league: the best legal lineup on any day, the week ahead
(how many lineup slots you'll actually fill), and streamers (free agents who fill your empty days).

Values are each player's 'base' (fantasy pts/game). A player counts on a day if his NBA team plays
and he isn't injured - an OUT player counts again from ESPN's expected return date."""
import time
from datetime import date, datetime, timedelta

from fantasy import OUT_STATUSES, form_of
from projections import nba_team_id

DEFAULT_SLOTS = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT", "UT", "UT"]
LONG_TERM_SLACK = 0.0  # never drop someone better per game than the streamer (fp/g)


def lineup_slots(league):
    """The league's active lineup slots, e.g. ['PG', 'SG', ..., 'UT', 'UT', 'UT'] (cached)."""
    if getattr(league, "_fh_slots", None):
        return league._fh_slots
    slots, ir, bench = DEFAULT_SLOTS, 1, 3
    try:
        from espn_api.basketball.constant import POSITION_MAP
        settings = league.espn_request.league_get(params={"view": "mSettings"})["settings"]
        counts = settings["rosterSettings"]["lineupSlotCounts"]
        found = [POSITION_MAP[int(k)] for k, n in counts.items() for _ in range(n)
                 if POSITION_MAP.get(int(k)) not in (None, "BE", "IR")]
        slots = found or DEFAULT_SLOTS
        ir = sum(n for k, n in counts.items() if POSITION_MAP.get(int(k)) == "IR")
        bench = sum(n for k, n in counts.items() if POSITION_MAP.get(int(k)) == "BE")
    except Exception:
        pass
    league._fh_slots, league._fh_ir, league._fh_bench = slots, ir, bench
    return slots


def open_roster_spots(league, team):
    """Free spots on the active roster (lineup + bench), e.g. after moving someone to IR."""
    slots = lineup_slots(league)
    used = sum(1 for p in team.roster if p.lineupSlot != "IR")
    return max(len(slots) + getattr(league, "_fh_bench", 3) - used, 0)


def open_ir_slots(league, team):
    lineup_slots(league)
    return max(getattr(league, "_fh_ir", 0) - sum(1 for p in team.roster if p.lineupSlot == "IR"), 0)


def ir_candidates(league, team, injuries, days=7):
    """Players who could go to IR and won't be back within `days`: [(player, return date or None, certain)].

    ESPN rules: a player listed INJ (injury reserve) can always go on IR (certain=True). A player
    listed O (out) can only if ESPN has also marked him IR-eligible - and ESPN doesn't publish that
    flag in any data this app can read, so those come back with certain=False and the app asks
    you to check his IR eligibility on ESPN before moving him."""
    soon = date.today() + timedelta(days=days)
    out = []
    for p in team.roster:
        status = p.injuryStatus or "ACTIVE"
        if p.lineupSlot == "IR" or status not in ("INJURY_RESERVE", "OUT"):
            continue
        info = injuries.get(p.playerId)
        back = info["return_date"] if info else None
        if back is None or back > soon:
            out.append((p, back, status == "INJURY_RESERVE"))
    return sorted(out, key=lambda x: not x[2])  # sure things first


def best_lineup(cands, slots):
    """Highest-value legal lineup. cands: [(value, player, eligible slot names)].
    Returns {slot index: cand}. Adding players best-first, re-shuffling earlier picks when
    needed (augmenting paths), is optimal here: lineups like this form a matroid."""
    owner = [None] * len(slots)

    def place(ci, seen):
        current = getattr(cands[ci][1], "lineupSlot", None)
        # try the player's current slot first, so the plan moves as few players as possible
        for si in sorted(range(len(slots)), key=lambda i: slots[i] != current):
            slot = slots[si]
            if slot in cands[ci][2] and si not in seen:
                seen.add(si)
                if owner[si] is None or place(owner[si], seen):
                    owner[si] = ci
                    return True
        return False

    for ci in sorted(range(len(cands)), key=lambda i: -cands[i][0]):
        place(ci, set())
    return {si: cands[ci] for si, ci in enumerate(owner) if ci is not None}


def _game(league, player, day):
    games = (getattr(league, "pro_schedule", None) or {}).get(nba_team_id(player), {}).get(str(day), [])
    return games[0] if games else None


def _available(player, game, injuries):
    """Will he play in this game? OUT players count again from their expected return date."""
    if game is None:
        return False
    if (player.injuryStatus or "ACTIVE") in OUT_STATUSES:
        info = injuries.get(player.playerId)
        back = info["return_date"] if info else None
        game_day = datetime.fromtimestamp(game["date"] / 1000).date()
        return back is not None and game_day >= back
    return True


def _cands(league, players, values, day, injuries, not_started=False):
    now_ms = time.time() * 1000
    out = []
    for p in players:
        g = _game(league, p, day)
        if not _available(p, g, injuries) or (not_started and g["date"] <= now_ms):
            continue
        out.append((values[p.playerId], p, set(p.eligibleSlots or [])))
    return out


def games_after(league, player, first_day, injuries):
    """Games he's expected to play from `first_day` to the end of the fantasy season
    (his NBA schedule, skipping games before an injured player's expected return)."""
    final = getattr(league, "finalScoringPeriod", None) or 174
    return sum(1 for day in range(first_day, final + 1) if _available(player, _game(league, player, day), injuries))


def _day_date(league, day):
    today = getattr(league, "current_week", 1) or 1
    return date.today() + timedelta(days=day - today)


def week(league, players, values, injuries, days=7):
    """One row per day from today: {day, date, playing, filled, slots, points, lineup, cands, slot_names}.
    cands = everyone available that day (value, player, eligible slots); lineup = who gets a slot."""
    slots = lineup_slots(league)
    today = getattr(league, "current_week", 1) or 1
    final = getattr(league, "finalScoringPeriod", None) or 174
    out = []
    for day in range(today, min(today + days, final + 1)):
        cands = _cands(league, players, values, day, injuries)
        best = best_lineup(cands, slots)
        out.append({"day": day, "date": _day_date(league, day), "playing": len(cands), "filled": len(best),
                    "slots": len(slots), "points": sum(c[0] for c in best.values()), "lineup": best,
                    "cands": cands, "slot_names": slots})
    return out


def streamers(league, team, free_agents, injuries, days=7, top=12, drop_pool=4):
    """Free agents who'd add the most points over the next `days` days, and how to make room:
    'ir' = an injured player who could go to your open IR slot (ir_certain=False means only if
    ESPN lists him IR-eligible), 'drop' = the best fair drop (never someone more than
    LONG_TERM_SLACK better long-term). [{fa, ir, ir_certain, drop, gain, drop_gain, games,
    light_games}] best first; gain assumes the IR move when there is one."""
    if not getattr(league, "pro_schedule", None):
        return []
    roster = list(team.roster)
    values = {p.playerId: form_of(p, league.year).baseline for p in roster}
    base_week = week(league, roster, values, injuries, days)
    base = sum(d["points"] for d in base_week)
    light_days = {d["day"] for d in base_week if d["filled"] < d["slots"]}
    # drop candidates: the lowest-value players, plus anyone injured (they score nothing meanwhile)
    from fantasy import load_keep
    keep = load_keep()  # players you've said never to drop
    by_value = sorted((p for p in roster if p.lineupSlot != "IR" and p.playerId not in keep),
                      key=lambda p: values[p.playerId])
    drops = list(dict.fromkeys(by_value[:drop_pool] + [p for p in roster if (p.injuryStatus or "") in OUT_STATUSES
                                                         and p.lineupSlot != "IR" and p.playerId not in keep]))
    # With an open IR slot, an injured player can go to IR instead of being dropped.
    ir_moves = ir_candidates(league, team, injuries, days)[:open_ir_slots(league, team)]
    free_spot = open_roster_spots(league, team) > 0  # e.g. right after moving someone to IR
    out = []
    for f in free_agents:
        fa = f.player
        if fa.proTeam == "FA" or not f.healthy or f.baseline <= 0:
            continue
        games = [d for d in range(base_week[0]["day"], base_week[-1]["day"] + 1) if _game(league, fa, d)]
        if not games:
            continue
        vals = {**values, fa.playerId: f.baseline}
        if free_spot:  # simplest case: just add him
            gain = sum(d["points"] for d in week(league, roster + [fa], vals, injuries, days)) - base
            if gain > 0.5:
                out.append({"fa": f, "open_spot": True, "ir": None, "ir_certain": False, "drop": None,
                            "later": f.baseline * games_after(league, fa, base_week[-1]["day"] + 1, injuries),
                            "drop_gain": None, "gain": gain, "games": len(games),
                            "light_games": sum(1 for d in games if d in light_days)})
            continue
        ir = ir_moves[0] if ir_moves else None  # (player, back, certain): nobody leaves your team
        ir_gain = (sum(d["points"] for d in week(league, roster + [fa], vals, injuries, days)) - base) if ir else None
        best_drop = None
        after = base_week[-1]["day"] + 1  # first day after the planning window
        fa_later = f.baseline * games_after(league, fa, after, injuries)
        for drop in drops:
            if values[drop.playerId] > f.baseline + LONG_TERM_SLACK:
                continue  # dropping is permanent: never lose a better player for a one-week stream
            trial = [p for p in roster if p is not drop] + [fa]
            gain = sum(d["points"] for d in week(league, trial, vals, injuries, days)) - base
            # after this week: his points vs the dropped player's, over all their remaining games
            later = fa_later - values[drop.playerId] * games_after(league, drop, after, injuries)
            if gain + later <= 0:
                continue  # wins this week but loses over the season: the dropped player is better overall
            if best_drop is None or gain > best_drop[0] + 0.05:
                best_drop = (gain, drop, later)
        # a sure IR move beats any drop; an uncertain one is shown together with the drop fallback
        gain = ir_gain if ir and (ir[2] or not best_drop) else (best_drop[0] if best_drop else None)
        if ir and not ir[2] and best_drop:
            gain = max(ir_gain, best_drop[0])
        if gain is not None and gain > 0.5:
            out.append({"fa": f, "open_spot": False, "ir": ir[0] if ir else None, "ir_certain": bool(ir and ir[2]),
                        "drop": best_drop[1] if best_drop else None,
                        "later": best_drop[2] if best_drop else fa_later,
                        "drop_gain": best_drop[0] if best_drop else None, "gain": gain, "games": len(games),
                        "light_games": sum(1 for d in games if d in light_days)})
    return sorted(out, key=lambda x: -x["gain"])[:top]


def swap_value(league, team, add, drop, injuries, days=7):
    """What adding `add` and dropping `drop` (None = open spot) does to `team`: points over the next
    `days` days (best lineup each day) and over the rest of the season after that (games left x base)."""
    roster = list(team.roster)
    values = {p.playerId: form_of(p, league.year).baseline for p in roster}
    add_base = form_of(add, league.year).baseline
    base_week = week(league, roster, values, injuries, days)
    trial = [p for p in roster if drop is None or p.playerId != drop.playerId] + [add]
    new_week = week(league, trial, {**values, add.playerId: add_base}, injuries, days)
    after = (base_week[-1]["day"] + 1) if base_week else (getattr(league, "current_week", 1) or 1)
    add_games = games_after(league, add, after, injuries)
    drop_games = games_after(league, drop, after, injuries) if drop else 0
    drop_base = values[drop.playerId] if drop else 0.0
    return {"this_week": sum(d["points"] for d in new_week) - sum(d["points"] for d in base_week),
            "rest_of_season": add_base * add_games - drop_base * drop_games,
            "add_base": add_base, "drop_base": drop_base, "add_games_after": add_games,
            "drop_games_after": drop_games, "days": days}


def today_moves(league, team, injuries):
    """Best lineup for the rest of today vs the current one. Players whose game has started are
    locked where they are. Returns {current, best, start: [(player, slot)], bench: [player],
    lineup: {slot index: cand}, slots} or None when there's nothing to plan (no schedule)."""
    if not getattr(league, "pro_schedule", None):
        return None
    day = getattr(league, "current_week", 1) or 1
    now_ms = time.time() * 1000
    values = {p.playerId: form_of(p, league.year).baseline for p in team.roster}
    slots = list(lineup_slots(league))
    starters = [p for p in team.roster if p.lineupSlot not in ("BE", "IR")]

    def started(p):
        g = _game(league, p, day)
        return g is not None and g["date"] <= now_ms

    locked = [p for p in starters if started(p)]  # already playing: can't be moved
    for p in locked:
        if p.lineupSlot in slots:
            slots.remove(p.lineupSlot)
    free = [p for p in team.roster if p.lineupSlot != "IR" and not started(p)]
    best = best_lineup(_cands(league, free, values, day, injuries, not_started=True), slots)
    chosen = {c[1].playerId for c in best.values()}

    def plays(p):
        return _available(p, _game(league, p, day), injuries)

    current = sum(values[p.playerId] for p in starters if plays(p))
    best_pts = sum(values[p.playerId] for p in locked if plays(p)) + sum(c[0] for c in best.values())
    start = [(c[1], slots[si]) for si, c in best.items() if c[1] not in starters]
    displaced = [p for p in starters if p not in locked and p.playerId not in chosen]
    displaced.sort(key=lambda p: (plays(p), values[p.playerId]))  # no game today first
    bench = displaced[:len(start)]  # only make room for the players coming in
    moves = [(c[1], c[1].lineupSlot, slots[si]) for si, c in best.items()
             if c[1] in starters and c[1].lineupSlot != slots[si]]  # starters switching slots
    return {"current": current, "best": best_pts, "start": start, "bench": bench, "moves": moves,
            "lineup": best, "slots": slots, "locked": locked}
