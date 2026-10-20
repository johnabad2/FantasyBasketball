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
import stats
from stats import BOX, SPLIT_LABELS, SPLITS, box, fp_per_game, games, owner_names, split_of

FONT = ("Segoe UI", 10)
ACCENT = "#1d6fd8"
ME_BG = "#e3eefc"
RED = "#c62828"
AMBER = "#b26a00"
GREEN = "#2e7d32"
PRIORITY = {1: ("DO NOW", RED), 2: ("CONSIDER", AMBER), 3: ("FYI", "#555555")}


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
        self._league_tab()
        self._player_tab()
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

    def _player_tab(self):
        f = self._tab("Player")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Any NBA player").pack(side="left")
        self.player_q = ttk.Entry(bar, width=30)
        self.player_q.pack(side="left", padx=6)
        self.player_q.bind("<Return>", lambda e: self.show_player(self.player_q.get()))
        ttk.Button(bar, text="Search", command=lambda: self.show_player(self.player_q.get())).pack(side="left")
        self.player_head = tk.StringVar(value="Search a player, or double-click one in any table.")
        ttk.Label(f, textvariable=self.player_head, font=(FONT[0], 12, "bold")).pack(anchor="w", pady=(4, 8))
        self.player_splits = Table(f, ("split", "gp", "fp/g", *BOX), text_cols=("split",),
                                   wide=("split",), height=5)
        self.player_splits.pack(fill="x")
        self.breakdown_head = tk.StringVar()
        ttk.Label(f, textvariable=self.breakdown_head, style="Sub.TLabel").pack(anchor="w", pady=(12, 2))
        self.breakdown = Table(f, ("stat", "per game", "pts each", "fantasy pts"),
                               text_cols=("stat",), height=11)
        self.breakdown.pack(fill="both", expand=True)

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
        self.status.set("")
        if self.tabs.index("current") == 3:  # player lookups explain themselves inline
            self.player_head.set(msg)
        else:
            from tkinter import messagebox
            messagebox.showerror("Fantasy Hoops", msg)

    # ------------------------------------------------------------ data

    def refresh(self):
        self.refresh_btn.state(["disabled"])
        self.status.set("Loading from ESPN...")
        self.run(self._load, self._show)

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
        return dict(league=league, cfg=cfg, rules=rules, me=me, prev=prev, prev_day=prev_day,
                    alerts=fantasy.build_alerts(league, me, cfg["thresholds"]), activity=activity)

    def _show(self, d):
        self.data = d
        league = d["league"]
        name = getattr(getattr(league, "settings", None), "name", "Fantasy Hoops")
        self.league_name.set(name)
        self.status.set(f"Updated {datetime.now():%H:%M}")
        self.refresh_btn.state(["!disabled"])
        self._fill_standings()
        self._fill_my_team()
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
        self.tabs.select(2)

    def show_player(self, name):
        name = str(name).replace(" *", "").strip()
        if not name or not self.data:
            return
        self.tabs.select(3)
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
        self.player_head.set(f"{p.name}   ·   {p.position}, {p.proTeam}   ·   {status_text(p)}   ·   {owner}")
        self.player_splits.set_rows(
            [(SPLIT_LABELS[k], games(split_of(p, year, k)), f"{fp_per_game(split_of(p, year, k), rules):.1f}",
              *(box(split_of(p, year, k), stat) for stat in BOX))
             for k in SPLITS if split_of(p, year, k)])
        s, proj = stats.season_or_projection(p, year)
        avg = s.get("avg") or {}
        parts = sorted(((stat, avg.get(stat, 0), pts) for stat, pts in rules.items() if avg.get(stat)),
                       key=lambda x: -abs(x[1] * x[2]))
        self.breakdown_head.set(f"Where the points come from ({'projected' if proj else 'season'}, per game)")
        self.breakdown.set_rows([(stat, f"{per:.1f}", f"{pts:+g}", f"{per * pts:+.1f}") for stat, per, pts in parts],
                                [("hot" if per * pts > 0 else "cold",) for _, per, pts in parts])


def main():
    try:  # crisp text on high-DPI Windows screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(demo="--demo" in sys.argv).mainloop()


if __name__ == "__main__":
    main()
