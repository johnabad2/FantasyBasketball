"""Standings over the season: each team's place at the end of every week (a bump chart). Your team
is the highlighted line; everyone else is grey, named at the right. Hover a week for that week's table."""
import tkinter as tk
import tkinter.font as tkfont

from chart import AXIS, FONT, GRID, HOVER_WASH, INK, INK_2, MUTED, SURFACE
from theme import C, GLOW, blend

ME = C["accent"]
OTHER = blend(C["chart_muted"], SURFACE, .45)


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


class RankChart(tk.Canvas):
    PAD_L, PAD_T, PAD_B = 58, 40, 50

    def __init__(self, parent, **kw):
        super().__init__(parent, background=SURFACE, highlightthickness=1, highlightbackground=C["chart_border"], **kw)
        self.weeks, self.names, self.me = [], {}, None
        self.cols = []  # (x0, x1, week) hover columns
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def show(self, weeks, names, me):
        """weeks: stats.weekly_standings(); names: {team id: team name}; me: your team id."""
        self.weeks, self.names, self.me = weeks, names, me
        self.draw()

    @staticmethod
    def _order(week):
        """Team ids best first; ties keep a stable order so the lines don't sit on top of each other."""
        return sorted(week["totals"], key=lambda tid: (-week["totals"][tid], tid))

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        if not self.weeks:
            self.create_text(w / 2, h / 2, text="No games played yet.", fill=MUTED, font=(FONT, 11))
            return
        n_teams = len(self.weeks[-1]["totals"])
        bold = tkfont.Font(family=FONT, size=10, weight="bold")
        label_w = max(bold.measure(f"10. {nm.strip()}    00,000") for nm in self.names.values())
        x0, x1 = self.PAD_L, w - label_w - 24
        y0, y1 = self.PAD_T + 8, h - self.PAD_B
        step_y = (y1 - y0) / max(n_teams - 1, 1)
        y = lambda pos: y0 + pos * step_y  # pos 0 = first place
        n = len(self.weeks)
        slot = (x1 - x0) / n
        x = lambda i: x0 + (i + 0.5) * slot
        self.cols = [(x0 + i * slot, x0 + (i + 1) * slot, wk) for i, wk in enumerate(self.weeks)]

        title = self.create_text(x0 - 40, 16, text="Rank by week", anchor="w", fill=INK, font=(FONT, 11, "bold"))
        self.create_text(self.bbox(title)[2] + 14, 17, text="hover a week for its table", anchor="w", fill=MUTED,
                         font=(FONT, 9))
        for pos in range(n_teams):  # recessive guides, one per place
            self.create_line(x0, y(pos), x1, y(pos), fill=GRID)
            self.create_text(x0 - 10, y(pos), text=ordinal(pos + 1), anchor="e", fill=MUTED, font=(FONT, 9))
        for i, wk in enumerate(self.weeks):
            self.create_text(x(i), y1 + 18, text=f"Week {wk['week']}", fill=MUTED, font=(FONT, 9))
            self.create_text(x(i), y1 + 32, text=f"{wk['start']:%d %b}", fill=MUTED, font=(FONT, 8))

        # position of every team in every week
        points = {tid: [] for tid in self.weeks[-1]["totals"]}
        for i, wk in enumerate(self.weeks):
            for pos, tid in enumerate(self._order(wk)):
                points.setdefault(tid, []).append((x(i), y(pos)))
        # everyone else first, your line on top
        for tid in sorted(points, key=lambda t: t == self.me):
            pts, mine = points[tid], tid == self.me
            colour, width, r = (ME, 3, 6) if mine else (OTHER, 2, 4.5)
            if len(pts) > 1:
                flat = [c for p in pts for c in p]
                if mine and GLOW:
                    for wdt, fade in ((9, .86), (6, .72)):
                        self.create_line(*flat, fill=blend(colour, SURFACE, fade), width=wdt, capstyle="round",
                                         joinstyle="round")
                self.create_line(*flat, fill=colour, width=width, capstyle="round", joinstyle="round")
            for cx, cy in pts:
                if mine and GLOW:
                    self.create_oval(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3, outline="",
                                     fill=blend(colour, SURFACE, .72))
                self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=colour, outline=SURFACE, width=2)

        # direct labels at the right: this week's order, with points so far
        last = self.weeks[-1]
        for pos, tid in enumerate(self._order(last)):
            mine = tid == self.me
            label = f"{last['ranks'][tid]}. {self.names.get(tid, '?').strip()}"
            t = self.create_text(x1 + 18, y(pos), text=label, anchor="w", fill=INK if mine else INK_2,
                                 font=(FONT, 10, "bold" if mine else "normal"))
            self.create_text(w - 12, y(pos), text=f"{last['totals'][tid]:,.0f}", anchor="e",
                             fill=INK if mine else MUTED, font=(FONT, 10, "bold" if mine else "normal"))

    def _hover(self, event):
        self.delete("hover")
        hit = next((c for c in self.cols if c[0] <= event.x < c[1]), None)
        if not hit or not (self.PAD_T <= event.y <= self.winfo_height() - self.PAD_B):
            return
        cx0, cx1, wk = hit
        self.tag_lower(self.create_rectangle(cx0, self.PAD_T, cx1, self.winfo_height() - self.PAD_B,
                                             fill=HOVER_WASH, outline="", tags="hover"))
        rows = [f"Week {wk['week']}  ·  {wk['start']:%d %b} – {wk['end']:%d %b}", ""]
        for tid in self._order(wk):
            you = "   (you)" if tid == self.me else ""
            rows.append(f"{wk['ranks'][tid]:>2}.  {self.names.get(tid, '?').strip():<24} "
                        f"{wk['totals'][tid]:>8,.1f}   +{wk['gained'][tid]:,.1f} this week{you}")
        t = self.create_text(event.x + 16, event.y, text="\n".join(rows), anchor="nw", fill=INK,
                             font=("Consolas", 9), tags="hover")
        bx = self.bbox(t)
        if bx[2] > self.winfo_width() - 6:
            self.move(t, -(bx[2] - bx[0]) - 32, 0)
        bx = self.bbox(t)
        if bx[3] > self.winfo_height() - 6:
            self.move(t, 0, -(bx[3] - self.winfo_height()) - 10)
        if bx[1] < 6:
            self.move(t, 0, 6 - bx[1])
        bx = self.bbox(t)
        self.tag_raise(t, self.create_rectangle(bx[0] - 10, bx[1] - 8, bx[2] + 10, bx[3] + 8, fill=C["tooltip_bg"],
                                                outline=C["tooltip_border"], tags="hover"))
