"""Compare tab pieces: per-game profiles for a player, and a parallel-coordinates chart.

Every axis is scaled to the range of all rostered players in the league, so "high on the
axis" means high for the league, not just higher than the other player. Axes are oriented
so up is always better for fantasy (turnovers are flipped)."""
import tkinter as tk

from stats import fp_per_game, season_or_projection, split_of

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#66655f"
AXIS = "#c3c2b7"
HOVER_WASH = "#eef3fb"
SERIES = ["#2a78d6", "#eb6834"]  # categorical slots 1 & 2 (blue, orange), in fixed order
FONT = "Segoe UI"

RAW_AXES = [("FP", "Fantasy pts"), ("PTS", "PTS"), ("REB", "REB"), ("AST", "AST"), ("STL", "STL"),
            ("BLK", "BLK"), ("3PM", "3PM"), ("TO", "TO"), ("FG%", "FG%"), ("FT%", "FT%"), ("MPG", "MIN")]
FANTASY_ORDER = ["PTS", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"]
LOWER_IS_BETTER = {"TO"}  # raw-stats view only; in the fantasy view every axis is points, so up is better


def pick_split(player, year, split_key):
    if split_key is None:
        return season_or_projection(player, year)[0]
    return split_of(player, year, split_key)


def profile(player, year, rules, split_key, mode):
    """{axis key: value} for one player. mode 'raw' = per-game stats, 'fantasy' = fantasy points
    each stat earns per game under the league's scoring."""
    s = pick_split(player, year, split_key)
    avg = s.get("avg") or {}
    out = {"FP": fp_per_game(s, rules)}
    if mode == "raw":
        for key, _ in RAW_AXES[1:]:
            v = avg.get(key) or 0
            out[key] = v * 100 if key in ("FG%", "FT%") and v <= 1 else v
    else:
        out.update({stat: (avg.get(stat) or 0) * pts for stat, pts in rules.items()})
    return out


def axes_for(mode, rules):
    """[(key, label)] in display order."""
    if mode == "raw":
        return RAW_AXES
    keys = [s for s in FANTASY_ORDER if s in rules] + [s for s in rules if s not in FANTASY_ORDER]
    return [("FP", "Total")] + [(s, f"{s} ({rules[s]:+g})") for s in keys]


def fmt(v, key=""):
    if key in ("FG%", "FT%"):
        return f"{v:.0f}%"
    return f"{v:.1f}"


class ParallelChart(tk.Canvas):
    PAD_L, PAD_R, PAD_T, PAD_B = 64, 52, 56, 78

    def __init__(self, parent, **kw):
        super().__init__(parent, background=SURFACE, highlightthickness=1, highlightbackground="#e3e3e0", **kw)
        self.axes, self.players, self.ranges, self.inverted = [], [], {}, set()
        self.title, self.note = "", ""
        self.message = "Pick two players above and press Compare."
        self.axis_x = []
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def show(self, axes, players, ranges, inverted, title, note=""):
        """players: [(name, {key: value})] - at most two, colored by SERIES in order."""
        self.axes, self.players, self.ranges, self.inverted = axes, players, ranges, inverted
        self.title, self.note, self.message = title, note, ""
        self.draw()

    def show_message(self, text):
        self.players, self.message = [], text
        self.draw()

    def _y(self, key, v):
        lo, hi = self.ranges[key]
        t = (v - lo) / (hi - lo) if hi > lo else 0.5
        if key in self.inverted:
            t = 1 - t
        h = self.winfo_height()
        return (h - self.PAD_B) - t * (h - self.PAD_B - self.PAD_T)

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 80:
            return
        if not self.players:
            self.create_text(w / 2, h / 2, text=self.message, fill=MUTED, font=(FONT, 11))
            return
        y0, y1 = self.PAD_T, h - self.PAD_B
        n = len(self.axes)
        step = (w - self.PAD_L - self.PAD_R) / max(n - 1, 1)
        self.axis_x = [self.PAD_L + i * step for i in range(n)]

        # title + legend (line + marker shape per player, so identity isn't color alone)
        self.create_text(self.PAD_L - 30, 16, text=self.title, anchor="w", fill=INK, font=(FONT, 11, "bold"))
        lx = w - 16
        for i, (name, _) in reversed(list(enumerate(self.players))):
            t = self.create_text(lx, 16, text=name, anchor="e", fill=INK_2, font=(FONT, 10))
            bx = self.bbox(t)[0] - 8
            self.create_line(bx - 26, 16, bx, 16, fill=SERIES[i], width=2)
            self._marker(bx - 13, 16, i)
            lx = bx - 40

        # axes: hairline, range ends, label underneath
        for (key, label), x in zip(self.axes, self.axis_x):
            self.create_line(x, y0, x, y1, fill=AXIS, width=1)
            lo, hi = self.ranges[key]
            top, bottom = (lo, hi) if key in self.inverted else (hi, lo)
            self.create_text(x, y0 - 10, text=fmt(top, key), fill=MUTED, font=(FONT, 8))
            self.create_text(x, y1 + 10, text=fmt(bottom, key), fill=MUTED, font=(FONT, 8))
            self.create_text(x, y1 + 28, text=label, fill=INK, font=(FONT, 9, "bold"))
            if key in self.inverted:
                self.create_text(x, y1 + 43, text="flipped: fewer is up", fill=MUTED, font=(FONT, 8))

        # one 2px line per player, then markers with a surface ring, then value labels
        for i, (_, vals) in enumerate(self.players):
            pts = []
            for (key, _), x in zip(self.axes, self.axis_x):
                pts += [x, self._y(key, vals.get(key, 0))]
            self.create_line(*pts, fill=SERIES[i], width=2)
        for i, (_, vals) in enumerate(self.players):
            for (key, _), x in zip(self.axes, self.axis_x):
                y = self._y(key, vals.get(key, 0))
                self._marker(x, y, i)
                # player 1 labels sit left of the axis, player 2 right, so they never collide
                side, anchor = (-9, "e") if i == 0 else (9, "w")
                if (i == 0 and x - self.PAD_L < 20) or (i == 1 and w - self.PAD_R - x < 20 and n > 1):
                    side, anchor = (-side, "w" if anchor == "e" else "e")
                    y += 14 if i == 0 else -14
                self.create_text(x + side, y, text=fmt(vals.get(key, 0), key), anchor=anchor,
                                 fill=INK_2, font=(FONT, 8, "bold"))
        if self.note:
            self.create_text(self.PAD_L - 30, h - 8, text=self.note, anchor="w", fill=MUTED, font=(FONT, 8))

    def _marker(self, x, y, i):
        r = 5
        shape = self.create_oval if i == 0 else self.create_rectangle  # circle vs square
        shape(x - r, y - r, x + r, y + r, fill=SERIES[i], outline=SURFACE, width=2)

    def _hover(self, event):
        self.delete("hover")
        if not self.players or not self.axis_x:
            return
        step = (self.axis_x[1] - self.axis_x[0]) if len(self.axis_x) > 1 else 100
        i = min(range(len(self.axis_x)), key=lambda k: abs(self.axis_x[k] - event.x))
        if abs(self.axis_x[i] - event.x) > step / 2:
            return
        key, label = self.axes[i]
        x = self.axis_x[i]
        wash = self.create_rectangle(x - step / 2, self.PAD_T - 20, x + step / 2,
                                     self.winfo_height() - self.PAD_B + 50, fill=HOVER_WASH, outline="", tags="hover")
        self.tag_lower(wash)
        vals = [(name, v.get(key, 0)) for name, v in self.players]
        lines = [label] + [f"{name}: {fmt(v, key)}" for name, v in vals]
        if len(vals) == 2:
            (n1, v1), (n2, v2) = vals
            better = (v1 < v2) if key in self.inverted else (v1 > v2)
            diff = abs(v1 - v2)
            shown = f"{diff:.1f}%" if key in ("FG%", "FT%") else f"{diff:.1f}"
            if diff < 0.05:
                lines.append("edge: even")
            else:
                lead = n1 if better else n2
                lines.append(f"edge: {lead.split()[-1]} by {shown}")
        t = self.create_text(event.x + 14, event.y, text="\n".join(lines), anchor="nw", fill=INK,
                             font=(FONT, 9), tags="hover")
        bx = self.bbox(t)
        if bx[2] > self.winfo_width() - 6:
            self.move(t, -(bx[2] - bx[0]) - 28, 0)
            bx = self.bbox(t)
        box = self.create_rectangle(bx[0] - 8, bx[1] - 6, bx[2] + 8, bx[3] + 6, fill="white",
                                    outline="#d6d5cf", tags="hover")
        self.tag_raise(t, box)
