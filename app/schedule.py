"""Schedule planner: how many games every NBA team plays per week, and how many NBA games there are
each day. More games = more fantasy points; light days are when streamers fill empty slots.

Weeks run Monday to Sunday (the current week counts from today). Read from the fantasy league's own
NBA schedule (scoring periods = days)."""
import tkinter as tk
from datetime import date, timedelta

from chart import FONT, GRID, INK, INK_2, MUTED, SURFACE
from theme import C, blend

BLUE = C["above"]  # one hue, light -> dark = few -> many games (sequential)
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


class ScheduleGrid(tk.Canvas):
    """Top: NBA games per day for two weeks. Below: games per team per week as a heat grid (darker =
    more games), with your players and the best free agents on each team. Hover a cell for dates."""
    LEFT, RIGHT_W = 70, 560

    def __init__(self, parent, **kw):
        super().__init__(parent, background=SURFACE, highlightthickness=1, highlightbackground=C["chart_border"], **kw)
        self.data, self.cells = None, []
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def show(self, data, abbrev, mine, free_agents):
        """abbrev: {team id: 'LAL'}; mine / free_agents: {team id: [short names]}."""
        self.data, self.abbrev, self.mine, self.fas = data, abbrev, mine, free_agents
        self.draw()

    def _fill(self, n):
        """One hue, brighter = more games (on the dark surface): 0-1 dim, 2, 3, 4+ brightest."""
        return blend(SURFACE, BLUE, {0: 0.05, 1: 0.15, 2: 0.3, 3: 0.55}.get(n, 0.95))

    def _title(self, x, y, title, note):
        t = self.create_text(x, y, text=title, anchor="w", fill=INK, font=(FONT, 11, "bold"))
        self.create_text(self.bbox(t)[2] + 14, y + 1, text=note, anchor="w", fill=MUTED, font=(FONT, 9))

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 300 or h < 200:
            return
        if not self.data:
            self.create_text(w / 2, h / 2, text="No NBA schedule (demo mode).", fill=MUTED, font=(FONT, 11))
            return
        d = self.data
        # --- games per day strip
        self._title(14, 16, "NBA games per day", "amber = light day: fewer of your players play, so streamers "
                                                   "have open slots to fill")
        top, bar_h = 36, 46
        n_days = len(d["days"])
        span = (w - 28) / n_days
        most = max(n for _, n in d["days"]) or 1
        for i, (day, n) in enumerate(d["days"]):
            x0 = 14 + i * span + 3
            x1 = x0 + span - 6
            light = n <= 5
            colour = C["amber"] if light else BLUE
            y1 = top + bar_h
            y0 = y1 - max(bar_h * n / most, 2)
            self.create_rectangle(x0, y0, x1, y1, fill=colour, outline="")
            self.create_text((x0 + x1) / 2, y0 - 8, text=str(n), fill=INK if light else INK_2, font=(FONT, 9, "bold"))
            self.create_text((x0 + x1) / 2, y1 + 10, text=f"{day:%a}", fill=INK_2 if light else MUTED, font=(FONT, 8))
            self.create_text((x0 + x1) / 2, y1 + 22, text=f"{day:%d %b}", fill=MUTED, font=(FONT, 8))

        # --- team x week grid
        gtop = top + bar_h + 48
        self._title(14, gtop, "Games per team per week", "brighter = more games  ·  sorted by the next two "
                                                         "weeks  ·  hover a cell for the dates")
        head_y = gtop + 22
        teams = sorted(d["teams"], key=lambda t: (-sum(len(g) for g in d["teams"][t][:2]), self.abbrev.get(t, "")))
        rows_top = head_y + 18
        row_h = max(min((h - rows_top - 8) / max(len(teams), 1), 26), 12)
        x0 = self.LEFT
        grid_w = w - x0 - self.RIGHT_W - 20
        col_w = grid_w / len(d["weeks"])
        for j, (start, end) in enumerate(d["weeks"]):
            label = "this week" if j == 0 else f"wk of {start:%d %b}"
            self.create_text(x0 + (j + 0.5) * col_w, head_y, text=label, fill=MUTED, font=(FONT, 8))
        rx = x0 + grid_w + 16
        self.create_text(rx, head_y, text="your players", anchor="w", fill=MUTED, font=(FONT, 8))
        self.create_text(rx + 250, head_y, text="best free agents", anchor="w", fill=MUTED, font=(FONT, 8))
        self.cells = []
        font_size = 9 if row_h >= 16 else 8
        for i, tid in enumerate(teams):
            y0 = rows_top + i * row_h
            yc = y0 + row_h / 2
            self.create_text(x0 - 10, yc, text=self.abbrev.get(tid, "?"), anchor="e", fill=INK_2,
                             font=(FONT, font_size, "bold"))
            for j, games in enumerate(d["teams"][tid]):
                cx0, cx1 = x0 + j * col_w + 1, x0 + (j + 1) * col_w - 1
                n = len(games)
                self.create_rectangle(cx0, y0 + 1, cx1, y0 + row_h - 1, fill=self._fill(n), outline="")
                self.create_text((cx0 + cx1) / 2, yc, text=str(n), fill=INK if n >= 3 else INK_2,
                                 font=(FONT, font_size, "bold" if n >= 4 else "normal"))
                self.cells.append((cx0, y0, cx1, y0 + row_h, tid, j))
            mine = self.mine.get(tid, [])
            if mine:
                self.create_text(rx, yc, text=", ".join(mine), anchor="w", fill=C["accent"], font=(FONT, font_size, "bold"))
            fas = self.fas.get(tid, [])
            if fas:
                self.create_text(rx + 250, yc, text=", ".join(fas), anchor="w", fill=INK_2, font=(FONT, font_size))
            self.create_line(x0, y0 + row_h, w - 14, y0 + row_h, fill=GRID)

    def _hover(self, e):
        self.delete("hover")
        hit = next((c for c in self.cells if c[0] <= e.x <= c[2] and c[1] <= e.y <= c[3]), None)
        if not hit:
            return
        x0, y0, x1, y1, tid, j = hit
        self.create_rectangle(x0, y0, x1, y1, outline=INK, width=2, tags="hover")
        games = self.data["teams"][tid][j]
        start, end = self.data["weeks"][j]
        lines = [f"{self.abbrev.get(tid, '?')}  ·  {start:%d %b} - {end:%d %b}  ·  {len(games)} games"]
        lines += [f"{d:%a %d %b}   {'vs' if home else '@'} {self.abbrev.get(opp, '?')}" for d, opp, home in games]
        t = self.create_text(e.x + 16, e.y, text="\n".join(lines), anchor="nw", fill=INK, font=(FONT, 9), tags="hover")
        bx = self.bbox(t)
        if bx[2] > self.winfo_width() - 6:
            self.move(t, -(bx[2] - bx[0]) - 32, 0)
        bx = self.bbox(t)
        if bx[3] > self.winfo_height() - 6:
            self.move(t, 0, -(bx[3] - self.winfo_height()) - 10)
        bx = self.bbox(t)
        self.tag_raise(t, self.create_rectangle(bx[0] - 8, bx[1] - 6, bx[2] + 8, bx[3] + 6, fill=C["tooltip_bg"],
                                                outline=C["tooltip_border"], tags="hover"))
