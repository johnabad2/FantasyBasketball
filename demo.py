"""Made-up league with the same shape as espn_api's, for trying the app without ESPN."""
import random
from types import SimpleNamespace

YEAR = 2027
TEAM_NAMES = ["Your Team", "Buckets R Us", "Glass Cleaners", "Brick City",
              "Fast Break Fam", "Zone Defense", "Splash Bros 2", "Bench Mob"]
NBA = ["BOS", "DEN", "LAL", "MIL", "OKC", "DAL", "NYK", "PHX", "MIN", "GSW", "MIA", "SAC"]
SLOTS = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT", "UT", "UT", "BE", "BE", "BE"]


def split(fp, gp=None):
    s = {"applied_avg": round(fp, 2), "avg": {}, "total": {}}
    if gp is not None:
        s["total"]["GP"] = gp
    return s


def player(pid, name, base, rng, status="ACTIVE", slot="BE", recent_mult=None):
    gp = rng.randint(6, 9)
    recent = base * (recent_mult if recent_mult is not None else rng.uniform(0.85, 1.15))
    return SimpleNamespace(
        playerId=pid, name=name, position=rng.choice(["PG", "SG", "SF", "PF", "C"]),
        proTeam=rng.choice(NBA), lineupSlot=slot, injuryStatus=status,
        stats={f"{YEAR}_total": split(base, gp), f"{YEAR}_last_15": split(recent),
               f"{YEAR}_projected": split(base * rng.uniform(0.9, 1.1))},
    )


def demo_league():
    rng = random.Random(7)
    pid = iter(range(1000, 9999))
    teams = []
    for tid, tname in enumerate(TEAM_NAMES, 1):
        roster = [player(next(pid), f"Demo Player {tid}-{i + 1}", rng.uniform(18, 48), rng, slot=s)
                  for i, s in enumerate(SLOTS)]
        teams.append(SimpleNamespace(team_id=tid, team_name=tname, wins=rng.randint(0, 2),
                                     losses=rng.randint(0, 2), points_for=rng.uniform(800, 1100),
                                     owners=[{"firstName": "Owner", "lastName": str(tid)}],
                                     roster=roster))
    # Scenarios the alerts should catch.
    mine = teams[0].roster
    mine[0].injuryStatus = "OUT"                       # starter at PG is out
    mine[1].injuryStatus = "DAY_TO_DAY"                # starter is questionable
    mine[2].stats[f"{YEAR}_last_15"] = split(mine[2].stats[f"{YEAR}_total"]["applied_avg"] * 1.45)  # hot
    mine[-1].stats[f"{YEAR}_total"] = split(12.0, 7)   # weak bench guy
    mine[-1].stats[f"{YEAR}_last_15"] = split(11.0)
    star = teams[3].roster[0]                          # their star is slumping: buy low
    star.stats[f"{YEAR}_total"] = split(55.0, 8)
    star.stats[f"{YEAR}_last_15"] = split(38.0)

    free_agents = [player(next(pid), f"Free Agent {i + 1}", rng.uniform(10, 24), rng) for i in range(30)]
    free_agents[0].stats[f"{YEAR}_last_15"] = split(31.0)  # breakout on waivers

    standings = sorted(teams, key=lambda t: (-t.wins, t.losses, -t.points_for))
    return SimpleNamespace(year=YEAR, teams=teams, standings=lambda: standings,
                           free_agents=lambda size=50: free_agents[:size], scoreboard=lambda: [])
