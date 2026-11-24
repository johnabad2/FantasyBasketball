"""Teammate out: how a player's fantasy output changes when a regular teammate doesn't play.

When a starter sits, his shots, touches and minutes go to the others, and some players gain far more
than others (a backup at the same position, or the next ball-handler). This season's box scores show
it directly: for each player, his average fantasy points in games the teammate played vs games he
missed. Few games without the teammate are noisy, so the ratio is shrunk toward no change by
games without / (games without + TRUST_GAMES) and capped.

Before there are any games without him (a fresh injury), falls back to sharing out his minutes
(opportunity.py's model): up to 3 healthy teammates at his positions split them, each gaining his
own fantasy points per minute for the extra minutes, damped.

Only used for the next day or so, when ESPN's injury status says who's actually out."""
from collections import defaultdict

import boxscores
from stats import scoring_rules

REGULAR_GAMES, REGULAR_MINUTES = 5, 20  # a teammate whose absence matters: 5+ games at 20+ minutes
TRUST_GAMES = 5    # games without him before the measured change counts fully
MIN_WITHOUT = 2    # fewer games without him than this: use the minutes-sharing estimate instead
LIMITS = (0.85, 1.6)


def _games(league):
    """{NBA team id: [(date, {player id: (minutes, fantasy points)})]}, cached on the league object."""
    if getattr(league, "_fh_usage_games", None) is None:
        rules = scoring_rules(league)
        out = defaultdict(list)
        for day, gid, home, away, box in boxscores.season(league):
            for t in (home, away):
                out[t].append((day, {r[3]: (r[1], boxscores.fantasy_points(r[2], rules)) for r in box.get(str(t), [])}))
        league._fh_usage_games = out
    return league._fh_usage_games


def with_without(league, team_id, player_id, mate_id):
    """(his average with the teammate, games, average without, games), counting only team games after
    both had played for this team (so trades and call-ups don't count as absences)."""
    games = _games(league).get(team_id, [])
    first = next((i for i, (d, p) in enumerate(games) if mate_id in p), None)
    first_me = next((i for i, (d, p) in enumerate(games) if player_id in p), None)
    if first is None or first_me is None:
        return None
    w, wo = [], []
    for day, played in games[max(first, first_me):]:
        if player_id not in played:
            continue
        (w if mate_id in played else wo).append(played[player_id][1])
    return (sum(w) / len(w) if w else 0.0, len(w), sum(wo) / len(wo) if wo else 0.0, len(wo))


def _regular(league, team_id, mate_id):
    ms = [p[mate_id][0] for d, p in _games(league).get(team_id, []) if mate_id in p]
    return len(ms) >= REGULAR_GAMES and sum(ms) / len(ms) >= REGULAR_MINUTES, (sum(ms) / len(ms) if ms else 0.0)


def out_teammates(league, player):
    """Regular teammates listed out right now: [(player, his minutes)]."""
    import opportunity
    from fantasy import OUT_STATUSES
    from projections import nba_team_id
    tid = nba_team_id(player)
    out = []
    for p, _ in opportunity._pool(league):
        if p.playerId == player.playerId or nba_team_id(p) != tid or (p.injuryStatus or "ACTIVE") not in OUT_STATUSES:
            continue
        regular, mpg = _regular(league, tid, p.playerId)
        if regular:
            out.append((p, mpg))
    return out


def _shared(league, player, mate, mate_mpg):
    """Minutes-sharing estimate of his boost when `mate` is out (no games without him yet)."""
    import opportunity
    from fantasy import OUT_STATUSES, form_of
    from projections import nba_team_id
    year = league.year
    spots = set(mate.eligibleSlots or []) & opportunity.POSITIONS
    tid = nba_team_id(player)
    mates = [p for p, _ in opportunity._pool(league) if nba_team_id(p) == tid and p.playerId != mate.playerId
             and (p.injuryStatus or "ACTIVE") not in OUT_STATUSES and spots & set(p.eligibleSlots or [])]
    mates.sort(key=lambda p: -opportunity.minutes(p, year))
    sharers = mates[:opportunity.SHARERS]
    if player not in sharers and all(p.playerId != player.playerId for p in sharers):
        return 1.0
    mpg = opportunity.minutes(player, year)
    if mpg < 8:
        return 1.0
    extra = min(mate_mpg / len(sharers), opportunity.MAX_MPG - mpg)
    worth = form_of(player, year).worth
    return 1 + opportunity.DAMPING * extra / mpg if worth and extra > 0 else 1.0


def boost(league, player):
    """(multiplier, [reason]) for his next game, from regular teammates who are out. 1.0 if none."""
    from projections import nba_team_id
    if not getattr(league, "pro_schedule", None):
        return 1.0, []
    tid = nba_team_id(player)
    total, why = 1.0, []
    for mate, mpg in out_teammates(league, player):
        ww = with_without(league, tid, player.playerId, mate.playerId)
        if ww and ww[3] >= MIN_WITHOUT and ww[1] >= 2 and ww[0] > 0:
            w = ww[3] / (ww[3] + TRUST_GAMES)
            f = 1 + w * (ww[2] / ww[0] - 1)
            note = f"{mate.name} out: {ww[2]:.1f} without him ({ww[3]} g) vs {ww[0]:.1f} with"
        else:
            f = _shared(league, player, mate, mpg)
            note = f"{mate.name} out: his {mpg:.0f} minutes to share"
        if abs(f - 1) >= 0.02:
            total *= f
            why.append(note + f" ({f - 1:+.0%})")
    return min(max(total, LIMITS[0]), LIMITS[1]), why


def for_game(league, player, game):
    """boost() when `game` is today or tomorrow (injury statuses are only good that far ahead), else 1.0."""
    if not game:
        return 1.0
    from datetime import datetime as dt

    from matchups import EASTERN
    day = dt.fromtimestamp(game["date"] / 1000, EASTERN).date()
    if (day - dt.now(EASTERN).date()).days > 1:
        return 1.0
    cache = getattr(league, "_fh_usage_boost", None)
    if cache is None:
        cache = league._fh_usage_boost = {}
    if player.playerId not in cache:
        cache[player.playerId] = boost(league, player)
    return cache[player.playerId][0]
