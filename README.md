# The Haber Process

Live at [process.jdhaber.com](https://process.jdhaber.com).

A full-stack web app that projects NFL and NBA player stats, prices the projections against live sportsbook prop lines, and ranks the best bets of the week.

**Markets:** rushing yards, receiving yards, passing yards, rush + receiving yards, receptions, passing touchdowns, longest reception, longest rush, anytime touchdown, first touchdown scorer, and team moneyline (each team's chance to win).

- **Front end:** React + TypeScript (Vite, TanStack Query, Recharts).
- **Back end:** FastAPI + SQLAlchemy on Postgres (locally too; SQLite still works, and the unit tests use it).
- **Model:** pandas / SciPy projection engine with a walk-forward backtest.
- **Data:** [nflverse](https://github.com/nflverse/nflverse-data) for stats, schedules (with game-time weather), rosters and injuries; ESPN's public injury feed for news lines and estimated returns; [The Odds API](https://the-odds-api.com) for prop lines; [Open-Meteo](https://open-meteo.com) (free, no key) for kickoff forecasts at outdoor stadiums.

> A modeling and software project, not betting advice. See [Limitations](#limitations).

**NBA too.** A switch at the top of the page (🏈 NFL / 🏀 NBA, remembered in your browser) flips the whole site to the NBA: see [NBA](#nba).

## What the app does

- **Games** (the first tab): a tile for every game on the slate with kickoff, weather, both teams' logos, records (from ESPN's standings, re-read every half hour; hover for the home or road split) and win chances (a bar in the teams' colors) and the spread, the top prop and how many props are +EV, and the injury count. Click a tile for that game's page: weather, the moneyline card (each team's record, with its record at home or on the road for this game, and its chance to win from the model, the market and the blend, with the best price; this replaced the old Moneyline tab), its best props, parlays built from that game only, projections and both injury reports, all at one sportsbook picked at the top of the page (FanDuel by default; the choice is remembered in your browser and carries into the player drawer), so one page covers what used to take several tabs. The page has its own link (`#game=<game_id>`) and the browser's back button returns to the slate.
- **Best props:** every Over/Under quote is priced and ranked by expected value, using the best price across sportsbooks. The board shows **one pick per player** by default, his best by EV, with a "+N related" tag for the rest: a player's picks win or lose together, so listing each as its own bet overstates the edges (a checkbox shows every pick, remembered in your browser); game pages group the same way before showing their top eight. Filter by market, over/under ("Overs", "Unders"; "Anytime TD"/"No TD" on the TD tab), game and sportsbook (a book filter shows only that book's lines, still priced against the all-book market consensus, and carries into the player drawer's chart and line table), search by player, and adjust **minimum EV** and **how much to trust the market** with sliders that re-price the board live (by default the market weight is fitted per market and time to kickoff; see [Reading the market](#reading-the-market)). A pick the market has moved toward or away from since the week's first odds fetch says so ("mkt +4 pts since open"), and **steam with / steam against** marks three or more books moving its line together between the last two fetches.
- **Who's Hot:** players whose last few games cleared the line they're posted at now: at least 60% of the last 3, 5 or 8 (NBA: 5, 10 or 15; 5 and 10 by default), ranked by games cleared, then the current streak, then how far the average beat the line. *Cold* flips it to players who stayed under. Each row shows those games against the line, their average beside his average before them, and the board's price, win chance and EV at that line, since a run isn't an edge by itself: the projections already weight recent games. Same market, game, search and sportsbook filters as Best props (`GET /api/hot`, `/api/nba/hot`; `engine/hot.py`).
- **Matchup grades:** every projection gets a letter for its defense against the player's position in that market (A = the softest fifth of the league, F the toughest), on Best props (green when it helps the pick's side, red when it hurts), Projections and the drawer's *Why* panel. Each defense is measured against what the players it faced usually produce (NBA: their per-minute rate × that night's minutes, by guard/forward/center; NFL: their average game, by QB/RB/WR/TE, last 12 months), shrunk toward par (`engine/dvp.py`). They're context, not an edge: the models already use each defense's factor for the stat, and in the backtests splitting it by position added little or nothing (NBA over rates 43-47% from the softest fifth to the toughest; NFL passing the one faint hint, on too few games to build in).
- **Player drawer:** click any row for a *Why* panel (what the projection is built from, then the model's chance, the market's, their blend and the EV at the price), hit rates at the play's line (last 5, 10 and 20, this season, last season, against this opponent and, for the NBA, at home or away) beside the model's chance, the last 12 games charted against the projection and the posted lines, how the player's market moved through the week (the consensus line and the books' fair chance of the over at today's line, one point per odds fetch), plus every available play for that player.
- **Alternate lines:** in the drawer, a ladder shows every posted line, main and alternate, across books. Each rung's chance blends the model with the **market curve**: the books' whole distribution for that player, read off every rung of every book (below). A rung priced 3%+ better than that curve says it's worth is marked *beats ladder*, whatever the model thinks. Refreshing odds fetches main and alternate lines for every game of the week (one API call per game, one credit per market, about 5 credits per game for alternates), so they're available by default. A "−300 to +300" toggle (on by default) hides prices outside that range, such as -2500 locks and +3900 longshots. *Load alternate lines* remains as a one-game fallback.
- **Betslip:** tap **+** on any quote to build a slip (saved in your browser). Each leg has a book switcher that lists every sportsbook offering that exact line, and "Put every leg at one book" moves the whole slip to a single book (showing how many legs it can take) so a parlay stays consistent. When that book doesn't offer a leg's exact line, the slip suggests the nearest lines the book does offer, with prices and EV, and one click swaps the leg. Selections are grouped by sportsbook with per-book parlay odds and payout and a stake box. Books whose links need your state (BetMGM, for example) get it from your device's location, worked out on the device. **Place bet** (or **Place at <book>** when the slip spans books) opens the sportsbook with the slip's legs: FanDuel and DraftKings get every linked leg in one tab (experimental: those multi-selection link formats are not yet confirmed against the live sites, so check the slip afterward); other books step through the legs one new tab per click (browsers block extra pop-ups from a single click), skipping legs that have no link. **Copy slip link** copies that same link so you can open the slip on another device, and **Clear all** empties the slip. **Get links** fetches missing betslip links for the slip's games on demand. **Add at book** opens the sportsbook with that selection added to its betslip where the Odds API provides a direct link (FanDuel and DraftKings do), and otherwise opens the book. The app never places bets; you sign in and confirm at the book.
- **Injuries:** every game of the week with both teams' injury report, offense and defense: who's ruled Out, Doubtful or Questionable, who is practicing limited or not at all with no game status yet (veterans' rest days are marked as such), and, behind a toggle, who's on IR or PUP. Before the week's report is out (teams start reporting Wednesday) it shows last week's final statuses and says so. ESPN's injury feed (the same one the NBA side uses) adds each player's latest news line and estimated return, and fills in a status where nflverse has none current (a practice-only listing, a player activated off IR, or last week's report), re-read every 15 minutes. Players the app projects link to their drawer.
- **Projections:** every projected player with the volume and efficiency behind the number, sortable. Receivers and backs with fewer than three games of history (a rookie, or a player back from IR), and a starting QB with fewer than three, are projected anyway and marked *Thin history*; those numbers are shown for reference only and are never priced, so they stay off Best props, parlays and the line ladder.
- **Weather:** outdoor games show the kickoff forecast; rows flag windy, cold or wet games with the adjustment made to that projection (for example "💨 19 mph · −22%"), and the player drawer spells it out.
- **Model check:** backtest accuracy per market, **graded picks** (below) with **closing-line value**, the fitted market and book weights, the fitted weather effect with this week's outdoor forecasts, and the freshness of projections and odds.
- **Graded picks:** every NFL sync logs the board for games still to kick off (`pick_log`; the last snapshot before a game is the one kept), and each model run grades finished games from nflverse's weekly stats once they land, usually the Tuesday after. Model check shows the record and units of what a visitor saw (Min EV 3%, flags hidden), split by market, side and EV, with one pick per player alongside, plus the projection's and the posted line's average miss per market: the backtest has no odds in it, so this is where the model is checked against the market (`GET /api/grades`). Each logged pick also keeps the price it was **first shown at** on the board (EV 3%+, no flag), and once its game starts it gets the **close**: the books' fair chance of its side at the last odds fetch before kickoff, at its line. Model check reports how often the picks beat the close, their EV measured at the close, and how far the market moved toward them, overall and by market.
- **Scheduled syncs:** the server runs the model and fetches odds on a weekly schedule (below), so the public site has no buttons that spend credits. The header shows when the next update is due. Odds are stored in the database, so re-running the model never spends API credits.
- **Admin page** (`/admin`, for the owner's account: an `ADMIN_EMAILS` address, signed in): the schedule with each slot's next and last run and a *Run now* button, a log of every sync (trigger, week, games fetched, credits used, full output), and the Odds API key's remaining credits.

## NBA

The NBA side covers the **slate**: the next two game dates (Eastern) with games still to tip off, so before opening night it shows opening night and the day after. Games drop off the board at tip-off.

- **Games:** a tile per game with tip-off, logos, both teams' records and win chances, the market spread and total (from ESPN), the chance of a 20+ point blowout (red at 30% or more), the top prop, and the injury count. A tile opens the game page: the moneyline card, a blowout card (the chance the game is decided by 15, 20, 25 or 30+ points, split by which team does it), best props, projections and both teams' injury reports (ESPN's status, injury, latest news line and estimated return), at one sportsbook picked at the top.
- **Best props / Projections:** points, rebounds, assists and 3-pointers made, with the same filters, pricing, player drawer and betslip as the NFL.
- **Parlays:** the NFL's tiles at one sportsbook (likely plays, ladders of points/rebounds/assists/threes overs on alternate lines stacked to +100 to +300, and the likeliest single alternate props), for the whole slate or chosen games; each game page has its own. No touchdown section.
- **Injuries:** ESPN's report for every slate team. *Out*, *Doubtful* and *Suspended* players aren't projected; *Day-to-day* players are, and are flagged.
- **Model check:** **graded picks** and the backtest. Every NBA sync logs the board for games still to tip off (`nba_pick_log`; the last snapshot before tip-off is the one kept), and each NBA model run grades finished games from ESPN's game logs, the morning after. A player missing from a finished game's box score is void (as the books settle it), but only once he has played again or two days have passed, so a stale gamelog fetch never reads as a DNP. The report has the NFL's shape (record and units for Min EV 3% with flags hidden, by market, side, EV and week, and the projection's vs the posted line's average miss, closing-line value and the fitted weights, fitted on NBA picks and lines alone) at `GET /api/nba/grades`.

**Data.** ESPN's public JSON API (no key) for the schedule, rosters, every rostered player's game logs (this season and the two before, All-Star weekend left out: ESPN files it under the regular season) and injuries; stats.nba.com and NBA.com's CDN refuse requests from hosting providers. Responses are cached in `backend/data/nba_cache/`, on the Railway volume, so finished seasons are fetched once (a first run takes about 25 seconds, later ones a few). Lines come from The Odds API (`basketball_nba`) with its own key, **`ODDS_API_KEY_NBA`**.

**Model** (`backend/app/nba/`). Each stat = projected minutes × per-minute rate × opponent factor. Minutes are a recency-weighted average of recent games (8-game half-life); rates use a 40-game half-life, shrunk toward the guard/forward/center average by 150 minutes of league-average play (30 for threes, since who shoots them varies so much within a position), with last season counting 60% as much as this one. The opponent factor compares what a defense allowed with what its opponents' players usually produce, shrunk to 1 and kept within ±15%. **Teammates out:** each player's recent games carry his team's usual mix of absences, so he was over-projected with everyone playing (over his points projection 37% of the time in 2025-26) and under-projected with a star out (57% over when 15-30% of the team's projected points were missing). Every stat and the minutes are scaled by 1 + a × (the share of the team's projected production its absent regulars, 25+ projected minutes and ruled Out, Doubtful or suspended, would have had) + b, with a and b fitted on the walk-forward backtest per stat; an absence already in the teammates' recent games counts for less (8-game half-life, gone after 20). Fitted on 2024-25 and tested on 2025-26, it cut points error 0.7%, points + rebounds + assists 1.1% and minutes 1.6%, and the over rate now runs 40-48% at every level of absence instead of 37-61%. The player drawer's *Why* panel shows each projection's minutes, per-36 rate, opponent factor and teammates-out effect, then how the model and market chances blend into the price's EV. Over/under probabilities come from the walk-forward backtest's own errors, as for the NFL, except threes: they're small whole numbers, so they're priced as a Poisson count (the error table underrated their overs by 4-5 points at 1.5+ lines; Poisson came within 0.4-2.7 points at every line). Team win chances come from a ridge regression of point margins (60-day half-life, last season at half weight) through a normal curve fitted in the backtest (about 12.5 points), and always lean at least 80% on the market's no-vig chance.

**Blowouts** (`backend/app/nba/blowout.py`; `python -m app.nba.blowout` prints the backtest and the slate). The final margin is a Student-t around the market spread (about 15 degrees of freedom: misses of 2.5 standard deviations happen 1.8% of the time, not a normal curve's 1.2%), its width quadratic in the size of the spread (pick'ems and big spreads miss by more than 3-6 point lines; the total added nothing). The width is then rescaled by how far the last 600 games missed their lines, bounded to ±15%: games decided by 20+ went from 16-19% a season in 2019-2023 to 22.8% in 2025-26, and the rescale follows that where weighting recent seasons didn't. Fitted on every game since 2018-19 with ESPN's closing spread (the median across its pregame books), kept in `backend/data/nba_blowout_history.csv` and topped up each NBA run; the first run on a fresh volume fetches the whole history, about 12,000 requests and two to three minutes. Walk-forward (2021-22 to 2025-26, each season fitted on the ones before):

| Margin | Games | Predicted | Actual | Log loss vs. last season's rate |
|---|---|---|---|---|
| 15+ | 6,632 | 33.8% | 32.8% | -1.9% |
| 20+ | 6,632 | 20.0% | 19.7% | -2.7% |
| 25+ | 6,632 | 11.1% | 11.4% | -3.9% |
| 30+ | 6,632 | 5.8% | 6.3% | -5.3% |

Walk-forward backtest (2025-26 and the 2026 playoffs, weekly, rostered players with 12+ projected minutes):

| Stat | Games | Model MAE | Last-10 MAE | Bias |
|---|---|---|---|---|
| Points | 22,994 | 5.02 | 5.07 | -0.1 |
| Rebounds | 22,994 | 1.98 | 2.05 | +0.0 |
| Assists | 22,994 | 1.46 | 1.48 | -0.0 |
| 3-pointers made | 22,994 | 0.95 | 0.97 | +0.0 |
| Moneyline (Brier; baseline = home team's usual rate) | 1,235 | 0.206 | 0.247 | +0.006 |

The edge over a plain last-10 average is small; the books' lines are sharper still.

**NBA syncs** (on the same schedule thread and admin page as the NFL's; Eastern time, every day):

| When | Sync | Odds (NBA key) |
|---|---|---|
| 10:00 AM | Model + odds | moneylines for the slate (1 credit), props already posted for games within 20 hours (4 per game: points, rebounds, assists, threes), plus alternate lines for the ladders the first time a game has them (up to 4 more) |
| 2:00 PM | Model + odds | moneylines, props for games that had none |
| 6:00 PM | Model + odds | moneylines, a fresh pull of props for games within 20 hours (4 per game) |

That is roughly 8 credits per game per day plus 3, about 2,000-2,700 credits a month in season, so a free 500-credit key won't last; trim the 6 PM re-pull (`nba-pregame` in `scheduler.py`) to save half. The credit floor (`ODDS_CREDIT_FLOOR`) applies to the NBA key too, and the admin page shows both keys' balances.

**NBA API:** `GET /api/nba/meta`, `/api/nba/picks`, `/api/nba/projections`, `/api/nba/moneylines`, `/api/nba/injuries`, `/api/nba/grades`, `/api/nba/players/{id}`, `/api/nba/players/{id}/lines?kind=pts|reb|ast|threes`, `/api/nba/players/{id}/history?kind=`, with the same parameters and shapes as the NFL routes.

Alternate lines are bought once per game and market, never re-pulled, and The Odds API only charges for markets it returns, so a game without alternates costs nothing extra.

**NBA limitations.** A teammate's absence moves everyone on the team by the same percentage, not more for the players at his position (a first try at weighting by position did no better in the backtest); rookies and players with fewer than 5 NBA games aren't projected; a move to a new team isn't modeled (their old role's minutes carry over), though in the 2025-26 backtest movers' first three games with a new team were projected no worse than anyone's (points MAE 5.1 vs 5.5); alternate lines aren't refreshed before tip-off; back-to-backs, rest days and blowouts aren't modeled.

## Architecture

```
┌────────────┐  /api   ┌──────────────────────────────┐        ┌────────────┐
│ React SPA  │ ──────▶ │ FastAPI                      │ ─────▶ │ nflverse   │ stats, schedule,
│ (Vite, TS) │ ◀────── │  routes · job runner         │        │ (CSV)      │ rosters, injuries
└────────────┘  JSON   │  engine/ model · picks · odds│ ─────▶ ├────────────┤
                       └──────────────┬───────────────┘        │ Odds API   │ prop lines
                                      │ SQLAlchemy             └────────────┘
                              ┌───────▼────────┐
                              │ SQLite/Postgres│ runs · projections · odds_lines
                              └────────────────┘
```

- **Pipeline** (`pipeline.py`): loads data, runs the backtest, projects the next unplayed week, applies injury and roster filters, and stores a `Run` with its projections. Odds are fetched separately and stored with a fetch timestamp.
- **Pricing is computed on request** (`engine/picks.py`), not stored, so the UI's sliders can re-price instantly. Results are memoized per (run, odds version, market weight).
- **Refresh jobs** run in a background task with a single-flight lock; the UI polls `/api/refresh/status`.
- In production the API serves the built React app, so it deploys as one container.

### API

| Endpoint | Purpose |
|---|---|
| `GET /api/meta` | Current run, backtest stats, odds freshness, game list, refresh job state |
| `GET /api/picks` | Priced plays. Params: `kind`, `game_id`, `q`, `min_ev`, `market_weight` (omit for the fitted weights), `side` (Over/Under), `book`, `include_flagged`, `limit` |
| `GET /api/projections` | Projections. Params: `kind`, `game_id`, `q` |
| `GET /api/moneylines` | Both teams' win chances (model, market, blend) and best moneyline per game. Params: `book`, `market_weight` |
| `GET /api/injuries` | This week's injury report and reserve lists for every team on the slate (re-read from nflverse at most hourly) |
| `GET /api/players/{id}` | One player: projections, available plays, recent game logs |
| `GET /api/players/{id}/lines?kind=` | Every main and alternate quote for one player and market, priced against the model and the market curve, with betslip links |
| `GET /api/players/{id}/history?kind=` | How the player's market moved: consensus line and fair over chance per odds fetch |
| `POST /api/players/{id}/alt-lines` | Fetch alternate lines and links for the player's game (spends API credits) |
| `POST /api/refresh` | Start a refresh. Body `{"odds": "none" \| "missing" \| "all"}`; `409` if one is running |
| `GET /api/refresh/status` | Progress log of the current or last refresh |

Interactive docs are at `/docs` when the API is running.

## The model

### Projection

Yards = **volume x efficiency**:

| Market | Volume | Efficiency |
|---|---|---|
| Rushing | carries | yards per carry |
| Receiving | targets | yards per target |
| Passing | pass attempts | yards per attempt |
| Rush + Receiving | both volumes, summed | summed projections |
| Receptions | targets | catch rate (receptions per target) |

- **Recency weighting:** exponential decay (5-game half-life) across seasons, so recent games dominate while last year anchors small samples.
- **Shrinkage:** efficiency is pulled toward the position mean with a pseudo-count prior, because yards per attempt is noisy. Volume is shrunk only lightly (and not at all for most quarterback volume), since roles are stable.
- **Opponent and game script:** a shrunk yards-allowed factor for the defense, and a point-spread nudge (favorites run more, underdogs pass more).
- **Weather:** at outdoor stadiums, wind and cold scale the yardage projection (see below).
- **Injured teammates:** see "Injury redistribution" below.
- **Availability:** the active roster decides a player's team. Injured reserve, Out/Doubtful, players with no practice and no game status, and offseason arrivals with no games for their new team are excluded.

### Injury redistribution (rushing)

When a regular is out, his volume goes to his teammates and the books price that in. For each team-game the model finds absent regulars (ruled Out or Doubtful, sat out practice with no game status, or no longer on the active roster, and projected at 8+ carries), takes the volume they would have had, subtracts the part teammates' recent history already reflects (the longer someone has been out, the more of that is already in), and hands a fitted share of the rest to the active teammates **in the same position group** in proportion to their own projected carries. The share is fitted in the backtest (about 62% of the missing carries) and then shrunk by 35% (to about 40%), because the fit on volume overshoots once volume becomes yards; held-out weeks and a replay of past weeks both put the best share at 50-75% of the fit.

It was tested on held-out weeks, market by market. Rushing is shared as above. Receiving (targets) made held-out error worse (overshooting), so it's off. Instead, receiving plays are tagged **teammate out** when a same-position rotation receiver (4+ projected targets) is in his first game out, as context only. In the backtest those teammates average 3-5 yards over projection (about 1 otherwise; from the second game on their recent games already show it), but from a few big games: they beat their projection no more often than anyone (42.6% vs 42.7%), so neither side is flagged or adjusted. Blending in each receiver's own games without the missing teammate was tested too and raised error at every strength.

**Quarterbacks work differently: the replacement takes over.** When a starting QB is ruled out, the QB who plays throws about as many passes as the starter was projected for (a median of 29 attempts for a true backup, 32 overall, and only 5.9 yards per attempt for a true backup against about 7 for starters). A backup's own history is a handful of snaps, which projected him at 15 attempts and 104 yards when the market had him near 198; with his attempts set to the missing starter's, the backtest bias on those games goes from +132 yards to about 0 and the average miss from 135 to 68 yards. The lead active QB gets the starter's projected attempts (never fewer than his own); QBs further down the depth chart are untouched. His yards per attempt stay what his own record says (a 0.9 adjustment for backups didn't help).

**A QB with one game can start.** Other players need two games of history to be projected; a quarterback needs one, so a third-stringer pressed into a start (Joe Fagnano, Baltimore, 2026 week 9, with one game behind him and Lamar Jackson and Tyler Huntley out) gets the missing starter's attempts instead of leaving the team with no passer at all. A starting QB with fewer than three games is marked *Thin history* and isn't priced: on 2021-2026 QB changes his attempts came out right (29.5 projected, 29.1 thrown) but his yards missed by 78 on average, against 59 for established starters, wider than the passing error table assumes.

**Receivers follow a downgrade at quarterback.** Their projections come from games thrown by whoever started then, so when a weaker passer takes over, their receiving yards are scaled by (the starter's projected yards per attempt ÷ the recency-weighted average of the starters behind their last 16 games)^1.5 (`QB_REC_K`, `redistribute.passer_ratio`). A team whose starter threw every one of those games gets exactly 1. Only a downgrade counts. On 2021-2026 (11,168 receiver-games), receivers behind a starter rated under 0.95 ran 0.90x their projection against 1.05x with the QB unchanged; the adjustment brings them to 1.03x and lowers receiving error in every two-season block (23.85 to 23.78 yards overall, 23.45 to 22.52 on those games). A better QB taking over showed no matching lift, and receptions showed no effect, so neither is adjusted. Longest reception and rush + rec follow the cut, and the drawer's *Why* panel shows it as its own step ("QB change"). Week 9 2026: Zay Flowers 74 to 63 yards with Fagnano starting.

**Only the expected starter is projected.** Each team's starting QB is the one who made its most recent start, looking past fill-in starts (made while another QB was ruled Out or Doubtful that week or the next, or listed Questionable and inactive) and last season's week 18 rest games. That names the actual starter in 90% of past games. Backups are dropped unless the starter is listed Questionable; projecting them used to split a team's passing between two QBs (Seattle had Drew Lock at 122 yards next to Sam Darnold). A QB's volume also leans on his starts: his relief snaps and injury exits count a third as much (`NONSTART_W = 0.35`, fitted on the backtest), which took the passing error on starters from 62.3 to 61.9 yards and their bias from +6 to +1.5 yards.

### Anytime touchdown

For each player with a real rushing or receiving role, the expected touchdowns in the game are
`carries x TD-per-carry + targets x TD-per-target`. Both rates are recency-weighted and shrunk toward the position average, and the total is scaled by how many points the team is expected to score (from the spread and total). The chance of at least one TD is the Poisson probability `1 - exp(-expected TDs)`, with one scale factor fit in the backtest so the average predicted rate matches the actual rate. Passing TDs don't count, matching the market.

The backtest scores it with a Brier score against always predicting the average TD rate, and shows a calibration table on the Model check tab. It's a weak signal: it has no red-zone or goal-line usage, and books price the market sharply. So TD plays always lean at least 60% on the market's chance (books post a lone "Yes" price with a large margin, which the app estimates and strips), are priced in their own tab rather than in "All yardage", and large edges are flagged. Prices aren't limited to -300..+300 for this market, since +400 to +2000 are ordinary.

### Receptions

Receptions use the same machinery as yardage with catches in place of yards: projected targets x a recency-weighted catch rate shrunk toward the position average (about 63% for wide receivers, 72% for tight ends, 79% for running backs), with the same opponent and game-script adjustments. Weather isn't applied (it was fitted on yards, not catches). Win probabilities come from the backtest's own errors, as for yardage; in the backtest, a predicted 48% chance of going over a line hit 48% of the time, and every band was within a few points.

### Team moneyline

Each team gets a rating in points from a ridge regression of recent games' point margins (home rating - away rating + home field), recency-weighted with a 10-week half-life, with earlier seasons counting half as much. The predicted margin becomes a win probability through a normal curve whose spread (about 14 points) is fitted on a walk-forward backtest every run; it takes a couple of seconds, so even a fast sync refits it.

It's honest about how weak that is: over the last two seasons it scores a Brier of about 0.222, against 0.249 for always giving the home team its usual win rate, but the closing moneyline scores about 0.209 on the same games, and no blend of the two beat the market alone. The ratings know nothing about injuries, quarterbacks or rest. So moneyline prices always lean at least 80% on the market's no-vig chance (the blend that came closest to the market's own score), and the Model check tab shows the comparison.

### Same-game parlays

Legs in one game aren't independent: a quarterback's passing yards rise and fall with his receivers' yards, and the two teams' lead backs pull apart (the team ahead runs, the one behind throws). Multiplying their chances underrated a quarterback-plus-receiver stack by about 30% and overrated a hedge (his Over, the receiver's Under) by about 25%.

Parlays are priced with a Gaussian copula instead (`engine/correlation.py`, mirrored in `frontend/src/copula.ts`): each leg keeps its own calibrated chance, and legs in one game share a correlation that depends on who they are to each other (the same player, teammates or opponents) and their market and position, e.g. `team|pass:QB|rec:WR`. Each correlation is fitted on the walk-forward backtest's own misses (Spearman's rho of the standardized errors, or the tetrachoric correlation for touchdowns), shrunk toward 0 by its sample size, and refit with the rest of the calibration. The joint chance uses the Mendell-Elston approximation, deterministic and within about a tenth of a point of simulation. Legs in different games stay independent.

On held-out weeks (the latest 35% of the backtest), correlated pairs hit together 22.2% of the time against 23.1% predicted (16.7% multiplied), and a quarterback with two of his receivers, all overs, 15.1% against 14.7% (10.0% multiplied). Pairs that pull apart are only partly corrected (11.1% actual, 12.5% predicted, 15.2% multiplied). The Model check tab shows the latest fit's numbers. First-TD legs are left out: two players can't both score first.

The NBA fits the same table on its own backtest (positions grouped as G, F and C), refit with every NBA run. In a measurement over 22,500 player-games, almost all of the correlation sits within one player: points and threes +0.65 to +0.71 for guards and forwards (+0.41 for centers), points and rebounds +0.29 to +0.40, assists with points or rebounds +0.21 to +0.26. Different players are nearly independent (|rho| at most 0.09). Held out, correlated pairs hit together 23.4% of the time against 23.9% predicted (18.7% multiplied). So it matters mostly in the betslip, where one player's markets get stacked; the NBA parlay tiles use one leg per player and barely move.

Books price same-game parlays their own way (usually below the legs multiplied), and The Odds API doesn't carry those prices, so same-game tiles show the **fair odds** (1 / hit chance) to compare with the book's price rather than an EV.

### Probabilities and EV

The chance of beating a line comes from the backtest's own errors, not an assumed curve. For each market the walk-forward backtest gives a robust spread of the errors (binned by projection size, so passing and rushing each get their own) and the empirical distribution of the standardized errors, which carries the real skew and the blow-up games. The probability of going over a line is read straight off that table, so it matches how often actual yardage beat the projection. Whole-number lines leave room for a push, and no probability is allowed to reach 0 or 100%. (An earlier version assumed a gamma curve, which was too skewed and made unders look better than they were; passing was hit hardest, with actuals beating the model's median 67% of the time instead of ~50%.) The model's win probability is then blended with the market's no-vig probability (by the fitted market weight, adjustable in the UI; see Reading the market). EV and quarter-Kelly come from that blend and the best available price.

Two guards keep bad data from topping the board:
- Quotes far from the multi-book consensus line (stale or alternate lines) are ignored.
- Plays where model and market disagree by more than ~40-67% are hidden by default and tagged "large gap" (for rushing, receiving, rush + receiving and passing yards, a projection more than 25% from the consensus line: in the graded 2026 picks, the further those projections sat from the line, the more often the line won). These are usually model blind spots (a teammate's injury, a role change), not value.
- A book that posts several lines in the main market (Bovada lists a ladder 10 yards apart) counts only its line nearest the consensus there; the rest are alternates, for the ladder only. They had been slipping onto the board at long plus-money prices and going 34%.

### Reading the market

`engine/market.py` and `app/market_fit.py`. The fits are redone with each model run after grading and stored in the settings table:

- **Power devig.** The margin is stripped with the power method (find k with p^k + q^k = 1), not proportionally. Books shade longshots more than favorites, so the proportional split overrates the longshot side; the two agree at even prices. Moneylines, props and ladders all use it. A lone anytime-TD "Yes" price still has an assumed margin stripped, since there's no other side to measure.
- **Book weights.** The market's chance is a weighted average of books, each moved to the median line along the model's slope before averaging (quotes far from that line are left out as alternates or stale lines). A book's weight is how close its quotes 6+ hours before the close came to where the other books closed at the same line, over the last six weeks of `odds_history` (two for the NBA): books whose early prices already sit at the close are the ones the market follows. Until a book has ~200 scored quotes its weight leans on a prior (Pinnacle 3, Circa 2, BetOnline/LowVig and the exchanges 1.5, FanDuel/DraftKings 1.25, the slowest offshore books 0.75, others 1). BetOnline and LowVig post the same lines, so they share one weight.
- **Market weights.** How much to trust the market against the model is fitted on graded picks: the blend (1 − w) · model + w · market with the best log loss, for every market and for how long before kickoff the price was fetched (game day, 6–48 h, 48 h+), each level pulled toward its parent by 150 picks and the top toward the old default of 35%. The anytime-TD, first-TD and moneyline floors still apply. Every sync appends the board's prices to `pick_snaps` (a row only when a price or chance changed), so the fit sees Thursday's prices as well as Sunday's. The "Trust market" slider overrides it.
- **The market curve.** Every rung of the ladder, main and alternate, across books, is devigged (a lone alternate price takes its book's own power exponent from its two-sided quotes), and the model's error shape is moved and stretched to fit them on the log-odds scale. The stretch is only fitted when the rungs span at least one typical miss. Rungs are priced from (1 − w) · model + w · curve.
- **Line movement.** `odds_history` keeps every fetch, so each pick carries how far the market's fair chance of its side has moved since the week's first fetch (at its own line) and whether three or more books moved together between the last two fetches with none moving the other way (steam). Fetches are a few per week, not minutes apart, so "steam" here is a move between syncs, not a sharp-money signal in real time.

### Backtest

Walk-forward: every game from 2025 week 6 onward is projected using only earlier data, compared with a naive last-5-games average.

| Market | Games | Model MAE | Last-5 MAE | Bias |
|---|---|---|---|---|
| Rushing | 1,002 | 23.2 | 25.1 | +2.1 |
| Receiving | 1,866 | 23.4 | 24.8 | +0.4 |
| Passing | 489 | 67.0 | 70.4 | +0.6 |
| Rush + Rec | 234 | 33.0 | 36.6 | +4.0 |
| Receptions | 2,116 | 1.69 | 1.78 | -0.0 |
| Anytime TD (Brier score, lower is better) | 2,634 | 0.190 | 0.201 | 0.000 |
| Moneyline (Brier; baseline is the home team's usual win rate; the closing line scores 0.209) | 621 | 0.222 | 0.249 | -0.021 |

MAE is mean absolute error in yards (catches for receptions); bias is the mean of (actual - projection). The passing market initially ran ~9 yards low because volume shrinkage pulled quarterback attempts toward zero; a per-market setting fixed it.

## Running it

Requires Python 3.9+ and Node 20+. Everything goes through one script:

```bash
./run.sh setup      # venv + npm install; creates backend/.env (add ODDS_API_KEY there, optional)
./run.sh refresh    # compute this week's projections (about a minute the first time)
./run.sh start      # app at http://localhost:8000 (API + built UI)
```

`./run.sh lan` starts the app so your phone on the same Wi-Fi can open it (other devices are read-only: refreshes and API-credit-spending fetches stay limited to this machine). Other commands: `./run.sh dev` (API with auto-reload plus the Vite dev server on :5173), `./run.sh build`, `./run.sh test`, and `./run.sh refresh --odds none --exclude "Player Name"`. Run `./run.sh help` for the list.

**Odds:** get a free key at [the-odds-api.com](https://the-odds-api.com) (about 500 credits a month; a full sync costs about 12 credits per game: seven main markets, including the moneyline, plus five alternate-line markets) and put it in `backend/.env`. Without a key the app still shows projections. In the UI, *Refresh data* fetches only markets that have no stored lines; *Re-fetch all odds* is the explicit paid action.

**Tests and CI:** `./run.sh test` runs the backend suite (pytest: model math, pricing logic, API behavior) and the frontend typecheck and tests. GitHub Actions runs the same on every push, and runs the backend suite a second time on Postgres. To do that locally, point `TEST_DATABASE_URL` at an empty Postgres database (the suite wipes it first): `TEST_DATABASE_URL=postgresql://localhost/haber_test .venv/bin/python -m pytest -q`.

**Docker** (untested so far): `docker build -t haber-process . && docker run -p 8000:8000 -v haber-process-data:/srv/backend/data haber-process`. Leave `DATABASE_URL` unset for a SQLite file on the volume, or set it to a Postgres URL.

## Hosting: Railway (one service, no sleep)

The `Dockerfile` builds the React app and the API into one container, so on Railway the whole app is one service at one URL. `.railway/railway.ts` describes the Railway project as code (Railway's Infrastructure as Code): build the `Dockerfile`, health-check `/healthz` with a 120 s timeout, restart on failure, one replica, plus the domain and the data volume. Variables stay on Railway; the file never holds them. Run `npm install --prefix .railway` once, then `railway config plan` (read-only) to see what would change. `railway.json` (the deprecated Config as Code file, read only until 2026-12-01) still governs deploys until the cutover: review `railway config plan`, then run `railway config migrate cutover` and `railway config apply` back to back (so no deploy runs without the health check), and only then delete `railway.json`. `railway config migrate undo` reverses the cutover. Railway doesn't read `.railway/` during deploys; changes to it take effect on `railway config apply`. Use the **Hobby** plan: the Free plan caps a service at 0.5 GB of RAM and $1 of usage a month, which this app outgrows. Leave **Serverless** off so the app never sleeps.

1. **New project → Deploy from GitHub repo** → this repo. Every push to `main` redeploys.
2. **Database:** *+ New → Database → PostgreSQL* (or `railway add --database postgres`).
   **Volume:** right-click the service → *Attach volume*, mount path `/srv/backend/data`, for the ESPN cache, which must survive redeploys.
3. **Variables:** `ODDS_API_KEY`, `DATABASE_URL=${{Postgres.DATABASE_URL}}`, and for accounts `PUBLIC_URL`, `ADMIN_EMAILS` (the
   accounts that get the admin page), `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `RESEND_API_KEY` and `EMAIL_FROM` (see
   `docs/auth-plan.md`). There's no admin password: sign in with an `ADMIN_EMAILS` account. Requests from the host itself (a
   `railway ssh` shell) also count as admin, the way back in if that account is ever unreachable. That URL uses Railway's private network, so the database needs no public port. Without `DATABASE_URL` the app uses a SQLite file on the volume.
4. **Settings → Networking → Generate domain** for a public `*.up.railway.app` URL.
5. **Bring your data** (optional), so the odds you already paid for come along. From your machine, open a tunnel with `railway connect Postgres --tunnel-only --port 54329`, then run `python -m app.copy_db --from sqlite:///path/to/app.db --to 'postgresql://postgres:<password>@127.0.0.1:54329/railway' --with-history`. The password is the Postgres service's `PGPASSWORD`.

**Scheduled syncs** (`backend/app/scheduler.py`) run inside the web process, on by default on Railway (`AUTO_SYNC=1`) and off elsewhere, so a laptop never spends credits by itself. Times are Eastern:

| When | Sync | Odds |
|---|---|---|
| Daily 9:00 AM | Model + top-up | games with no lines, or under 100 lines at least 3 h old |
| Tue 10:00 AM | New week: recalibrate and project | none |
| Wed, Thu 10:00 AM | Model + missing odds | games and markets with no lines yet |
| Thu 4:00 PM, Sat 10:00 AM, Sun 11:00 AM | Model only (injury reports, game statuses) | none |
| Sun 11:30 AM | Model + fresh lines before the early kickoffs | the main markets again for every game still to kick off today (9 credits a game, about 100 for a Sunday slate), so picks are priced against Sunday morning's lines rather than Thursday's; alternates aren't re-bought |

A slot missed by a redeploy (within 3 hours) runs when the server comes back. Every sync, scheduled or *Run now*, is logged on the admin page.

**Credit floor and key rotation.** Before fetching odds, a sync checks the key's balance (a free call) and skips any game whose cost would take it under `ODDS_CREDIT_FLOOR` (default 10). With a spare key set (`ODDS_API_KEY_NFL_SECONDARY`, `ODDS_API_KEY_NBA_SECONDARY` for the NBA), a sync switches to it instead once the first key can't afford a game, notes *rotate ODDS_API_KEY* in the sync log, and the admin page shows a banner naming the key to replace. Every sync starts from the first key, so once it's replaced it's spent first again. With no spare left, games are skipped and the sync is logged as *Partial*. To rotate, replace the key in the service's *Variables* and deploy. A free key's 500 credits a month covers roughly two to three weeks of syncs (a full week is about 180 credits before top-ups), so expect to rotate keys during the season.

### Refreshing a local database

Production runs on Postgres, so local development does too. A local database doesn't sync on its own and goes stale. To
bring it up to date, copy production's projections, odds, logged picks and settings down instead of re-fetching them,
which would spend Odds API credits.

**One-time setup** (Homebrew; production is on Postgres 18):

```bash
brew install postgresql@18 && brew services start postgresql@18
createdb haber           # the dev database
createdb haber_test      # optional: for running the tests on Postgres (the suite wipes it)
```

**Refresh from production:**

```bash
railway connect Postgres --tunnel-only --port 54329        # from propmodel/, in another terminal; Ctrl-C when done
cd backend && NFLPROPS_NO_DOTENV=1 .venv/bin/python -m app.copy_db \
  --from 'postgresql://claude_ro@127.0.0.1:54329/railway' --to postgresql://localhost/haber --without-accounts
```

**Run the app on it,** without spending Odds API credits (`NFLPROPS_NO_DOTENV=1` skips `backend/.env` and its real
key; `AUTO_SYNC=0` turns off the schedule):

```bash
cd backend && DATABASE_URL=postgresql://localhost/haber AUTO_SYNC=0 NFLPROPS_NO_DOTENV=1 \
  .venv/bin/uvicorn app.main:app --port 8000
```

On another port, also set `PUBLIC_URL=http://localhost:<port>`, or sign-in is refused as cross-site. With no email key,
the sign-in code is printed in the server log.

- **`--without-accounts`:** leaves production's people out (accounts, sessions, tracked bets, saved slips) and keeps the
  laptop's own test accounts. Always pass it.
- **Odds history:** it stays behind unless you add `--with-history`.
- **Credentials:** `claude_ro` is read-only, so the copy can't change production. Its password comes from `~/.pgpass`;
  without one, use the `postgres` user and the Postgres service's `PGPASSWORD`. Never use it as the `--to` target.
- **Time:** about 25 seconds.
- **SQLite still works:** leave `DATABASE_URL` unset for `backend/data/app.db`, and refresh that file with
  `--to "sqlite:///$PWD/data/app.db"` (back it up first with `sqlite3 data/app.db ".backup <file>"`). The unit tests
  run on in-memory SQLite by default.

### Test accounts

`app/dev_users.py` makes four signed-in accounts (all `@haber.test`) in states worth testing, built from props on the
current boards. Re-run `seed` after each refresh so their bets and slip point at games on the new boards.

```bash
cd backend && DATABASE_URL=postgresql://localhost/haber NFLPROPS_NO_DOTENV=1 .venv/bin/python -m app.dev_users seed
```

| Account   | State                                                                                                      |
|-----------|------------------------------------------------------------------------------------------------------------|
| `new`     | Signed in, 21+ not confirmed: the age gate. Nothing can be saved.                                          |
| `member`  | 21+, nothing saved: every empty state (My bets, favorites, the slip).                                      |
| `regular` | 21+, with prefs, favorites, a saved slip, and 8 bets: open and settled (won, lost, push), NFL, NBA and custom, a same-game parlay, a bonus bet, a profit boost, a no-sweat bet. |
| `admin`   | 21+, the admin page, when the server has `ADMIN_EMAILS=admin@haber.test`.                                  |

**Sign one in** without the emailed code: `python -m app.dev_users cookie regular` (same env as above) prints a
session token and a line to paste in the browser console. The line signs out any current session, sets the cookie, and
reloads the page. With curl, use `-b sid=<token>`.

**See the site as a non-admin.** Every request from this machine counts as admin, so a laptop normally shows admin
controls to everyone. Start the server with `LOCAL_IS_ADMIN=0` to turn that off. Add `ADMIN_EMAILS=admin@haber.test`
to keep the admin account working.

The script refuses any database that isn't on this machine, including the prod tunnel on port 54329.

## Using it from your phone anywhere (Tailscale, free)

[Tailscale](https://tailscale.com) is a free private network between your own devices, so your phone can reach the app on your Mac from any network without hosting anything.

1. Install Tailscale on the Mac and on the phone ([tailscale.com/download](https://tailscale.com/download)) and sign in to the same account on both.
2. On the Mac, run `./run.sh lan`. It prints a Tailscale address like `http://100.x.y.z:8000`.
3. Open that address on the phone.

To use a Tailscale MagicDNS name instead of the numeric address, set `APP_URL=http://your-mac.your-tailnet.ts.net:8000` in `backend/.env`. The Mac must be awake and running the app. Other devices are read-only.

## Project layout

```
backend/
  app/
    main.py        FastAPI routes, job runner, static file serving
    pipeline.py    data -> backtest -> projections -> DB; odds refresh
    db.py          SQLAlchemy models (runs, projections, odds_lines, alt_lines)
    schemas.py     Pydantic response models
    cli.py         run the pipeline without the web app
    copy_db.py     copy one database into another (e.g. onto the Railway volume)
    engine/        model.py (projections, backtest, probabilities) · injuries.py (injury report for the UI) · td.py (anytime touchdown) · moneyline.py (team ratings, win probability) · weather.py (fitted wind/cold effect, stadium forecasts) · picks.py (pricing) · ladder.py (alt-line pricing) · odds.py · data.py
  tests/           pytest suite
frontend/src/      App, components (picks table, player drawer, line ladder, betslip, controls, refresh), betslip math in slip.ts, typed API client
```

## Limitations

- **The backtest has no historical odds,** so it validates accuracy, not profitability. Every odds fetch is appended to `odds_history`, and graded picks are scored against the close (closing-line value), which is the out-of-sample check against the market. The "close" is the last fetch before kickoff, which for a Thursday game can be that morning's. Sportsbook prop markets are efficient; a large apparent edge usually means the model is missing context.
- **The fitted weights need history.** Until enough picks are graded, market weights sit near 35% and book weights near their priors. Pinnacle, Circa and the exchanges only count if the odds fetch includes them (the `us` region doesn't carry Pinnacle).
- **Teammate redistribution covers carries and quarterback attempts:** a missing back's carries move to his teammates and a missing starting QB's attempts go to his replacement, but a missing receiver's targets are not redistributed (tested, and it made held-out error worse), so receiver backups are still under-projected. A replacement QB's receivers are cut for his weaker passing, but only by how his yards per attempt compare, which is heavily steadied toward the league; the market often cuts them further (Flowers' line sat near 44 against the model's 63).
- **Heuristic adjustments:** opponent and game-script effects are reasonable but untuned.
- **Weather is wind and cold only.** Rain and snow aren't modeled (no history to fit), retractable roofs are assumed closed, and the forecast is as of the last model run. The weather effect is fitted on a few hundred outdoor games with strong wind, so it's noisy, and sportsbooks price wind too.
- **Rush + Rec** treats its two parts as independent, though they are often negatively correlated.
- **Early-season samples are small;** projections lean on last season until enough of this one is played.
- **Injury data is only as fresh as the nflverse feed.** Check game-day statuses before relying on a pick.
- Parameters were tuned on the same games used to report accuracy, so the backtest is somewhat optimistic.

## Roadmap

- Redistribute targets and attempts too, if a better allocation than "same position group" can be shown to help on held-out weeks.
- Model team-level pass/run volume explicitly, then split it among players.
- Pace and offensive-line context; precipitation, once there's a history of it to fit.
- More markets (attempts, touchdowns).
- A team model that knows about injuries and quarterbacks (for example, built up from the player projections), and moneylines in the betslip.
- Multi-leg deep links per sportsbook (today each selection opens as its own link).
- Same-game hit chances in the betslip (its legs don't carry positions yet), and the book's own same-game price.
