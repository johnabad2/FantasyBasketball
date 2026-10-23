"""Compare tab pieces: per-game profiles for a player, scaled for the parallel-coordinates chart.

Every axis is scaled to the range of all rostered players in the league, so "high on the
axis" means high for the league, not just higher than the other player. Axes are oriented
so up is always better for fantasy (turnovers are flipped)."""
from stats import fp_per_game, season_or_projection, split_of

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
    v = 0.0 if abs(v) < 0.05 else v  # no "-0.0"
    if key in ("FG%", "FT%"):
        return f"{v:.0f}%"
    return f"{v:.1f}"
