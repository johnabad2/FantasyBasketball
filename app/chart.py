"""Per-game bar chart for one stat: bars above the player's average in blue, below in red,
a dashed average line, best/worst games labeled, and a hover tooltip for every game."""
import math
import tkinter as tk

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#66655f"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
ABOVE = "#2a78d6"  # blue/red diverging pair: validated for color-vision deficiency
BELOW = "#d9493f"
HOVER_WASH = "#eef3fb"
FONT = "Segoe UI"


def nice_ticks(lo, hi, target=5):
    """Round axis ticks (steps of 1, 2 or 5 x 10^k) covering lo..hi."""
    span = max(hi - lo, 1e-9)
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks = [round(start, 6)]
    while ticks[-1] < hi:  # always end at or above the top value, so the tallest bar fits
        ticks.append(round(ticks[-1] + step, 6))
    return ticks


def fmt(v):
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


class GameChart(tk.Canvas):
    PAD_L, PAD_R, PAD_T, PAD_B = 48, 16, 34, 60

    def __init__(self, parent, **kw):
        super().__init__(parent, background=SURFACE, highlightthickness=1, highlightbackground="#e3e3e0", **kw)
        self.games, self.stat, self.title, self.note = [], "FP", "", ""
        self.fewer_is_better = False
        self.message = "Pick a player to see their game-by-game chart."
        self.bars = []  # (x0, x1, game) for hover hit-testing
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def show(self, games, stat, title, note="", fewer_is_better=False):
        """fewer_is_better: for stats that cost fantasy points (TO, FGA, FTA), swap the colors so
        blue still means a good game for you and red a bad one."""
        self.games, self.stat, self.title, self.note, self.message = games, stat, title, note, ""
        self.fewer_is_better = fewer_is_better
        self.draw()

    def show_message(self, text):
        self.games, self.message = [], text
        self.draw()

    # ------------------------------------------------------------ drawing

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        if not self.games:
            self.create_text(w / 2, h / 2, text=self.message, fill=MUTED, font=(FONT, 11))
            return
        vals = [g[self.stat] for g in self.games]
        avg = sum(vals) / len(vals)
        ticks = nice_ticks(min(0, min(vals)), max(vals) * 1.08 or 1)
        lo, hi = ticks[0], ticks[-1]
        x0, x1 = self.PAD_L, w - self.PAD_R
        y0, y1 = self.PAD_T, h - self.PAD_B
        y = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)

        # title + legend (identity is never color alone: the legend names both colors)
        self.create_text(x0, 14, text=self.title, anchor="w", fill=INK, font=(FONT, 11, "bold"))
        lx = w - self.PAD_R
        above_c, below_c = (BELOW, ABOVE) if self.fewer_is_better else (ABOVE, BELOW)
        for label, color in (("below average", below_c), ("above average", above_c)):
            t = self.create_text(lx, 14, text=label, anchor="e", fill=INK_2, font=(FONT, 9))
            bx = self.bbox(t)[0] - 6
            self.create_rectangle(bx - 10, 9, bx, 19, fill=color, outline="")
            lx = bx - 22

        # recessive grid + y labels
        for t in ticks:
            self.create_line(x0, y(t), x1, y(t), fill=GRID)
            self.create_text(x0 - 8, y(t), text=fmt(t), anchor="e", fill=MUTED, font=(FONT, 9))
        self.create_line(x0, y(0), x1, y(0), fill=AXIS)

        # bars: 2px surface gap between neighbours, at least 2px wide
        n = len(self.games)
        slot = (x1 - x0) / n
        gap = 2 if slot > 5 else 1 if slot > 3 else 0
        self.bars = []
        bar_w = min(slot - gap, 36)  # thin marks: cap the width when there are only a few games
        for i, (g, v) in enumerate(zip(self.games, vals)):
            cx = x0 + (i + 0.5) * slot
            bx0, bx1 = cx - bar_w / 2, cx + bar_w / 2
            self.bars.append((x0 + i * slot, x0 + (i + 1) * slot, g, v))
            if v != 0:
                self.create_rectangle(bx0, min(y(v), y(0)), max(bx1, bx0 + 2), max(y(v), y(0)),
                                      fill=above_c if v >= avg else below_c, outline="")

        # average line, labeled at the right end
        self.create_line(x0, y(avg), x1, y(avg), fill=INK_2, dash=(5, 3), width=2)
        t = self.create_text(x1 - 6, y(avg) - 11, text=f"avg {avg:.1f}", anchor="e", fill=INK, font=(FONT, 9, "bold"))
        bx = self.bbox(t)
        self.tag_lower(self.create_rectangle(bx[0] - 4, bx[1] - 1, bx[2] + 4, bx[3] + 1, fill=SURFACE,
                                             outline=AXIS), t)

        # selective direct labels: best and worst game only
        best = max(range(n), key=lambda i: vals[i])
        worst = min(range(n), key=lambda i: vals[i])
        for i, tag in ((best, "best"), (worst, "worst")):
            if i == best and tag == "worst":
                continue
            cx = x0 + (i + 0.5) * slot
            ty = y(vals[i]) - 9 if vals[i] >= 0 else y(vals[i]) + 9
            self.create_text(cx, ty, text=fmt(vals[i]), fill=INK_2, font=(FONT, 9, "bold"))

        # x labels: each game when few, otherwise the first game of each month
        last_month = None
        months = []  # (x, label) of the first game in each month
        for i, g in enumerate(self.games):
            cx = x0 + (i + 0.5) * slot
            if n <= 16:
                self.create_text(cx, y1 + 12, text=g["date"].strftime("%d/%m"), fill=MUTED, font=(FONT, 8))
                self.create_text(cx, y1 + 25, text=g["opp"].split()[-1], fill=MUTED, font=(FONT, 8))
            elif g["date"].month != last_month and cx < x1 - 28:  # skip a label that would hang off the edge
                last_month = g["date"].month
                months.append((cx, g["date"].strftime("%b")))
        # if a player missed most of a month, two month starts land close together: keep the later one
        for (cx, label), nxt in zip(months, months[1:] + [(None, None)]):
            if nxt[0] is not None and nxt[0] - cx < 34:
                continue
            self.create_line(cx, y1, cx, y1 + 4, fill=AXIS)
            self.create_text(cx, y1 + 14, text=label, anchor="w", fill=MUTED, font=(FONT, 9))
        if self.note:
            self.create_text(x0, h - 11, text=self.note, anchor="w", fill=MUTED, font=(FONT, 8))
        self.avg = avg

    # ------------------------------------------------------------ hover

    def _hover(self, event):
        self.delete("hover")
        hit = next((b for b in self.bars if b[0] <= event.x < b[1]), None)
        if not hit or not (self.PAD_T <= event.y <= self.winfo_height() - self.PAD_B):
            return
        bx0, bx1, g, v = hit
        # column wash behind the hovered game (hit target is the full column, not just the bar)
        wash = self.create_rectangle(bx0, self.PAD_T, bx1, self.winfo_height() - self.PAD_B,
                                     fill=HOVER_WASH, outline="", tags="hover")
        self.tag_lower(wash)
        diff = v - self.avg
        playoff = "  ·  playoffs" if g.get("playoffs") else ""
        lines = [f"{g['date']:%a %d %b}  ·  {g['opp']}  ·  {g['result']}{playoff}",
                 f"{fmt(v)} {self.stat if self.stat != 'FP' else 'fantasy pts'}   ({diff:+.1f} vs avg)",
                 f"{fmt(g['PTS'])} pts  {fmt(g['REB'])} reb  {fmt(g['AST'])} ast  {fmt(g['STL'])} stl  "
                 f"{fmt(g['BLK'])} blk  {fmt(g['TO'])} to  ·  {fmt(g['MIN'])} min"]
        tx = event.x + 14
        t = self.create_text(tx, event.y, text="\n".join(lines), anchor="nw", fill=INK,
                             font=(FONT, 9), tags="hover")
        bx = self.bbox(t)
        if bx[2] > self.winfo_width() - 6:  # flip to the left near the right edge
            self.move(t, -(bx[2] - bx[0]) - 28, 0)
            bx = self.bbox(t)
        if bx[3] > self.winfo_height() - 6:
            self.move(t, 0, -(bx[3] - self.winfo_height()) - 10)
            bx = self.bbox(t)
        box = self.create_rectangle(bx[0] - 8, bx[1] - 6, bx[2] + 8, bx[3] + 6, fill="white",
                                    outline="#d6d5cf", tags="hover")
        self.tag_raise(t, box)
