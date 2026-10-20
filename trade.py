"""Trade Lab: simulate a trade between two fantasy teams and measure what it does to each.

Lineup strength = per-game value (the same 'base' the alerts use) of a team's best players,
as many as it has active lineup slots. That's what scores in a points league; the bench
mostly doesn't. Rest-of-season impact = change in lineup strength x games left."""
import tkinter as tk

from fantasy import form_of

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
OTHER = "#c3c2b7"   # uninvolved teams recede
SERIES = ["#2a78d6", "#eb6834"]  # team A blue, team B orange (categorical slots 1, 2)
FONT = "Segoe UI"
POSITIONS = ["PG", "SG", "SF", "PF", "C"]


def lineup_slots(team):
    return sum(1 for p in team.roster if p.lineupSlot not in ("BE", "IR")) or 10


def strength(forms, slots):
    """Sum of the best `slots` players' base value. Players on injured reserve can't start."""
    vals = sorted((f.baseline for f in forms if (f.player.injuryStatus or "") != "INJURY_RESERVE"), reverse=True)
    return sum(vals[:slots])


def games_left(league):
    """Rough NBA games left per player: 82 x share of the fantasy season still to play."""
    final = getattr(league, "finalScoringPeriod", None) or 174
    current = getattr(league, "current_week", 1) or 1
    return round(82 * max(final - current + 1, 0) / final)


def positions(forms):
    return {pos: sum(1 for f in forms if f.player.position == pos) for pos in POSITIONS}


def simulate(league, team_a, team_b, send_ids, get_ids):
    """team_a sends send_ids to team_b and gets get_ids back. Returns before/after for both teams
    plus every team's lineup strength (before and after) for the league-wide picture."""
    year = league.year
    forms = {t.team_id: [form_of(p, year) for p in t.roster] for t in league.teams}
    a, b = forms[team_a.team_id], forms[team_b.team_id]
    a_after = [f for f in a if f.player.playerId not in send_ids] + [f for f in b if f.player.playerId in get_ids]
    b_after = [f for f in b if f.player.playerId not in get_ids] + [f for f in a if f.player.playerId in send_ids]

    before = {t.team_id: strength(forms[t.team_id], lineup_slots(t)) for t in league.teams}
    after = dict(before)
    after[team_a.team_id] = strength(a_after, lineup_slots(team_a))
    after[team_b.team_id] = strength(b_after, lineup_slots(team_b))
    rank = lambda table, tid: 1 + sum(v > table[tid] for v in table.values())

    def side(team, old, new):
        return {"team": team, "before": before[team.team_id], "after": after[team.team_id],
                "delta": after[team.team_id] - before[team.team_id],
                "rank_before": rank(before, team.team_id), "rank_after": rank(after, team.team_id),
                "size_before": len(old), "size_after": len(new),
                "pos_before": positions(old), "pos_after": positions(new),
                # lowest-value player left: who they'd likely drop if the trade overfills the roster
                "drop": min(new, key=lambda f: f.worth).player.name if len(new) > len(old) else None}

    return {"a": side(team_a, a, a_after), "b": side(team_b, b, b_after),
            "before": before, "after": after, "games_left": games_left(league),
            "names": {t.team_id: t.team_name for t in league.teams},
            "sent": [f for f in a if f.player.playerId in send_ids],
            "got": [f for f in b if f.player.playerId in get_ids]}


def verdict(result):
    """One-line read on the trade from team A's side."""
    a, b = result["a"], result["b"]
    if not result["sent"] and not result["got"]:
        return "Tick players on both sides to build a trade."
    if a["delta"] <= 0 and b["delta"] <= 0:
        return "Both lineups get worse. Nobody should take this."
    if a["delta"] <= 0:
        return f"This hurts your lineup by {-a['delta']:.1f} fp/g. They gain {b['delta']:+.1f}."
    if b["delta"] >= 0:
        return f"Win-win: you gain {a['delta']:+.1f} fp/g and so do they ({b['delta']:+.1f}). Good chance they'd accept."
    share = -b["delta"] / b["before"] if b["before"] else 1
    if share < 0.03:
        return (f"Close to fair: you gain {a['delta']:+.1f} fp/g; they lose only {-b['delta']:.1f}. "
                "Worth offering.")
    return (f"Lopsided: you gain {a['delta']:+.1f} fp/g but they lose {-b['delta']:.1f}. "
            "Unlikely to accept as is. Try adding a sweetener.")


class StrengthChart(tk.Canvas):
    """Every team's lineup strength as a dot plot: hollow dot = before, filled = after, arrow
    between them. Dots (not bars) because the axis is zoomed in, and bars must start at zero.
    The two trading teams are colored; the rest recede to gray."""

    def __init__(self, parent, **kw):
        super().__init__(parent, background=SURFACE, highlightthickness=1, highlightbackground="#e3e3e0", **kw)
        self.result = None
        self.bind("<Configure>", lambda e: self.draw())

    def show(self, result):
        self.result = result
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        r = self.result
        if not r or w < 160 or h < 60:
            return
        sides = {r["a"]["team"].team_id: 0, r["b"]["team"].team_id: 1}
        order = sorted(r["after"], key=lambda tid: -r["after"][tid])
        self.create_text(14, 14, text="League lineup strength (fp/g of each team's starters)",
                         anchor="w", fill=INK, font=(FONT, 11, "bold"))
        self.create_text(w - 14, 14, text="o before   ● after", anchor="e", fill=INK_2, font=(FONT, 9))
        top = 34
        row_h = max(min((h - top - 26) / max(len(order), 1), 30), 14)
        label_w, value_w = 220, 150
        x0, x1 = 14 + label_w, w - value_w
        vals = list(r["after"].values()) + list(r["before"].values())
        lo, hi = min(vals), max(vals)
        pad = (hi - lo) * 0.08 or 1
        lo, hi = lo - pad, hi + pad
        x = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)
        for i, tid in enumerate(order):
            y = top + (i + 0.5) * row_h
            involved = tid in sides
            color = SERIES[sides[tid]] if involved else OTHER
            self.create_line(x0, y, x1, y, fill=GRID)
            self.create_text(14, y, text=f"{i + 1}. {r['names'][tid]}", anchor="w",
                             fill=INK if involved else INK_2, font=(FONT, 9, "bold" if involved else "normal"))
            b, a = x(r["before"][tid]), x(r["after"][tid])
            if involved and abs(a - b) > 1:
                self.create_line(b, y, a, y, fill=color, width=2, arrow="last", arrowshape=(8, 9, 3))
            if involved:
                self.create_oval(b - 5, y - 5, b + 5, y + 5, outline=color, width=2, fill=SURFACE)
            self.create_oval(a - 5, y - 5, a + 5, y + 5, fill=color, outline=SURFACE, width=2)
            d = r["after"][tid] - r["before"][tid]
            label = f"{r['after'][tid]:.1f}" + (f"  ({d:+.1f})" if involved else "")
            self.create_text(x1 + 12, y, text=label, anchor="w", fill=INK if involved else MUTED,
                             font=(FONT, 9, "bold" if involved else "normal"))
        self.create_text(x0, h - 9, text=f"axis zoomed to {lo:.0f}-{hi:.0f} fp/g so the differences show",
                         anchor="w", fill=MUTED, font=(FONT, 8))
