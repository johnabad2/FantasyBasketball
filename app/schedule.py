"""Schedule planner: how many games every NBA team plays per week, and how many NBA games there are
each day. More games = more fantasy points; light days are when streamers fill empty slots.

Weeks run Monday to Sunday (the current week counts from today). Read from the fantasy league's own
NBA schedule (scoring periods = days)."""
from datetime import date, timedelta

WEEKS = 6
DAYS = 14


def plan(league):
    """{days: [(date, games)], weeks: [(monday, sunday)], teams: {team id: [[(date, opponent id, home)]
    per week]}} from today."""
    sched = getattr(league, "pro_schedule", None) or {}
    today_day = getattr(league, "current_week", 1) or 1
    final = getattr(league, "finalScoringPeriod", None) or 174
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    weeks = [(max(monday + timedelta(weeks=w), today), monday + timedelta(weeks=w, days=6)) for w in range(WEEKS)]
    day_of = lambda d: today_day + (d - today).days
    teams = {}
    for tid, periods in sched.items():
        if not tid:
            continue  # id 0 = free agents (no NBA team)
        rows = []
        for start, end in weeks:
            games = []
            d = start
            while d <= end:
                if day_of(d) <= final:
                    for g in periods.get(str(day_of(d)), []):
                        home = g["homeProTeamId"] == tid
                        games.append((d, g["awayProTeamId"] if home else g["homeProTeamId"], home))
                d += timedelta(days=1)
            rows.append(games)
        teams[tid] = rows
    per_day = []
    for i in range(DAYS):
        d = today + timedelta(days=i)
        n = sum(1 for periods in sched.values() for _ in periods.get(str(day_of(d)), []))
        per_day.append((d, n // 2))  # each game is listed under both teams
    return {"days": per_day, "weeks": weeks, "teams": teams}
