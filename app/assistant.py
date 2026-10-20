"""The Ask tab's brain: Claude answers fantasy questions about your league.

It can't see the app's screens, so it gets read-only tools built on the same code the tabs use
(player stats, your roster, the Trade Lab simulation, add/drop math, the week plan...). It can
never change anything on ESPN. Needs a Claude API key: "anthropic_api_key" in config/config.json
(or ANTHROPIC_API_KEY / an `ant auth login` profile)."""
import json
from datetime import date

import anthropic
from anthropic import beta_tool

import fantasy
import lineup
import planner
import projections
import stats
import trade
from stats import SPLITS, SPLIT_LABELS, box, fp_per_game, games, owner_names, split_of

MODEL = "claude-opus-5-5"
# $ per million tokens for Claude Opus 5.5, to show roughly what each answer cost
PRICES = {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}
SHOWN_STATS = ["PTS", "REB", "AST", "STL", "BLK", "TO", "FG", "3P", "FT", "MPG"]

SYSTEM = """You are the fantasy basketball advisor inside John's desktop app for his ESPN league \
with friends. Answer his questions about players, trades, pickups, drops and lineups.

How his league works:
- Season-long total points: no weekly matchups. The team with the most fantasy points at the end \
of the season wins, so every game a player plays in a lineup slot counts the same.
- Scoring: PTS +1, REB +1, AST +2, STL +4, BLK +4, 3PM +1, FGM +2, FGA -1, FTM +1, FTA -1, TO -2.
- Daily lineups that lock per game: 10 active slots (PG, SG, SF, PF, C, G, F, UT, UT, UT), 3 bench, 1 IR. \
No acquisition limit, so streaming free agents is free. Trades go through a 24-hour review.
- IR: players listed INJ always qualify; players listed O only once ESPN marks them IR-eligible.

How to work:
- Always look things up with your tools before answering; never rely on memory for stats, rosters, \
injuries or schedules. Your knowledge of the 2026-27 NBA season may be out of date.
- "base" is a player's fantasy points per game: ESPN's projection before he has played, shifting to his \
real season average as games are played. "L15" is the last 15 days. Early in the season nearly every \
number is a projection - say so when it matters.
- For "is X worth it over Y" questions, use evaluate_add_drop or evaluate_trade and weigh both this week \
(empty lineup days) and the rest of the season.
- Give a clear recommendation first, then the 2-4 numbers that justify it. Be concise and friendly; \
he's a casual fan, so avoid jargon.
- Write plain text. Short "-" bullet lists and **bold** are fine; no headings or tables.
- You can only look things up. If he asks you to make a move, tell him what to do on ESPN."""


def _r(x, n=1):
    return round(float(x), n)


class Advisor:
    def __init__(self, api_key, data):
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.data = data  # the app's latest loaded league data; replaced on every refresh
        self.messages = []
        self.tools = self._make_tools()

    def reset(self):
        self.messages = []

    # ------------------------------------------------------------ helpers over the loaded data

    @property
    def league(self):
        return self.data["league"]

    def _find(self, name):
        """(player, fantasy team or None) or raises ValueError with a helpful message."""
        try:
            return stats.find_player(self.league, name)
        except SystemExit as e:
            raise ValueError(str(e))

    def _injury(self, p):
        info = (self.data.get("injuries") or {}).get(p.playerId)
        if not info:
            return None
        return {"status": info["status"], "injury": info["injury"],
                "expected_return": info["return_date"].isoformat() if info["return_date"] else None,
                "latest_update": info["comment"]}

    def _player(self, p, team, detail=True):
        year, rules = self.league.year, self.data["rules"]
        f = fantasy.form_of(p, year)
        today = getattr(self.league, "current_week", 1) or 1
        left = planner.games_after(self.league, p, today, self.data.get("injuries") or {})
        out = {"name": p.name, "nba_team": p.proTeam, "position": p.position,
               "status": (p.injuryStatus or "ACTIVE").replace("_", " ").lower(),
               "fantasy_team": team.team_name if team else "free agent",
               "my_player": bool(team and team.team_id == self.data["me"].team_id),
               "base_fp_per_game": _r(f.baseline), "last15_fp_per_game": _r(f.recent),
               "games_played": f.games, "games_left_this_season": left,
               "projected_rest_of_season_fp": _r(f.baseline * left, 0),
               "kept": p.playerId in fantasy.load_keep()}
        inj = self._injury(p)
        if inj:
            out["injury"] = inj
        if detail:
            # only windows with games played, plus ESPN's projection (keeps answers cheap early on)
            out["splits"] = {SPLIT_LABELS[k]: {"games": games(split_of(p, year, k)),
                                               "fp_per_game": _r(fp_per_game(split_of(p, year, k), rules)),
                                               **{s: box(split_of(p, year, k), s) for s in SHOWN_STATS}}
                             for k in SPLITS if split_of(p, year, k)
                             and (k == "projected" or games(split_of(p, year, k)) > 0)}
            out["shooting_format"] = "made-attempted per game, then percentage"
            out["game_today"] = lineup.game_today(self.league, p) or "no game"
        return out

    def _team(self, team, detail=False):
        proj = (self.data.get("projected") or {}).get(team.team_id)
        players = sorted((self._player(p, team, detail) | {"lineup_slot": p.lineupSlot} for p in team.roster),
                         key=lambda x: -x["base_fp_per_game"])
        return {"team": team.team_name, "owner": owner_names(team), "points_so_far": _r(team.points_for),
                "projected_final": _r(proj[2], 0) if proj else None, "players": players}

    # ------------------------------------------------------------ tools

    def _make_tools(self):
        advisor = self

        def safe(fn):
            def run(*args, **kwargs):
                try:
                    return json.dumps(fn(*args, **kwargs), default=str)
                except ValueError as e:
                    return json.dumps({"error": str(e)})
            return run

        @beta_tool
        def get_player(name: str) -> str:
            """Full stats for any NBA player: fantasy points per game (season, last 7/15/30 days, ESPN
            projection), box-score averages, shooting, injury report, who owns him in the league, and
            games left this season. Accepts nicknames like "kd" or "the joker".

            Args:
                name: Player name or nickname.
            """
            return safe(lambda: advisor._player(*advisor._find(name)))()

        @beta_tool
        def get_my_team() -> str:
            """John's own fantasy team: every player with lineup slot, base and last-15 fantasy points per
            game, games left, injuries, and the team's points so far and projected final total."""
            return safe(lambda: advisor._team(advisor.data["me"]))()

        @beta_tool
        def get_fantasy_team(team_name: str) -> str:
            """Another fantasy team in the league (one of John's friends): roster with per-game values.

            Args:
                team_name: Team name or part of it, e.g. "Rovic".
            """
            def run():
                matches = [t for t in advisor.league.teams if team_name.lower() in t.team_name.lower()]
                if len(matches) != 1:
                    raise ValueError(f"Teams: {', '.join(t.team_name for t in advisor.league.teams)}")
                return advisor._team(matches[0])
            return safe(run)()

        @beta_tool
        def evaluate_add_drop(add_player: str, drop_player: str = "") -> str:
            """What adding one player (usually a free agent) and dropping one of John's players would do:
            extra points over the next 7 days (filling empty lineup days) and over the rest of the season.

            Args:
                add_player: Player to add.
                drop_player: John's player to drop. Leave empty if he has an open roster spot.
            """
            def run():
                add, add_team = advisor._find(add_player)
                drop = None
                if drop_player:
                    drop, drop_team = advisor._find(drop_player)
                    if not drop_team or drop_team.team_id != advisor.data["me"].team_id:
                        raise ValueError(f"{drop.name} isn't on John's team.")
                note = (f"{add.name} is on {add_team.team_name}, so this would need a trade, not a pickup."
                        if add_team else "free agent: can be added straight away")
                v = planner.swap_value(advisor.league, advisor.data["me"], add, drop,
                                       advisor.data.get("injuries") or {})
                return {"add": add.name, "drop": drop.name if drop else None, "note": note,
                        "points_next_7_days": _r(v["this_week"]), "points_rest_of_season_after_that": _r(v["rest_of_season"], 0),
                        "add_base_fp_per_game": _r(v["add_base"]), "drop_base_fp_per_game": _r(v["drop_base"]),
                        "add_games_left_after_this_week": v["add_games_after"],
                        "drop_games_left_after_this_week": v["drop_games_after"]}
            return safe(run)()

        @beta_tool
        def evaluate_trade(you_send: list[str], you_receive: list[str]) -> str:
            """Simulate a trade between John's team and ONE other fantasy team (the Trade Lab math): each
            team's lineup strength (fantasy pts/game of its best 10 starters) before and after, league rank,
            and roughly how many season points it's worth to each side.

            Args:
                you_send: John's players to send.
                you_receive: Players to get back; all must be on the same other fantasy team.
            """
            def run():
                me = advisor.data["me"]
                send, get, partner = [], [], None
                for n in you_send:
                    p, t = advisor._find(n)
                    if not t or t.team_id != me.team_id:
                        raise ValueError(f"{p.name} isn't on John's team.")
                    send.append(p)
                for n in you_receive:
                    p, t = advisor._find(n)
                    if not t or t.team_id == me.team_id:
                        raise ValueError(f"{p.name} isn't on another fantasy team (use evaluate_add_drop for free agents).")
                    if partner and t.team_id != partner.team_id:
                        raise ValueError("All received players must come from one team.")
                    partner = t
                    get.append(p)
                if not partner:
                    raise ValueError("Name at least one player to receive.")
                send_ids, get_ids = {p.playerId for p in send}, {p.playerId for p in get}
                r = trade.simulate(advisor.league, me, partner, send_ids, get_ids)
                # day-by-day rest-of-season totals: counts bench players on days they'd fill an empty slot
                season = lambda roster, team: projections.rest_of_season(advisor.league, roster, trade.lineup_slots(team))
                mine_after = [p for p in me.roster if p.playerId not in send_ids] + get
                theirs_after = [p for p in partner.roster if p.playerId not in get_ids] + send
                season_change = {"john": season(mine_after, me) - season(me.roster, me),
                                 "partner": season(theirs_after, partner) - season(partner.roster, partner)}
                side = lambda s, who: {"team": s["team"].team_name, "starters_strength_before": _r(s["before"]),
                                  "starters_strength_after": _r(s["after"]), "starters_change_per_game": _r(s["delta"]),
                                  "season_points_change_day_by_day": _r(season_change[who], 0),
                                  "rank_before": s["rank_before"], "rank_after": s["rank_after"],
                                  "roster_size_after": s["size_after"]}
                return {"john": side(r["a"], "john"), "partner": side(r["b"], "partner"),
                        "how_to_read": "starters_* = fantasy pts/game of each team's best 10 players if all "
                                       "played every day; season_points_change_day_by_day = the real NBA schedule "
                                       "simulated to season end, including bench players filling empty days "
                                       "(the better number for bench-level players)",
                        "verdict": trade.verdict(r)}
            return safe(run)()

        @beta_tool
        def get_free_agents(position: str = "", limit: int = 15) -> str:
            """Best available free agents by value (average of base and last-15 fantasy points per game).

            Args:
                position: Optional filter: PG, SG, SF, PF or C.
                limit: How many to return (max 40).
            """
            def run():
                fas = [f for f in advisor.data.get("fas") or []
                       if not position or f.player.position == position.upper()]
                fas.sort(key=lambda f: -f.worth)
                return [advisor._player(f.player, None, detail=False) for f in fas[:max(1, min(limit, 40))]]
            return safe(run)()

        @beta_tool
        def get_standings() -> str:
            """League standings: points so far and projected final total for every team."""
            def run():
                proj = advisor.data.get("projected") or {}
                teams = sorted(advisor.league.teams, key=lambda t: -(proj.get(t.team_id, (0, 0, t.points_for))[2]))
                return [{"team": t.team_name, "owner": owner_names(t), "points": _r(t.points_for),
                         "projected_final": _r(proj[t.team_id][2], 0) if t.team_id in proj else None,
                         "is_john": t.team_id == advisor.data["me"].team_id} for t in teams]
            return safe(run)()

        @beta_tool
        def get_week_plan() -> str:
            """John's next 7 days: for each day, how many of his 10 lineup slots he can fill, projected
            points, who starts, and who plays but doesn't fit. Includes today's best lineup changes."""
            def run():
                plan = advisor.data.get("plan") or {}
                days = [{"date": d["date"].isoformat(), "slots_filled": f"{d['filled']}/{d['slots']}",
                         "projected_points": _r(d["points"], 0),
                         "starting": [c[1].name for c in d["lineup"].values()],
                         "playing_but_no_slot": [c[1].name for c in d["cands"]
                                                 if c[1].playerId not in {x[1].playerId for x in d["lineup"].values()}]}
                        for d in plan.get("week") or []]
                moves = plan.get("moves")
                today = None
                if moves:
                    today = {"current_lineup_points": _r(moves["current"]), "best_possible": _r(moves["best"]),
                             "start": [f"{p.name} at {slot}" for p, slot in moves["start"]],
                             "bench": [p.name for p in moves["bench"]]}
                return {"days": days, "today": today}
            return safe(run)()

        return [get_player, get_my_team, get_fantasy_team, evaluate_add_drop, evaluate_trade,
                get_free_agents, get_standings, get_week_plan]

    # ------------------------------------------------------------ asking

    def ask(self, question, on_step=None):
        """Answer one question (keeps the conversation for follow-ups). Returns (text, cost in $).
        on_step(str) is called with progress like 'Looking up Isaiah Hartenstein'."""
        self.messages.append({"role": "user", "content": f"(Today is {date.today():%A %d %B %Y}.) {question}"})
        runner = self.client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=16000,
            max_iterations=12,
            system=SYSTEM,
            tools=self.tools,
            messages=list(self.messages),
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},
            # if a safety check declines, the API retries on a fallback model in the same call
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        cost, last = 0.0, None
        for message in runner:
            last = message
            cost += self._cost(message.usage)
            self.messages.append({"role": "assistant", "content": message.content})
            calls = [b for b in message.content if b.type == "tool_use"]
            if on_step and calls:
                on_step(", ".join(_describe(b) for b in calls))
            tool_response = runner.generate_tool_call_response()  # cached: tools still run once
            if tool_response is not None:
                self.messages.append(tool_response)
        if last is None:
            return "No answer came back.", cost
        if last.stop_reason == "refusal":
            return "Claude declined to answer that one. Try rephrasing the question.", cost
        text = "\n".join(b.text for b in last.content if b.type == "text").strip()
        if last.stop_reason == "max_tokens":
            text += "\n\n(The answer was cut off - ask for a shorter version.)"
        return text or "No answer came back.", cost

    @staticmethod
    def _cost(usage):
        if usage is None:
            return 0.0
        get = lambda name: getattr(usage, name, 0) or 0
        return (get("input_tokens") * PRICES["input"] + get("output_tokens") * PRICES["output"]
                + get("cache_creation_input_tokens") * PRICES["cache_write"]
                + get("cache_read_input_tokens") * PRICES["cache_read"]) / 1_000_000


def _describe(block):
    """'Looking up X' style progress text for one tool call."""
    i = block.input if isinstance(block.input, dict) else {}
    return {
        "get_player": lambda: f"Looking up {i.get('name', 'a player')}",
        "get_my_team": lambda: "Checking your team",
        "get_fantasy_team": lambda: f"Checking {i.get('team_name', 'a team')}",
        "evaluate_add_drop": lambda: f"Working out adding {i.get('add_player')}"
                                     + (f" for {i.get('drop_player')}" if i.get("drop_player") else ""),
        "evaluate_trade": lambda: "Simulating the trade",
        "get_free_agents": lambda: "Scanning free agents",
        "get_standings": lambda: "Checking the standings",
        "get_week_plan": lambda: "Checking your week",
    }.get(block.name, lambda: block.name)()
