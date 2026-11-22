import type { Kind } from "./api";
import type { Leg } from "./slip";
import { mlLeague } from "./moneyline";

// Tracked bets (backend app/bets.py): the bets a signed-in visitor placed at a book, settled from the box scores. Private
// to their owner; the session cookie identifies them, so these calls carry no admin password.

export type BetStatus = "open" | "won" | "lost" | "push" | "void";
export interface TrackedLeg {
  id: number; game_id: string; gameday: string; kickoff: string; player_id: string; name: string; team: string; opp: string;
  kind: Kind | "custom"; side: "Over" | "Under"; line: number; odds: number; alt: boolean; prob: number | null; ev: number | null;
  actual: number | null; result: "win" | "loss" | "push" | "void" | null;
  /** A moneyline's team's largest lead in the game (fetched for an early-win bet that didn't win outright). */
  lead?: number | null;
  /** The market's fair chance of this leg at kickoff, and what the price taken was worth at it (null until it closes). */
  close_p: number | null; ev_close: number | null;
}
export interface TrackedBet {
  id: number; league: "nfl" | "nba" | "oth"; book: string; kind: "single" | "parlay" | "custom"; stake: number; odds: number; status: BetStatus;
  payout: number | null; profit: number | null; to_pay: number; settled_by: "grading" | "user" | null; notes: string | null; tags: string[];
  /** A custom bet (anything the site can't grade), as the bettor described it; they mark its result. */
  description: string | null;
  /** Placed with a sportsbook's bonus bet: its stake wasn't the bettor's money, so a win pays (and profits) only the winnings. */
  bonus: boolean;
  /** A profit boost, in percent: `odds` is the price before it, and `to_pay` and `payout` include it. */
  boost: number | null;
  /** A no-sweat bet: what the book refunds as a bonus bet if it loses (at most the stake), and what a lost one earned back. */
  no_sweat: number | null; refund: number;
  /** An early-win token on a single moneyline: the lead (points) that pays it as a win, whatever the final score. */
  early_win: number | null;
  /** Every leg is in and none lost, but a leg that voided or pushed has no known price, so the payout can't be worked out:
   *  the bettor enters what the book paid. */
  needs_payout: boolean;
  /** Recorded after its first game had started (up to LATE_DAYS later): the bettor may have known how it went. */
  late: boolean;
  placed_at: string; settled_at: string | null; started: boolean; legs: TrackedLeg[];
  /** The bet's price measured at the close (a parlay's legs multiplied); null until every leg has closed. */
  ev_close: number | null;
}
export interface BetSummary {
  wins: number; losses: number; pushes: number; open: number; open_stake: number; staked: number; profit: number;
  /** Bonus bets' stakes on settled bets: not in `staked` (they weren't the bettor's money), though their winnings are profit. */
  bonus_staked: number;
  /** Bonus bets earned back from no-sweat bets that lost: not profit until they're bet and win. */
  refunds: number;
  roi: number | null; curve: [string, number][];
  /** Bets with a closing price, the share priced better than the close, and their average EV at the close. */
  clv_n: number; beat_close: number | null; ev_close: number | null;
}
/** A view of My bets: one league, one sportsbook, one tag and the last 1, 3 or 12 months (unset: everything). */
export interface BetFilters { league?: "nfl" | "nba" | "oth"; book?: string; tag?: string; months?: 1 | 3 | 12 }

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/me/${path}`, {
    ...init, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "fetch", ...(init.headers as Record<string, string>) },
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Something went wrong. Try again.");
  return body as T;
}

const query = (f: BetFilters) => {
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)]));
  return qs.size ? `?${qs}` : "";
};
/** The view as a spreadsheet: one row per leg (backend bets.export_csv). */
export const csvUrl = (f: BetFilters) => `/api/me/bets.csv${query(f)}`;

export const myBets = (f: BetFilters) => {
  return call<{ bets: TrackedBet[]; summary: BetSummary; books: string[]; tags: string[]; leagues: string[]; can_read_screenshots: boolean }>(`bets${query(f)}`);
};

// a moneyline's "player" is its team (the slip's id also carries the game: moneyline.ts)
const legBody = (l: Leg) => ({ game_id: l.gameId, player_id: l.kind === "ml" ? l.team : l.playerId, kind: l.kind, side: l.side, line: l.line, odds: l.odds,
  alt: l.alt, prob: l.prob, ev: l.ev });

/** Track a slip group as one parlay (with the price recorded at the book), or each leg as a single at `stake`. */
export async function trackBets(book: string, legs: Leg[], asParlay: boolean, stake: number, parlayOdds: number, tags: string[] = [], promo: PromoBody = { bonus: false }) {
  if (asParlay) return [await call<TrackedBet>("bets", { method: "POST", body: JSON.stringify({ book, kind: "parlay", stake, odds: parlayOdds, tags, ...promo, legs: legs.map(legBody) }) })];
  const out: TrackedBet[] = [];
  for (const l of legs) out.push(await call<TrackedBet>("bets", { method: "POST", body: JSON.stringify({ book, kind: "single", stake, tags, ...promo, legs: [legBody(l)] }) }));
  return out;
}

export const editBet = (id: number, patch: { stake?: number; odds?: number; notes?: string; status?: BetStatus; tags?: string[];
  /** with status "won": what the book actually paid back */ payout?: number } & Partial<PromoBody>) =>
  call<TrackedBet>(`bets/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
export const deleteBet = (id: number) => call<void>(`bets/${id}`, { method: "DELETE" });

/** The leagues' player markets don't overlap, so a leg's market says which league it's from; a moneyline is in both, so its
 *  game says (as the server works it out). */
const NBA_KINDS = new Set<string>(["pts", "reb", "ast", "threes", "pra", "pr", "pa", "stl", "blk", "dd", "tpl"]);
export const legLeague = (l: { kind: Kind; gameId?: string }) => (l.kind === "ml" ? mlLeague(l.gameId ?? "") : NBA_KINDS.has(l.kind) ? "nba" : "nfl");

/** American odds typed by hand: "+250", "250" or "-110"; null if it isn't a real price. */
export function parseOdds(text: string): number | null {
  const n = Number(text.trim().replace(/^\+/, ""));
  return Number.isInteger(n) && Math.abs(n) >= 100 && Math.abs(n) <= 100_000 ? n : null;
}

/** If it wins, what the book pays back: the stake and winnings, or for a bonus bet only the winnings (its stake isn't returned). */
export const winReturn = (stake: number, winnings: number, bonus: boolean) => (bonus ? winnings : stake + winnings);

// ---------- promos: one a bet ----------
/** A bonus bet (free bet), a profit boost on the winnings, a no-sweat bet (the stake back as a bonus bet if it loses), or an
 *  early-win token (a moneyline paid as a win once its team leads by enough). */
export type PromoKind = "none" | "bonus" | "boost" | "no_sweat" | "early_win";
/** The promo as a form holds it: the boost's percent, the no-sweat refund (blank: the whole stake) and the early win's lead,
 *  as typed. */
export interface Promo { kind: PromoKind; boost: string; refund: string; lead: string }
export const NO_PROMO: Promo = { kind: "none", boost: "", refund: "", lead: "" };
/** FanDuel's early-win tokens: a moneyline pays once the team is up 10 (NBA) or 17 (NFL). The most a lead can be: backend
 *  bets.MAX_EARLY_WIN. */
export const EARLY_WIN: Record<"nfl" | "nba", number> = { nba: 10, nfl: 17 };
export const MAX_EARLY_WIN = 40;
/** What a profit boost runs to (backend bets.MAX_BOOST). */
export const MAX_BOOST = 500;
export const promoOf = (b: Pick<TrackedBet, "bonus" | "boost" | "no_sweat" | "stake" | "early_win">): Promo => ({
  kind: b.bonus ? "bonus" : b.boost ? "boost" : b.no_sweat ? "no_sweat" : b.early_win ? "early_win" : "none",
  boost: b.boost ? String(b.boost) : "", refund: b.no_sweat && b.no_sweat < b.stake ? String(b.no_sweat) : "",
  lead: b.early_win ? String(b.early_win) : "" });
/** The early win's lead typed, as whole points from 1 to MAX_EARLY_WIN; null if it isn't one. */
export function leadOf(p: Promo): number | null {
  const n = Number(p.lead.trim());
  return p.kind === "early_win" && Number.isInteger(n) && n >= 1 && n <= MAX_EARLY_WIN ? n : null;
}
/** The boost typed, as a whole percent from 1 to MAX_BOOST; null if it isn't one. */
export function boostPct(p: Promo): number | null {
  const n = Number(p.boost.trim().replace(/%$/, ""));
  return p.kind === "boost" && Number.isInteger(n) && n >= 1 && n <= MAX_BOOST ? n : null;
}
/** The no-sweat refund typed, or the stake when it's blank; null if it isn't a dollar amount. */
export function refundOf(p: Promo, stake: number): number | null {
  if (p.kind !== "no_sweat") return null;
  if (!p.refund.trim()) return stake > 0 ? stake : null;
  const n = Number(p.refund.replace(/[$,\s]/g, ""));
  return Number.isFinite(n) && n > 0 ? Math.min(n, stake || n) : null;
}
/** The form's promo is complete: a boost needs its percent, a no-sweat refund a dollar amount (or blank). */
export const promoReady = (p: Promo, stake: number) =>
  p.kind === "boost" ? boostPct(p) != null : p.kind === "no_sweat" ? !p.refund.trim() || refundOf(p, stake) != null
    : p.kind === "early_win" ? leadOf(p) != null : true;
/** The promo as the server takes it: every field, so switching promos takes the old one off (0 is none). */
export interface PromoBody { bonus: boolean; boost?: number; no_sweat?: number; early_win?: number }
export const promoBody = (p: Promo, stake: number): PromoBody =>
  ({ bonus: p.kind === "bonus", boost: boostPct(p) ?? 0, no_sweat: refundOf(p, stake) ?? 0, early_win: leadOf(p) ?? 0 });
/** If it wins, what the book pays back with the promo: a boost multiplies the winnings, a bonus bet's stake isn't returned. */
export const promoReturn = (stake: number, winnings: number, p: Promo) => winReturn(stake, winnings * (1 + (boostPct(p) ?? 0) / 100), p.kind === "bonus");
/** What a bet cost the bettor: nothing for a bonus bet. */
export const costOf = (b: Pick<TrackedBet, "stake" | "bonus">) => (b.bonus ? 0 : b.stake);

export const money = (n: number) => `${n < 0 ? "−" : ""}$${Math.abs(n).toFixed(2)}`;
export const signedMoney = (n: number) => `${n > 0 ? "+" : n < 0 ? "−" : ""}$${Math.abs(n).toFixed(2)}`;

export const MAX_TAGS = 6, TAG_LEN = 24;
/** Add a typed tag to a list as the server keeps them: trimmed, at most TAG_LEN long, no repeat ignoring case. */
export function addTag(tags: string[], text: string): string[] {
  const t = text.split(/\s+/).filter(Boolean).join(" ").slice(0, TAG_LEN);
  return !t || tags.length >= MAX_TAGS || tags.some((x) => x.toLowerCase() === t.toLowerCase()) ? tags : [...tags, t];
}

// ---------- bets placed elsewhere (My bets → Add a bet) ----------
/** A player whose game hasn't started, with his game and the markets he's projected in (backend bets.bet_players). */
export interface BetPlayer { player_id: string; name: string; pos: string; team: string; opp: string; game_id: string; kickoff: string; kinds: Kind[];
  /** The game has started (in the last LATE_DAYS days): a bet on it is added late. */
  started?: boolean }
/** How long after a game starts a forgotten bet on it can still be added (backend bets.LATE_DAYS); it's marked late. */
export const LATE_DAYS = 7;
export const findBetPlayers = (league: "nfl" | "nba", q: string) =>
  call<BetPlayer[]>(`bet-players?${new URLSearchParams({ league, q })}`);

/** A prop leg (graded from the box scores), or, in a parlay, a leg the site can't grade, kept in words and marked by hand.
 *  `odds` is the leg's own price: most books show only the parlay's, so a parlay leg may have none. */
export type ManualLeg =
  | { custom?: false; player: BetPlayer; kind: Kind; side: "Over" | "Under"; line: number; odds: number | null }
  | { custom: true; text: string; odds: number | null };
/** A prop bet placed elsewhere: one leg is a single, more a parlay at `parlayOdds`. Graded like any tracked bet (a parlay's
 *  hand-marked legs wait for the bettor). */
export const addPropBet = (book: string, legs: ManualLeg[], stake: number, parlayOdds: number, tags: string[], promo: PromoBody = { bonus: false }) =>
  call<TrackedBet>("bets", { method: "POST", body: JSON.stringify({
    book, stake, tags, ...promo, kind: legs.length > 1 ? "parlay" : "single", odds: legs.length > 1 ? parlayOdds : undefined,
    legs: legs.map((l) => (l.custom ? { kind: "custom", description: l.text, odds: l.odds }
      : { game_id: l.player.game_id, player_id: l.player.player_id, kind: l.kind, side: l.side, line: l.line, odds: l.odds })),
  }) });

// ---------- reading a slip screenshot (backend app/bet_screens.py) ----------
export interface ScreenLeg { graded: boolean; player: BetPlayer | null; kind: Kind | "custom"; side: "Over" | "Under" | ""; line: number; odds: number | null; text: string }
export interface ScreenDraft {
  book: string | null; league: "nfl" | "nba" | null; kind: "single" | "parlay"; odds: number | null; stake: number | null;
  bonus_stake: number | null; to_return: number | null; legs: ScreenLeg[]; notes: string[];
  /** A profit boost on top of `odds`, and whether the slip is a no-sweat bet (only the Claude reader sees these). */
  boost?: number | null; no_sweat?: boolean;
}
/** A draft bet from a screenshot (a data URL), to check in the Add a bet sheet; nothing is saved. */
export const readScreenshot = (image: string) => call<ScreenDraft>("bets/read-screenshot", { method: "POST", body: JSON.stringify({ image }) });
/** A screenshot shared to the installed app from another app's share menu (Android), held by the service worker
 * (pwa/sw.js): taken once, so it opens a single time; one older than an hour is dropped rather than opened out of the blue.
 * `keep` only looks (signed out: it waits for them to sign in). From an iPhone it's the slip's text instead (holdSharedText). */
export async function takeSharedSlip(keep = false): Promise<File | string | null> {
  const text = takeSharedText(keep);
  if (text) return text;
  if (typeof caches === "undefined") return null;
  try {
    const c = await caches.open("shared-slip");
    const res = await c.match("slip");
    if (!res) return null;
    const fresh = Date.now() - Number(res.headers.get("x-at") ?? 0) <= 3600_000;
    if (!keep || !fresh) await c.delete("slip");
    if (!fresh) return null;
    if (res.headers.get("content-type") === "text/plain") return await res.text();   // shared text, such as a FanDuel share link
    return new File([await res.blob()], decodeURIComponent(res.headers.get("x-name") ?? "slip"), { type: res.headers.get("content-type") ?? "image/png" });
  } catch { return null; }
}
/** An iPhone can't share to a web app, so a Shortcut in its share menu reads the screenshot on the phone (Apple's "Extract
 * Text from Image") and opens /bets#slip=<the text>. Called before the app renders: the text is held in this browser until My
 * bets takes it (it's after the #, so it never reaches the server in the address), and the address is tidied. */
const SLIP_TEXT = "shared-slip-text";
export function holdSharedText() {
  const m = window.location.hash.match(/^#slip=([\s\S]*)$/);
  if (!m) return;
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  try {
    const text = decodeURIComponent(m[1].replace(/\+/g, " ")).slice(0, 20000);
    if (text.trim()) localStorage.setItem(SLIP_TEXT, JSON.stringify({ text, at: Date.now() }));
  } catch { /* a garbled link, or storage off: nothing to open */ }
}
function takeSharedText(keep: boolean): string | null {
  try {
    const held = JSON.parse(localStorage.getItem(SLIP_TEXT) ?? "null") as { text: string; at: number } | null;
    if (!held) return null;
    const fresh = Date.now() - held.at <= 3600_000;
    if (!keep || !fresh) localStorage.removeItem(SLIP_TEXT);
    return fresh ? held.text : null;
  } catch { return null; }
}
/** A slip's text, from either reader, read against the sportsbooks' layouts (backend slip_text) and matched to the board. */
export const readSlipText = (text: string) =>
  call<ScreenDraft>("bets/read-slip-text", { method: "POST", body: JSON.stringify({ text: text.slice(0, 20000) }) });
/** The free way, when Claude isn't set up: OCR reads the screenshot on this device (Tesseract.js, loaded only when needed),
 * and only its text goes to the server, which knows bet365's, FanDuel's and DraftKings' layouts (backend slip_text). */
export async function readSlipOnDevice(file: File): Promise<ScreenDraft> {
  const { createWorker } = await import("tesseract.js");
  const worker = await createWorker("eng");
  let text: string;
  try { text = (await worker.recognize(file)).data.text; } finally { await worker.terminate(); }
  return readSlipText(text);
}

/** A screenshot as a data URL the server accepts: under 5 MB, re-encoded as JPEG (scaled down if need be) when it's bigger. */
export async function screenshotDataUrl(file: File): Promise<string> {
  const read = (blob: Blob) => new Promise<string>((ok, fail) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.onerror = fail; r.readAsDataURL(blob); });
  const types = ["image/png", "image/jpeg", "image/webp", "image/gif"];
  if (types.includes(file.type) && file.size < 4.5 * 1024 * 1024) return read(file);
  const img = await createImageBitmap(file);
  const scale = Math.min(1, 2400 / Math.max(img.width, img.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(img.width * scale); canvas.height = Math.round(img.height * scale);
  canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.85);
}
/** Anything the site can't grade (a spread, a total, another sport, a boost), before its event starts; marked by hand. */
export const addCustomBet = (b: { book: string; description: string; startsAt: string; league: "nfl" | "nba" | "oth"; odds: number; stake: number; tags: string[]; promo?: PromoBody }) =>
  call<TrackedBet>("bets", { method: "POST", body: JSON.stringify({ kind: "custom", book: b.book, description: b.description, starts_at: b.startsAt,
    league: b.league, odds: b.odds, stake: b.stake, tags: b.tags, ...(b.promo ?? { bonus: false }) }) });

/** Sportsbooks to suggest when typing one in; the bettor can type any other. */
export const COMMON_BOOKS = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "Fanatics", "ESPN BET", "BetRivers", "Hard Rock", "bet365", "PrizePicks", "Underdog"];
