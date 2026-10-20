"""Daily-lineup planning for a points league: the best legal lineup on any day, the week ahead
(how many lineup slots you'll actually fill), and streamers (free agents who fill your empty days).

Values are each player's 'base' (fantasy pts/game). A player counts on a day if his NBA team plays
and he isn't injured - an OUT player counts again from ESPN's expected return date."""
import time
from datetime import date, datetime, timedelta

from fantasy import OUT_STATUSES, form_of
from projections import nba_team_id

DEFAULT_SLOTS = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT", "UT", "UT"]
LONG_TERM_SLACK = 2.0  # a streamer may replace someone at most this many fp/g better than him


def lineup_slots(league):
    """The league's active lineup slots, e.g. ['PG', 'SG', ..., 'UT', 'UT', 'UT'] (cached)."""
    if getattr(league, "_fh_slots", None):
        return league._fh_slots
    slots, ir = DEFAULT_SLOTS, 1
    try:
        from espn_api.basketball.constant import POSITION_MAP
        settings = league.espn_request.league_get(params={"view": "mSettings"})["settings"]
        counts = settings["rosterSettings"]["lineupSlotCounts"]
        found = [POSITION_MAP[int(k)] for k, n in counts.items() for _ in range(n)
                 if POSITION_MAP.get(int(k)) not in (None, "BE", "IR")]
        slots = found or DEFAULT_SLOTS
        ir = sum(n for k, n in counts.items() if POSITION_MAP.get(int(k)) == "IR")
    except Exception:
        pass
    league._fh_slots, league._fh_ir = slots, ir
    return slots


def open_ir_slots(league, team):
    lineup_slots(league)
    return max(getattr(league, "_fh_ir", 0) - sum(1 for p in team.roster if p.lineupSlot == "IR"), 0)


def ir_candidates(league, team, injuries, days=7):
    """IR-eligible players not on IR yet who won't be back within `days`: (player, return date or None).
    ESPN only lets players listed INJ (injury reserve) into the IR slot - plain OUT isn't enough."""
    soon = date.today() + timedelta(days=days)
    out = []
    for p in team.roster:
        if p.lineupSlot == "IR" or (p.injuryStatus or "ACTIVE") != "INJURY_RESERVE":
            continue
        info = injuries.get(p.playerId)
        back = info["return_date"] if info else None
        if back is None or back > soon:
            out.append((p, back))
    return out


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


def _day_date(league, day):
    today = getattr(league, "current_week", 1) or 1
    return date.today() + timedelta(days=day - today)


def week(league, players, values, injuries, days=7):
    """One row per day from today: {day, date, playing, filled, slots, points, lineup}."""
    slots = lineup_slots(league)
    today = getattr(league, "current_week", 1) or 1
    final = getattr(league, "finalScoringPeriod", None) or 174
    out = []
    for day in range(today, min(today + days, final + 1)):
        cands = _cands(league, players, values, day, injuries)
        best = best_lineup(cands, slots)
        out.append({"day": day, "date": _day_date(league, day), "playing": len(cands), "filled": len(best),
                    "slots": len(slots), "points": sum(c[0] for c in best.values()), "lineup": best})
    return out


def streamers(league, team, free_agents, injuries, days=7, top=12, drop_pool=4):
    """Free agents who'd add the most points over the next `days` days, each paired with the
    roster player whose drop costs the least - or, with an open IR slot, an injured player to move
    to IR instead. [{fa, drop, to_ir, gain, games, light_games}] best first."""
    if not getattr(league, "pro_schedule", None):
        return []
    roster = list(team.roster)
    values = {p.playerId: form_of(p, league.year).baseline for p in roster}
    base_week = week(league, roster, values, injuries, days)
    base = sum(d["points"] for d in base_week)
    light_days = {d["day"] for d in base_week if d["filled"] < d["slots"]}
    # drop candidates: the lowest-value players, plus anyone injured (they score nothing meanwhile)
    by_value = sorted((p for p in roster if p.lineupSlot != "IR"), key=lambda p: values[p.playerId])
    drops = list(dict.fromkeys(by_value[:drop_pool] + [p for p in roster if (p.injuryStatus or "") in OUT_STATUSES
                                                         and p.lineupSlot != "IR"]))
    # With an open IR slot, an injured player can go to IR instead of being dropped.
    ir_moves = [p for p, _ in ir_candidates(league, team, injuries, days)][:open_ir_slots(league, team)]
    out = []
    for f in free_agents:
        fa = f.player
        if fa.proTeam == "FA" or not f.healthy or f.baseline <= 0:
            continue
        games = [d for d in range(base_week[0]["day"], base_week[-1]["day"] + 1) if _game(league, fa, d)]
        if not games:
            continue
        vals = {**values, fa.playerId: f.baseline}
        best = None
        for ir in ir_moves:  # first choice: nobody leaves your team
            gain = sum(d["points"] for d in week(league, roster + [fa], vals, injuries, days)) - base
            best = (gain, ir, True)
        for drop in drops:
            if values[drop.playerId] > f.baseline + LONG_TERM_SLACK:
                continue  # dropping is permanent: never lose a better player for a one-week stream
            trial = [p for p in roster if p is not drop] + [fa]
            gain = sum(d["points"] for d in week(league, trial, vals, injuries, days)) - base
            if best is None or gain > best[0] + 0.05:
                best = (gain, drop, False)
        if best and best[0] > 0.5:
            out.append({"fa": f, "drop": best[1], "to_ir": best[2], "gain": best[0], "games": len(games),
                        "light_games": sum(1 for d in games if d in light_days)})
    return sorted(out, key=lambda x: -x["gain"])[:top]


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
