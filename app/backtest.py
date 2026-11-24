"""Backtest: replay this season's games to check (and tune) how the app projects a player's next game.

    .venv\\Scripts\\python app\\fantasy.py backtest

For every finished game, each player who played (and had at least MIN_PRIOR earlier games) is
projected using only what was known before that game: his earlier box scores this season plus
ESPN's season projection (the prior), exactly as fantasy.form_of does. That projection is compared
with what he actually scored.

1. Form settings: how hard to lean on ESPN's projection vs his own games (rate prior), and how much
   his last 7 / last 15 days of minutes count. Each combination is scored by its average miss.
2. Game factors (opponent pace + defence, defence vs his position, back-to-back, blowout risk):
   for each, how players actually did relative to the projection in games where the factor said
   better or worse. "Slope" 1.0 = the factor is the right size, 0 = it has no signal, above 1 = it
   should be stronger. Defence vs position is rebuilt from earlier games only; the opponent and
   blowout ratings use today's team table (a small look-ahead, noted in the report).

Only games where a player actually played are counted: whether he plays at all is availability,
handled separately (ESPN's projected games)."""
from collections import defaultdict
from datetime import timedelta
from itertools import product

import boxscores
import defense
import fantasy
import matchups
from stats import scoring_rules

MIN_PRIOR = 3  # games a player needs before he's projected (the app treats fewer as mostly ESPN's guess)

# The form grid: rate prior (games ESPN's projection counts as), minutes prior, weight of each game in the
# last 7 days, weight of each game in the last 15 days (on top of counting once in the season).
GRID = {"rate_prior": (8, 12, 20, 30), "min_prior": (4, 8), "w7": (0, 1, 2, 3), "w15": (0, 1, 2)}


def _projection(league):
    """{player id: (ESPN projected fantasy pts/game, projected minutes)} for every player we can see."""
    import opportunity
    out = {}
    for p, _ in opportunity._pool(league):
        proj = p.stats.get(f"{league.year}_projected", {})
        fp, mpg = fantasy.split_value(proj), fantasy._avg(proj, "MPG") or fantasy._avg(proj, "MIN")
        if fp and mpg:
            out[p.playerId] = (fp, mpg)
    return out


def base(history, day, proj, rate_prior, min_prior, w7, w15):
    """Projected fantasy points for a game on `day`, from earlier games [(date, minutes, fp)] and ESPN's
    (fp, minutes) projection. The same model as fantasy.form_of."""
    n = len(history)
    s_min = sum(h[1] for h in history) / n
    s_rate = sum(h[2] for h in history) / max(sum(h[1] for h in history), 1)
    r7 = [h[1] for h in history if h[0] >= day - timedelta(days=7)]
    r15 = [h[1] for h in history if h[0] >= day - timedelta(days=15)]
    p_fp, p_min = proj
    w = n / (n + rate_prior)
    rate = w * s_rate + (1 - w) * p_fp / p_min
    minutes = ((min_prior * p_min + n * s_min + w7 * sum(r7) + w15 * sum(r15))
               / (min_prior + n + w7 * len(r7) + w15 * len(r15)))
    return rate * minutes


def samples(league):
    """[(actual fp, history, day, projection, factors {name: multiplier})] for every projectable game."""
    games = boxscores.season(league)
    rules = scoring_rules(league)
    proj = _projection(league)
    table = matchups.for_league(league)
    dates, _ = matchups._dates(league)
    history = defaultdict(list)
    out = []
    played = []  # games before the current day, for defence vs position
    tilt, tilt_day = {}, None
    for day, gid, home, away, box in games:
        if day != tilt_day:
            tilt = defense.tilts(defense.tally(played, rules)) if played else {}
            tilt_day = day
        for team, opp in ((home, away), (away, home)):
            # schedule effects, as matchups.schedule_factor does them
            b2b = matchups.B2B if day - timedelta(days=1) in dates.get(team, ()) else 1.0
            me, them = table.get(team), table.get(opp)
            blow = 1.0
            if me and them and me.get("margin") is not None and them.get("margin") is not None:
                margin = me["margin"] - them["margin"] + (matchups.HOME if team == home else -matchups.HOME)
                blow = 1 - min(max(abs(margin) - matchups.BLOWOUT_FROM, 0) * 0.01, matchups.BLOWOUT_MAX)
            for pos, minutes, s, pid in box.get(str(team), []):
                fp = boxscores.fantasy_points(s, rules)
                if pid in proj and len(history[pid]) >= MIN_PRIOR:
                    out.append((fp, list(history[pid]), day, proj[pid],
                                {"opponent": matchups.factor(table, opp), "position": tilt.get(opp, {}).get(pos, 1.0),
                                 "back-to-back": b2b, "blowout": blow}))
        for team in (home, away):
            for pos, minutes, s, pid in box.get(str(team), []):
                history[pid].append((day, minutes, boxscores.fantasy_points(s, rules)))
        played.append((day, gid, home, away, box))
    return out


def rest_rates(league, min_minutes=24, min_games=5):
    """(share of second nights of back-to-backs that rotation players sat, share of other games), and
    how many team games each is from. Rotation = averaging min_minutes+ in his games (from his first game)."""
    dates, _ = matchups._dates(league)
    team_games = defaultdict(list)
    for day, gid, home, away, box in boxscores.season(league):
        for t in (home, away):
            team_games[t].append((day, {r[3]: r[1] for r in box.get(str(t), [])}))
    counts = {True: [0, 0], False: [0, 0]}  # back-to-back? -> [games, sat]
    for t, games in team_games.items():
        minutes = defaultdict(list)
        for day, played in games:
            for pid, m in played.items():
                minutes[pid].append(m)
        for pid, ms in minutes.items():
            if len(ms) < min_games or sum(ms) / len(ms) < min_minutes:
                continue
            first = next(i for i, (d, p) in enumerate(games) if pid in p)
            for day, played in games[first:]:
                c = counts[day - timedelta(days=1) in dates.get(t, ())]
                c[0] += 1
                c[1] += pid not in played
    return {k: (sat / n if n else 0.0, n) for k, (n, sat) in counts.items()}


def _mae(pairs):
    return sum(abs(a - p) for a, p in pairs) / len(pairs) if pairs else 0.0


def form_grid(rows):
    """[(average miss, settings)] for every GRID combination, best first."""
    out = []
    for combo in product(*GRID.values()):
        kw = dict(zip(GRID, combo))
        out.append((_mae([(a, base(h, d, p, **kw)) for a, h, d, p, _ in rows]), kw))
    return sorted(out, key=lambda r: r[0])


def calibrate(rows, kw):
    """{factor: (slope, standard error, games where it moved the projection, miss with, miss without)}."""
    preds = [(a, base(h, d, p, **kw), f) for a, h, d, p, f in rows]
    out = {}
    for name in preds[0][2] if preds else ():
        # how much the other factors' projection missed by, vs this factor's own nudge (weighted by size)
        num = den = 0.0
        resid = []
        moved = 0
        for a, b, f in preds:
            others = b
            for k, v in f.items():
                if k != name:
                    others *= v
            x = f[name] - 1
            if abs(x) > 1e-9:
                moved += 1
            y = a / others - 1 if others > 0 else 0
            num += others * x * y
            den += others * x * x
            resid.append((others, x, y))
        slope = num / den if den else 0.0
        var = sum(w * (y - slope * x) ** 2 for w, x, y in resid) / max(len(resid) - 1, 1)
        se = (var / den) ** 0.5 if den else 0.0
        full = [(a, b * _prod(f)) for a, b, f in preds]
        without = [(a, b * _prod({k: v for k, v in f.items() if k != name})) for a, b, f in preds]
        out[name] = (slope, se, moved, _mae(full), _mae(without))
    return out


def _prod(f):
    r = 1.0
    for v in f.values():
        r *= v
    return r


def current_settings():
    return {"rate_prior": fantasy.PRIOR_GAMES, "min_prior": fantasy.MINUTES_PRIOR, "w7": fantasy.RECENT_7_WEIGHT,
            "w15": fantasy.RECENT_15_WEIGHT}


def report(league):
    rows = samples(league)
    if len(rows) < 200:
        return f"Only {len(rows)} player-games to test so far; try again after a couple of weeks of games."
    lines = [f"Backtest: {len(rows)} player-games from {rows[0][2]} to {rows[-1][2]} "
             f"(players with {MIN_PRIOR}+ earlier games and an ESPN projection).", ""]
    season = _mae([(a, sum(x[2] for x in h) / len(h)) for a, h, d, p, f in rows])
    espn = _mae([(a, p[0]) for a, h, d, p, f in rows])
    now = current_settings()
    app = _mae([(a, base(h, d, p, **now)) for a, h, d, p, f in rows])
    grid = form_grid(rows)
    lines.append("Average miss per game, fantasy points (lower is better):")
    lines.append(f"  season average so far       {season:5.2f}")
    lines.append(f"  ESPN projection             {espn:5.2f}")
    lines.append(f"  the app (current settings)  {app:5.2f}   {now}")
    lines.append(f"  best settings tested        {grid[0][0]:5.2f}   {grid[0][1]}")
    lines.append("")
    lines.append("Game factors (slope 1.0 = right size, 0 = no signal; +- = one standard error):")
    for name, (slope, se, moved, with_, without) in calibrate(rows, grid[0][1]).items():
        lines.append(f"  {name:13} slope {slope:5.2f} +- {se:4.2f}   moved {moved:5} games   "
                     f"miss {with_:.3f} with vs {without:.3f} without")
    rest = rest_rates(league)
    (b2b, nb), (other, no) = rest[True], rest[False]
    lines.append(f"  (back-to-back above only covers games he played. Rest: rotation players sat {b2b:.0%} of "
                 f"second nights ({nb} chances) vs {other:.0%} of other games ({no}); "
                 f"the app uses {matchups.B2B} for both effects together.)")
    lines.append("")
    lines.append("Note: opponent and blowout use today's team ratings (a little hindsight); everything "
                 "else only uses games before each one.")
    return "\n".join(lines)
