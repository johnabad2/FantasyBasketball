"""The Activity tab's feed: trades as cards (like ESPN's League Assistant), adds/drops and chat as
compact rows, grouped by day and scrollable."""
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from theme import C as T

FONT = "Segoe UI"
MOVE_COLOURS = {"added": T["green"], "fa added": T["green"], "waiver added": T["green"], "dropped": T["red"]}
AVATAR_COLOURS = ["#2f5a9e", "#b4532a", "#2e7d5b", "#7b4fb0", "#a8326e", "#3d7f99", "#8a6d1f", "#5b6b8c"]
MAX_ITEMS = 150


def initials(name):
    words = [w for w in name.replace("'s", "").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


class ActivityFeed(ttk.Frame):
    """items: [(when, kind, data)] with kind "trade" (a feed.trades() entry), "move" (team, action,
    player name) or "chat" (a chat.league_chat() entry). on_player(name) opens a player;
    load_photo(pid, done) fetches a headshot path in the background and calls done(path)."""

    def __init__(self, master, on_player, load_photo):
        super().__init__(master)
        self.on_player, self.load_photo = on_player, load_photo
        self.photos = {}  # player id -> PhotoImage (Tk drops images nobody holds)
        self.canvas = tk.Canvas(self, bg=T["page"], highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=T["page"])
        win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        # the wheel scrolls the feed only while the mouse is over it
        self.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(int(-e.delta / 120), "units")

    # ------------------------------------------------------------ building

    def show(self, items):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)
        if not items:
            self._label(self.inner, "Nothing here yet.", fg=T["muted"]).pack(anchor="w", padx=4, pady=8)
            return
        day = None
        for when, kind, data in items[:MAX_ITEMS]:
            if when.date() != day:
                day = when.date()
                self._label(self.inner, f"{when:%A %d %b}", fg=T["muted"], size=10, bold=True).pack(
                    anchor="w", padx=4, pady=(14 if self.inner.winfo_children() else 2, 4))
            {"trade": self._trade, "move": self._move, "chat": self._chat}[kind](when, data)

    def _label(self, parent, text, fg=None, bg=None, size=11, bold=False, **kw):
        return tk.Label(parent, text=text, fg=fg or T["text"], bg=bg or parent["bg"],
                        font=(FONT, size, "bold" if bold else "normal"), **kw)

    def _player_link(self, parent, name, size=11, bold=True):
        lbl = self._label(parent, name, size=size, bold=bold, cursor="hand2")
        lbl.bind("<Button-1>", lambda e: self.on_player(name))
        lbl.bind("<Enter>", lambda e: lbl.configure(fg=T["accent"]))
        lbl.bind("<Leave>", lambda e: lbl.configure(fg=T["text"]))
        return lbl

    def _trade(self, when, t):
        card = tk.Frame(self.inner, bg=T["card"], highlightthickness=1, highlightbackground=T["border"],
                        padx=16, pady=12)
        card.pack(fill="x", padx=4, pady=4)
        head = tk.Frame(card, bg=T["card"])
        head.pack(fill="x")
        title = "Trade Accepted" if t["kind"] == "accepted" else "Trade Completed"
        self._label(head, f"⇄  {title}", fg=T["accent"], size=12, bold=True).pack(side="left")
        self._label(head, f"{when:%d %b %H:%M}", fg=T["muted"], size=10).pack(side="right")
        if t["process_at"]:
            if t["process_at"] > datetime.now():
                note = f"Goes through {t['process_at']:%a %d %b %H:%M} unless it's vetoed"
            else:
                note = "Review period over: players move at ESPN's next update (or it was vetoed)"
            self._label(card, note, fg=T["muted"], size=10).pack(anchor="w", pady=(0, 6))
        tk.Frame(card, bg=T["border"], height=1).pack(fill="x", pady=(4, 10))
        row = tk.Frame(card, bg=T["card"])
        row.pack(fill="x")
        for i, side in enumerate(t["sides"]):
            self._side(row, side).grid(row=0, column=i, sticky="nw", padx=(0, 48))

    def _side(self, parent, side):
        f = tk.Frame(parent, bg=T["card"])
        top = tk.Frame(f, bg=T["card"])
        top.pack(anchor="w")
        av = tk.Canvas(top, width=40, height=40, bg=T["card"], highlightthickness=0)
        colour = AVATAR_COLOURS[sum(map(ord, side["team"])) % len(AVATAR_COLOURS)]
        av.create_oval(2, 2, 38, 38, fill=colour, outline="")
        av.create_text(20, 20, text=initials(side["team"]), fill="#ffffff", font=(FONT, 11, "bold"))
        av.pack(side="left", padx=(0, 10))
        names = tk.Frame(top, bg=T["card"])
        names.pack(side="left")
        self._label(names, side["team"].strip(), bold=True).pack(anchor="w")
        self._label(names, side["manager"], fg=T["muted"], size=10).pack(anchor="w")
        for p in side["receives"]:
            self._received(f, p)
        for p in side["drops"]:
            line = tk.Frame(f, bg=T["card"])
            line.pack(anchor="w", pady=(4, 0))
            self._label(line, "Drops", fg=T["red"], size=10).pack(side="left", padx=(50, 6))
            self._player_link(line, p["name"], size=10, bold=False).pack(side="left")
        return f

    def _received(self, parent, p):
        line = tk.Frame(parent, bg=T["card"])
        line.pack(anchor="w", pady=(8, 0))
        photo = tk.Label(line, bg=T["photo_bg"], width=9, height=3)  # text units until the photo arrives
        photo.pack(side="left", padx=(50, 10))
        words = tk.Frame(line, bg=T["card"])
        words.pack(side="left")
        self._label(words, "Receives", fg=T["muted"], size=10).pack(anchor="w")
        self._player_link(words, p["name"]).pack(anchor="w")
        self._label(words, f"{p['pro']} {p['pos']}".strip(), fg=T["muted"], size=10).pack(anchor="w")
        self._set_photo(photo, p["id"])

    def _set_photo(self, label, pid):
        if pid in self.photos:
            label.configure(image=self.photos[pid], width=75, height=55)
            return

        def done(path):
            if path is None or not label.winfo_exists():
                return
            try:
                self.photos[pid] = tk.PhotoImage(file=str(path)).subsample(8)  # 600x436 -> 75x55
            except tk.TclError:
                return
            label.configure(image=self.photos[pid], width=75, height=55)
        self.load_photo(pid, done)

    def _row(self, when):
        row = tk.Frame(self.inner, bg=T["page"])
        row.pack(fill="x", padx=4, pady=1)
        self._label(row, f"{when:%H:%M}", fg=T["muted"], size=10, width=6, anchor="w").pack(side="left", anchor="n")
        return row

    def _move(self, when, m):
        team, action, player = m
        row = self._row(when)
        self._label(row, team.strip(), width=28, anchor="w").pack(side="left")
        self._label(row, action.lower(), fg=MOVE_COLOURS.get(action.lower(), T["muted"]),
                    width=13, anchor="w").pack(side="left")
        self._player_link(row, player, bold=False).pack(side="left")

    def _chat(self, when, m):
        row = self._row(when)
        who = f"{m['author']}" + (f"  ·  {m['team'].strip()}" if m["team"] else "")
        self._label(row, who, fg=T["chat"], bold=True).pack(side="left", anchor="n", padx=(0, 10))
        self._label(row, m["text"], fg=T["chat"], wraplength=900, justify="left", anchor="w").pack(
            side="left", anchor="n", fill="x")
