"""Loading everything the app shows from ESPN in one go, plus small display helpers. Shared by the
desktop window (main.py) and the web app (webapp.py), so both always show the same numbers."""
import chat
import fantasy
import feed
import news
import opportunity
import planner
import projections
import stats
import trade

HEADSHOTS = stats.DATA / "headshots"


def load(demo=False):
    """Everything both apps need, as one dict (ESPN objects, not JSON; see views.py for that)."""
    if demo:
        from demo import demo_league
        league = demo_league()
        cfg = {"my_team_id": 1, "thresholds": dict(fantasy.DEFAULT_THRESHOLDS)}
    else:
        cfg = fantasy.load_config()
        league = fantasy.connect(cfg)
    rules = stats.scoring_rules(league)
    me = fantasy.my_team(league, cfg)
    if not demo:
        stats.snapshot(league, rules)
    try:
        activity = league.recent_activity(size=50)
    except Exception:
        activity = []
    real = bool(getattr(league, "pro_schedule", None))  # the demo league has no ESPN feed or schedule
    chat_msgs = chat.league_chat(league) if real else []
    trades = feed.trades(league) if real else []
    if real:
        fantasy.waivers(league)  # before any planning: waiver players only count once they clear
    # Accepted trades count as done for everything forward-looking; today's lineup uses the real roster.
    after = feed.after_trades(league) if real else {t.team_id: t for t in league.teams}
    planned = after[me.team_id]
    pending = [t for t in feed.pending(league) if any(s["team_id"] == me.team_id for s in t["sides"])] if real else []
    analysis = fantasy.analyze(league, planned, fa_count=100, teams=list(after.values()))
    mine, others, fas = analysis
    scores = stats.season_scores(league)
    live, weekly = stats.live_points(league, scores), stats.weekly_standings(league, scores)
    projected = projections.projected_final(league, live, list(after.values()))
    day = getattr(league, "current_week", 1) or 1
    today_pts = {tid: days[day] for tid, (_, days) in scores.items() if day in days}  # live; none in the demo
    injuries = news.injuries() if real else {}
    values = {p.playerId: fantasy.form_of(p, league.year).baseline for p in list(me.roster) + list(planned.roster)}
    week = planner.week(league, me.roster, values, injuries) if real else []
    if pending:  # from the day after a trade goes through, plan with the new roster
        switch = max(t["process_at"] for t in pending).date()
        later = planner.week(league, planned.roster, values, injuries)
        week = [b if a["date"] > switch else a for a, b in zip(week, later)]
    plan = dict(week=week, moves=planner.today_moves(league, me, injuries),
                streamers=planner.streamers(league, planned, fas, injuries))
    return dict(league=league, cfg=cfg, rules=rules, me=me, planned=planned, pending=pending, demo=demo,
                today_pts=today_pts, projected=projected, live=live, weekly=weekly,
                injuries=injuries, plan=plan,
                opportunities=opportunity.opportunities(league, injuries) if real else [],
                partners=trade.partners(league, planned, fantasy.load_keep()),
                alerts=fantasy.build_alerts(league, planned, cfg["thresholds"], analysis, injuries),
                mine=mine, fas=fas, trades=fantasy.trade_ideas(mine, others, cfg["thresholds"], per_team=3,
                                                          leaving=feed.leaving(league, me.team_id) if real else ()),
                activity=activity, chat=chat_msgs, trade_feed=trades)


def points(d, team):
    """Season points including games in progress."""
    return (d.get("live") or {}).get(team.team_id, team.points_for)


# ---------------------------------------------------------------- display helpers

def headshot(player_id):
    """Path to the player's ESPN headshot, downloaded once and cached. None if ESPN has no photo."""
    import urllib.error
    import urllib.request
    path = HEADSHOTS / f"{int(player_id)}.png"
    missing = path.with_suffix(".none")
    if path.exists():
        return path
    if missing.exists():
        return None
    HEADSHOTS.mkdir(parents=True, exist_ok=True)
    url = f"https://a.espncdn.com/i/headshots/nba/players/full/{int(player_id)}.png"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            path.write_bytes(r.read())
        return path
    except urllib.error.HTTPError as e:
        if e.code == 404:
            missing.touch()  # remember, so we don't ask ESPN again every time
        return None
    except OSError:  # offline / timeout: no photo this time, try again next time
        return None


def status_text(p):
    return (p.injuryStatus or "ACTIVE").replace("_", " ").lower()


def short_name(name):
    """Last name, skipping suffixes: 'Scotty Pippen Jr.' -> 'Pippen', 'Jaren Jackson Jr.' -> 'Jackson'."""
    parts = [w for w in name.split() if w.rstrip(".").upper() not in ("JR", "SR", "II", "III", "IV", "V")]
    return parts[-1] if parts else name


def minutes_text(p, year):
    """'32' or '32 (+5)' when his last-7-day minutes differ from usual by 2 or more."""
    usual = opportunity.minutes(p, year)
    if not usual:
        return "-"
    trend = opportunity.minutes_trend(p, year)
    return f"{usual:.0f}" + (f" ({trend:+.0f})" if trend is not None and abs(trend) >= 2 else "")


def status_tag(p):
    s = p.injuryStatus or "ACTIVE"
    return "out" if s in fantasy.OUT_STATUSES else "shaky" if s in fantasy.SHAKY_STATUSES else ""
