from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

Kind = Literal["rush", "rec", "pass", "rr", "td", "recs", "ptd", "lng", "rlng", "ftd", "pts", "reb", "ast", "threes", "pra", "pr", "pa", "stl", "blk", "dd", "tpl"]  # pts on: NBA


class RunInfo(BaseModel):
    id: int
    season: int
    week: int
    created_at: str
    excluded: list[str]


class CalibrationBin(BaseModel):
    predicted: float
    actual: float
    n: int


class BacktestStat(BaseModel):
    n: int
    mae_model: float   # mean absolute error in yards; for anytime TD, the Brier score
    mae_naive: float   # same measure for the naive baseline
    bias: float
    metric: str = "mae"  # "mae" or "brier"
    calibration: list[CalibrationBin] = []
    mae_market: Optional[float] = None              # moneyline: Brier score of the closing moneyline...
    mae_model_market_games: Optional[float] = None  # ...and the model's, on the games that have one
    n_market: Optional[int] = None


class OddsInfo(BaseModel):
    fetched_at: str
    lines: int


class GameWeather(BaseModel):
    stadium: str = ""
    roof: str = "unknown"
    indoor: bool = False
    source: str = "none"              # forecast | indoor | none (no forecast available)
    temp: Optional[float] = None      # degrees F, average over the three hours from kickoff
    wind: Optional[float] = None      # mph, same window
    gust: Optional[float] = None
    wind_dir: Optional[float] = None  # degrees the wind blows FROM (0 = north), averaged over the window
    sky: Optional[str] = None         # e.g. "Partly cloudy", "Rain showers"
    precip_prob: Optional[float] = None  # %, highest in the window
    precip: Optional[float] = None    # inches; shown, not applied (no history to fit it on)
    snow: Optional[float] = None
    fetched_at: Optional[str] = None
    provider: Optional[str] = None    # Open-Meteo | National Weather Service
    factors: dict[str, float] = {}    # per market: multiplier applied to yardage projections


class TeamRecord(BaseModel):
    overall: str                  # "5-2", or "5-2-1" with ties
    home: Optional[str] = None    # at home
    road: Optional[str] = None    # on the road


class Game(BaseModel):
    game_id: str
    label: str
    gameday: str
    gametime: str
    weather: Optional[GameWeather] = None
    records: dict[str, TeamRecord] = {}   # both teams' records this season, by team; empty before they've played


class ByeTeam(BaseModel):
    team: str
    record: Optional[str] = None   # "5-2" this season, when standings are available


class JobStatus(BaseModel):
    state: Literal["idle", "running", "done", "error"]
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    log: list[str] = []
    error: Optional[str] = None
    stage: Optional[str] = None      # what it is doing now, e.g. "Running the model"
    step: int = 0                    # which step (1-based) of `steps`
    steps: int = 0
    detail: Optional[str] = None     # the latest sub-step message, e.g. "game 3 of 15"
    progress: Optional[float] = None # 0..1 within the odds step (None while the model runs)


class AutoSync(BaseModel):
    enabled: bool
    next_at: Optional[str] = None     # UTC, when the next scheduled sync runs
    next_label: Optional[str] = None


class Meta(BaseModel):
    run: Optional[RunInfo]
    backtest: dict[str, BacktestStat]
    odds: dict[str, OddsInfo]
    games: list[Game]
    next_games: list[Game] = []  # next week's schedule, for a preview once this week's slate is down to its last games
    next_byes: list[ByeTeam] = []  # teams with no game next week
    has_odds_key: bool
    job: JobStatus
    lan_url: Optional[str] = None
    books: list[str] = []
    calibration: Optional[dict] = None  # when/for which week the error tables were last fitted
    redistribution: Optional[dict] = None  # what teammate redistribution did in the backtest (see engine/redistribute.py)
    weather: Optional[dict] = None  # the fitted weather effect and what it did in the backtest (see engine/weather.py)
    can_write: bool = False  # this client may refresh data / fetch alt lines (local, or sent the admin token)
    can_run_projections: bool = True  # False on small hosts that only fetch odds
    auto_sync: Optional[AutoSync] = None


class ProjectionOut(BaseModel):
    player_id: str
    name: str
    pos: str
    team: str
    opp: str
    home: bool
    kind: Kind
    mu: float
    sd: float
    vol: float
    eff: float
    spread: float
    status: str
    game_id: str
    gameday: str
    gametime: str
    last5: list[float]
    wx: Optional[float] = None  # weather multiplier already in mu (1 or None: no effect)
    teammate_out: Optional[str] = ""  # same-position rotation receivers in their first game out
    thin: Optional[bool] = False      # thin history (a rookie, or back from IR): shown, never priced
    qb_adj: Optional[float] = None    # receiving cut for a downgrade at QB, already in mu (None: no effect)
    qb_name: Optional[str] = ""       # the starting QB behind qb_adj
    parts: Optional[dict] = None      # NBA: what mu is made of (min, rate, opp, tm, tm_min, out); None for the NFL
    dvp: Optional[float] = None       # matchup grade (engine/dvp.py): the defense's factor against his position
    dvp_rank: Optional[int] = None    # 1 = the softest matchup of dvp_n
    dvp_n: Optional[int] = None


class PickOut(BaseModel):
    player_id: str
    name: str
    pos: str
    team: str
    opp: str
    home: bool
    kind: Kind
    mu: float
    sd: float
    status: str
    game_id: str
    gameday: str
    gametime: str
    side: Literal["Over", "Under"]
    line: float
    odds: int
    book: str
    p_model: float
    p_mkt: float
    prob: float
    ev: float
    kelly: float
    books: int
    edge_yds: float
    flagged: bool
    last5: list[float]
    wx: Optional[float] = None
    flag: str = ""             # why it's flagged: "large gap" | "teammate out"
    teammate_out: str = ""     # same-position rotation receivers in their first game out (shown on Overs too)
    w: Optional[float] = None          # the market weight this pick was priced with (fitted, or the slider's)
    median: Optional[float] = None     # the outcome he beats half the time (below mu when long plays skew the average up)
    hours: Optional[float] = None      # hours from the price's fetch to kickoff
    open_line: Optional[float] = None  # the consensus line at the first fetch of the week
    move: Optional[float] = None       # market's fair chance of this side since that first fetch (+ = toward it)
    steam: int = 0                     # +1 books moved together toward this side between the last two fetches, -1 against
    dvp_rank: Optional[int] = None     # matchup grade: 1 = the softest defense for his position and this market, of dvp_n
    dvp_n: Optional[int] = None


class HotOut(PickOut):
    """A Who's hot row (engine/hot.py): a priced pick whose last `window` games mostly cleared its line."""
    window: int
    hits: int                          # of the last `window` games, how many cleared the line on this side
    streak: int                        # games in a row, most recent back, that cleared it
    recent: list[float]                # those games' values, oldest first
    avg: float                         # their average
    prior_avg: Optional[float] = None  # his average in the games before them (this season and last), when there are any


class GradeLine(BaseModel):
    """Record and units (1 unit a pick at the logged price) for one slice of the graded board."""
    label: str
    n: int
    wins: int
    losses: int
    pushes: int
    voids: int                     # the player didn't play: books void these
    units: float
    roi: Optional[float] = None    # units per decided pick
    expected_wins: float           # what the model's win chances added up to


class MarketCheck(BaseModel):
    """Model projection vs the posted line: which landed closer to the result, per market."""
    kind: Kind
    n: int
    mae_model: float
    mae_line: float
    model_closer: float            # share of player-games where the projection was closer than the line


class Correlations(BaseModel):
    """Same-game correlations for pricing parlays (engine/correlation.py): pair key -> latent correlation. Empty when
    the latest run was fitted before they existed; parlays are then priced as independent legs."""
    pairs: dict[str, float] = {}
    check: dict = {}


class ClvLine(BaseModel):
    """Closing-line value for one slice of the logged board (grading._clv)."""
    label: str
    n: int
    beat: Optional[float] = None       # share whose price beat the close's fair chance
    ev_close: Optional[float] = None   # average EV measured at the close's fair chance
    move: Optional[float] = None       # average move of the market's fair chance toward the pick, priced -> close


class BookWeight(BaseModel):
    book: str
    w: float
    n: int = 0                         # early quotes the fit scored (0 = the prior alone)


class WeightCell(BaseModel):
    label: str                         # a market, or a market's time bucket
    n: int
    w: float
    w_fit: Optional[float] = None
    ll_model: Optional[float] = None
    ll_market: Optional[float] = None
    ll_blend: Optional[float] = None
    floor: Optional[float] = None      # pricing never leans on the market less than this (anytime TD, moneyline)


class MarketFit(BaseModel):
    """The fitted book weights and market weights the board uses (app/market_fit.py)."""
    fitted_at: Optional[str] = None
    n: int = 0
    w: Optional[float] = None
    kinds: list[WeightCell] = []
    buckets: dict[str, list[WeightCell]] = {}
    books: list[BookWeight] = []


class GradeReport(BaseModel):
    season: Optional[int] = None
    graded: int
    pending: int                   # logged, waiting on the game or its stats
    weeks: list[int]
    dates: list[str] = []          # NBA: the first and last graded game dates (it has no weeks)
    board: list[GradeLine]
    by_market: list[GradeLine]
    by_ev: list[GradeLine]
    by_prob: list[GradeLine] = []  # by the win chance shown, for calibration: expected_wins vs wins in each
    by_week: list[GradeLine]
    model_vs_line: list[MarketCheck]
    clv: list[ClvLine] = []
    clv_by_market: list[ClvLine] = []
    market_fit: Optional[MarketFit] = None


class GameLogEntry(BaseModel):
    label: str
    opp: str
    yards: float
    volume: float
    season: Optional[int] = None    # for the drawer's hit-rate splits (this season, last season)
    home: Optional[bool] = None     # NBA only: home or away


class PlayerHit(BaseModel):
    """A player search result: an active player (NFL: offensive skill positions), flagged with whether he is projected this week or slate."""
    player_id: str
    name: str
    pos: str
    team: str
    headshot: Optional[str] = None
    projected: bool


class PlayerDetail(BaseModel):
    player_id: str
    name: str
    pos: str
    team: str
    headshot: Optional[str] = None
    projections: list[ProjectionOut]
    picks: list[PickOut]
    logs: dict[str, list[GameLogEntry]]


class RefreshRequest(BaseModel):
    odds: Literal["none", "missing", "thin", "today", "all"] = "missing"
    full: bool = False  # refit the model's calibration instead of reusing the saved one


class QuoteSide(BaseModel):
    odds: int
    prob: float
    ev: float
    link: Optional[str] = None
    p_model: Optional[float] = None    # the model's chance alone
    p_curve: Optional[float] = None    # the market curve's fair chance (market.fit_market_curve)
    off_curve: Optional[float] = None  # EV against the market curve alone: + = priced better than the book's own ladder


class LadderRow(BaseModel):
    line: float
    book: str
    alt: bool
    event_link: Optional[str] = None
    over: Optional[QuoteSide] = None
    under: Optional[QuoteSide] = None


class LadderOut(BaseModel):
    kind: Union[Kind, Literal["ml"]]   # ml: one team's moneyline at every book (engine/moneyline.side_ladder)
    mu: float
    sd: float
    game_id: str
    alt_fetched_at: Optional[str] = None
    quotes: list[LadderRow]
    curve: Optional[dict] = None       # the market's distribution read off the ladder: {m, s, median, n_lines}
    w: Optional[float] = None          # the market weight blended into each rung's chance


class HistoryPoint(BaseModel):
    at: str
    line: float
    books: int
    p_over: Optional[float] = None       # fair chance of the over at that fetch's consensus line
    p_now_line: Optional[float] = None   # fair chance of the over at the current consensus line
    best_over: Optional[int] = None
    best_under: Optional[int] = None


class AltFetchResult(BaseModel):
    kinds: list[str]
    alt_quotes: int
    linked: int
    credits_remaining: Optional[str] = None


class PlanGame(BaseModel):
    game_id: str
    label: str
    gameday: str
    need_main: list[str]
    need_alt: bool
    alt_kinds: list[str] = []     # which alternate markets (a game may lack only newly added ones)
    credits: int
    reason: str = "missing"       # missing | thin | today | all
    stored_quotes: int = 0


class SyncPlan(BaseModel):
    season: Optional[int] = None
    week: Optional[int] = None
    total_games: int
    games: list[PlanGame]
    credits: int
    will_run_projections: bool
    has_odds_key: bool
    will_recalibrate: bool = False  # True: the model run refits its calibration (slow); False: it reuses the saved one
    calibration: Optional[dict] = None

class MoneylineSide(BaseModel):
    team: str
    name: str
    home: bool
    p_model: float                 # the team-rating model's win chance
    p_mkt: Optional[float] = None  # no-vig consensus across books (None: no two-way prices yet)
    prob: float                    # the blend that prices EV (leans at least 80% on the market)
    books: int = 0
    odds: Optional[int] = None     # best price (at the chosen book, if one is chosen)
    book: Optional[str] = None
    ev: Optional[float] = None
    kelly: Optional[float] = None
    link: Optional[str] = None
    event_link: Optional[str] = None


class InjuredPlayer(BaseModel):
    player_id: str
    name: str
    pos: str
    team: str
    side: Literal["off", "def", "st"]
    status: str             # Out | Doubtful | Questionable | "" (no game status) | IR | PUP | NFI | Reserve
    injury: str
    practice: str           # DNP | Limited | Full | "" (not on the practice report)
    rest: bool              # a veteran's rest day, not an injury
    kind: Optional[str] = None  # a market the app projects for him (opens his player drawer)
    comment: str = ""           # ESPN: the latest news line, e.g. "won't play Monday against the Thunder"
    return_date: Optional[str] = None  # ESPN: estimated return, YYYY-MM-DD
    updated: Optional[str] = None      # ESPN: when the status was last updated (UTC ISO)


class InjuryReport(BaseModel):
    season: Optional[int] = None
    week: Optional[int] = None
    report_week: Optional[int] = None   # the week the report is from (earlier than week until this week's is out)
    fetched_at: Optional[str] = None
    news_fetched_at: Optional[str] = None   # NFL: when ESPN's feed (news lines, return dates) was last read
    teams: dict[str, list[InjuredPlayer]] = {}


class BlowoutChance(BaseModel):
    margin: int        # decided by this many points or more
    p: float           # either way
    p_home: float      # the home team winning by that much
    p_away: float


class Blowout(BaseModel):
    games: int                     # games the model was fitted on
    chances: list[BlowoutChance]   # 15+, 20+, 25+, 30+


class HotTeamOut(MoneylineSide):
    """A team on a winning streak (Who's hot), with its moneyline (engine/moneyline.hot_teams)."""
    game_id: str
    label: str
    gameday: str
    gametime: str
    opp: str
    streak: int                    # wins in a row, most recent back
    window: int                    # how many recent results `recent` holds
    wins: int                      # of those, how many it won
    recent: list[float]            # their point margins, oldest first
    avg_margin: float


class MoneylineGame(BaseModel):
    game_id: str
    label: str
    gameday: str
    gametime: str
    margin: float                  # the model's predicted home margin, points
    spread: Optional[float] = None # the market's (nflverse) home spread, points
    total: Optional[float] = None  # the market's points total (NBA, from ESPN)
    sides: list[MoneylineSide]     # away, home
    blowout: Optional[Blowout] = None   # NBA: chances the game is decided by 15/20/25/30+, from the spread


class KeyBalance(BaseModel):
    key_name: str                     # env var, e.g. ODDS_API_KEY_NFL_SECONDARY
    key_hint: str                     # last 4 characters
    remaining: Optional[int] = None
    error: Optional[str] = None


class CreditStatus(BaseModel):
    remaining: Optional[int] = None   # credits left on the key syncs are spending (the first one over the floor)
    floor: int                        # odds fetches stop before the balance drops under this
    low: bool = False                 # every key at or under the floor: syncs have stopped fetching odds
    key_hint: Optional[str] = None    # last 4 characters, to tell keys apart
    key_name: Optional[str] = None    # its env var, e.g. ODDS_API_KEY_NFL_SECONDARY
    rotate: list[str] = []            # env vars of keys at or under the floor, to replace
    others: list[KeyBalance] = []     # the league's other keys (the spare, or the spent main key)
    checked_at: Optional[str] = None
    error: Optional[str] = None


class SyncRunOut(BaseModel):
    id: int
    slot: str
    label: str
    trigger: str                      # schedule | catch-up | manual
    scheduled_for: Optional[str] = None
    started_at: str
    finished_at: Optional[str] = None
    status: str                       # running | ok | partial | error | skipped | interrupted
    odds_mode: str
    full: bool
    season: Optional[int] = None
    week: Optional[int] = None
    games_fetched: int = 0
    credits_before: Optional[int] = None
    credits_after: Optional[int] = None
    summary: str = ""
    error: Optional[str] = None


class SyncRunDetail(SyncRunOut):
    log: list[str] = []


class SlotOut(BaseModel):
    id: str
    label: str
    league: str = "nfl"
    when: str                         # "Wed 10:00 AM ET"
    odds_mode: str
    full: bool
    why: str
    next_at: str
    last_run: Optional[SyncRunOut] = None


class AdminState(BaseModel):
    auto_sync: AutoSync
    timezone: str
    credits: CreditStatus
    credits_nba: Optional[CreditStatus] = None   # the NBA's own key (ODDS_API_KEY_NBA)
    slots: list[SlotOut]
    runs: list[SyncRunOut]
    job: JobStatus                       # the NFL sync
    job_nba: Optional[JobStatus] = None  # the NBA sync, which can run at the same time
    can_run_projections: bool
    hide_offshore: bool = False


class SiteSettings(BaseModel):
    hide_offshore: bool


class NbaRunInfo(BaseModel):
    id: int
    season: int                       # ESPN's season year: 2027 is the 2026-27 season
    slate: list[str]                  # the Eastern dates the run projects
    created_at: str
    excluded: list[str]


class NbaMeta(BaseModel):
    league: Literal["nba"] = "nba"
    run: Optional[NbaRunInfo]
    backtest: dict[str, BacktestStat]
    odds: dict[str, OddsInfo]
    games: list[Game]
    has_odds_key: bool
    books: list[str] = []
    auto_sync: Optional[AutoSync] = None
    can_write: bool = False
    supports_alt: bool = False        # no alternate-line fetches for the NBA yet


# ---------- accounts (app/auth.py) ----------
class Prefs(BaseModel):
    """What the preferences page saves to an account. Unset fields keep the site's default."""
    theme: Optional[Literal["auto", "light", "dark"]] = None
    game_book: Optional[str] = None         # the sportsbook game pages open at ("all": every book)
    one_per_player: Optional[bool] = None   # Best props: one pick per player
    detailed_cards: Optional[bool] = None   # Best props on phones: the detailed card layout
    # the main sections' order in the top bar (frontend prefs.ts MAIN_TABS)
    tab_order: Optional[list[Literal["games", "picks", "parlays", "hot", "projections"]]] = Field(None, max_length=5)


class FavPlayer(BaseModel):
    """A favorite player as the compare page lists him (the search hit's fields), so the list shows without a lookup."""
    player_id: str = Field(max_length=40)
    name: str = Field(max_length=80)
    pos: str = Field("", max_length=8)
    team: str = Field("", max_length=8)
    headshot: Optional[str] = Field(None, max_length=500)


class LeagueFavorites(BaseModel):
    teams: list[str] = []
    players: list[FavPlayer] = []


class Favorites(BaseModel):
    nfl: LeagueFavorites = LeagueFavorites()
    nba: LeagueFavorites = LeagueFavorites()


class FavoritesIn(BaseModel):
    """Replace one league's favorite teams or players (a field left out keeps what the account has)."""
    league: Literal["nfl", "nba"]
    teams: Optional[list[str]] = Field(None, max_length=40)
    players: Optional[list[FavPlayer]] = Field(None, max_length=40)


class AccountOut(BaseModel):
    email: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    is_admin: bool
    age_confirmed: bool
    prefs: Prefs
    favorites: Favorites = Favorites()


class AuthState(BaseModel):
    user: Optional[AccountOut] = None
    google: bool           # Google sign-in is set up on this server
    next: Optional[str] = None   # after a sign-in: where to go


class EmailSignIn(BaseModel):
    email: str
    next: str = "/"


class EmailVerify(BaseModel):
    token: Optional[str] = None
    email: Optional[str] = None
    code: Optional[str] = None


# ---------- tracked bets (app/bets.py) ----------
class BetLegIn(BaseModel):
    game_id: str = ""
    player_id: str = ""
    # "custom": a leg the site can't grade (a team total, a market it doesn't project), described in words and marked by hand
    # "ml": a team's moneyline (player_id is the team), graded from the final score
    kind: Union[Kind, Literal["custom", "ml"]]
    side: Literal["Over", "Under"] = "Over"
    line: float = 0.0
    odds: Optional[int] = None      # a parlay leg's own price, when the book showed one (a single's is required)
    description: Optional[str] = None
    alt: bool = False
    prob: Optional[float] = None
    ev: Optional[float] = None


class BetIn(BaseModel):
    book: str
    kind: Literal["single", "parlay", "custom"]
    stake: float
    odds: Optional[int] = None      # a parlay's or custom bet's price at the book; a single uses its leg's
    legs: list[BetLegIn] = []
    notes: Optional[str] = None
    tags: list[str] = []
    # a custom bet (anything the site can't grade: a spread, a total, another sport, a boost), settled by hand
    description: Optional[str] = None
    starts_at: Optional[str] = None  # when its event starts (ISO, UTC): it can only be tracked before then
    league: Optional[Literal["nfl", "nba", "oth"]] = None
    bonus: bool = False              # placed with a bonus bet: not the bettor's money, and a win pays only the profit


class BetPlayer(BaseModel):
    """A player with a game still to start, for adding a prop placed elsewhere (app/bets.py bet_players)."""
    player_id: str
    name: str
    pos: str
    team: str
    opp: str
    game_id: str
    kickoff: str                     # UTC
    kinds: list[str]                 # the markets he's projected in


class BetPatch(BaseModel):
    stake: Optional[float] = None
    odds: Optional[int] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
    bonus: Optional[bool] = None
    # settle by hand (the book settled differently), or "open" to hand it back to grading
    status: Optional[Literal["open", "won", "lost", "push", "void"]] = None


class BetLegOut(BaseModel):
    id: int
    game_id: str
    gameday: str
    kickoff: str
    player_id: str
    name: str
    team: str
    opp: str
    kind: str
    side: str
    line: float
    odds: int
    alt: bool
    prob: Optional[float] = None
    ev: Optional[float] = None
    actual: Optional[float] = None
    result: Optional[str] = None
    close_p: Optional[float] = None   # the market's fair chance of this leg at kickoff (None: not started, or no closing quotes)
    ev_close: Optional[float] = None  # what this price was worth at that chance: close_p x decimal odds - 1


class BetOut(BaseModel):
    id: int
    league: str
    book: str
    kind: str
    stake: float
    odds: int
    status: str
    payout: Optional[float] = None
    profit: Optional[float] = None
    to_pay: float                    # what it returns if it wins, at the recorded odds
    settled_by: Optional[str] = None
    notes: Optional[str] = None
    tags: list[str] = []
    description: Optional[str] = None
    bonus: bool = False              # a bonus bet: profit = payout, and it adds nothing to what was staked
    placed_at: str
    settled_at: Optional[str] = None
    started: bool                    # its first game has kicked off
    ev_close: Optional[float] = None # the bet's price at the close: a parlay's legs' closing chances multiplied, x its odds, - 1
    legs: list[BetLegOut]


class BetSummary(BaseModel):
    wins: int
    losses: int
    pushes: int                      # pushes and voids: stake back
    open: int
    open_stake: float
    staked: float                    # the bettor's own money on bets that won or lost (bonus bets left out)
    bonus_staked: float = 0.0        # bonus bets' stakes on bets that won or lost
    profit: float
    roi: Optional[float] = None
    curve: list[tuple[str, float]]   # (settled_at, running profit), oldest first
    clv_n: int = 0                   # bets with a closing price (not void)
    beat_close: Optional[float] = None   # share of those priced better than the close
    ev_close: Optional[float] = None     # their average EV at the close


class BetsOut(BaseModel):
    bets: list[BetOut]
    summary: BetSummary
    books: list[str]                 # every book this person has tracked at, for the filter
    tags: list[str]                  # every tag they've used, for the filter and suggestions
    leagues: list[str] = []          # the leagues they have bets in (nfl, nba, oth), for the filter
    can_read_screenshots: bool = False   # Add a bet can read a slip screenshot (app/bet_screens.py)


# ---------- the betslip, synced between a signed-in visitor's devices (app/slip_sync.py) ----------
class SlipIn(BaseModel):
    legs: list[dict]
    stakes: dict[str, float] = {}
    base: Optional[str] = None      # the updated_at this device last saw: a newer one on the server means another device saved


class SlipOut(BaseModel):
    legs: list[dict]
    stakes: dict[str, float]
    updated_at: Optional[str] = None   # None: nothing saved yet
