"""Desktop window for the fantasy tracker.

    .venv\\Scripts\\pythonw app\\main.py          (or double-click "Fantasy Hoops.bat")
    .venv\\Scripts\\pythonw app\\main.py --demo   made-up league, no ESPN needed
"""
import queue
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import fantasy
import gamelog
import lineup
import news
import planner
import projections
import stats
from chart import GameChart
from compare import SERIES, ParallelChart, axes_for, profile
import trade
from stats import BOX, SHOOTING_WIDTHS, SPLIT_LABELS, SPLITS, box, fp_per_game, games, owner_names, split_of

FONT = ("Segoe UI", 11)
COLUMN_SCALE = 1.1  # column widths were tuned at 10pt; scale them with the 11pt font
# palette: navy chrome, basketball-orange actions, high-contrast text on light panels
NAVY = "#14213d"        # header bar
NAVY_2 = "#1f3a5f"      # table headings, selected rows, active toggles
ORANGE = "#f26b1d"      # primary buttons and the logo
ORANGE_DARK = "#d4560b"
PAGE = "#e9eef5"        # window background
CARD = "#ffffff"        # tables and text panels
BORDER = "#cfd8e3"
TEXT = "#111827"
TEXT_2 = "#374151"      # secondary text (alert details, notes)
MUTED = "#4b5563"       # small print: dark enough to read comfortably
STRIPE = "#f3f6fa"      # every other table row
ACCENT = NAVY_2
INK_DARK = TEXT
ME_BG = "#ffe9d9"       # your team / ticked rows: light orange
RED = "#c62828"
AMBER = "#b26a00"
GREEN = "#2e7d32"
PRIORITY = {1: ("DO NOW", RED), 2: ("CONSIDER", AMBER), 3: ("FYI", MUTED)}
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


def short_name(name):
    """Last name, skipping suffixes: 'Scotty Pippen Jr.' -> 'Pippen', 'Jaren Jackson Jr.' -> 'Jackson'."""
    parts = [w for w in name.split() if w.rstrip(".").upper() not in ("JR", "SR", "II", "III", "IV", "V")]
    return parts[-1] if parts else name


def status_tag(p):
    s = p.injuryStatus or "ACTIVE"
    return "out" if s in fantasy.OUT_STATUSES else "shaky" if s in fantasy.SHAKY_STATUSES else ""


class Table(ttk.Frame):
    """Treeview with a scrollbar and click-to-sort headers."""

    def __init__(self, parent, columns, text_cols=(), wide=(), height=8, on_open=None, widths=None):
        super().__init__(parent)
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=height)
        for c in columns:
            self.tree.heading(c, text=c, command=lambda c=c: self.sort(c))
            # short codes like pos/nba/slot don't need a full text-column width
            base = 190 if c in wide else 54 if len(c) <= 4 else 110 if c in text_cols else 50
            width = (widths or {}).get(c) or max(base, 10 * len(c) + 26)
            width = round(width * COLUMN_SCALE)
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
        self.tree.tag_configure("odd", background=STRIPE)
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

    def set_rows(self, rows, tags=None, iids=None):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(rows):
            self.tree.insert("", "end", iid=iids[i] if iids else None, values=r, tags=tags[i] if tags else ())
        self._stripe()

    def _stripe(self):
        """Shade every other row so the eye can follow a row across; rows with their own
        background (your team, ticked players) keep it."""
        for i, k in enumerate(self.tree.get_children("")):
            tags = [t for t in self.tree.item(k, "tags") if t != "odd"]
            if i % 2 and "me" not in tags:
                tags.append("odd")
            self.tree.item(k, tags=tags)

    @staticmethod
    def _number(v):
        """The number in a cell ('#10' -> 10, '40,505' -> 40505, '+3.2' -> 3.2, '52%' -> 52), else None."""
        s = str(v).strip().replace(",", "").replace(" *", "").lstrip("#").rstrip("%").replace("+", "")
        try:
            return float(s)
        except ValueError:
            pass
        m = re.match(r"^(\d+(?:\.\d+)?)-\d", s)  # shooting cell '8.6-17.5 49%': sort by made per game
        return float(m.group(1)) if m else None

    def sort(self, col):
        cells = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        numbers = [(self._number(v), k) for v, k in cells if self._number(v) is not None]
        words = [(str(v).lower(), k) for v, k in cells if self._number(v) is None]
        # First click: ranks (#1, #2...) and text ascending, other numbers biggest first.
        rank_like = col == "#" or any(str(v).startswith("#") for v, _ in cells)
        default = not numbers or rank_like
        ascending = self._reverse.get(col, default)
        numbers.sort(key=lambda x: x[0], reverse=not ascending)
        if numbers:
            words.sort(key=lambda x: x[0])  # '-', 'loading...' etc. stay at the bottom
            items = numbers + words
        else:
            items = sorted(words, key=lambda x: x[0], reverse=not ascending)
        for i, (_, k) in enumerate(items):
            self.tree.move(k, "", i)
        self._reverse[col] = not ascending
        self._stripe()


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
        s.configure(".", font=FONT, background=PAGE, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, troughcolor=PAGE)
        s.configure("TFrame", background=PAGE)
        s.configure("TLabel", background=PAGE, foreground=TEXT)
        s.configure("Sub.TLabel", foreground=MUTED, font=(FONT[0], 10))

        # tables: white, taller rows, navy headings with white text
        s.configure("Treeview", rowheight=32, font=FONT, background=CARD, fieldbackground=CARD,
                    foreground=TEXT, bordercolor=BORDER, borderwidth=1)
        s.configure("Treeview.Heading", font=(FONT[0], 10, "bold"), background=NAVY_2, foreground="white",
                    relief="flat", padding=(6, 6))
        s.map("Treeview.Heading", background=[("active", "#2b4c7e")], foreground=[("active", "white")])
        s.map("Treeview", background=[("selected", NAVY_2)], foreground=[("selected", "white")])

        # tabs: the selected one turns white with navy bold text
        s.configure("TNotebook", background=PAGE, borderwidth=0, tabmargins=(8, 6, 8, 0))
        s.configure("TNotebook.Tab", padding=(16, 8), font=(FONT[0], 11), background="#d7dfea",
                    foreground=TEXT_2, borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", CARD), ("active", "#e6ecf4")],
              foreground=[("selected", NAVY)], font=[("selected", (FONT[0], 11, "bold"))],
              expand=[("selected", (1, 2, 1, 0))])

        # buttons
        s.configure("TButton", padding=(12, 6), background="#dfe6ef", foreground=TEXT, borderwidth=1)
        s.map("TButton", background=[("active", "#cfd9e6")])
        s.configure("Accent.TButton", foreground="white", background=ORANGE, font=(FONT[0], 11, "bold"))
        s.map("Accent.TButton", background=[("active", ORANGE_DARK), ("disabled", "#f5b48f")],
              foreground=[("disabled", "white")])
        s.configure("Toolbutton", padding=(10, 5), background="#dfe6ef", foreground=TEXT)
        s.map("Toolbutton", background=[("selected", NAVY_2), ("active", "#cfd9e6")],
              foreground=[("selected", "white")])
        s.configure("TCombobox", padding=4, arrowsize=16)
        s.configure("TEntry", padding=4)
        self.configure(background=PAGE)

    def _build(self):
        top = tk.Frame(self, background=NAVY, padx=18, pady=12)
        top.pack(fill="x")
        logo = tk.Canvas(top, width=40, height=40, background=NAVY, highlightthickness=0)
        logo.pack(side="left", padx=(0, 12))
        logo.create_oval(3, 3, 37, 37, fill=ORANGE, outline="#1a1a1a", width=2)  # a basketball
        logo.create_line(20, 3, 20, 37, fill="#1a1a1a", width=2)
        logo.create_line(3, 20, 37, 20, fill="#1a1a1a", width=2)
        logo.create_arc(-12, 6, 14, 34, start=-60, extent=120, style="arc", outline="#1a1a1a", width=2)
        logo.create_arc(26, 6, 52, 34, start=120, extent=120, style="arc", outline="#1a1a1a", width=2)
        self.league_name = tk.StringVar(value="Fantasy Hoops")
        titles = tk.Frame(top, background=NAVY)
        titles.pack(side="left")
        tk.Label(titles, textvariable=self.league_name, font=(FONT[0], 19, "bold"), background=NAVY,
                 foreground="white").pack(anchor="w")
        tk.Label(titles, text="Fantasy Hoops", font=(FONT[0], 10), background=NAVY,
                 foreground="#9fb3cf").pack(anchor="w")
        self.refresh_btn = ttk.Button(top, text="Refresh", style="Accent.TButton", command=self.refresh)
        self.refresh_btn.pack(side="right")
        self.status = tk.StringVar()
        tk.Label(top, textvariable=self.status, font=(FONT[0], 10), background=NAVY,
                 foreground="#c7d3e3").pack(side="right", padx=14)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        self._standings_tab()
        self._my_team_tab()
        self.week_tab = self._week_tab()
        self._free_agents_tab()
        self._trades_tab()
        self._trade_lab_tab()
        self.league_tab = self._league_tab()
        self.player_tab = self._player_tab()
        self.compare_tab = self._compare_tab()
        self.news_tab = self._news_tab()
        self._activity_tab()

    def _tab(self, title):
        f = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(f, text=title)
        return f

    def _standings_tab(self):
        f = self._tab("Standings")
        self.standings = Table(f, ("#", "team", "owner", "points", "behind", "change", "proj final", "proj rank"),
                               text_cols=("team", "owner"), wide=("team", "owner"), height=10,
                               on_open=lambda v: self.show_team(v[1]))
        self.standings.pack(fill="both", expand=True)
        ttk.Label(f, text="Double-click a team to see its roster. 'change' is points since the "
                          "last saved snapshot.", style="Sub.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Label(f, text="proj final = points so far + the rest of the season simulated day by day on the real "
                          "NBA schedule: each day a team scores with its best 10 players who have a game, at their "
                          "base value (ESPN's projection early on, real form later).",
                  style="Sub.TLabel", wraplength=1400).pack(anchor="w", pady=(2, 0))

    def _my_team_tab(self):
        f = self._tab("My Team")
        self.my_title = tk.StringVar()
        ttk.Label(f, textvariable=self.my_title, font=(FONT[0], 12, "bold")).pack(anchor="w")
        ttk.Label(f, text="Alerts", style="Sub.TLabel").pack(anchor="w", pady=(8, 2))
        self.alerts = tk.Text(f, height=4, wrap="word", relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=10, pady=8,
                              background="white", font=FONT, cursor="arrow")
        self.alerts.pack(fill="x")
        for prio, (_, color) in PRIORITY.items():
            self.alerts.tag_configure(f"p{prio}", foreground=color, font=(FONT[0], 10, "bold"))
        self.alerts.tag_configure("detail", foreground=TEXT_2, lmargin1=18, lmargin2=18,
                                  spacing3=6)
        ttk.Label(f, text="Roster  (base = season avg, blended with projection early on;"
                          " L15 = last 15 days)", style="Sub.TLabel").pack(anchor="w", pady=(10, 2))
        keep_bar = ttk.Frame(f)
        keep_bar.pack(fill="x", pady=(0, 4))
        ttk.Button(keep_bar, text="Keep / unkeep selected player", command=self._toggle_keep).pack(side="left")
        ttk.Label(keep_bar, text="Kept players are never suggested as a drop or as trade bait (streamers, waiver "
                                 "pickups, trade ideas). Saved in config\\keep.json.",
                  style="Sub.TLabel").pack(side="left", padx=10)
        self.my_roster = Table(f, ("player", "keep", "pos", "your lineup", "today", "proj today", "NBA role", "nba",
                                   "status", "gp", "base", "L15", "trend", "vs proj", "fp tot"),
                               text_cols=("player", "keep", "pos", "your lineup", "today", "NBA role", "nba", "status"),
                               wide=("player",), widths={"keep": 60, "your lineup": 130, "today": 180,
                                                          "NBA role": 215},
                               on_open=lambda v: self.show_player(v[0]))
        ttk.Label(f, text="your lineup = your fantasy slot today  ·  today = their NBA game (your time)  ·  "
                          "NBA role = how many of their last games they started (pre = preseason)  ·  "
                  "proj today = expected fantasy pts if he plays today  ·  vs proj = season pts/game minus "
                  "ESPN's projection (after 3 games)",
                  style="Sub.TLabel", wraplength=1700).pack(anchor="w", pady=(4, 0))
        self.my_roster.pack(fill="both", expand=True)

    def _trade_lab_tab(self):
        f = self._tab("Trade Lab")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Team").pack(side="left")
        self.tl_a = ttk.Combobox(bar, state="readonly", width=30)
        self.tl_a.pack(side="left", padx=6)
        ttk.Label(bar, text="trades with", font=(FONT[0], 10, "bold")).pack(side="left", padx=8)
        self.tl_b = ttk.Combobox(bar, state="readonly", width=30)
        self.tl_b.pack(side="left", padx=6)
        for box in (self.tl_a, self.tl_b):
            box.bind("<<ComboboxSelected>>", lambda e: self._trade_teams_changed())
        ttk.Button(bar, text="Clear picks", command=self._trade_clear).pack(side="left", padx=12)
        ttk.Label(bar, text="Click players to tick them. base = per-game value, L15 = last 15 days.",
                  style="Sub.TLabel").pack(side="right")

        lists = ttk.Frame(f)
        lists.pack(fill="x")
        self.tl_send, self.tl_get = set(), set()
        self.tl_tables, self.tl_heads = [], []
        tick = "✔"
        cols = (tick, "player", "pos", "nba", "status", "base", "L15")
        for i in range(2):
            col = ttk.Frame(lists)
            col.pack(side="left", fill="both", expand=True, padx=(0, 10) if i == 0 else 0)
            head = tk.StringVar()
            row = tk.Frame(col, background=PAGE)
            row.pack(fill="x")
            tk.Frame(row, background=SERIES[i], width=6, height=20).pack(side="left", padx=(0, 6))
            ttk.Label(row, textvariable=head, font=(FONT[0], 11, "bold")).pack(side="left")
            table = Table(col, cols, text_cols=(tick, "player", "pos", "nba", "status"), wide=("player",),
                          height=7, widths={tick: 40})
            table.pack(fill="both", expand=True, pady=(4, 0))
            table.tree.bind("<ButtonRelease-1>", lambda e, i=i: self._trade_toggle(e, i))
            self.tl_tables.append(table)
            self.tl_heads.append(head)

        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(10, 0))
        self.tl_text = tk.Text(body, width=54, wrap="word", relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10,
                               background="white", font=FONT, cursor="arrow")
        self.tl_text.pack(side="left", fill="y")
        self.tl_text.tag_configure("verdict", font=(FONT[0], 11, "bold"), spacing3=8)
        self.tl_text.tag_configure("a", foreground=SERIES[0], font=(FONT[0], 10, "bold"), spacing1=6)
        self.tl_text.tag_configure("b", foreground=SERIES[1], font=(FONT[0], 10, "bold"), spacing1=6)
        self.tl_text.tag_configure("body", foreground="#333333", lmargin1=12, lmargin2=12)
        self.tl_text.tag_configure("muted", foreground=MUTED, lmargin1=12, lmargin2=12)
        sugg = ttk.Frame(body, width=560)
        sugg.pack(side="left", fill="y", padx=(10, 0))
        sugg.pack_propagate(False)
        self.tl_sugg_head = tk.StringVar()
        ttk.Label(sugg, textvariable=self.tl_sugg_head, font=(FONT[0], 10, "bold")).pack(anchor="w")
        ttk.Label(sugg, text="Updates as you tick players. Click one to load it.", style="Sub.TLabel").pack(anchor="w")
        self.tl_sugg = Table(sugg, ("sends", "gets", "you", "them"), text_cols=("sends", "gets"),
                             widths={"sends": 185, "gets": 185, "you": 56, "them": 62})
        self.tl_sugg.pack(fill="both", expand=True, pady=(4, 0))
        self.tl_sugg.tree.bind("<ButtonRelease-1>", self._trade_apply_suggestion)
        self._tl_options = []
        self.tl_chart = trade.StrengthChart(body, height=320)
        self.tl_chart.pack(side="left", fill="both", expand=True, padx=(10, 0))

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
        self.league_roster = Table(f, ("player", "pos", "nba", "slot", "status", "gp", "fp tot", "fp/g",
                                       "proj/g", "vs proj", *BOX),
                                   text_cols=("player", "pos", "nba", "slot", "status"), wide=("player",),
                                   widths=SHOOTING_WIDTHS, on_open=lambda v: self.show_player(v[0]))
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
                                     "vs proj", "worth", "drop for them", "gain"),
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
        ttk.Button(bar, text="Compare with...", command=self._compare_current).pack(side="left", padx=6)
        top = ttk.Frame(f)
        top.pack(fill="x")
        # A blank image makes the label size itself in pixels; text shows on top when there's no photo.
        self._blank = tk.PhotoImage(width=PHOTO_W, height=PHOTO_H)
        self.photo_label = tk.Label(top, image=self._blank, compound="center", background="#e9ecf1",
                                    text="", foreground=MUTED, font=FONT)
        self.photo_label.pack(side="left", padx=(0, 14))
        self._photo = None        # keep a reference or Tk drops the image
        self._photo_for = None    # player id the photo belongs to, so slow downloads can't show the wrong face
        right = ttk.Frame(top)
        right.pack(side="left", fill="both", expand=True)
        self.player_head = tk.StringVar(value="Search a player, or double-click one in any table.")
        ttk.Label(right, textvariable=self.player_head, font=(FONT[0], 12, "bold")).pack(anchor="w", pady=(4, 8))
        self.player_splits = Table(right, ("split", "gp", "fp/g", *BOX), text_cols=("split",),
                                   wide=("split",), height=5, widths=SHOOTING_WIDTHS)
        self.player_splits.pack(fill="both", expand=True)
        # Clicking a stat's column header charts that stat game by game.
        header_stat = {"fp/g": "FP", "MPG": "MIN", "FG": "FGM", "3P": "3PM", "FT": "FTM",
                       **{s: s for s in gamelog.CHART_STATS}}
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
        self.breakdown = Table(side, ("stat", "per game", "pts each", "fantasy pts"), text_cols=("stat",),
                               widths={"stat": 64, "per game": 88, "pts each": 84, "fantasy pts": 104})
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

    def _compare_tab(self):
        f = self._tab("Compare")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        self.cmp_q = []
        for i in range(2):
            if i:
                ttk.Label(bar, text="vs", font=(FONT[0], 11, "bold")).pack(side="left", padx=8)
            e = ttk.Entry(bar, width=24)
            e.pack(side="left")
            e.bind("<Return>", lambda ev: self.run_compare())
            self.cmp_q.append(e)
        ttk.Button(bar, text="Compare", style="Accent.TButton", command=self.run_compare).pack(side="left", padx=10)
        ttk.Label(bar, text="Stats").pack(side="left", padx=(16, 4))
        self.cmp_split = ttk.Combobox(bar, state="readonly", width=30,
                                      values=["season (projected before games)"] + [SPLIT_LABELS[k] for k in SPLITS])
        self.cmp_split.current(0)
        self.cmp_split.pack(side="left")
        self.cmp_split.bind("<<ComboboxSelected>>", lambda e: self._draw_compare())
        self.cmp_mode = tk.StringVar(value="fantasy")
        for value, text in (("fantasy", "Fantasy points by stat"), ("raw", "Per-game stats")):
            ttk.Radiobutton(bar, text=text, value=value, variable=self.cmp_mode, style="Toolbutton",
                            command=self._draw_compare).pack(side="right", padx=(2, 0))

        cards = ttk.Frame(f)
        cards.pack(fill="x", pady=(0, 8))
        self.cmp_cards = []
        for i in range(2):
            card = tk.Frame(cards, background="white")
            card.pack(side="left", fill="x", expand=True, padx=(0, 10) if i == 0 else 0)
            tk.Frame(card, background=SERIES[i], width=6).pack(side="left", fill="y")
            blank = tk.PhotoImage(width=PHOTO_W // 2, height=PHOTO_H // 2)
            photo = tk.Label(card, image=blank, background="#e9ecf1")
            photo.image = blank
            photo.pack(side="left", padx=8, pady=6)
            text = tk.StringVar(value=f"Player {i + 1}")
            tk.Label(card, textvariable=text, background="white", foreground=INK_DARK, justify="left",
                     anchor="w", font=(FONT[0], 11)).pack(side="left", fill="x", expand=True)
            self.cmp_cards.append((photo, blank, text))

        body = ttk.Frame(f)
        body.pack(fill="both", expand=True)
        side = ttk.Frame(body, width=470)
        side.pack(side="right", fill="y", padx=(14, 0))
        side.pack_propagate(False)
        ttk.Label(side, text="Side by side (edge = extra fantasy pts per game)", style="Sub.TLabel").pack(anchor="w")
        self.cmp_table = Table(side, ("stat", "player 1", "player 2", "edge"), text_cols=("stat", "edge"),
                               wide=("edge",), widths={"stat": 110, "player 1": 80, "player 2": 80, "edge": 140})
        self.cmp_table.pack(fill="both", expand=True)
        self.cmp_chart = ParallelChart(body, height=360)
        self.cmp_chart.pack(side="left", fill="both", expand=True)
        self._cmp = None
        return f

    def _week_tab(self):
        f = self._tab("Week Ahead")
        self.wk_today = tk.StringVar()
        ttk.Label(f, textvariable=self.wk_today, font=(FONT[0], 12, "bold")).pack(anchor="w")
        self.wk_caption = tk.StringVar()
        ttk.Label(f, textvariable=self.wk_caption, style="Sub.TLabel").pack(anchor="w")
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(6, 10))
        self.wk_lineup = Table(top, ("slot", "player", "game", "pts", "change"),
                               text_cols=("slot", "player", "game", "change"), wide=("player",),
                               widths={"slot": 60, "game": 185, "pts": 70, "change": 230}, height=10,
                               on_open=lambda v: self.show_player(v[1]) if v[1] else None)
        self.wk_lineup.pack(side="left", fill="both", expand=True)
        days = ttk.Frame(top)
        days.pack(side="left", fill="both", expand=True, padx=(14, 0))
        ttk.Label(days, text="Next 7 days: lineup slots you'll fill  (click a day to see its lineup)",
                  font=(FONT[0], 10, "bold")).pack(anchor="w")
        self.wk_days = Table(days, ("day", "slots filled", "proj pts", "who's playing"),
                             text_cols=("day", "slots filled", "who's playing"), wide=("who's playing",),
                             widths={"day": 110, "slots filled": 300, "proj pts": 80}, height=7)
        self.wk_days.pack(fill="both", expand=True, pady=(4, 0))
        self.wk_days.tree.bind("<<TreeviewSelect>>", self._week_day_selected)
        self._wk_week, self._wk_selected = [], 0
        self.wk_days_note = tk.StringVar()
        ttk.Label(days, textvariable=self.wk_days_note, style="Sub.TLabel", wraplength=700).pack(anchor="w", pady=(4, 0))

        ttk.Label(f, text="Streamers: free agents who'd add the most points over the next 7 days",
                  font=(FONT[0], 10, "bold")).pack(anchor="w")
        ttk.Label(f, text="Counts only games on days you'd actually start him. 'make room' = who leaves your active "
                          "roster: an open roster spot if you have one, then your empty IR slot (INJ players always qualify; O "
                          "players only once ESPN marks them IR-eligible, so check on ESPN), otherwise a drop - only if "
                          "you also come out ahead over the 'rest of season' (his expected remaining games vs the "
                          "dropped player's, at their base value; expected = scheduled games x the share ESPN expects "
                          "him to play). No acquisition limit in your league.",
                  style="Sub.TLabel", wraplength=1800).pack(anchor="w")
        self.wk_streams = Table(f, ("add", "pos", "nba", "base", "games", "on light days", "gain (7 days)",
                                    "rest of season", "make room"),
                                text_cols=("add", "pos", "nba", "make room"), wide=("add", "make room"), height=8,
                                on_open=lambda v: self.show_player(v[0]))
        self.wk_streams.pack(fill="both", expand=True, pady=(4, 0))
        return f

    def _news_tab(self):
        f = self._tab("News")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Show").pack(side="left")
        self.news_scope = ttk.Combobox(bar, state="readonly", width=34,
                                       values=["My team", "Every rostered player", "Every injured NBA player"])
        self.news_scope.current(0)
        self.news_scope.pack(side="left", padx=6)
        self.news_scope.bind("<<ComboboxSelected>>", lambda e: self._fill_news())
        ttk.Label(bar, text="From ESPN's injury report. Click a player for the full note and latest news.",
                  style="Sub.TLabel").pack(side="right")
        self.news_table = Table(f, ("player", "fantasy team", "status", "injury", "back", "latest update"),
                                text_cols=("player", "fantasy team", "status", "injury", "back", "latest update"),
                                wide=("latest update",),
                                widths={"player": 190, "fantasy team": 200, "status": 110, "injury": 110,
                                        "back": 130}, height=12,
                                on_open=lambda v: self.show_player(v[0]))
        self.news_table.pack(fill="both", expand=True)
        self.news_table.tree.bind("<<TreeviewSelect>>", self._news_selected)
        self.news_detail = tk.Text(f, height=9, wrap="word", relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10, background="white",
                                   font=FONT, cursor="arrow")
        self.news_detail.pack(fill="x", pady=(8, 0))
        self.news_detail.tag_configure("head", font=(FONT[0], 11, "bold"))
        self.news_detail.tag_configure("muted", foreground=MUTED)
        self._news_ids = []
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
        projected = projections.projected_final(league)
        real = bool(getattr(league, "pro_schedule", None))
        injuries = news.injuries() if real else {}
        values = {p.playerId: fantasy.form_of(p, league.year).baseline for p in me.roster}
        plan = dict(week=planner.week(league, me.roster, values, injuries) if real else [],
                    moves=planner.today_moves(league, me, injuries),
                    streamers=planner.streamers(league, me, fas, injuries))
        return dict(league=league, cfg=cfg, rules=rules, me=me, prev=prev, prev_day=prev_day, projected=projected,
                    injuries=injuries, plan=plan,
                    alerts=fantasy.build_alerts(league, me, cfg["thresholds"], analysis, injuries),
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
        self._fill_trade_lab()
        self._fill_week()
        self._fill_news()
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
        proj = d.get("projected") or {}
        proj_rank = {tid: r for r, tid in enumerate(sorted(proj, key=lambda k: -proj[k][2]), 1)}
        rows, tags = [], []
        for i, t in enumerate(teams, 1):
            change = f"{t.points_for - d['prev'][t.team_id]:+.1f}" if t.team_id in d["prev"] else "-"
            final = f"{proj[t.team_id][2]:,.0f}" if t.team_id in proj else "-"
            rows.append((i, t.team_name, owner_names(t), f"{t.points_for:.1f}",
                         f"{lead - t.points_for:.1f}" if i > 1 else "-", change, final,
                         f"#{proj_rank[t.team_id]}" if t.team_id in proj_rank else "-"))
            tags.append(("me",) if t.team_id == d["me"].team_id else ())
        self.standings.set_rows(rows, tags)

    def _fill_my_team(self):
        d = self.data
        me, year = d["me"], d["league"].year
        rank = 1 + sum(t.points_for > me.points_for for t in d["league"].teams)
        forms_all = [fantasy.form_of(p, year) for p in me.roster]
        today_pts = projections.today_points(d["league"], forms_all)
        lineup_today = sum(v for f in forms_all for v in [today_pts[f.player.playerId]]
                           if f.player.lineupSlot not in ("BE", "IR"))
        proj = (d.get("projected") or {}).get(me.team_id)
        extra = f"   ·   projected final {proj[2]:,.0f}" if proj else ""
        self.my_title.set(f"{me.team_name}   ·   {me.points_for:.1f} pts   ·   #{rank} of {len(d['league'].teams)}"
                          f"   ·   your lineup projects {lineup_today:.1f} pts today{extra}")

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
        games = lineup.todays_games(d["league"])
        roles = getattr(self, "_roles", {})
        kept = fantasy.load_keep()
        rows, tags, iids = [], [], []
        for f in forms:
            p = f.player
            trusted = f.games >= min_games
            slot = p.lineupSlot
            in_lineup = "IR" if slot == "IR" else "bench" if slot == "BE" else f"starting ({slot})"
            vp = projections.vs_projection(p, year, d["rules"])
            iids.append(str(p.playerId))
            rows.append((p.name, "kept" if p.playerId in kept else "", p.position, in_lineup,
                         lineup.game_today(d["league"], p, games) or "no game",
                         f"{today_pts[p.playerId]:.1f}" if today_pts[p.playerId] else "-",
                         roles.get(p.playerId, "-" if self.demo else "loading..."), p.proTeam, status_text(p),
                         f.games, f"{f.baseline:.1f}", f"{f.recent:.1f}", f"{f.trend:+.0%}" if trusted else "-",
                         f"{vp:+.1f}" if vp is not None else "-",
                         f"{split_of(p, year, 'total').get('applied_total', 0):.0f}"))
            hot = trusted and f.trend >= d["cfg"]["thresholds"]["trend_pct"]
            cold = trusted and f.trend <= -d["cfg"]["thresholds"]["trend_pct"]
            tags.append((status_tag(p) or ("hot" if hot else "cold" if cold else ""),))
        self.my_roster.set_rows(rows, tags, iids)
        # NBA roles need box scores: fetch in the background (cached after the first time), then redraw.
        missing = [p.playerId for p in me.roster if p.playerId not in roles]
        if missing and not self.demo:
            self.run(lambda: lineup.nba_roles(missing, year), self._roles_loaded)

    def _toggle_keep(self):
        sel = self.my_roster.tree.selection()
        if not sel or not self.data:
            self.status.set("Select a player on your roster first.")
            return
        pid = int(sel[0])
        p = next(x for x in self.data["me"].roster if x.playerId == pid)
        fantasy.set_keep(p, pid not in fantasy.load_keep())
        self.refresh()  # suggestions everywhere are rebuilt without (or with) him

    def _roles_loaded(self, found):
        self._roles = {**getattr(self, "_roles", {}), **found}
        if self.data:
            self._fill_my_team()

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
            vp = projections.vs_projection(p, d["league"].year, d["rules"])
            rows.append((p.name, p.position, p.proTeam, status_text(p), fa.games, f"{fa.baseline:.1f}",
                         f"{fa.recent:.1f}", f"{fa.trend:+.0%}" if trusted else "-",
                         f"{vp:+.1f}" if vp is not None else "-", f"{fa.worth:.1f}",
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
                         f"{fp_per_game(split_of(p, year, 'projected'), rules):.1f}",
                         f"{vp:+.1f}" if (vp := projections.vs_projection(p, year, rules)) is not None else "-",
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
        fewer_is_better = scoring.get(stat, 0) < 0  # e.g. TO, FGA, FTA cost you points
        worse = "  (fewer is better for you)" if fewer_is_better else ""
        span = "" if pick == "All games" else f", {pick.lower()}"
        self.chart.show(games, stat, f"{gamelog.STAT_NAMES[stat]} per game  ·  {self._chart_name}{worse}",
                        note=f"{self._games_label}{span}  ·  {len(games)} games  ·  average is over the games shown"
                             "  ·  hover a bar for that game's box score",
                        fewer_is_better=fewer_is_better)

    def _breakdown_clicked(self, _event):
        sel = self.breakdown.tree.selection()
        if sel:
            stat = self.breakdown.tree.item(sel[0], "values")[0]
            if stat in gamelog.CHART_STATS:
                self.chart_stat.set(stat)

    # ------------------------------------------------------------ week ahead / news

    def _fill_week(self):
        d = self.data
        plan, league = d.get("plan") or {}, d["league"]
        week = plan.get("week") or []
        self._wk_week = week
        if self._wk_selected >= len(week):
            self._wk_selected = 0
        self._fill_week_days()
        self._show_week_day(self._wk_selected)
        self._fill_streamers()

    def _show_today_lineup(self):
        d = self.data
        plan, league = d.get("plan") or {}, d["league"]
        games = lineup.todays_games(league)
        moves = plan.get("moves")
        self.wk_caption.set("Best legal lineup for the rest of today (positions respected; players whose game has "
                            "started are locked). Make these changes on ESPN.")
        if not moves:
            self.wk_today.set("No NBA schedule (demo mode).")
            self.wk_lineup.set_rows([])
        else:
            gain = moves["best"] - moves["current"]
            self.wk_today.set(f"Today: your lineup scores {moves['current']:.1f} pts  ->  best possible "
                              f"{moves['best']:.1f}" + (f"  (+{gain:.1f}: make the changes below)" if gain >= 0.5
                                                       else "  - already optimal"))
            starting = {p.playerId for p in d["me"].roster if p.lineupSlot not in ("BE", "IR")}
            rows, tags = [], []
            for p in moves["locked"]:
                rows.append((p.lineupSlot, p.name, lineup.game_today(league, p, games), f"{fantasy.form_of(p, league.year).baseline:.1f}",
                             "locked (game started)"))
                tags.append(())
            for si, slot in enumerate(moves["slots"]):
                c = moves["lineup"].get(si)
                if not c:
                    rows.append((slot, "", "", "", "empty (no game)"))
                    tags.append(("cold",))
                    continue
                pl = c[1]
                if pl.playerId not in starting:
                    change, tag = "START (from bench)", ("hot",)
                elif pl.lineupSlot != slot:
                    change, tag = f"move from {pl.lineupSlot}", ()
                else:
                    change, tag = "keep", ()
                rows.append((slot, pl.name, lineup.game_today(league, pl, games), f"{c[0]:.1f}", change))
                tags.append(tag)
            for pl in moves["bench"]:
                rows.append(("BE", pl.name, lineup.game_today(league, pl, games) or "no game", "", "BENCH to make room"))
                tags.append(("shaky",))
            self.wk_lineup.set_rows(rows, tags)

    def _show_day_lineup(self, day):
        """A future day's best lineup: who'd start in each slot, plus anyone playing who doesn't fit."""
        league = self.data["league"]
        games = lineup.games_on(league, day["day"])
        self.wk_today.set(f"{day['date']:%A %d %b}: {day['filled']}/{day['slots']} slots filled, "
                          f"about {day['points']:.0f} pts")
        self.wk_caption.set("Best legal lineup for that day from your current roster (injured players count from "
                            "their expected return). Click today's row to get back to today's moves.")
        team_of = projections.nba_team_id
        rows, tags = [], []
        for si, slot in enumerate(day["slot_names"]):
            c = day["lineup"].get(si)
            if c:
                rows.append((slot, c[1].name, games.get(team_of(c[1]), ""), f"{c[0]:.1f}", "start"))
                tags.append(())
            else:
                rows.append((slot, "", "", "", "empty (no game)"))
                tags.append(("cold",))
        used = {c[1].playerId for c in day["lineup"].values()}
        for value, p, _ in sorted(day["cands"], key=lambda c: -c[0]):
            if p.playerId not in used:
                rows.append(("BE", p.name, games.get(team_of(p), ""), f"{value:.1f}", "plays, but no open slot fits"))
                tags.append(("shaky",))
        self.wk_lineup.set_rows(rows, tags)

    def _show_week_day(self, index):
        self._wk_selected = index
        if index == 0 or not self._wk_week:
            self._show_today_lineup()
        else:
            self._show_day_lineup(self._wk_week[index])

    def _week_day_selected(self, _event):
        sel = self.wk_days.tree.selection()
        if sel and sel[0].isdigit():
            self._show_week_day(int(sel[0]))

    def _fill_week_days(self):
        week = self._wk_week
        rows, tags = [], []
        for day in week:
            bar = "\u2588" * day["filled"] + "\u2591" * (day["slots"] - day["filled"])
            starters = sorted(day["lineup"].values(), key=lambda c: -c[0])
            used = {c[1].playerId for c in starters}
            names = [short_name(c[1].name) for c in starters]
            spare = [short_name(c[1].name) for c in day["cands"] if c[1].playerId not in used]
            who = ", ".join(names) + (f"   (no slot: {', '.join(spare)})" if spare else "")
            rows.append((f"{day['date']:%a %d %b}", f"{bar}  {day['filled']}/{day['slots']}",
                         f"{day['points']:.0f}", who or "nobody"))
            tags.append(("cold",) if day["filled"] <= day["slots"] // 2 else ())
        self.wk_days.set_rows(rows, tags, [str(i) for i in range(len(rows))])
        if week:
            self.wk_days.tree.selection_set(str(self._wk_selected))
        light = [day for day in week if day["filled"] < day["slots"]]
        empty = sum(day["slots"] - day["filled"] for day in week)
        self.wk_days_note.set(f"{empty} empty slot-days this week. Red = half your lineup or more is empty: "
                              "that's where streamers earn points." if week else "")

    def _fill_streamers(self):
        plan, league = self.data.get("plan") or {}, self.data["league"]
        rows = []
        for s in plan.get("streamers") or []:
            fa = s["fa"]
            drop = (f"drop {s['drop'].name} ({fantasy.form_of(s['drop'], league.year).baseline:.1f})"
                    if s["drop"] else "")
            if s["open_spot"]:
                room = "open roster spot: just add him"
            elif s["ir"] and s["ir_certain"]:
                room = f"IR: {s['ir'].name} (INJ)"
            elif s["ir"]:
                room = f"IR: {s['ir'].name} if IR-eligible" + (f", else {drop}" if drop else "")
            else:
                room = drop
            rows.append((fa.player.name, fa.player.position, fa.player.proTeam, f"{fa.baseline:.1f}", s["games"],
                         s["light_games"], f"+{s['gain']:.0f}", f"{s['later']:+,.0f}", room))
        self.wk_streams.set_rows(rows or [("No free agent is worth a move right now.", "", "", "", "", "",
                                           "", "", "")])

    def _fill_news(self):
        d = self.data
        injuries, league = d.get("injuries") or {}, d["league"]
        owner = {p.playerId: (p, t) for t in league.teams for p in t.roster}
        scope = self.news_scope.current()
        if scope == 0:
            ids = [p.playerId for p in d["me"].roster]
        elif scope == 1:
            ids = list(owner)
        else:
            ids = list(injuries)
        rows, tags, self._news_ids = [], [], []
        for pid in ids:
            info = injuries.get(pid)
            pl, team = owner.get(pid, (None, None))
            if scope != 0 and not info:
                continue  # wider views list injured players only
            name = pl.name if pl else (info or {}).get("name", "?")
            status = (info or {}).get("status") or (status_text(pl) if pl else "")
            back = info["return_date"].strftime("%a %d %b") if info and info["return_date"] else "-"
            rows.append((name, team.team_name if team else "free agent", status, (info or {}).get("injury", "") or "-",
                         back, (info or {}).get("comment", "") or "healthy: no injury report"))
            tags.append(("me",) if team and team.team_id == d["me"].team_id and scope else
                        (("out",) if status.lower() == "out" else ("shaky",) if info else ()))
            self._news_ids.append(pid)
        self.news_table.set_rows(rows, tags, [str(i) for i in range(len(rows))])
        self.news_detail.configure(state="normal")
        self.news_detail.delete("1.0", "end")
        self.news_detail.insert("end", "Click a player to see ESPN's full injury note and latest news.", "muted")
        self.news_detail.configure(state="disabled")

    def _news_selected(self, _event):
        sel = self.news_table.tree.selection()
        if not sel or not sel[0].isdigit() or int(sel[0]) >= len(self._news_ids):
            return
        pid = self._news_ids[int(sel[0])]
        info = (self.data.get("injuries") or {}).get(pid)
        name = self.news_table.tree.item(sel[0], "values")[0]
        txt = self.news_detail
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        txt.insert("end", name + "\n", "head")
        if info:
            txt.insert("end", f"{info['status']} - {info['injury'] or 'injury'}"
                              + (f", expected back {info['return_date']:%a %d %b}" if info["return_date"] else "")
                              + "\n" + info["comment"] + "\n" + (info["details"] or "") + "\n\n")
        txt.insert("end", "Loading latest news...", "muted")
        txt.configure(state="disabled")
        if not self.demo:
            self.run(lambda: (pid, news.player_news(pid)), self._news_loaded)

    def _news_loaded(self, result):
        pid, items = result
        sel = self.news_table.tree.selection()
        if not sel or not sel[0].isdigit() or self._news_ids[int(sel[0])] != pid:
            return  # a different player was clicked meanwhile
        txt = self.news_detail
        txt.configure(state="normal")
        txt.delete("end-1c linestart", "end")
        if not items:
            txt.insert("end", "No recent news.", "muted")
        for it in items:
            when = f"{it['published']:%a %d %b %H:%M}  " if it["published"] else ""
            txt.insert("end", when, "muted")
            txt.insert("end", it["headline"] + "\n")
            if it["story"]:
                txt.insert("end", it["story"] + "\n\n", "muted")
        txt.configure(state="disabled")

    # ------------------------------------------------------------ trade lab

    def _team_by_name(self, name):
        return next((t for t in self.data["league"].teams if t.team_name == name), None)

    def _fill_trade_lab(self):
        league = self.data["league"]
        names = [t.team_name for t in league.teams]
        a, b = self.tl_a.get(), self.tl_b.get()
        self.tl_a["values"] = names
        self.tl_b["values"] = names
        if a not in names:
            self.tl_a.set(self.data["me"].team_name)
        if b not in names or b == self.tl_a.get():
            self.tl_b.set(next(n for n in names if n != self.tl_a.get()))
        self._trade_render()

    def _trade_teams_changed(self):
        if self.tl_a.get() == self.tl_b.get():
            names = list(self.tl_b["values"])
            self.tl_b.set(next(n for n in names if n != self.tl_a.get()))
        self._trade_clear()

    def _trade_clear(self):
        self.tl_send.clear()
        self.tl_get.clear()
        self._trade_render()

    def _trade_toggle(self, event, side):
        tree = self.tl_tables[side].tree
        iid = tree.identify_row(event.y)
        if not iid:
            return
        picks = self.tl_send if side == 0 else self.tl_get
        pid = int(iid)
        picks.symmetric_difference_update({pid})  # tick / untick
        self._trade_render()

    def _trade_render(self):
        if not self.data:
            return
        league, year = self.data["league"], self.data["league"].year
        team_a, team_b = self._team_by_name(self.tl_a.get()), self._team_by_name(self.tl_b.get())
        if not team_a or not team_b:
            return
        mine = team_a.team_id == self.data["me"].team_id
        self.tl_heads[0].set(f"{team_a.team_name} sends" + ("  (you)" if mine else ""))
        self.tl_heads[1].set(f"{team_b.team_name} sends")
        for side, (team, picks) in enumerate(((team_a, self.tl_send), (team_b, self.tl_get))):
            forms = sorted((fantasy.form_of(p, year) for p in team.roster), key=lambda f: -f.worth)
            rows, tags, iids = [], [], []
            for f in forms:
                p = f.player
                ticked = p.playerId in picks
                rows.append(("\u2714" if ticked else "", p.name, p.position, p.proTeam, status_text(p),
                             f"{f.baseline:.1f}", f"{f.recent:.1f}"))
                tags.append(("me",) if ticked else (status_tag(p),))
                iids.append(str(p.playerId))
            self.tl_tables[side].set_rows(rows, tags, iids)

        r = trade.simulate(league, team_a, team_b, self.tl_send, self.tl_get)
        self.tl_chart.show(r)
        self._trade_suggestions(league, team_a, team_b)
        txt = self.tl_text
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        txt.insert("end", trade.verdict(r) + "\n", "verdict")
        gl = r["games_left"]
        for key, tag in (("a", "a"), ("b", "b")):
            s = r[key]
            gives = r["sent"] if key == "a" else r["got"]
            gets = r["got"] if key == "a" else r["sent"]
            txt.insert("end", f"{s['team'].team_name}\n", tag)
            txt.insert("end", f"Lineup {s['before']:.1f} -> {s['after']:.1f} fp/g ({s['delta']:+.1f})   "
                              f"rank #{s['rank_before']} -> #{s['rank_after']}\n", "body")
            txt.insert("end", f"About {s['delta'] * gl:+.0f} season points over the ~{gl} games left\n", "body")
            if gives or gets:
                txt.insert("end", "Gives: " + (", ".join(f"{f.player.name} ({f.baseline:.1f})" for f in gives) or "nothing")
                           + "\nGets: " + (", ".join(f"{f.player.name} ({f.baseline:.1f})" for f in gets) or "nothing")
                           + "\n", "body")
            if s["size_after"] != s["size_before"]:
                note = f"Roster {s['size_before']} -> {s['size_after']} players"
                if s["drop"]:
                    note += f": would need to drop someone (lowest value: {s['drop']})"
                txt.insert("end", note + "\n", "muted")
            changed = [f"{pos} {s['pos_before'][pos]}->{s['pos_after'][pos]}" for pos in trade.POSITIONS
                       if s["pos_before"][pos] != s["pos_after"][pos]]
            if changed:
                txt.insert("end", "Positions: " + ", ".join(changed) + "\n", "muted")
        txt.insert("end", "\nLineup = the best 10 players a team can start. Bench players still score on days "
                          "a starter has no game, so depth adds points across a season too.", "muted")
        txt.configure(state="disabled")

    def _trade_suggestions(self, league, team_a, team_b):
        """Fill the 'even it out' list: balanced versions of the picked trade, or fair trades
        with this team when nothing is picked."""
        picked = bool(self.tl_send or self.tl_get)
        if picked:
            self.tl_sugg_head.set("Ways to even out this trade (adds up to 2 players)")
        else:
            self.tl_sugg_head.set(f"Fair trades with {team_b.team_name} (1-2 players each way)")
        self._tl_options = trade.balance(league, team_a, team_b, self.tl_send, self.tl_get)
        short = lambda names: ", ".join(f"{n.split()[0][0]}. {' '.join(n.split()[1:])}" if " " in n else n
                                        for n in names)  # "J. Johnson, L. James" fits the columns
        rows = [(short(o["send_names"]), short(o["get_names"]), f"{o['da']:+.1f}", f"{o['db']:+.1f}")
                for o in self._tl_options]
        tags = [("hot",) if min(o["da"], o["db"]) >= -0.05 else () for o in self._tl_options]
        self.tl_sugg.set_rows(rows or [("No trade changes either lineup.", "", "", "")], tags,
                              [str(i) for i in range(len(rows))] if rows else None)

    def _trade_apply_suggestion(self, event):
        iid = self.tl_sugg.tree.identify_row(event.y)
        if not iid or not iid.isdigit() or int(iid) >= len(self._tl_options):
            return
        o = self._tl_options[int(iid)]
        self.tl_send, self.tl_get = set(o["send"]), set(o["get"])
        self._trade_render()

    # ------------------------------------------------------------ compare

    def _compare_current(self):
        name = self.player_q.get().strip()
        self.cmp_q[0].delete(0, "end")
        self.cmp_q[0].insert(0, name)
        self.cmp_q[1].delete(0, "end")
        self.tabs.select(self.compare_tab)
        self.cmp_q[1].focus_set()

    def run_compare(self):
        names = [e.get().strip() for e in self.cmp_q]
        if not self.data or not all(names):
            self.cmp_chart.show_message("Type two players (names or nicknames), then press Compare.")
            return
        league = self.data["league"]
        self.cmp_chart.show_message(f"Looking up {names[0]} and {names[1]}...")

        def work():
            found = []
            for n in names:
                try:
                    found.append(stats.find_player(league, n))
                except SystemExit as e:  # not found / ambiguous: explain in the chart area
                    return str(e)
            return found
        self.run(work, self._compare_found)

    def _compare_found(self, found):
        if isinstance(found, str):
            self.cmp_chart.show_message(found)
            return
        self._cmp = found
        for i, (p, team) in enumerate(found):
            photo, blank, text = self.cmp_cards[i]
            owner = team.team_name if team else "free agent"
            text.set(f"{p.name}\n{p.position}, {p.proTeam}  ·  {status_text(p)}\n{owner}")
            photo.image = blank
            photo.configure(image=blank)
            if not self.demo:
                pid = p.playerId
                self.run(lambda pid=pid, i=i: (i, pid, headshot(pid)), self._compare_photo)
        self._draw_compare()

    def _compare_photo(self, result):
        i, pid, path = result
        if not self._cmp or self._cmp[i][0].playerId != pid or path is None:
            return  # a different pair was compared meanwhile, or no photo
        try:
            img = tk.PhotoImage(file=str(path)).subsample(4)  # 600x436 -> 150x109
        except tk.TclError:
            return
        photo = self.cmp_cards[i][0]
        photo.image = img  # keep a reference or Tk drops the image
        photo.configure(image=img)

    def _draw_compare(self):
        if not self._cmp or not self.data:
            return
        league, rules, year = self.data["league"], self.data["rules"], self.data["league"].year
        pick = self.cmp_split.current()
        split_key = SPLITS[pick - 1] if pick > 0 else None
        mode = self.cmp_mode.get()
        axes = axes_for(mode, rules)
        players = [(p.name, profile(p, year, rules, split_key, mode)) for p, _ in self._cmp]
        # Scale each axis to every rostered player in the league (plus these two).
        pool = [profile(p, year, rules, split_key, mode) for tm in league.teams for p in tm.roster]
        pool += [v for _, v in players]
        ranges = {}
        for key, _ in axes:
            vals = [v.get(key, 0) for v in pool]
            lo, hi = min(vals), max(vals)
            ranges[key] = (lo, hi if hi > lo else lo + 1)
        inverted = {"TO"} if mode == "raw" else set()
        title = "Fantasy points each stat earns per game" if mode == "fantasy" else "Per-game stats"
        self.cmp_chart.show(axes, players, ranges, inverted, title,
                            note=f"{self.cmp_split.get()}  ·  each axis spans every rostered player in your league"
                                 "  ·  up is always better for fantasy  ·  hover an axis to compare")
        self._fill_compare_table(axes, players, rules, mode)

    def _fill_compare_table(self, axes, players, rules, mode):
        (n1, v1), (n2, v2) = players
        short = [n1.split()[-1], n2.split()[-1]]
        if short[0] == short[1]:
            short = [n1, n2]
        self.cmp_table.tree.heading("player 1", text=short[0])
        self.cmp_table.tree.heading("player 2", text=short[1])
        rows = []
        for key, label in axes:
            a, b = v1.get(key, 0), v2.get(key, 0)
            if mode == "fantasy" or key == "FP":
                gap = a - b  # already in fantasy points
            elif key in rules:
                gap = (a - b) * rules[key]  # raw stat difference -> fantasy points
            else:
                gap = None  # FG%, FT%, minutes don't score directly
            if gap is None:
                edge = "   -"
            elif abs(gap) < 0.05:
                edge = "   even"
            else:
                edge = f"   {short[0] if gap > 0 else short[1]} +{abs(gap):.1f}"
            rows.append((label, f"{a:.1f}", f"{b:.1f}", edge))
        self.cmp_table.set_rows(rows)

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
