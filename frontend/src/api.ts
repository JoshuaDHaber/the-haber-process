import { getLeague, type League } from "./league";
import { mlLeague, parseMlId } from "./moneyline";

/** NFL markets, then the NBA's points, rebounds, assists and 3-pointers made; "ml" is a team's moneyline (both leagues), which
 *  rides on the betslip and in parlays beside the props but has no board, projection or drawer of its own. */
export type Kind = "rush" | "rec" | "pass" | "rr" | "td" | "recs" | "ptd" | "lng" | "rlng" | "ftd" | "pts" | "reb" | "ast" | "threes" | "pra" | "pr" | "pa" | "stl" | "blk" | "dd" | "tpl" | "ml";
export type NbaKind = "pts" | "reb" | "ast" | "threes" | "pra" | "pr" | "pa" | "stl" | "blk" | "dd" | "tpl";
const NBA_KIND_SET = new Set<Kind>(["pts", "reb", "ast", "threes", "pra", "pr", "pa", "stl", "blk", "dd", "tpl"]);

export interface CalibrationBin { predicted: number; actual: number; n: number }
/** Yardage kinds report mean absolute error in yards; anytime TD reports Brier scores (metric: "brier"). */
/** What moving an absent regular's volume to his teammates did in the backtest. */
export interface Redistribution {
  rho: Record<string, number>;       // shares actually applied (0 = that role is switched off)
  rho_fit: Record<string, number>;   // shares the data suggested for every role
  active_roles: string[];
  replace_roles?: string[];          // roles where a replacement takes over outright (quarterback attempts)
  by_kind: Record<string, { n: number; bias_before: number; bias_after: number; mae_before: number; mae_after: number }>;
  summary: string;
}

/** The sync schedule's state for the admin page. Times are UTC without a zone suffix, like the rest of the API. */
export interface AutoSync { enabled: boolean; next_at: string | null; next_label: string | null }
export interface KeyBalance { key_name: string; key_hint: string; remaining: number | null; error: string | null }
export interface CreditStatus { remaining: number | null; floor: number; low: boolean; key_hint: string | null; key_name?: string | null; rotate?: string[]; others?: KeyBalance[]; checked_at: string | null; error: string | null }
export interface SyncRun {
  id: number; slot: string; label: string; trigger: "schedule" | "catch-up" | "manual";
  scheduled_for: string | null; started_at: string; finished_at: string | null;
  status: "running" | "ok" | "partial" | "error" | "skipped" | "interrupted";
  odds_mode: string; full: boolean; season: number | null; week: number | null;
  games_fetched: number; credits_before: number | null; credits_after: number | null; summary: string; error: string | null;
}
export interface SyncRunDetail extends SyncRun { log: string[] }
export interface ScheduleSlot { id: string; label: string; league?: "nfl" | "nba"; when: string; odds_mode: string; full: boolean; why: string; next_at: string; last_run: SyncRun | null }
export interface AdminState { auto_sync: AutoSync; timezone: string; credits: CreditStatus; credits_nba?: CreditStatus | null; slots: ScheduleSlot[]; runs: SyncRun[]; job: JobStatus; job_nba?: JobStatus | null; can_run_projections: boolean;
  /** Offshore books (licensed in no US state) are kept off every list and price. */
  hide_offshore: boolean }

export interface BacktestStat {
  n: number; mae_model: number; mae_naive: number; bias: number; metric?: "mae" | "brier"; calibration?: CalibrationBin[];
  mae_market?: number | null; mae_model_market_games?: number | null; n_market?: number | null;  // moneyline: vs the closing line
}
/** Kickoff weather at the stadium (outdoor games), and the multiplier it puts on each market's yardage. */
export interface GameWeather {
  stadium: string; roof: string; indoor: boolean;
  source: "forecast" | "indoor" | "none";
  temp: number | null; wind: number | null; gust: number | null;   // °F and mph, three hours from kickoff
  wind_dir?: number | null;   // degrees the wind blows FROM (0 = north)
  sky?: string | null;        // "Partly cloudy", "Rain showers", ...
  precip_prob: number | null; precip: number | null; snow: number | null;  // shown, not applied
  fetched_at: string | null;
  provider?: string | null;   // Open-Meteo, or the National Weather Service as a US fallback
  factors: Partial<Record<"rush" | "rec" | "pass", number>>;
}
/** The fitted weather effect (per mph of wind over wind_from, per degree under cold_from) and what it did in the backtest. */
export interface WeatherFit {
  coef: Record<string, { wind: number; cold: number; wind_raw: number; cold_raw: number; wind_se: number | null; cold_se: number | null; n: number; shared?: boolean }>;
  wind_from: number; cold_from: number;
  by_kind: Record<string, { n: number; bias_before: number; bias_after: number; mae_before: number; mae_after: number }>;
}
/** A team's record this season: "5-2", or "5-2-1" with ties. */
export interface TeamRecord { overall: string; home?: string | null; road?: string | null }
/** records: both teams' records by team, empty before they've played. */
export interface Game { game_id: string; label: string; gameday: string; gametime: string; weather?: GameWeather | null; records?: Record<string, TeamRecord> }
export interface JobStatus {
  state: "idle" | "running" | "done" | "error";
  started_at: string | null; finished_at: string | null; log: string[]; error: string | null;
  stage?: string | null;      // what it is doing now
  step?: number; steps?: number;
  detail?: string | null;     // latest sub-step, e.g. "game 3 of 15"
  progress?: number | null;   // 0..1 through the odds step
}
export interface Meta {
  run: { id: number; season: number; week: number; created_at: string; excluded: string[] } | null;
  backtest: Record<string, BacktestStat>;
  odds: Record<string, { fetched_at: string; lines: number }>;
  games: Game[];
  /** Next week's schedule (no projections or lines yet), previewed under the slate once it's down to its last games. */
  next_games?: Game[];
  /** Teams with no game next week, with their record when standings are in. */
  next_byes?: { team: string; record?: string | null }[];
  has_odds_key: boolean;
  job: JobStatus;
  lan_url: string | null;
  books: string[];
  redistribution?: Redistribution | null;
  weather?: WeatherFit | null;
  calibration?: { at: string; season: number; week: number; reused_from_run?: number } | null;
  can_write: boolean;
  can_run_projections: boolean;
  auto_sync?: AutoSync | null;
}
export interface Projection {
  player_id: string; name: string; pos: string; team: string; opp: string; home: boolean; kind: Kind;
  mu: number; sd: number; vol: number; eff: number; spread: number; status: string;
  game_id: string; gameday: string; gametime: string; last5: number[];
  wx?: number | null;   // weather multiplier already in mu (1 or null: none)
  /** Thin history (a rookie, or back from IR): shown for reference, never priced. Older snapshots lack it. */
  thin?: boolean | null;
  /** NFL receiving: the cut for a starting QB who passes less efficiently than the QBs in the team's recent games
   *  (backend model.qb_fields), already in mu, and that QB's name. Null / "" with no cut. */
  qb_adj?: number | null; qb_name?: string | null;
  /** NBA: what mu is made of. min: minutes from his own games; tm_min: the teammates-out multiplier on them; rate: his
   *  per-minute rate (shrunk to his position); opp: the opponent's factor; tm: the whole teammates-out multiplier on the
   *  stat; out: the absent regulars counted (missed: team games already missed). Doubles carry only min, tm_min and out. */
  parts?: { min: number; tm_min: number; rate?: number; opp?: number; tm?: number; out: { name: string; missed: number }[] } | null;
  /** Matchup grade (backend engine/dvp.py): the defense's factor against his position, its rank (1 = softest) of dvp_n. */
  dvp?: number | null; dvp_rank?: number | null; dvp_n?: number | null;
  /** NFL: what the grade is for, his depth slot ("WR2", "RB1") or, past the depth tracked, his position ("WR"). */
  dvp_slot?: string | null;
}
export interface Pick {
  player_id: string; name: string; pos: string; team: string; opp: string; home: boolean; kind: Kind;
  mu: number; sd: number; status: string; game_id: string; gameday: string; gametime: string;
  side: "Over" | "Under"; line: number; odds: number; book: string;
  p_model: number; p_mkt: number; prob: number; ev: number; kelly: number; books: number;
  edge_yds: number; flagged: boolean; last5: number[];
  wx?: number | null;
  /** Why it's flagged (hidden by default): model and market far apart, a double/triple-double Yes longer than +400,
   *  or (NBA) EV over 20%. Older snapshots lack it. */
  flag?: "" | "large gap" | "long shot" | "edge too big";
  /** Same-position rotation receivers in their first game out (receiving yards). Context only, not a flag. */
  teammate_out?: string;
  /** The market weight it was priced with (the fitted one for its market and fetch time, or the slider's). */
  w?: number | null;
  /** Hours from the price's fetch to kickoff. */
  hours?: number | null;
  /** The consensus line at the week's first fetch, and how far the market's fair chance of this side has moved since
   *  (at this line; positive = toward this side). Null with only one fetch. */
  open_line?: number | null;
  move?: number | null;
  /** +1: books moved together toward this side between the last two fetches; -1: against it. */
  steam?: number;
  /** The outcome he beats half the time: below mu where a few long plays pull the average up (longest rush). Null for yes/no markets. */
  median?: number | null;
  /** Matchup grade: the defense's rank against his position for this market (1 = softest) of dvp_n. */
  dvp_rank?: number | null; dvp_n?: number | null; dvp_slot?: string | null;
}
/** A player search hit: an active player; projected is false for players on bye, off the NBA slate, or without a projection. */
export interface PlayerHit { player_id: string; name: string; pos: string; team: string; headshot?: string | null; projected: boolean }
export interface GameLogEntry { label: string; opp: string; yards: number; volume: number;
  /** For the hit-rate splits; older servers leave them out (home: NBA only). */
  season?: number | null; home?: boolean | null }
export interface PlayerDetail {
  player_id: string; name: string; pos: string; team: string; headshot?: string | null;
  projections: Projection[]; picks: Pick[]; logs: Record<string, GameLogEntry[]>;
}

/** prob blends the model (p_model) with the market curve read off the whole ladder (p_curve); off_curve is the EV against the
 *  curve alone: positive means the price beats what the books' own ladder implies. flag "long shot": an NBA alternate over
 *  longer than +250, which has hit well below its price, so its EV isn't counted as an edge. */
export interface QuoteSide { odds: number; prob: number; ev: number; link: string | null; p_model?: number | null; p_curve?: number | null; off_curve?: number | null;
  flag?: "" | "long shot" }
export interface LadderRow { line: number; book: string; alt: boolean; event_link: string | null; over: QuoteSide | null; under: QuoteSide | null }
export interface Ladder { kind: Kind; mu: number; sd: number; game_id: string; alt_fetched_at: string | null; quotes: LadderRow[];
  /** The market's distribution read off the ladder (location m, spread s, its median) and the weight it gets in each rung's chance. */
  curve?: { m: number; s: number; median: number; n_lines: number } | null; w?: number | null }
/** One odds fetch of a player's market: the consensus line, its fair over chance, and the over's fair chance at the current line. */
/** Players in one depth slot who faced a defense lately (backend engine/dvp.recent_vs), newest first. */
export interface VsDefenseRow { player_id: string; name: string; team: string; season: number; week: number; actual: number; usual: number | null;
  line: number | null; result: "over" | "under" | "push" | null }
export interface VsDefense { kind: Kind; opp: string; slot: string; games: number; rows: VsDefenseRow[] }
/** One defense's grade against a depth slot (NFL) or position (NBA) in one market (backend engine/dvp.soft_spots). */
export interface SoftSpotPlayer { player_id: string; name: string; team: string; pos: string; mu: number; game_id: string }
export interface SoftSpot { defense: string; key: string; kind: Kind; factor: number; rank: number; n: number; grade: string;
  allowed: number | null; games: number | null; league: number | null; players: SoftSpotPlayer[] }
/** The slate's market from stored odds history (backend market_fit.slate_market). move: the fair chance of the over at
 *  today's line, now minus the first fetch (+ = toward the over). */
export interface MarketMove { game_id: string; kind: Kind; player_id: string; name: string; team: string; pos: string; line: number; books: number;
  p_over: number | null; fetches: number; open_line: number | null; move: number | null; steam: number }
export interface MarketOutlier { game_id: string; kind: Kind; player_id: string; name: string; team: string; pos: string; book: string; line: number;
  consensus: number; side: "Over" | "Under"; odds: number; fair: number; ev: number; books: number }
export interface MarketBoard { fetches: number; first_at: string | null; last_at: string | null; movers: number; flat: number; moves: MarketMove[]; outliers: MarketOutlier[] }
/** The game page's context (backend engine/gamectx.py): implied points, a script read, and (NFL) each team's shares. */
export interface ShareRow { player_id: string; name: string; pos: string; team: string; games: number; played: number;
  tgt_l5: number; tgt_prior: number | null; tgt_trend: number; car_l5: number; car_prior: number | null; car_trend: number;
  rz_share: number | null; rz: number; rz_team: number; p_td: number | null; exp_td: number | null; p_td2: number | null }
export interface TeamContext { team: string; implied: number | null; opp_implied: number | null; spread: number | null; total: number | null; script: string[]; players: ShareRow[] }
export interface GameContext { game_id: string; teams: TeamContext[] }
export interface HistoryPoint { at: string; line: number; books: number; p_over: number | null; p_now_line: number | null; best_over: number | null; best_under: number | null }
/** One team's side of a game: win chance from the team model, the market (no-vig) and the blend, and the best moneyline. */
export interface MoneylineSide {
  team: string; name: string; home: boolean;
  p_model: number; p_mkt: number | null; prob: number; books: number;
  odds: number | null; book: string | null; ev: number | null; kelly: number | null; link: string | null; event_link: string | null;
  token?: LeadToken | null;
}
/** An early-win token on a moneyline, which pays as a winner once the team goes up by `threshold`. The bet cashes if it
 *  goes up by that much or wins (p_cash); games/up/up_lost are this season's record: games, how many it went up, how many of those it lost. */
export interface LeadToken { threshold: number; p_lead: number; p_cash: number; ev: number | null; games: number; up: number; up_lost: number }
/** A team on a winning streak (Who's hot), with its moneyline: `recent` holds its last `window` point margins, oldest first. */
export interface HotTeam extends MoneylineSide {
  game_id: string; label: string; gameday: string; gametime: string; opp: string;
  streak: number; window: number; wins: number; recent: number[]; avg_margin: number;
}
/** NBA: the chance a game is decided by `margin`+ points, either way and by each team. */
export interface BlowoutChance { margin: number; p: number; p_home: number; p_away: number }
export interface MoneylineGame {
  game_id: string; label: string; gameday: string; gametime: string; margin: number; spread: number | null; total?: number | null; sides: MoneylineSide[];
  blowout?: { games: number; chances: BlowoutChance[] } | null;
}
/** NBA King of the Court: a player's chance to have the most points + rebounds + assists on one day's slate (ties count), and
 *  his PRA over at the board's book when one is posted. A sleeper sits outside the top projections with a soft matchup and a real chance. */
export interface KingRow {
  player_id: string; name: string; pos: string; team: string; opp: string; home: boolean; game_id: string; gameday: string; gametime: string; status: string;
  mu: number; sd: number; proj_rank: number; p_top: number; p_top3: number; dvp_rank: number | null; dvp_n: number | null; sleeper: boolean; pick: Pick | null;
}
export interface KingBoard { gameday: string | null; games: number; sims: number; players: KingRow[] }
/** NFL King of the Endzone: each entry's chance to score one game's longest touchdown (ties split: dead-heat equity). An entry is a
 *  listed scorer (with his anytime TD at the board's book when posted), the rest of a team's offense, or its defense and returns. */
export interface EndzoneRow {
  player_id: string | null; name: string; pos: string; team: string; kind: "player" | "other" | "ret";
  p_td: number; p_long: number; p_long_if_td: number; median_td: number | null; n_tds: number; status: string; pick: Pick | null;
}
export interface EndzoneBoard {
  game_id: string | null; label: string; gameday: string; gametime: string; total: number | null; total_assumed: boolean; spread: number | null;
  median_longest: number | null; p_no_td: number; p_tie: number; sims: number; rows: EndzoneRow[];
}
/** This week's injury report per team (both sides of the ball) plus reserve lists. Times are UTC without a zone suffix. */
export interface InjuredPlayer {
  player_id: string; name: string; pos: string; team: string; side: "off" | "def" | "st";
  status: string;     // Out | Doubtful | Questionable | "" (no game status) | IR | PUP | NFI | Reserve
  injury: string; practice: "DNP" | "Limited" | "Full" | ""; rest: boolean;
  kind: Kind | null;  // a market the app projects for him (opens his drawer)
  comment?: string;            // ESPN: the latest news line
  return_date?: string | null; // ESPN: estimated return, YYYY-MM-DD
  updated?: string | null;     // ESPN: when the status changed (UTC ISO)
}
export interface InjuryReport {
  season: number | null; week: number | null; report_week: number | null; fetched_at: string | null;
  news_fetched_at?: string | null;  // NFL: when ESPN's feed (news, return dates) was last read
  teams: Record<string, InjuredPlayer[]>;
}
/** /api/nba/meta: the NBA slate (the next two game dates), its odds and the model's backtest. */
export interface NbaMeta {
  league: "nba";
  run: { id: number; season: number; slate: string[]; created_at: string; excluded: string[] } | null;
  backtest: Record<string, BacktestStat>;
  odds: Record<string, { fetched_at: string; lines: number }>;
  games: Game[];
  has_odds_key: boolean;
  books: string[];
  auto_sync?: AutoSync | null;
  can_write: boolean;
  supports_alt: boolean;
}
export interface AltFetchResult { kinds: string[]; alt_quotes: number; linked: number; credits_remaining: string | null }

/** fetch against the API (same origin). Who's asking is the session cookie (the owner's account opens admin endpoints). */
export async function request(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(path, init);
}

/** A team's moneyline at every book, as a ladder (its leg's "player" id is the team and its game: moneyline.ts). */
function moneylineLadder(id: string, marketWeight: number | null): Promise<Ladder> {
  const ml = parseMlId(id);
  if (!ml) return Promise.reject(new Error("Not a moneyline"));
  return get<Ladder>(`${mlLeague(ml.gameId) === "nba" ? "nba/" : ""}moneylines/${encodeURIComponent(ml.gameId)}/${encodeURIComponent(ml.team)}/lines`,
    { market_weight: marketWeight ?? undefined });
}

/** League data lives under /api/nba/... for the NBA; admin and sync endpoints are shared. */
const lg = (path: string) => (getLeague() === "nba" ? `nba/${path}` : path);

async function get<T>(path: string, params: Record<string, string | number | boolean | undefined> = {}): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "" && v !== "all") qs.set(k, String(v));
  const res = await request(`/api/${path}${qs.size ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

/** Record and units (1 unit a pick at the logged price) for one slice of the graded board. */
export interface GradeLine { label: string; n: number; wins: number; losses: number; pushes: number; voids: number; units: number; roi: number | null; expected_wins: number }
/** Model projection vs posted line, per market: average miss of each, and how often the model was closer. */
export interface MarketCheck { kind: Kind; n: number; mae_model: number; mae_line: number; model_closer: number }
/** Same-game correlations (pair key -> latent correlation), for pricing parlays; see parlays.ts and backend engine/correlation.py. */
export interface Correlations { pairs: Record<string, number>; check: Record<string, { n: number; actual?: number; copula?: number; independent?: number }> }

/** Closing-line value for a slice of the logged board: share that beat the close, EV at the close, and how far the market
 *  moved toward the picks from when they were priced. */
export interface ClvLine { label: string; n: number; beat: number | null; ev_close: number | null; move: number | null }
export interface WeightCell { label: string; n: number; w: number; w_fit: number | null; ll_model: number | null; ll_market: number | null; ll_blend: number | null; floor?: number | null }
/** The fitted weights the board uses: the market weight overall, per market and per market and time to kickoff, and each book's
 *  weight in the market consensus. */
export interface MarketFit { fitted_at: string | null; n: number; w: number | null; kinds: WeightCell[]; buckets: Record<string, WeightCell[]>; books: { book: string; w: number; n: number }[] }

export interface GradeReport {
  season: number | null; graded: number; pending: number; weeks: number[]; dates?: string[];
  board: GradeLine[]; by_market: GradeLine[]; by_ev: GradeLine[]; by_week: GradeLine[];
  /** The board by the win chance it showed: expected_wins / decided against wins / decided is the calibration. */
  by_prob?: GradeLine[]; model_vs_line: MarketCheck[];
  clv?: ClvLine[]; clv_by_market?: ClvLine[]; market_fit?: MarketFit | null;
}

/** A Who's Hot row: a priced pick whose last `window` games mostly cleared its line (backend engine/hot.py). */
export interface HotPick extends Pick {
  window: number; hits: number; streak: number;
  /** The last `window` games' values, oldest first. */
  recent: number[]; avg: number;
  /** His average in the games before those, when there are any. */
  prior_avg: number | null;
}

/** marketWeight: how much to trust the market, 0-1, or null for the weights fitted per market and time to kickoff. */
export interface Filters { kind: Kind | "all"; side: "all" | "Over" | "Under"; game: string; book: string; q: string; minEv: number; marketWeight: number | null; flagged: boolean;
  /** NBA only: one Eastern game date ("YYYY-MM-DD"), or every date when unset. */
  date?: string }

/** Books legal where the bettor is (legalBooks.ts), or null for every book. Set by main.tsx, which resets the query caches
 *  whenever it changes, so every list of books and every "best price" below only ever covers these. */
let allowed: string[] | null = null;
export const setAllowedBooks = (books: string[] | null) => { allowed = books; };
const keepBooks = (books: string[]) => (allowed ? books.filter((b) => allowed!.includes(b)) : books);
/** "all" means every allowed book (a comma list the backend understands); a book that isn't allowed falls back to the same.
 *  "none" matches no book, for a state with no legal sportsbook. */
const bookParam = (book: string) => (!allowed || (book !== "all" && allowed.includes(book)) ? book : allowed.join(",") || "none");

export const api = {
  meta: () => get<Meta>(lg("meta")).then((m) => ({ ...m, books: keepBooks(m.books) })),
  nbaMeta: () => get<NbaMeta>("nba/meta").then((m) => ({ ...m, books: keepBooks(m.books) })),
  picks: (f: Filters, limit = 300) =>
    get<Pick[]>(lg("picks"), { kind: f.kind, exclude_kind: f.kind === "all" ? "td" : undefined, side: f.side, game_id: f.game, gameday: f.date, book: bookParam(f.book), q: f.q, min_ev: f.minEv, market_weight: f.marketWeight ?? undefined,
      include_flagged: f.flagged, limit }),
  /** Players whose last `window` games cleared tonight's line (Under: stayed below it), best run first. */
  hot: (f: Filters, window: number, side: "Over" | "Under") =>
    get<HotPick[]>(lg("hot"), { kind: f.kind, exclude_kind: f.kind === "all" ? "td" : undefined, side, game_id: f.game, gameday: f.date,
      book: bookParam(f.book), q: f.q, window }),
  projections: (f: Filters) => get<Projection[]>(lg("projections"), { kind: f.kind, exclude_kind: f.kind === "all" ? "td" : undefined, game_id: f.game, gameday: f.date, q: f.q }),
  /** Every market for every projected player, anytime TD included (the player compare tab). */
  allProjections: () => get<Projection[]>(lg("projections")),
  /** The NFL's projections whatever league is on screen: the betslip reads players' positions from them. */
  nflProjections: () => get<Projection[]>("projections"),
  /** The NBA's projections whatever league is on screen, for the same reason. */
  nbaProjections: () => get<Projection[]>("nba/projections"),
  searchPlayers: (q: string) => get<PlayerHit[]>(lg("player-search"), { q }),
  player: (id: string, marketWeight: number | null) => get<PlayerDetail>(lg(`players/${id}`), { market_weight: marketWeight ?? undefined }),
  king: (gameday: string | undefined, book: string) => get<KingBoard>("nba/promos/king", { gameday, book: bookParam(book) }),
  endzone: (gameId: string | undefined, book: string) => get<EndzoneBoard>("promos/endzone", { game_id: gameId, book: bookParam(book) }),
  moneylines: (book: string, marketWeight: number | null, leadBy?: number) =>
    get<MoneylineGame[]>(lg("moneylines"), { book: bookParam(book), market_weight: marketWeight ?? undefined, lead_by: leadBy }),
  /** Teams on a winning streak of the league on screen, with their moneyline at `book` (best price anywhere for "all"). */
  hotTeams: (f: Filters, window: number) => get<HotTeam[]>(lg("hot-teams"), { book: bookParam(f.book), game_id: f.game, gameday: f.date, window }),
  /** How a player's market moved through the week (one point per odds fetch). */
  /** NFL only: same-slot players' recent games against this player's opponent. */
  vsDefense: (id: string, kind: Kind) => get<VsDefense>(`players/${id}/vs-defense`, { kind }),
  /** The league on screen's defenses by how soft they are for each slot and market, softest first. */
  softSpots: () => get<SoftSpot[]>(lg("soft-spots")),
  /** The league on screen's market: moves since the first fetch, steam, and quotes far from the consensus. */
  market: () => get<MarketBoard>(lg("market")),
  gameContext: (gameId: string) => get<GameContext>(lg(`games/${gameId}/context`)),
  history: (id: string, kind: Kind) => get<HistoryPoint[]>(`${NBA_KIND_SET.has(kind) ? "nba/" : ""}players/${id}/history`, { kind }),
  injuries: () => get<InjuryReport>(lg("injuries")),
  grades: () => get<GradeReport>("grades"),
  correlations: () => get<Correlations>("correlations"),
  nbaCorrelations: () => get<Correlations>("nba/correlations"),
  nbaGrades: () => get<GradeReport>("nba/grades"),
  /** oddsRange hides prices outside -N..+N (American); 0 shows everything. */
  // by the market, not the league on screen: a betslip can hold legs from both
  lines: (id: string, kind: Kind, oddsRange = 300, marketWeight: number | null = null) =>
    (kind === "ml" ? moneylineLadder(id, marketWeight) : get<Ladder>(`${NBA_KIND_SET.has(kind) ? "nba/" : ""}players/${id}/lines`, { kind, odds_range: oddsRange, market_weight: marketWeight ?? undefined }))
    .then((l) => ({ ...l, quotes: l.quotes.filter((q) => !allowed || allowed.includes(q.book)) })),
  fetchAlt: async (id: string): Promise<AltFetchResult> => {
    const res = await request(`/api/players/${id}/alt-lines`, { method: "POST" });
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail);
    return res.json();
  },
  /** Admin page (the owner's account). 403: signed out, or not an admin account. */
  admin: async (): Promise<AdminState> => {
    const res = await request("/api/admin");
    if (!res.ok) throw Object.assign(new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail), { status: res.status });
    return res.json();
  },
  adminRun: (id: number) => get<SyncRunDetail>(`admin/runs/${id}`),
  runSlotNow: async (slot: string): Promise<JobStatus> => {
    const res = await request(`/api/admin/run/${slot}`, { method: "POST" });
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail);
    return res.json();
  },
  setHideOffshore: async (hide: boolean): Promise<{ hide_offshore: boolean }> => {
    const res = await request("/api/admin/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ hide_offshore: hide }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail);
    return res.json();
  },
  status: () => get<JobStatus>("refresh/status"),
};



// ---------- the games page in one request ----------
/** The games page's four reads: the slate, its plays at any edge (the tiles' top prop and counts), moneylines, injuries. */
export type SlatePart = "meta" | "picks" | "moneylines" | "injuries";
export interface SlateParts<M> { meta: M; picks: Pick[]; moneylines: MoneylineGame[]; injuries: InjuryReport }
interface Ask { part: SlatePart; book?: string; mw?: number | null; resolve: (v: unknown) => void; reject: (e: unknown) => void }
const asks: Record<League, Ask[]> = { nfl: [], nba: [] };
const leaguePrefix = (l: League) => (l === "nba" ? "nba/" : "");
/** What the games page asks for its tiles: every over/under (touchdowns aside) at any edge, flagged ones left out. */
const slatePicksParams = (book: string, mw: number | null) =>
  ({ exclude_kind: "td", book: bookParam(book), min_ev: 0, market_weight: mw ?? undefined, include_flagged: false, limit: 1000 });

function readOne(league: League, a: Ask): Promise<unknown> {
  const p = leaguePrefix(league);
  if (a.part === "picks") return get(`${p}picks`, slatePicksParams(a.book!, a.mw ?? null));
  if (a.part === "moneylines") return get(`${p}moneylines`, { book: bookParam(a.book!), market_weight: a.mw ?? undefined });
  return get(`${p}${a.part}`);
}

/** Every part asked for in the same moment (the games page mounting, or a prefetch) goes out as one /slate request: one
 *  round trip instead of four, which is most of the wait on a phone. Parts priced at another book or market weight, or
 *  one part on its own (a refetch), use their own endpoints. */
function flush(league: League) {
  const queue = asks[league].splice(0);
  const priced = queue.find((a) => a.part === "picks" || a.part === "moneylines");
  const together = queue.filter((a) => a.part === "meta" || a.part === "injuries" || (a.book === priced!.book && a.mw === priced!.mw));
  const parts = new Set(together.map((a) => a.part));
  const alone = parts.size > 1 ? queue.filter((a) => !together.includes(a)) : queue;
  if (parts.size > 1) {
    get<Partial<SlateParts<unknown>>>(`${leaguePrefix(league)}slate`, {
      parts: [...parts].join(), ...(priced && { book: bookParam(priced.book!), market_weight: priced.mw ?? undefined }),
    }).then((s) => together.forEach((a) => a.resolve(s[a.part])), (e) => together.forEach((a) => a.reject(e)));
  }
  alone.forEach((a) => readOne(league, a).then(a.resolve, a.reject));
}

/** One part of a league's games page (either league, whichever is on screen: prefetching reads the other one). */
export function slatePart<K extends SlatePart, M = Meta | NbaMeta>(league: League, part: K, book?: string, mw?: number | null): Promise<SlateParts<M>[K]> {
  return new Promise<unknown>((resolve, reject) => {
    if (asks[league].push({ part, book, mw, resolve, reject }) === 1) setTimeout(() => flush(league), 0);
  }).then((v) => (part === "meta" ? { ...(v as Meta), books: keepBooks((v as Meta).books) } : v) as SlateParts<M>[K]);
}
