# FantasyBasketball

Reads your ESPN NBA fantasy league and tells you what to do: bench injured starters, grab hot
free agents, sell players on hot streaks, and trade for slumping stars on your friends' teams.

## Setup

1. Install Python 3.11+ from python.org (tick "Add python.exe to PATH"), then double-click
   **`Setup.bat`**. It installs what the app needs and makes `config\config.json` for you. (By hand:
   `python -m venv .venv`, `.venv\Scripts\pip install -r config\requirements.txt`, and copy
   `config\config.example.json` to `config\config.json`.)
2. **league_id**: the number after `leagueId=` in your ESPN league URL.
3. **year**: `2027` for the 2026-27 season (ESPN names a season by the year it ends).
4. **espn_s2 / swid** (private leagues only): on espn.com, logged in, open DevTools (F12) →
   Application → Cookies → `https://www.espn.com`, then copy the `espn_s2` and `SWID` values.
   These cookies log into your ESPN account, so never share `config\config.json`.
5. Run `.venv\Scripts\python app\fantasy.py teams` and put your team's id in **my_team_id**.

## Folder layout

```
Setup.bat           double-click once on a new PC
Fantasy Hoops.bat   double-click to open the app
app/                all the Python code (webapp.py + web/ = the app, loader.py = loading from ESPN,
                    fantasy.py = commands + alerts)
config/             config.json (your ESPN cookies, never committed), nicknames.json, requirements
scripts/            check.ps1, run by the scheduled task
data/               exports, history, cached photos, check log (stays on your PC)
```

Keep `app/`, `config/` and `scripts/` where they are relative to each other; the code finds
its config and data from its own location.

## The app

Double-click **`Fantasy Hoops.bat`** (or run `.venv\Scripts\python app\webapp.py`; add `--browser` to use
your web browser instead, `--demo` for the made-up league). It opens in its own window, in a compact
dark design: sortable tables (click a header), player photos, hoverable charts, and player names you
can click anywhere to open that player's page. Long pages are split into sub-tabs (My Team: Alerts / Roster;
Free Agents & Trades: Tonight / Offers / Free agents / Streamers / Trade partners / Trade ideas; Trade Lab: This trade /
Suggestions), and the app remembers which one you had open.

Under the hood a small local server (`app\webapp.py`, only reachable from this PC) loads the league
(`app\loader.py`) and hands the page its data as JSON (`app\views.py`, `app\views_more.py`); the page is
plain HTML/CSS/JavaScript in `app\web\` with ECharts for the charts.

Tabs:

- **Standings** (points update live while games are on, ranked automatically, with a live **today** column and the
  **7-day race**: every team's projected points over the next 7 days with its best daily lineups, how many player-games
  fit its lineup, and where the gap to first will be (green if you'd close it); below it a week-by-week rank chart with your team highlighted - hover a week for that week's table), **My Team** (alerts + roster form). The roster shows whether each player is
  **in your lineup or on your bench**, **their NBA game today** (your local time), and their
  **NBA role**: how many of their last 5 games they started, from ESPN box scores (preseason
  until the regular season has games). If a bench player plays today and a starter doesn't,
  you get a "Start X instead of Y" alert
- **Week Ahead**: today's **best legal lineup** (respects positions, locks players whose game has
  started) with exactly what to change on ESPN; the **next 7 days** showing how many of your 10
  slots you'll fill each day and who's playing (click a day to see that day's full lineup); and **streamers**: free agents ranked by how many points they'd add
  this week, counting only games on days you'd actually start them, and who to make room with
  (an open roster spot if you have one, then your empty IR slot: INJ players always qualify, O
  players only once ESPN marks them IR-eligible; otherwise a drop, only if you also come out ahead over
  the rest of the season: his remaining games vs the dropped player's, at their base value).
  The **matchup** column shows how much a game is worth vs his normal average (see below)
- **News**: ESPN's injury report for your team, every rostered player or every injured NBA
  player, with expected return dates; click a player for the full note and latest news. Beside it,
  **Opportunities**: free agents set to play more because a teammate is out (his minutes are shared
  by up to 3 healthy teammates at his positions; the gain is their points per minute x the extra
  minutes, damped to 75% and capped at +60%) or because their minutes are rising this week.
  For tonight and tomorrow the same idea feeds every projection directly (**teammate out**, `app\usage.py`): once a
  regular teammate has missed 2+ games this season, it uses how the player actually did without him (his average
  without vs with, shrunk by games without / (games without + 5)); before that, the minutes-sharing estimate
- **Schedule**: NBA games per day for the next two weeks (light days in amber) and a grid of
  games per team per week for six weeks, with your players and the best free agents on each team;
  hover a cell for the dates and opponents
- **Free Agents & Trades**: **Tonight** lists the best free agents playing tonight (healthy, not day-to-day, game
  not started) with the projection for that game and **why**: minutes x points per minute, minutes trend, hot or
  cold, opponent, position defence, back-to-back, blowout risk, and regular teammates who are out. Above it, the
  add/drop moves worth making. **Offers** scores every pending trade offer involving you (sent to you or by you) like
  Trade Lab, with a verdict; click one to open it there. Then the top 100 available players; green
  ones beat your weakest player per game and over the next 30 days (**next 30d**). Right, up to 3
  trade ideas per team, best gain first, and **trade partners**: fair one-for-one swaps (within about
  10% in value) that fix a position imbalance for you, for them or both, without either starting
  lineup getting worse. Double-click one to open it in Trade Lab

**Waivers.** Recently dropped players sit on waivers until a set time (claims, not instant adds).
The app reads that time from ESPN: such players show as e.g. "waivers Thu" (amber) on Free Agents
and in streamers, alerts say "claim X off waivers (clears Thu 08:00)", and none of their games before
they clear count in the 7-day, 30-day or streamer numbers.

**Pending trades.** If your league has a trade review period (set in ESPN's league settings; 0 hours =
instant), an accepted trade waits that long before going through. Meanwhile the app
already counts it as done for everything forward-looking (Week Ahead from the day after it
processes, streamers, alerts, the 30-day numbers, trade partners and every team's projected final);
today's lineup still uses the roster you actually have. My Team shows a note while one is pending.
A trade still unprocessed 6 hours after its review ended is treated as vetoed.

**Matchups.** Projections for a specific game are nudged by the opponent, using the
total-points method: a game's possessions = pace A x pace B / league pace, and a team scores its
offence x the opponent's defence / league average per 100 of them. A player's own team is already
in his average, so only the opponent matters: `opponent pace / league pace x (1 + 0.5 x (opponent
defensive rating / league average - 1))`, capped at ±10%. Pace counts fully, defence half (a bad
defence gives up points, not rebounds or steals). Ratings come from ESPN's team stats: last season,
blended toward this season as games are played; cached daily in `data\team_ratings.json`. It feeds
the daily lineups, streamers, projected totals and the 30-day pickup check.
- **Trade Lab**: build any trade between any two teams by ticking players on each side. Shows each
  team's lineup strength (per-game value of its best 10 starters) before and after, the change in
  league rank, expected points over the **next 30 days** for both teams (daily best lineups with
  schedule, injuries and matchups), which position imbalances it fixes or creates, roster-size and
  position changes, a verdict on whether they'd accept, and where every team stands afterwards.
  The **even-it-out list** updates as you tick: with players picked it suggests adding 1-2 more
  (from either side) so both teams come out even; with nothing picked it lists fair 1-2 player
  trades with that team. Win-win trades (both lineups improve) are green. Click one to load it.
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
- **Activity**: a feed grouped by day. Trades show as cards like ESPN's League Assistant (each
  team, its manager and who it receives, with photos; accepted trades say when they go through),
  adds/drops as rows, and the league chat in blue (private DMs are never read). Filter to
  everything, trades, moves or chat; click a player to open him

Click a column header to sort; double-click a player or team to open it. The window refreshes
from ESPN every 15 minutes while open, or click **Refresh**. `--demo` works here too.

## Keep list

Players you never want suggested as a drop or as trade bait (streamers, waiver pickups, trade
ideas, Trade Lab suggestions). Select a player on My Team and click **Keep / unkeep**, or:

```
.venv\Scripts\python app\fantasy.py keep "jalen green"
.venv\Scripts\python app\fantasy.py unkeep "jalen green"
.venv\Scripts\python app\fantasy.py keep                  # show the list
```

Saved in `config\keep.json` (stays on your PC). You can still tick kept players yourself in the Trade Lab.

## Projections

- **Projected standings** (Standings tab, `standings` command): points so far plus the rest of the
  season simulated day by day on the real NBA schedule. Each day a team scores with its best 10
  players who have a game, at their base value.
- **Projected today** (My Team): each player's expected fantasy points if he plays today, and what
  your current lineup projects in total.
- **Availability**: ESPN projects how many games each player will play (injury history, rest,
  minutes limits). Rest-of-season numbers - projected standings, trade checks and the streamers'
  "rest of season" column - count only that share of his scheduled games, so a 64-game player is
  worth less than his per-game average suggests.
- **vs proj** (My Team, Free Agents, League): season points per game minus ESPN's projection, after
  3 games. Positive = beating expectations.
- ESPN's season projection itself is the **projected** row on the Player tab and a **projected**
  option in the League and Compare tabs' Stats menus.

## Commands

```
.venv\Scripts\python app\fantasy.py standings
.venv\Scripts\python app\fantasy.py roster              # your team
.venv\Scripts\python app\fantasy.py roster "Brick"      # a friend's team (id or part of the name)
.venv\Scripts\python app\fantasy.py alerts
.venv\Scripts\python app\fantasy.py alerts --notify     # also pops Windows notifications for new alerts
.venv\Scripts\python app\fantasy.py --demo alerts       # made-up league, no ESPN needed
.venv\Scripts\python app\fantasy.py tonight              # best streams tonight, and why
.venv\Scripts\python app\fantasy.py backtest             # replay the season to check the projections
```

League-wide stats:

```
.venv\Scripts\python app\fantasy.py league                    # every team: points, each player's box-score averages
.venv\Scripts\python app\fantasy.py league --split last_15    # same, for last_7 / last_15 / last_30 / projected
.venv\Scripts\python app\fantasy.py player jokic              # any NBA player: every split + where their points come from
.venv\Scripts\python app\fantasy.py scoring                   # the league's scoring rules
.venv\Scripts\python app\fantasy.py activity -n 50            # recent adds, drops and trades
.venv\Scripts\python app\fantasy.py export                    # data\players.csv, teams.csv, league.json (opens in Excel)
.venv\Scripts\python app\fantasy.py history "Ballymum"        # a team's points day by day
```

`export` and `alerts` each save a daily snapshot to `data\history.db`, which is what `history`
and the "+X since" numbers in `league` read. The more days it runs (see Automatic checks),
the more history you get. `data\` stays on your PC; it's in `.gitignore`.

## How it judges players

- **base**: season fantasy points per game. Early in the season it's blended with ESPN's
  projection until the player has 10 games. Category leagues use a rough composite of the
  counting stats instead.
- **base** (how good a player really is per game) = expected **minutes** x fantasy points **per minute**.
  Points per minute is his own season shrunk toward ESPN's projection by games / (games + 8), so one
  huge or awful night barely moves it; minutes blend ESPN's projection, his season, and his last 15 days
  and last 7 days counted extra (a game this week counts 5x, one last week 3x), so a new role or a
  benching shows within days. These settings were picked by replaying the season (see the backtest below). **worth** (used for pickups and trades) adds recent
  form, again shrunk so a one- or two-game streak only counts a little.
- **L15**: average over the last 15 days.
- **trend**: L15 against base. ±25% or more counts as hot or slumping (after 5 games); shown in
  green or red on My Team rather than as an alert.

The alerts:

| Alert | Fires when |
|---|---|
| Bench X (do now) | A starter is OUT, on IR, or suspended |
| Set your lineup (do now) | Your best legal lineup today scores more than the one you've set |
| Status change | One of your players went e.g. active -> out, or is back (alerts for a day) |
| Move X to IR | He's listed INJ (always IR-eligible), out beyond this week, and your IR slot is empty |
| Check if X is IR-eligible | He's listed O and out beyond this week: O players can go on IR only once ESPN marks them IR-eligible, which ESPN doesn't publish to apps, so check his card on ESPN |
| Stream X | A free agent would add 15+ points over the next 7 days |
| Stream today / tomorrow | Daily streaming: a free agent plays today in a lineup slot that would otherwise be empty, and you have a bench player with no game today who is no better than the free agents (so someone like him can be picked up again). Up to 3 a day |
| Pick up / drop (consider) | A free agent beats one of your weakest players by 10%+ per game AND is expected to score more over the next 30 days AND gains 40+ rest-of-season lineup points (a drop is permanent) |
| Opportunity | A free agent's teammate is out (or his minutes are up) and at his boosted value he beats one of your players |
| Minutes down | One of your players is playing 5+ fewer minutes a game this week than usual (often a smaller role) |
| Buy low / Sell high | After 5 games: a player's season average is 15%+ off ESPN's projection while his minutes are normal, and 4+ points a game off what the app thinks he's really worth. A slump or spike on normal minutes is usually shooting luck that evens out, but his manager sees the average. More minutes than projected is a real role change, so it doesn't count |
| Trade offer | Someone sent you a trade: worth accepting (you gain 40+ rest-of-season lineup points), about even, or costs you |
| Trade idea | One of their players has a higher base than one of yours, but their recent form is no better than yours, so they'd likely accept |
| Roster balance | You have more than 3 or fewer than 2 players at a main position (e.g. 4 PGs, or 1 C). IR players count toward "too many" but not "too few" |

Tune any of these under `thresholds` in `config\config.json`.

Daily streaming is the biggest lever in this league. With 13 players, only about half have a game
on a given day, so every team fills about 5.5 of its 10 lineup slots a day. The league counts total
points and allows unlimited pickups, so a slot filled by a streamer is pure gain. Cycling 2 bench
spots a day projected about +5,000 points over the season, more than the gap between first and
seventh. Players in your own pending waiver claims or trade offers (from the last 14 days) are never
suggested as drops. A one-night stream only drops a player if at least 3 free agents are as good as him.

Pickup, streamer and trade suggestions never create a balance problem you don't already have
(e.g. a 4th PG, or leaving you with 1 C). They also never touch players in a trade that's
waiting to go through, since dropping one would break the deal.

## How it values trades and pickups

It uses the methods fantasy analysts use for trade values and pickups:

- **Lineup points, not averages.** A move is worth the change in your starting lineup's points for
  the rest of the season. That means each day's best 10 players, the schedule, matchups, the games
  each player is expected to miss, and injured players counted only from their return date. A
  player who would sit on your bench adds little.
- **Value over replacement.** A trade that frees a roster spot is credited with the best free agent
  you could add. One that overfills your roster costs your weakest player. Both teams first get the
  free waiver moves anyone could make, so a trade only counts what you couldn't get for free.
- **Regression to the mean.** Early-season numbers lean on ESPN's projection (games / (games + 8)),
  so one big night doesn't turn a bench player into a must-add.
- **Would they accept?** Other managers judge trades by the public numbers, so "looks fair to them"
  is checked with ESPN's projected points per game. They may lose up to 1.5 a game.
- **Margin.** Gains under 40 rest-of-season points are within the projections' noise, so pickups
  below that aren't suggested. Trades the other manager would likely accept that gain you 40+ are
  highlighted in Trade Lab.

Each game is also adjusted for things that change how much a player scores that night:

| Factor | Effect | Why |
|---|---|---|
| Opponent | Up to ±10% for pace and defence | More possessions mean more of every stat; weak defences give up more points |
| Defence vs position | Up to ±3% | Some teams may be weak against guards, forwards or centres in particular. Worked out from this season's ESPN box scores (fantasy points per minute each team allows to G / F / C, in your league's scoring), counting only the tilt beyond the team's overall defence, and shrunk hard (games / (games + 60)) because the backtest found no reliable signal in it yet (`app\defense.py`, cached in `data\`) |
| Back-to-back, second night | −8% | Teams shoot worse and play slower on no rest, and that's when players get rested: this season, players averaging 24+ minutes sat 17% of second nights vs 7.5% of other games (re-measured by the backtest) |
| Teammate out (tonight / tomorrow) | Up to +60% | When a regular is out, others get his shots and minutes. Measured from each player's games without that teammate this season (`app\usage.py`) |
| Blowout risk | −1% for each point the expected margin is past 8, at most −6% | Starters sit 4th quarters in lopsided games. The margin comes from each team's scoring margin, with last season's counted at 60% because teams regress |
| Last 2 weeks of the season | −5%, or −15% for teams winning under 40% | Teams rest players late, and losing teams shut them down |

These matter most in a total-points league like this one, where every game counts.

Sources: CBS Sports and FantasyPros trade value charts; Yahoo's articles on 2-for-1 trades and roster
spots; FanGraphs on stat stabilization (how many games a stat needs before it means something);
FFToday on trading strategy; Aicardi & Zitzewitz's back-to-back study (20,000+ games, via Economist
Writing Every Day); RotoWire and FantasyLabs on blowouts and spreads; the NBA's player participation
policy (rest rules for stars and veterans); CBS Sports on end-of-season tanking.

## How accurate are the projections?

Every day (when the app loads, or the scheduled check runs) it saves what it projects for every
rostered player, the best free agents and every team in `data\accuracy.db`. After a week or more,
`.venv\Scripts\python app\fantasy.py accuracy` shows how far off it was: players' projected points per
game vs what they then averaged (next to two simple guesses, their season average and ESPN's
projection, so you can see whether the app beats them), and teams' projected 7-day points vs what
they scored. Use it to tune the model rather than guessing.

**Backtest** (no waiting needed): `.venv\Scripts\python app\fantasy.py backtest` replays every finished
game this season (box scores from ESPN, cached in `data\boxscores_fp\`). Each player-game is projected
using only what was known before it and compared with what he scored. It reports the average miss of
the app vs his season average and ESPN's projection, tries a grid of form settings (how fast to trust
his own games, how much recent minutes count), and checks each game factor: a slope of 1.0 means the
factor is the right size, 0 means no signal. It also re-measures how often rotation players sit the
second night of a back-to-back. On 3,400+ player-games through 23 Nov 2026 the app missed by 10.2 points
a game vs 10.5 for the season average and 11.2 for ESPN's projection; the opponent factor held up, while
defence vs position didn't (so it was turned down). Re-run it every few weeks.

## Automatic checks

`scripts\check.ps1` runs `export` and `alerts --notify` and logs to `data\check.log`. You only get a notification for
alerts that are new since the last run. To run it every 3 hours, set up a scheduled task:

```
schtasks /Create /TN "FantasyHoops" /SC HOURLY /MO 3 /TR "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"<full path to>\scripts\check.ps1\""
```

**Pre-lock reminder.** `scripts\tonight.ps1` runs `fantasy.py tonight --notify`: a notification with
tonight's stream moves (only when one is worth it), shortly before the first games lock. NBA games
mostly start at 7pm US Eastern, which is midnight in Ireland in winter, so 11pm Irish time works:

```
schtasks /Create /TN "FantasyHoopsTonight" /SC DAILY /ST 23:00 /TR "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"<full path to>\scripts\tonight.ps1\""
```

`.venv\Scripts\python app\fantasy.py tonight` prints the same list, with the reasons, any time.
