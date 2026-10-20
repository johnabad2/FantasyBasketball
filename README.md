# FantasyBasketball

Reads your ESPN NBA fantasy league and tells you what to do: bench injured starters, grab hot
free agents, sell players on hot streaks, and trade for slumping stars on your friends' teams.

## Setup

1. Set up Python: `python -m venv .venv`, then `.venv\Scripts\pip install -r config\requirements.txt`.
   Copy `config\config.example.json` to `config\config.json`.
2. **league_id**: the number after `leagueId=` in your ESPN league URL.
3. **year**: `2027` for the 2026-27 season (ESPN names a season by the year it ends).
4. **espn_s2 / swid** (private leagues only): on espn.com, logged in, open DevTools (F12) →
   Application → Cookies → `https://www.espn.com`, then copy the `espn_s2` and `SWID` values.
   These cookies log into your ESPN account, so never share `config\config.json`.
5. Run `.venv\Scripts\python fantasy.py teams` and put your team's id in **my_team_id**.

## The window

Double-click **`Fantasy Hoops.bat`** (or run `.venv\Scripts\pythonw app.py`). Tabs:

- **Standings**, **My Team** (alerts + roster form)
- **Free Agents**: the top 100 available players; green ones beat your weakest player
- **Trades**: up to 3 trade ideas per team, best gain first
- **League**: any team's roster, any stat window
- **Player**: search any NBA player, with their ESPN photo (cached in `data\headshots`). Nicknames
  work too ("kd", "slim reaper", "the joker", "sga"); add your own in `config\nicknames.json`.
  Below the stats is a **game-by-game chart**: pick fantasy points or any stat (or click a stat's
  column header / a row in "Points by stat"), choose all games or the last 5-30, and hover a bar
  for that game's box score. Blue = above the player's average, red = below. Game logs come from
  ESPN's public game log; until this season has games it shows last season.
- **Compare**: two players head to head ("lebron" vs "kd" works) on a parallel-coordinates chart,
  either as per-game stats or as the fantasy points each stat earns. Every axis spans all rostered
  players in your league and up is always better for fantasy (turnovers are flipped). The side
  table shows who has the edge on each stat in fantasy points per game. "Compare with..." on the
  Player tab starts a comparison from the player you're looking at.
- **Activity**: recent adds, drops and trades

Click a column header to sort; double-click a player or team to open it. The window refreshes
from ESPN every 15 minutes while open, or click **Refresh**. `--demo` works here too.

## Commands

```
.venv\Scripts\python fantasy.py standings
.venv\Scripts\python fantasy.py roster              # your team
.venv\Scripts\python fantasy.py roster "Brick"      # a friend's team (id or part of the name)
.venv\Scripts\python fantasy.py alerts
.venv\Scripts\python fantasy.py alerts --notify     # also pops Windows notifications for new alerts
.venv\Scripts\python fantasy.py --demo alerts       # made-up league, no ESPN needed
```

League-wide stats:

```
.venv\Scripts\python fantasy.py league                    # every team: points, each player's box-score averages
.venv\Scripts\python fantasy.py league --split last_15    # same, for last_7 / last_15 / last_30 / projected
.venv\Scripts\python fantasy.py player jokic              # any NBA player: every split + where their points come from
.venv\Scripts\python fantasy.py scoring                   # the league's scoring rules
.venv\Scripts\python fantasy.py activity -n 50            # recent adds, drops and trades
.venv\Scripts\python fantasy.py export                    # data\players.csv, teams.csv, league.json (opens in Excel)
.venv\Scripts\python fantasy.py history "Ballymum"        # a team's points day by day
```

`export` and `alerts` each save a daily snapshot to `data\history.db`, which is what `history`
and the "+X since" numbers in `league` read. The more days it runs (see Automatic checks),
the more history you get. `data\` stays on your PC; it's in `.gitignore`.

## How it judges players

- **base**: season fantasy points per game. Early in the season it's blended with ESPN's
  projection until the player has 10 games. Category leagues use a rough composite of the
  counting stats instead.
- **L15**: average over the last 15 days.
- **trend**: L15 against base. ±25% or more counts as hot or slumping (after 5 games).

The alerts:

| Alert | Fires when |
|---|---|
| Bench X (do now) | A starter is OUT, on IR, or suspended |
| Pick up / drop (do now) | A free agent beats one of your weakest players by 10%+ |
| Trade idea | One of their players has a higher base than one of yours, but their recent form is no better than yours, so they'd likely accept |
| Sell high | Your player is 25%+ above their base right now |
| Slumping | Your player is 25%+ below base. Usually hold, don't panic-sell |

Tune any of these under `thresholds` in `config\config.json`.

## Automatic checks

`check.ps1` runs `alerts --notify` and logs to `check.log`. You only get a notification for
alerts that are new since the last run. To run it every 3 hours, set up a scheduled task:

```
schtasks /Create /TN "FantasyHoops" /SC HOURLY /MO 3 /TR "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"<full path to>\check.ps1\""
```
