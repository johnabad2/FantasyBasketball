"""How much a trade or pickup is really worth: rest-of-season lineup points above replacement.

Built on the methods fantasy analysts use:
- Value over replacement: a player is worth what he adds over the best you could pick up for free.
  A trade that opens a roster spot is credited with the free agent you'd fill it with; one that
  overfills your roster costs you the weakest player you'd drop.
- Lineup-based evaluation: what counts is how many points your starting lineup scores over the rest
  of the season (daily best lineup, schedule, missed games, injuries until they're back, matchups),
  not comparing players' averages. A player who'd sit on your bench adds little.
- Regression to the mean is handled in fantasy.form_of (early numbers lean on projections).
- Would they accept? Other managers judge on public values, so "looks fair to them" is checked
  with ESPN's own projections, which is what they see.
"""
import fantasy
import projections
import trade

MARGIN = 40          # rest-of-season points: smaller gains are within the noise of the projections
SLOTS = 10           # starting lineup size
FAIR_TO_THEM = -1.5  # ESPN-projected points per game they may lose and still see it as fair


def replacement_players(league, free_agents, n=4):
    """The best healthy free agents you could add today (not on waivers): who fills an open spot."""
    waivers = getattr(league, "_fh_waivers", None) or {}
    return [f.player for f in sorted(free_agents, key=lambda f: -f.worth)
            if f.healthy and f.player.proTeam != "FA" and f.player.playerId not in waivers][:n]


def ros(league, roster, injuries=None):
    """Rest-of-season lineup points for a roster (see projections.rest_of_season)."""
    return projections.rest_of_season(league, roster, SLOTS, injuries)


def _value(league, p):
    return fantasy.form_of(p, league.year).baseline * projections.availability(p, league.year)


def after_trade(league, roster, out_ids, incoming, fill):
    """The roster after a trade, kept the same size: open spots filled from `fill` (best free agents
    not already on it), extra players cut from the bottom."""
    have = {p.playerId for p in roster} | {p.playerId for p in incoming}
    spare = [p for p in fill if p.playerId not in have]
    new = [p for p in roster if p.playerId not in out_ids] + list(incoming)
    vals = {p.playerId: _value(league, p) for p in new + spare}
    return trade.fit_roster(new, len(roster), vals, spare)


def free_upgrade(league, roster, fill):
    """The roster after the free moves anyone could make: any player worse than an available free
    agent is swapped for him. Applied to both sides of every comparison, so a trade only gets credit
    for what you couldn't get off the waiver wire anyway."""
    roster = list(roster)
    have = {p.playerId for p in roster}
    for fa in sorted((f for f in fill if f.playerId not in have), key=lambda f: -_value(league, f)):
        active = [p for p in roster if p.lineupSlot != "IR" and (p.injuryStatus or "ACTIVE") not in fantasy.OUT_STATUSES]
        if not active:
            break
        worst = min(active, key=lambda p: _value(league, p))
        if _value(league, fa) > _value(league, worst) + 1:
            roster = [p for p in roster if p is not worst] + [fa]
    return roster


def espn_ppg(league, p):
    """ESPN's projected fantasy points per game: the public number the other manager sees."""
    import stats
    return stats.fp_per_game(stats.split_of(p, league.year, "projected"), stats.scoring_rules(league)) \
        if stats.split_of(p, league.year, "projected") else fantasy.form_of(p, league.year).baseline


def evaluate(league, mine, theirs, send_ids, get_ids, fill, injuries=None, cache=None):
    """One trade, both sides: {me, them} = change in rest-of-season lineup points (replacement
    adjusted), {looks_fair} = whether it looks fair to them on ESPN's projections."""
    cache = {} if cache is None else cache

    def cached(roster):
        key = frozenset(p.playerId for p in roster)
        if key not in cache:
            cache[key] = ros(league, roster, injuries)
        return cache[key]
    send = [p for p in mine if p.playerId in send_ids]
    get = [p for p in theirs if p.playerId in get_ids]
    my_after = after_trade(league, mine, set(send_ids), get, fill)
    their_after = after_trade(league, theirs, set(get_ids), send, fill)
    # value over replacement: both sides of the comparison get the free waiver moves first
    mine, my_after = free_upgrade(league, mine, fill), free_upgrade(league, my_after, fill)
    theirs, their_after = free_upgrade(league, theirs, fill), free_upgrade(league, their_after, fill)
    # how it looks to them on public numbers: what they get minus what they give (ESPN per game),
    # an extra player they must cut counts as their weakest; a spot they open is worth a waiver pickup
    rep = espn_ppg(league, fill[0]) if fill else 0.0
    gets, gives = sorted((espn_ppg(league, p) for p in send), reverse=True), sorted((espn_ppg(league, p) for p in get), reverse=True)
    market = sum(gets) - sum(gives) + (len(gives) - len(gets)) * rep
    return {"me": cached(my_after) - cached(mine), "them": cached(their_after) - cached(theirs),
            "market_them": market, "looks_fair": market >= FAIR_TO_THEM,
            "my_after": my_after, "their_after": their_after}


def pickup_gain(league, roster, add, drop, injuries=None):
    """Rest-of-season lineup points from adding `add` and dropping `drop`."""
    after = [p for p in roster if p.playerId != drop.playerId] + [add]
    return ros(league, after, injuries) - ros(league, roster, injuries)
