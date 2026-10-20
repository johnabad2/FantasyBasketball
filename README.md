# FantasyBasketball

Reads your ESPN NBA fantasy league and tells you what to do: bench injured starters, grab hot
free agents, sell players on hot streaks, and trade for slumping stars on your friends' teams.

## Setup

1. Copy `config.example.json` to `config.json`.
2. **league_id**: the number after `leagueId=` in your ESPN league URL.
3. **year**: `2027` for the 2026-27 season (ESPN names a season by the year it ends).
4. **espn_s2 / swid** (private leagues only): on espn.com, logged in, open DevTools (F12) â†’
   Application â†’ Cookies â†’ `https://www.espn.com`, then copy the `espn_s2` and `SWID` values.
   These cookies log into your ESPN account, so never share `config.json`.
5. Run `.venv\Scripts\python fantasy.py teams` and put your team's id in **my_team_id**.

## Commands

```
.venv\Scripts\python fantasy.py standings
.venv\Scripts\python fantasy.py roster              # your team
.venv\Scripts\python fantasy.py roster "Brick"      # a friend's team (id or part of the name)
.venv\Scripts\python fantasy.py alerts
.venv\Scripts\python fantasy.py alerts --notify     # also pops Windows notifications for new alerts
.venv\Scripts\python fantasy.py --demo alerts       # made-up league, no ESPN needed
```

## How it judges players

- **base**: season fantasy points per game. Early in the season it's blended with ESPN's
  projection until the player has 10 games. Category leagues use a rough composite of the
  counting stats instead.
- **L15**: average over the last 15 days.
- **trend**: L15 against base. Â±25% or more counts as hot or slumping (after 5 games).

The alerts:

| Alert | Fires when |
|---|---|
| Bench X (do now) | A starter is OUT, on IR, or suspended |
| Pick up / drop (do now) | A free agent beats one of your weakest players by 10%+ |
| Trade idea | One of their players has a higher base than one of yours, but their recent form is no better than yours, so they'd likely accept |
| Sell high | Your player is 25%+ above their base right now |
| Slumping | Your player is 25%+ below base. Usually hold, don't panic-sell |

Tune any of these under `thresholds` in `config.json`.

## Automatic checks

`check.ps1` runs `alerts --notify` and logs to `check.log`. You only get a notification for
alerts that are new since the last run. To run it every 3 hours, set up a scheduled task:

```
schtasks /Create /TN "FantasyHoops" /SC HOURLY /MO 3 /TR "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"<full path to>\check.ps1\""
```
