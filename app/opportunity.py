"""Opportunity: when an NBA rotation player is out, his minutes go to teammates at his positions.
Free agents among those teammates can be worth far more than their averages for a while - and
they're usually picked up within a day or two, so the earlier you know the better.

Model: the injured player's minutes per game are shared by up to 3 healthy teammates who can play
one of his positions (most minutes first), each capped at 36 minutes. A beneficiary gains his own
fantasy points per minute x the extra minutes (damped, capped). Also flags rising minutes: a player whose last-7-day
minutes are well above his season or projected minutes has likely won a bigger role."""
from datetime import date, timedelta

from fantasy import OUT_STATUSES, form_of

POSITIONS = {"PG", "SG", "SF", "PF", "C"}
MIN_INJURED_MPG = 22  # only a real rotation player's absence frees meaningful minutes
MIN_DAYS_OUT = 3      # a one-game absence isn't worth a roster move
SHARERS = 3
MAX_MPG = 36
RISING_MPG = 5        # last 7 days this many minutes above normal = a bigger role
DAMPING = 0.75        # teammates rarely soak up every freed minute at their usual efficiency
MAX_BOOST = 1.6       # never project more than 60% above his usual value


def _avg(player, year, key):
    return ((player.stats.get(f"{year}_{key}") or {}).get("avg") or {})


def minutes(player, year):
    """Usual minutes per game: this season after 3 games, else ESPN's projection."""
    season = _avg(player, year, "total")
    if (season.get("GP") or 0) >= 3 and season.get("MPG"):
        return float(season["MPG"])
    return float(_avg(player, year, "projected").get("MPG") or season.get("MPG") or 0)


def minutes_trend(player, year):
    """Last-7-days minutes minus his usual minutes, or None without recent games."""
    last = _avg(player, year, "last_7")
    if not last.get("MPG") or (last.get("GP") or 0) < 2:
        return None
    return float(last["MPG"]) - minutes(player, year)


def _surname(name):
    parts = [w for w in name.split() if w.rstrip(".").upper() not in ("JR", "SR", "II", "III", "IV")]
    return parts[-1] if parts else name


def _pool(league, size=300):
    """Every NBA player we can see: all fantasy rosters plus the free-agent pool (cached)."""
    if getattr(league, "_fh_pool", None) is None:
        rostered = [(p, t) for t in league.teams for p in t.roster]
        try:
            free = [(p, None) for p in league.free_agents(size=size) if p.proTeam != "FA"]
        except Exception:
            free = []
        league._fh_pool = rostered + free
    return league._fh_pool


def opportunities(league, injuries):
    """[{player, form, boosted, extra_min, reason, back, kind}] for free agents, best boost first.
    kind = "injury" (a teammate is out) or "minutes" (his minutes are rising)."""
    year, today = league.year, date.today()
    pool = _pool(league)
    out = {}
    for injured, _ in pool:
        if (injured.injuryStatus or "ACTIVE") not in OUT_STATUSES:
            continue
        info = injuries.get(injured.playerId) or {}
        back = info.get("return_date")
        if back and back < today + timedelta(days=MIN_DAYS_OUT):
            continue
        freed = minutes(injured, year)
        if freed < MIN_INJURED_MPG:
            continue
        spots = set(injured.eligibleSlots or []) & POSITIONS
        mates = [(p, team) for p, team in pool if p.proTeam == injured.proTeam and p.playerId != injured.playerId
                 and (p.injuryStatus or "ACTIVE") not in OUT_STATUSES and spots & set(p.eligibleSlots or [])]
        mates.sort(key=lambda m: -minutes(m[0], year))
        share = freed / max(min(len(mates), SHARERS), 1)
        for p, team in mates[:SHARERS]:
            mpg = minutes(p, year)
            if team is not None or mpg < 8:
                continue  # rostered already, or too far out of the rotation to say
            extra = min(share, MAX_MPG - mpg)
            f = form_of(p, year)
            boosted = min(f.worth + DAMPING * f.worth / mpg * extra, f.worth * MAX_BOOST)
            best = out.get(p.playerId)
            if extra > 0 and (not best or boosted > best["boosted"]):
                until = f" until ~{back:%d %b}" if back else ""
                out[p.playerId] = {"player": p, "form": f, "boosted": boosted, "extra_min": extra,
                                   "reason": f"{injured.name} out{until}",
                                   "short": f"{_surname(injured.name)} out{until}",
                                   "back": back, "kind": "injury"}
    for p, team in pool:  # bigger role: minutes rising this week
        trend = minutes_trend(p, year) if team is None else None
        if trend is not None and trend >= RISING_MPG and p.playerId not in out \
                and (p.injuryStatus or "ACTIVE") not in OUT_STATUSES:
            f = form_of(p, year)
            out[p.playerId] = {"player": p, "form": f, "boosted": max(f.recent, f.worth), "extra_min": trend,
                               "reason": f"minutes up {trend:+.0f} a game this week",
                               "short": f"minutes {trend:+.0f}/game", "back": None, "kind": "minutes"}
    return sorted(out.values(), key=lambda o: -o["boosted"])
