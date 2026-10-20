"""Desktop window for the fantasy tracker.

    .venv\\Scripts\\pythonw app.py          (or double-click "Fantasy Hoops.bat")
    .venv\\Scripts\\pythonw app.py --demo   made-up league, no ESPN needed
"""
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import fantasy
import gamelog
import stats
from chart import GameChart
from stats import BOX, SPLIT_LABELS, SPLITS, box, fp_per_game, games, owner_names, split_of

FONT = ("Segoe UI", 10)
ACCENT = "#1d6fd8"
ME_BG = "#e3eefc"
RED = "#c62828"
AMBER = "#b26a00"
GREEN = "#2e7d32"
PRIORITY = {1: ("DO NOW", RED), 2: ("CONSIDER", AMBER), 3: ("FYI", "#555555")}
AUTO_REFRESH_MIN = 15
PHOTO_W, PHOTO_H = 300, 218  # ESPN headshots are 600x436; shown at half size
HEADSHOTS = stats.DATA / "headshots"


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


def status_tag(p):
    s = p.injuryStatus or "ACTIVE"
    return "out" if s in fantasy.OUT_STATUSES else "shaky" if s in fantasy.SHAKY_STATUSES else ""


class Table(ttk.Frame):
    """Treeview with a scrollbar and click-to-sort headers."""

    def __init__(self, parent, columns, text_cols=(), wide=(), height=8, on_open=None):
        super().__init__(parent)
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=height)
        for c in columns:
            self.tree.heading(c, text=c, command=lambda c=c: self.sort(c))
            # short codes like pos/nba/slot don't need a full text-column width
            base = 200 if c in wide else 64 if len(c) <= 4 else 110 if c in text_cols else 58
            width = max(base, 10 * len(c) + 24)
            self.tree.column(c, width=width, minwidth=40, anchor="w" if c in text_cols else "e",
                             stretch=c in wide)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=bar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.tree.tag_configure("me", background=ME_BG)
        self.tree.tag_configure("out", foreground=RED)
        self.tree.tag_configure("shaky", foreground=AMBER)
        self.tree.tag_configure("hot", foreground=GREEN)
        self.tree.tag_configure("cold", foreground=RED)
        self._reverse = {}
        if on_open:
            self.tree.bind("<Double-1>", lambda e: self._open(on_open))

    def _open(self, callback):
        sel = self.tree.selection()
        if sel:
            callback(self.tree.item(sel[0], "values"))

    def set_rows(self, rows, tags=None):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(rows):
            self.tree.insert("", "end", values=r, tags=tags[i] if tags else ())

    def sort(self, col):
        def key(v):
            try:
                return (0, float(str(v).rstrip("%").replace("+", "").replace(" *", "")))
            except ValueError:
                return (1, str(v).lower())
        rev = self._reverse.get(col, True)  # first click: biggest numbers on top
        items = sorted(((self.tree.set(k, col), k) for k in self.tree.get_children("")),
                       key=lambda x: key(x[0]), reverse=rev)
        for i, (_, k) in enumerate(items):
            self.tree.move(k, "", i)
        self._reverse[col] = not rev


class App(tk.Tk):
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self.data = None
        self.jobs = queue.Queue()  # (callback, result) handed from worker threads to the UI thread
        self.title("Fantasy Hoops" + (" (demo)" if demo else ""))
        self.geometry("1280x760")
        self.minsize(900, 520)
        try:
            self.state("zoomed")  # open maximized on Windows so every stat column fits
        except tk.TclError:
            pass
        self._style()
        self._build()
        self.after(100, self._drain)
        self.refresh()

    # ------------------------------------------------------------ layout

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        self.option_add("*Font", FONT)
        s.configure(".", font=FONT, background="#f6f7f9")
        s.configure("Treeview", rowheight=30, font=FONT, background="white", fieldbackground="white")
        s.configure("Treeview.Heading", font=(FONT[0], 10, "bold"), background="#e9ecf1")
        s.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        s.configure("TNotebook.Tab", padding=(14, 6), font=(FONT[0], 10))

        s.configure("Sub.TLabel", foreground="#666666")

        s.configure("Toolbutton", padding=(8, 3), background="#e9ecf1")
        s.map("Toolbutton", background=[("selected", ACCENT), ("active", "#dbe4f3")],
              foreground=[("selected", "white")])
        s.configure("Accent.TButton", foreground="white", background=ACCENT)
        s.map("Accent.TButton", background=[("active", "#1559b0"), ("disabled", "#9bb8e0")])
        self.configure(background="#f6f7f9")

    def _build(self):
        top = ttk.Frame(self, padding=(14, 10))
        top.pack(fill="x")
        self.league_name = tk.StringVar(value="Fantasy Hoops")
        ttk.Label(top, textvariable=self.league_name, font=(FONT[0], 16, "bold")).pack(side="left")
        self.refresh_btn = ttk.Button(top, text="Refresh", style="Accent.TButton", command=self.refresh)
        self.refresh_btn.pack(side="right")
        self.status = tk.StringVar()
        ttk.Label(top, textvariable=self.status, style="Sub.TLabel").pack(side="right", padx=12)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._standings_tab()
        self._my_team_tab()
        self._free_agents_tab()
        self._trades_tab()
        self.league_tab = self._league_tab()
        self.player_tab = self._player_tab()
        self._activity_tab()

    def _tab(self, title):
        f = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(f, text=title)
        return f

    def _standings_tab(self):
        f = self._tab("Standings")
        self.standings = Table(f, ("#", "team", "owner", "points", "behind", "change"),
                               text_cols=("team", "owner"), wide=("team", "owner"), height=10,
                               on_open=lambda v: self.show_team(v[1]))
        self.standings.pack(fill="both", expand=True)
        ttk.Label(f, text="Double-click a team to see its roster. 'change' is points since the "
                          "last saved snapshot.", style="Sub.TLabel").pack(anchor="w", pady=(6, 0))

    def _my_team_tab(self):
        f = self._tab("My Team")
        self.my_title = tk.StringVar()
        ttk.Label(f, textvariable=self.my_title, font=(FONT[0], 12, "bold")).pack(anchor="w")
        ttk.Label(f, text="Alerts", style="Sub.TLabel").pack(anchor="w", pady=(8, 2))
        self.alerts = tk.Text(f, height=6, wrap="word", relief="flat", padx=10, pady=8,
                              background="white", font=FONT, cursor="arrow")
        self.alerts.pack(fill="x")
        for prio, (_, color) in PRIORITY.items():
            self.alerts.tag_configure(f"p{prio}", foreground=color, font=(FONT[0], 10, "bold"))
        self.alerts.tag_configure("detail", foreground="#444444", lmargin1=18, lmargin2=18,
                                  spacing3=6)
        ttk.Label(f, text="Roster  (base = season avg, blended with projection early on;"
                          " L15 = last 15 days)", style="Sub.TLabel").pack(anchor="w", pady=(10, 2))
        self.my_roster = Table(f, ("player", "pos", "slot", "nba", "status", "gp", "base", "L15", "trend",
                                   "fp tot"),
                               text_cols=("player", "status", "slot", "nba", "pos"), wide=("player",),
                               on_open=lambda v: self.show_player(v[0]))
        self.my_roster.pack(fill="both", expand=True)

    def _league_tab(self):
        f = self._tab("League")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Team").pack(side="left")
        self.team_pick = ttk.Combobox(bar, state="readonly", width=34)
        self.team_pick.pack(side="left", padx=(6, 18))
        self.team_pick.bind("<<ComboboxSelected>>", lambda e: self._fill_league())
        ttk.Label(bar, text="Stats").pack(side="left")
        self.split_pick = ttk.Combobox(bar, state="readonly", width=32,
                                       values=["season (projected before games)"]
                                       + [SPLIT_LABELS[k] for k in SPLITS])
        self.split_pick.current(0)
        self.split_pick.pack(side="left", padx=6)
        self.split_pick.bind("<<ComboboxSelected>>", lambda e: self._fill_league())
        self.team_info = tk.StringVar()
        ttk.Label(f, textvariable=self.team_info, font=(FONT[0], 12, "bold")).pack(anchor="w", pady=(0, 6))
        self.league_roster = Table(f, ("player", "pos", "nba", "slot", "status", "gp", "fp tot", "fp/g", *BOX),
                                   text_cols=("player", "pos", "nba", "slot", "status"), wide=("player",),
                                   on_open=lambda v: self.show_player(v[0]))
        self.league_roster.pack(fill="both", expand=True)
        ttk.Label(f, text="* no games yet: numbers are ESPN's projection. Double-click a player for details.",
                  style="Sub.TLabel").pack(anchor="w", pady=(6, 0))
        return f

    def _free_agents_tab(self):
        f = self._tab("Free Agents")
        self.fa_head = tk.StringVar()
        ttk.Label(f, textvariable=self.fa_head, font=(FONT[0], 12, "bold")).pack(anchor="w")
        ttk.Label(f, text="Green = better than one of your players right now (worth = average of base and L15)."
                          " Double-click for details.", style="Sub.TLabel").pack(anchor="w", pady=(2, 8))
        self.free_agents = Table(f, ("player", "pos", "nba", "status", "gp", "base", "L15", "trend",
                                     "worth", "drop for them", "gain"),
                                 text_cols=("player", "pos", "nba", "status", "drop for them"),
                                 wide=("player", "drop for them"), on_open=lambda v: self.show_player(v[0]))
        self.free_agents.pack(fill="both", expand=True)

    def _trades_tab(self):
        f = self._tab("Trades")
        ttk.Label(f, text="Trade ideas", font=(FONT[0], 12, "bold")).pack(anchor="w")
        ttk.Label(f, text="Their player is better on base, but not hotter than yours lately, so the offer"
                          " looks fair to them. Up to 3 per team. Double-click to open the player you'd get.",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 8))
        self.trades = Table(f, ("team", "owner", "you send", "send base", "send L15",
                                "you get", "get base", "get L15", "gain/g"),
                            text_cols=("team", "owner", "you send", "you get"),
                            wide=("team", "you send", "you get"),
                            on_open=lambda v: self.show_player(v[5]) if v[6] else None)
        self.trades.pack(fill="both", expand=True)

    def _player_tab(self):
        f = self._tab("Player")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Any NBA player").pack(side="left")
        self.player_q = ttk.Entry(bar, width=30)
        self.player_q.pack(side="left", padx=6)
        self.player_q.bind("<Return>", lambda e: self.show_player(self.player_q.get()))
        ttk.Button(bar, text="Search", command=lambda: self.show_player(self.player_q.get())).pack(side="left")
        top = ttk.Frame(f)
        top.pack(fill="x")
        # A blank image makes the label size itself in pixels; text shows on top when there's no photo.
        self._blank = tk.PhotoImage(width=PHOTO_W, height=PHOTO_H)
        self.photo_label = tk.Label(top, image=self._blank, compound="center", background="#e9ecf1",
                                    text="", foreground="#888888", font=FONT)
        self.photo_label.pack(side="left", padx=(0, 14))
        self._photo = None        # keep a reference or Tk drops the image
        self._photo_for = None    # player id the photo belongs to, so slow downloads can't show the wrong face
        right = ttk.Frame(top)
        right.pack(side="left", fill="both", expand=True)
        self.player_head = tk.StringVar(value="Search a player, or double-click one in any table.")
        ttk.Label(right, textvariable=self.player_head, font=(FONT[0], 12, "bold")).pack(anchor="w", pady=(4, 8))
        self.player_splits = Table(right, ("split", "gp", "fp/g", *BOX), text_cols=("split",),
                                   wide=("split",), height=5)
        self.player_splits.pack(fill="both", expand=True)
        # Clicking a stat's column header charts that stat game by game.
        header_stat = {"fp/g": "FP", "MPG": "MIN", **{s: s for s in gamelog.CHART_STATS}}
        for col in ("fp/g", *BOX):
            if col in header_stat:
                self.player_splits.tree.heading(col, command=lambda s=header_stat[col]: self.chart_stat.set(s))

        bottom = ttk.Frame(f)
        bottom.pack(fill="both", expand=True, pady=(12, 0))
        side = ttk.Frame(bottom, width=430)
        side.pack(side="right", fill="y", padx=(14, 0))
        side.pack_propagate(False)
        self.breakdown_head = tk.StringVar()
        ttk.Label(side, textvariable=self.breakdown_head, style="Sub.TLabel").pack(anchor="w", pady=(0, 2))
        self.breakdown = Table(side, ("stat", "per game", "pts each", "fantasy pts"), text_cols=("stat",))
        self.breakdown.pack(fill="both", expand=True)
        self.breakdown.tree.bind("<<TreeviewSelect>>", self._breakdown_clicked)

        main = ttk.Frame(bottom)
        main.pack(side="left", fill="both", expand=True)
        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=(0, 6))
        self.chart_stat = tk.StringVar(value="FP")
        for s in gamelog.CHART_STATS:
            ttk.Radiobutton(controls, text="Fantasy pts" if s == "FP" else s, value=s, variable=self.chart_stat,
                            style="Toolbutton").pack(side="left", padx=(0, 2))
        self.chart_stat.trace_add("write", lambda *a: self._draw_chart())
        self.chart_range = ttk.Combobox(controls, state="readonly", width=12,
                                        values=["All games", "Last 30", "Last 15", "Last 10", "Last 5"])
        self.chart_range.current(0)
        self.chart_range.pack(side="right")
        self.chart_range.bind("<<ComboboxSelected>>", lambda e: self._draw_chart())
        ttk.Label(controls, text="Show", style="Sub.TLabel").pack(side="right", padx=6)
        self.chart = GameChart(main, height=300)
        self.chart.pack(fill="both", expand=True)
        self._games, self._games_label, self._games_for = [], "", None
        return f

    def _activity_tab(self):
        f = self._tab("Activity")
        self.activity = Table(f, ("when", "team", "move", "player"),
                              text_cols=("when", "team", "move", "player"), wide=("team", "player"),
                              on_open=lambda v: self.show_player(v[3]))
        self.activity.pack(fill="both", expand=True)

    # ------------------------------------------------------------ background work

    def run(self, work, done):
        """Run work() off the UI thread, then done(result) on it. Errors land in the status bar."""
        def worker():
            try:
                self.jobs.put((done, work()))
            except SystemExit as e:  # fantasy/stats report user-facing problems this way
                self.jobs.put((self._error, str(e)))
            except Exception as e:
                self.jobs.put((self._error, f"{type(e).__name__}: {e}"))
        threading.Thread(target=worker, daemon=True).start()

    def _drain(self):
        while not self.jobs.empty():
            callback, result = self.jobs.get()
            callback(result)
        self.after(100, self._drain)

    def _error(self, msg):
        self.refresh_btn.state(["!disabled"])
        if self._auto_loading:  # don't pop a dialog every 15 minutes if the wifi drops
            self._auto_loading = False
            self.status.set(f"Auto-refresh failed at {datetime.now():%H:%M}; will retry")
            self._schedule_auto()
            return
        self.status.set("")
        if self.tabs.select() == str(self.player_tab):  # player lookups explain themselves inline
            self.player_head.set(msg)
        else:
            from tkinter import messagebox
            messagebox.showerror("Fantasy Hoops", msg)

    # ------------------------------------------------------------ data

    _auto_loading = False
    _auto_job = None

    def refresh(self, auto=False):
        if "disabled" in self.refresh_btn.state():
            return  # already loading
        self._auto_loading = auto
        self.refresh_btn.state(["disabled"])
        self.status.set("Refreshing..." if auto else "Loading from ESPN...")
        self.run(self._load, self._show)

    def _schedule_auto(self):
        if self._auto_job:
            self.after_cancel(self._auto_job)
        self._auto_job = self.after(AUTO_REFRESH_MIN * 60 * 1000, lambda: self.refresh(auto=True))

    def _load(self):
        if self.demo:
            from demo import demo_league
            league = demo_league()
            cfg = {"my_team_id": 1, "thresholds": dict(fantasy.DEFAULT_THRESHOLDS)}
        else:
            cfg = fantasy.load_config()
            league = fantasy.connect(cfg)
        rules = stats.scoring_rules(league)
        me = fantasy.my_team(league, cfg)
        prev, prev_day = stats.previous_points()
        if not self.demo:
            stats.snapshot(league, rules)
        try:
            activity = league.recent_activity(size=50)
        except Exception:
            activity = []
        analysis = fantasy.analyze(league, me, fa_count=100)
        mine, others, fas = analysis
        return dict(league=league, cfg=cfg, rules=rules, me=me, prev=prev, prev_day=prev_day,
                    alerts=fantasy.build_alerts(league, me, cfg["thresholds"], analysis),
                    mine=mine, fas=fas, trades=fantasy.trade_ideas(mine, others, cfg["thresholds"], per_team=3),
                    activity=activity)

    def _show(self, d):
        self.data = d
        self._auto_loading = False
        league = d["league"]
        name = getattr(getattr(league, "settings", None), "name", "Fantasy Hoops")
        self.league_name.set(name)
        self.status.set(f"Updated {datetime.now():%H:%M}  ·  auto-refresh every {AUTO_REFRESH_MIN} min")
        self.refresh_btn.state(["!disabled"])
        self._schedule_auto()
        self._fill_standings()
        self._fill_my_team()
        self._fill_free_agents()
        self._fill_trades()
        names = [t.team_name for t in sorted(league.teams, key=lambda t: -t.points_for)]
        current = self.team_pick.get()
        self.team_pick["values"] = names
        self.team_pick.set(current if current in names else d["me"].team_name)
        self._fill_league()
        self._fill_activity()

    def _fill_standings(self):
        d = self.data
        teams = sorted(d["league"].teams, key=lambda t: -t.points_for)
        lead = teams[0].points_for if teams else 0
        rows, tags = [], []
        for i, t in enumerate(teams, 1):
            change = f"{t.points_for - d['prev'][t.team_id]:+.1f}" if t.team_id in d["prev"] else "-"
            rows.append((i, t.team_name, owner_names(t), f"{t.points_for:.1f}",
                         f"{lead - t.points_for:.1f}" if i > 1 else "-", change))
            tags.append(("me",) if t.team_id == d["me"].team_id else ())
        self.standings.set_rows(rows, tags)

    def _fill_my_team(self):
        d = self.data
        me, year = d["me"], d["league"].year
        rank = 1 + sum(t.points_for > me.points_for for t in d["league"].teams)
        self.my_title.set(f"{me.team_name}   ·   {me.points_for:.1f} pts   ·   #{rank} of {len(d['league'].teams)}")

        self.alerts.configure(state="normal")
        self.alerts.delete("1.0", "end")
        if not d["alerts"]:
            self.alerts.insert("end", "Nothing to do right now.", "detail")
        for a in d["alerts"]:
            label, _ = PRIORITY[a.priority]
            self.alerts.insert("end", f"{label}  {a.title}\n", f"p{a.priority}")
            self.alerts.insert("end", a.detail.replace("\n      ", "\n") + "\n", "detail")
        self.alerts.configure(state="disabled")

        min_games = d["cfg"]["thresholds"]["min_games"]
        forms = sorted((fantasy.form_of(p, year) for p in me.roster), key=lambda f: -f.worth)
        rows, tags = [], []
        for f in forms:
            p = f.player
            trusted = f.games >= min_games
            rows.append((p.name, p.position, p.lineupSlot, p.proTeam, status_text(p), f.games,
                         f"{f.baseline:.1f}", f"{f.recent:.1f}", f"{f.trend:+.0%}" if trusted else "-",
                         f"{split_of(p, year, 'total').get('applied_total', 0):.0f}"))
            hot = trusted and f.trend >= d["cfg"]["thresholds"]["trend_pct"]
            cold = trusted and f.trend <= -d["cfg"]["thresholds"]["trend_pct"]
            tags.append((status_tag(p) or ("hot" if hot else "cold" if cold else ""),))
        self.my_roster.set_rows(rows, tags)

    def _fill_free_agents(self):
        d = self.data
        t = d["cfg"]["thresholds"]
        droppable = sorted((f for f in d["mine"] if f.player.lineupSlot != "IR"), key=lambda f: f.worth)
        worst = droppable[0] if droppable else None
        rows, tags, upgrades = [], [], 0
        for fa in sorted(d["fas"], key=lambda f: -f.worth):
            p = fa.player
            # The weakest player of yours this free agent clearly beats (start from your very worst).
            beats = worst if worst and fa.healthy and fa.worth > worst.worth * (1 + t["waiver_margin"]) else None
            upgrades += bool(beats)
            trusted = fa.games >= t["min_games"]
            rows.append((p.name, p.position, p.proTeam, status_text(p), fa.games, f"{fa.baseline:.1f}",
                         f"{fa.recent:.1f}", f"{fa.trend:+.0%}" if trusted else "-", f"{fa.worth:.1f}",
                         beats.player.name if beats else "", f"+{fa.worth - beats.worth:.1f}" if beats else ""))
            tags.append((status_tag(p) or ("hot" if beats else ""),))
        self.free_agents.set_rows(rows, tags)
        floor = f"your weakest is {worst.player.name} at {worst.worth:.1f}" if worst else ""
        self.fa_head.set(f"Top {len(rows)} available players  ·  {upgrades} would upgrade your roster  ·  {floor}")

    def _fill_trades(self):
        d = self.data
        rows = [(team.team_name, owner_names(team), give.player.name, f"{give.baseline:.1f}", f"{give.recent:.1f}",
                 get.player.name, f"{get.baseline:.1f}", f"{get.recent:.1f}", f"+{gain:.1f}")
                for team, gain, give, get in d["trades"]]
        if not rows:
            n = d["cfg"]["thresholds"]["min_games"]
            rows = [("No trade ideas right now.", "", f"Needs players with {n}+ games played,", "", "",
                     "so expect these once the season is a week or two in.", "", "", "")]
        self.trades.set_rows(rows)

    def _fill_league(self):
        d = self.data
        if not d:
            return
        league, rules, year = d["league"], d["rules"], d["league"].year
        team = next((t for t in league.teams if t.team_name == self.team_pick.get()), d["me"])
        pick = self.split_pick.current()
        split_key = SPLITS[pick - 1] if pick > 0 else None
        rank = 1 + sum(t.points_for > team.points_for for t in league.teams)
        self.team_info.set(f"{team.team_name}  ({owner_names(team)})   ·   {team.points_for:.1f} pts   ·   #{rank}")
        rows, tags = [], []
        for p in team.roster:
            season = split_of(p, year, "total")
            if split_key:
                s, proj = split_of(p, year, split_key), False
            else:
                s, proj = stats.season_or_projection(p, year)
            rows.append((p.name + (" *" if proj else ""), p.position, p.proTeam, p.lineupSlot,
                         status_text(p), games(s if split_key else season),
                         f"{season.get('applied_total', 0):.0f}", f"{fp_per_game(s, rules):.1f}",
                         *(box(s, stat) for stat in BOX)))
            tags.append((status_tag(p),))
        order = sorted(range(len(rows)), key=lambda i: -float(rows[i][7]))
        self.league_roster.set_rows([rows[i] for i in order], [tags[i] for i in order])

    def _fill_activity(self):
        rows = []
        for act in self.data["activity"]:
            when = datetime.fromtimestamp(act.date / 1000).strftime("%d %b %H:%M")
            for team, action, player, *_ in act.actions:
                rows.append((when, getattr(team, "team_name", team) or "-", action.lower(),
                             getattr(player, "name", player) or "-"))
        self.activity.set_rows(rows or [("", "No recent activity.", "", "")])

    # ------------------------------------------------------------ navigation

    def show_team(self, team_name):
        self.team_pick.set(team_name)
        self._fill_league()
        self.tabs.select(self.league_tab)

    def show_player(self, name):
        name = str(name).replace(" *", "").strip()
        if not name or not self.data:
            return
        self.tabs.select(self.player_tab)
        self.player_q.delete(0, "end")
        self.player_q.insert(0, name)
        self.player_head.set(f"Looking up {name}...")
        league, rules = self.data["league"], self.data["rules"]
        self.run(lambda: stats.find_player(league, name), self._fill_player)

    def _fill_player(self, found):
        p, team = found
        if p is None:
            self.player_head.set("Player not found.")
            return
        league, rules, year = self.data["league"], self.data["rules"], self.data["league"].year
        owner = f"{team.team_name} ({owner_names(team)})" if team else "free agent"
        nicks = stats.nicknames_for(p.name)
        aka = f"  “{'”, “'.join(nicks[:3])}”" if nicks else ""
        self.player_head.set(f"{p.name}{aka}   ·   {p.position}, {p.proTeam}   ·   {status_text(p)}   ·   {owner}")
        self._photo_for = p.playerId
        self._games_for = p.playerId
        self._games, self._games_label = [], ""
        self._chart_name = p.name
        self.photo_label.configure(image=self._blank, text="Loading photo...")
        if self.demo:  # demo ids are made up and could match a real player's photo or games
            self.photo_label.configure(text="No photo")
            self.chart.show_message("No game logs in demo mode.")
        else:
            pid = p.playerId
            self.run(lambda: (pid, headshot(pid)), self._show_photo)
            self.chart.show_message(f"Loading {p.name}'s games...")
            self.run(lambda: (pid, *self._fetch_games(pid, year, rules)), self._show_games)
        self.player_splits.set_rows(
            [(SPLIT_LABELS[k], games(split_of(p, year, k)), f"{fp_per_game(split_of(p, year, k), rules):.1f}",
              *(box(split_of(p, year, k), stat) for stat in BOX))
             for k in SPLITS if split_of(p, year, k)])
        s, proj = stats.season_or_projection(p, year)
        avg = s.get("avg") or {}
        parts = sorted(((stat, avg.get(stat, 0), pts) for stat, pts in rules.items() if avg.get(stat)),
                       key=lambda x: -abs(x[1] * x[2]))
        self.breakdown_head.set(f"Points by stat ({'projected' if proj else 'season'}) · click to chart")
        self.breakdown.set_rows([(stat, f"{per:.1f}", f"{pts:+g}", f"{per * pts:+.1f}") for stat, per, pts in parts],
                                [("hot" if per * pts > 0 else "cold",) for _, per, pts in parts])

    @staticmethod
    def _fetch_games(pid, year, rules):
        try:
            return gamelog.latest_games(pid, year, rules)
        except OSError:  # offline / ESPN hiccup: say so in the chart, don't pop an error
            return None, "Couldn't load game logs from ESPN. Try Refresh."

    def _show_games(self, result):
        pid, games, label = result
        if pid != self._games_for:
            return  # a different player was opened meanwhile
        if games is None:
            self.chart.show_message(label)
            return
        self._games, self._games_label = games, label
        self._draw_chart()

    def _draw_chart(self):
        if not self._games:
            if self._games_label:
                self.chart.show_message(f"No games in the {self._games_label}.")
            return
        stat = self.chart_stat.get()
        pick = self.chart_range.get()
        games = self._games if pick == "All games" else self._games[-int(pick.split()[-1]):]
        scoring = (self.data or {}).get("rules", {})
        worse = "  (fewer is better for you)" if scoring.get(stat, 0) < 0 else ""
        span = "" if pick == "All games" else f", {pick.lower()}"
        self.chart.show(games, stat, f"{gamelog.STAT_NAMES[stat]} per game  ·  {self._chart_name}{worse}",
                        note=f"{self._games_label}{span}  ·  {len(games)} games  ·  average is over the games shown"
                             "  ·  hover a bar for that game's box score")

    def _breakdown_clicked(self, _event):
        sel = self.breakdown.tree.selection()
        if sel:
            stat = self.breakdown.tree.item(sel[0], "values")[0]
            if stat in gamelog.CHART_STATS:
                self.chart_stat.set(stat)

    def _show_photo(self, result):
        pid, path = result
        if pid != self._photo_for:
            return  # a different player was opened while this one downloaded
        if path is None:
            self.photo_label.configure(image=self._blank, text="No photo")
            return
        try:
            self._photo = tk.PhotoImage(file=str(path)).subsample(2)  # 600x436 -> 300x218
        except tk.TclError:
            path.unlink(missing_ok=True)  # corrupt download; fetch again next time
            self.photo_label.configure(image=self._blank, text="No photo")
            return
        self.photo_label.configure(image=self._photo, text="")


def main():
    try:  # crisp text on high-DPI Windows screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(demo="--demo" in sys.argv).mainloop()


if __name__ == "__main__":
    main()
