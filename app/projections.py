"""Projections: today's expected points, projected final standings, and projected vs actual.

All of them use a player's 'base' value (ESPN's projection early on, real season average once
he has played enough), so they sharpen automatically as the season goes."""
import time

from fantasy import form_of
import lineup


def nba_team_id(player):
    tid = getattr(player, "proTeamId", None)
    if tid is not None:
        return tid
    from espn_api.basketball.constant import PRO_TEAM_MAP
    return next((k for k, v in PRO_TEAM_MAP.items() if v == player.proTeam), None)


def today_points(league, forms, games=None):
    """{player id: projected fantasy points today}: his base value if he has a game, else 0."""
    games = lineup.todays_games(league) if games is None else games
    return {f.player.playerId: (f.baseline if nba_team_id(f.player) in games else 0.0) for f in forms}


def vs_projection(player, year, rules, min_games=3):
    """Season fantasy pts/game minus ESPN's projected pts/game, or None until he's played enough."""
    from stats import fp_per_game, games, split_of
    season = split_of(player, year, "total")
    if games(season) < min_games:
        return None
    return fp_per_game(season, rules) - fp_per_game(split_of(player, year, "projected"), rules)


def projected_final(league):
    """{fantasy team id: (points so far, projected rest of season, projected final)} or None
    when the league has no NBA schedule (demo). Simulates each remaining day: a team scores
    with its best `slots` players whose NBA team plays that day (injured-reserve players sit)."""
    schedule = getattr(league, "pro_schedule", None)
    if not schedule:
        return None
    from trade import lineup_slots
    out = {}
    for team in league.teams:
        rest = rest_of_season(league, team.roster, lineup_slots(team))
        out[team.team_id] = (team.points_for, rest, team.points_for + rest)
    return out


def rest_of_season(league, players, slots):
    """Fantasy points `players` would score from now to the end of the season: each remaining day,
    the best `slots` of them whose NBA team plays (injured-reserve players sit). Bench players count
    on days a starter has no game - which is why depth matters in a daily-lineup league."""
    schedule = getattr(league, "pro_schedule", None) or {}
    today = getattr(league, "current_week", 1) or 1
    final = getattr(league, "finalScoringPeriod", None) or 174
    now_ms = time.time() * 1000
    roster = [(form_of(p, league.year).baseline, nba_team_id(p)) for p in players
              if (p.injuryStatus or "") != "INJURY_RESERVE"]
    total = 0.0
    for day in range(today, final + 1):
        key = str(day)
        playing = []
        for value, tid in roster:
            for game in schedule.get(tid, {}).get(key, []):
                if day > today or game["date"] > now_ms:  # today: only games not started yet
                    playing.append(value)
                    break
        total += sum(sorted(playing, reverse=True)[:slots])
    return total
