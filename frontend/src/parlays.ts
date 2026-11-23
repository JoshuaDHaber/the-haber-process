import type { Kind, Ladder, Pick } from "./api";
import { jointProb, legRho, parlayProb, type CorrTable } from "./copula";
import { isYesNo } from "./format";
import { matchupGrade } from "./components/MatchupGrade";
import { getLeague } from "./league";
import { legId, linkStatus, toAmerican, toDecimal, type Leg } from "./slip";

/** Shortest price allowed on a leg: heavier chalk adds hit chance but almost no payout. */
export const MIN_LEG_ODDS = -300;
/** Lowest win probability a leg needs to be considered "high probability". */
export const MIN_LEG_PROB = 0.6;

/** A parlay leg: a priced pick, plus the betslip link when it came from a line ladder (and the player's main line, to show how far down the rung is). */
export interface ParlayLeg extends Pick { link?: string | null; eventLink?: string | null; alt?: boolean; mainLine?: number }

export interface Parlay {
  key: string;
  title: string;
  blurb: string;
  legs: ParlayLeg[];
  /** True when two legs share a game: their chance is correlated (see copula.ts), and the book prices them its own way. */
  sameGame: boolean;
  /** Chance every leg hits: independent across games, through the backtest's correlations within one (when given). */
  prob: number;
  decimal: number;
  american: number;
  /** Expected profit per $1 staked at the legs' best prices. */
  ev: number;
  /** The sportsbook every leg is priced at, so the parlay can be placed as shown. */
  book: string;
  /** The tile it extends with more legs (the 5-leg builds on the likeliest 3), so the overlap reads as deliberate. */
  extends?: string;
}

/** At most this many players in common between two tiles of a section, so the tiles are different bets to choose between. */
export const MAX_SHARED_PLAYERS = 1;
const players = (legs: Pick[]) => new Set(legs.map((l) => l.player_id));
const shared = (a: Set<string>, legs: Pick[]) => legs.filter((l) => a.has(l.player_id)).length;
/** True when `legs` shares more than MAX_SHARED_PLAYERS players with any of `earlier`. */
export const overlapsAny = (legs: Pick[], earlier: Pick[][]) => earlier.some((e) => shared(players(e), legs) > MAX_SHARED_PLAYERS);

export const pickKey = (p: Pick) => `${p.player_id}|${p.kind}|${p.side}|${p.line}|${p.book}`;

/** High-probability, sanely priced plays that the model and market don't disagree wildly on. */
export function candidates(picks: Pick[]): Pick[] {
  return picks.filter((p) => !p.flagged && !isYesNo(p.kind) && p.prob >= MIN_LEG_PROB && p.odds >= MIN_LEG_ODDS);
}

/** Moneylines in one props tile: favorites are the likeliest legs on any board and would crowd the props out, so they get a
 *  tile of their own (Favorites) and at most this many places in the others. */
export const MAX_ML_PER_TILE = 1;

/** Take legs in order, at most one per player (a player's other lines and markets would be the same bet twice) and at most
 *  `maxMl` moneylines, skipping any that would share more than MAX_SHARED_PLAYERS players with one of the `avoid` tiles.
 *  Several legs may share a game; the parlay is flagged `sameGame` then, since the book prices those its own way. */
function onePerPlayer(sorted: Pick[], n: number, avoid: Pick[][] = [], maxMl = MAX_ML_PER_TILE): Pick[] {
  const used = new Set<string>();
  const legs: Pick[] = [];
  for (const p of sorted) {
    if (used.has(p.player_id) || overlapsAny([...legs, p], avoid)) continue;
    if (p.kind === "ml" && legs.filter((l) => l.kind === "ml").length >= maxMl) continue;
    used.add(p.player_id);
    legs.push(p);
    if (legs.length === n) break;
  }
  return legs;
}

/** Price a parlay at its book. With `corr`, legs that share a game are priced with their correlation; without, as independent.
 *  The payout is the legs' odds multiplied: what a book pays for legs in different games, and only a guide within one. */
export function priceParlay(legs: Pick[], corr?: CorrTable): Omit<Parlay, "key" | "title" | "blurb" | "legs"> {
  if (new Set(legs.map((l) => l.book)).size > 1) throw new Error("A parlay's legs must all be at one sportsbook");
  const decimal = legs.reduce((d, l) => d * toDecimal(l.odds), 1);
  const prob = parlayProb(legs, corr);
  return { prob, decimal, american: toAmerican(decimal), ev: prob * decimal - 1, book: legs[0].book,
    sameGame: new Set(legs.map((l) => l.game_id)).size < legs.length };
}

const byProb = (a: Pick, b: Pick) => b.prob - a.prob || b.ev - a.ev;
const byEv = (a: Pick, b: Pick) => b.ev - a.ev || b.prob - a.prob;

/** The recommended parlays. `picks` must all be quotes from one sportsbook. A tile is dropped when there aren't enough eligible legs or it repeats an earlier tile. */
export function recommendParlays(picks: Pick[], corr?: CorrTable): Parlay[] {
  const pool = candidates(picks);
  // each 3-leg tile shares at most one player with the ones before it, so they're different bets; the 5-leg openly extends the first
  const likeliest = onePerPlayer([...pool].sort(byProb), 3);
  const value = onePerPlayer([...pool].sort(byEv), 3, [likeliest]);
  // a moneyline is stored as an "over" (the team wins), but it isn't one
  const overs = onePerPlayer(pool.filter((p) => p.side === "Over" && p.kind !== "ml").sort(byProb), 3, [likeliest, value].filter((l) => l.length === 3));
  const five = onePerPlayer([...pool].sort(byProb), 5);
  const extendsFirst = likeliest.length === 3 && likeliest.every((l, i) => five[i] === l);
  const favorites = onePerPlayer(pool.filter((p) => p.kind === "ml").sort(byProb), 3, [], 3);
  const specs: { key: string; title: string; blurb: string; n: number; legs: Pick[]; extends?: string }[] = [
    { key: "safest", title: "Likeliest 3-leg", blurb: "The three likeliest plays on the board.", n: 3, legs: likeliest },
    { key: "value", title: "Best value 3-leg", blurb: "Highest expected return among high-probability plays, mostly different players from the likeliest.", n: 3, legs: value },
    { key: "overs", title: "Overs only", blurb: "Three likely overs.", n: 3, legs: overs },
    { key: "payout", title: "Bigger payout 5-leg", n: 5, legs: five, extends: extendsFirst ? "safest" : undefined,
      blurb: extendsFirst ? "The likeliest 3-leg plus the next two likeliest plays, each from a different player." : "Five likely plays, each from a different player." },
    { key: "favorites", title: "Favorites 3-leg", blurb: "The three likeliest winners' moneylines, each in a different game.", n: 3, legs: favorites },
  ];
  const seen = new Set<string>();
  const out: Parlay[] = [];
  for (const s of specs) {
    if (s.legs.length < s.n) continue;
    const sig = s.legs.map(pickKey).sort().join("&");
    if (seen.has(sig)) continue;
    seen.add(sig);
    out.push({ key: s.key, title: s.title, blurb: s.blurb, legs: s.legs, extends: s.extends, ...priceParlay(s.legs, corr) });
  }
  return out;
}

/** Recommended parlays at each sportsbook (`picksByBook`: that book's priced picks), keyed by book. Books with none are left out. */
export function parlaysByBook(picksByBook: Record<string, Pick[]>, corr?: CorrTable): Record<string, Parlay[]> {
  const out: Record<string, Parlay[]> = {};
  for (const [book, picks] of Object.entries(picksByBook)) {
    const ps = recommendParlays(picks.filter((p) => p.book === book), corr);
    if (ps.length) out[book] = ps;
  }
  return out;
}

/** Keep only picks from the chosen games; an empty selection means every game. */
export function filterGames(picksByBook: Record<string, Pick[]>, gameIds: string[]): Record<string, Pick[]> {
  if (!gameIds.length) return picksByBook;
  const keep = new Set(gameIds);
  return Object.fromEntries(Object.entries(picksByBook).map(([book, ps]) => [book, ps.filter((p) => keep.has(p.game_id))]));
}

/** The book to show first: where the lead (safest) parlay has the best expected return. */
export function bestBook(byBook: Record<string, Parlay[]>): string | null {
  const books = Object.keys(byBook).sort();
  return books.sort((a, b) => byBook[b][0].ev - byBook[a][0].ev)[0] ?? null;
}

/** The book the tab opens on when it has a parlay to show, unless the visitor chose a default sportsbook (prefs.ts). */
export const PREFERRED_BOOK = "FanDuel";

/** The book to show first: the preferred one (the visitor's default sportsbook, else FanDuel) when it has a parlay, else the
 *  best lead parlay, else the book with the most overs to ladder from. A book with nothing to build never opens the tab just
 *  for being preferred. */
export function defaultBook(byBook: Record<string, Parlay[]>, picksByBook: Record<string, Pick[]>, preferred = PREFERRED_BOOK): string | null {
  if (byBook[preferred]?.length) return preferred;
  const best = bestBook(byBook);
  if (best) return best;
  const overs = (b: string) => (picksByBook[b] ?? []).filter((p) => p.side === "Over" && isLadderKind(p.kind)).length;
  return Object.keys(picksByBook).sort((a, b) => overs(b) - overs(a) || a.localeCompare(b))[0] ?? null;
}

// ---------- ladder parlays: likely overs (NFL rushing / receiving, NBA points / rebounds / assists / threes / combos / steals / blocks) stacked to a modest price ----------
/** The markets ladders are built from, for the league on screen. */
export const ladderKinds = (): Kind[] => (getLeague() === "nba" ? ["pts", "reb", "ast", "threes", "pra", "pr", "pa", "stl", "blk"] : ["rush", "rec"]);
const isLadderKind = (k: Kind) => ladderKinds().includes(k);
export const LADDER_MIN_PROB = 0.6;
/** Shortest price on a rung: heavier chalk can't help a parlay reach +100. */
export const LADDER_MIN_ODDS = -400;
/** Share of each rung's probability taken from the book's price: alternate lines are one-sided, so there is no no-vig price to blend with. */
export const LADDER_MARKET_WEIGHT = 0.35;
export const LADDER_MAX_LEGS = 4;
export const LADDER_PLAYERS = 12;
export const LADDER_PLAYERS_PER_TEAM = 6;
/** Lowest win probability for a single alternate prop to be listed on its own. */
export const LADDER_PROP_MIN_PROB = 0.65;
/** Combined-price bands, as decimal odds: +100 to +150, +150 to +225, +225 to +300. */
export const LADDER_BANDS = [
  { key: "ladder-1", title: "Ladder +100 to +150", lo: 2, hi: 2.5 },
  { key: "ladder-2", title: "Ladder +150 to +225", lo: 2.5, hi: 3.25 },
  { key: "ladder-3", title: "Ladder +225 to +300", lo: 3.25, hi: 4.0001 },
];

/** The players worth pulling a line ladder for: those whose main-line over looks likeliest by the blended (model + market)
 *  chance, as on the Best props board, and flagged plays are left out. Ranking by the model alone favoured exactly the plays
 *  the books disagree with most, usually a role change the model hasn't caught (Troy Franklin: 63% to the model, 52% to the market).
 *  At most `perTeam` per team, so one team's players can't crowd the other side of a game out of the ladders. */
export function ladderTargets(picks: Pick[], n = LADDER_PLAYERS, perTeam = LADDER_PLAYERS_PER_TEAM, kinds: Kind[] = ladderKinds()): Pick[] {
  const seen = new Set<string>();
  const teams = new Map<string, number>();
  const out: Pick[] = [];
  const overs = picks.filter((p) => p.side === "Over" && kinds.includes(p.kind) && !p.flagged);
  for (const p of overs.sort((a, b) => b.prob - a.prob)) {
    const team = `${p.game_id}|${p.team}`;
    if (seen.has(p.player_id) || (teams.get(team) ?? 0) >= perTeam) continue;
    seen.add(p.player_id);
    teams.set(team, (teams.get(team) ?? 0) + 1);
    out.push(p);
    if (out.length === n) break;
  }
  return out;
}

/** Likely overs at this book for one player, one per line. Only rungs with a usable betslip link count, so every ladder leg can be added at the book
 *  (main lines have none, and some books publish no links or templates the slip can't fill). */
export function ladderRungs(base: Pick, ladder: Ladder | undefined, book: string, minOdds = LADDER_MIN_ODDS, minProb = LADDER_MIN_PROB): ParlayLeg[] {
  const byLine = new Map<number, ParlayLeg>();
  for (const r of ladder?.quotes ?? []) {
    const q = r.over;
    if (r.book !== book || !q || q.flag || q.odds < minOdds || linkStatus({ link: q.link }, "xx") !== "direct") continue;
    const dec = toDecimal(q.odds);
    const prob = (1 - LADDER_MARKET_WEIGHT) * q.prob + LADDER_MARKET_WEIGHT / dec;
    if (prob < minProb) continue;
    const cur = byLine.get(r.line);
    if (cur && cur.odds >= q.odds) continue;
    byLine.set(r.line, { ...base, side: "Over", line: r.line, odds: q.odds, book, p_model: q.prob, p_mkt: 1 / dec, prob, ev: prob * dec - 1,
      link: q.link, eventLink: r.event_link, alt: r.alt, mainLine: base.line });
  }
  return [...byLine.values()].sort((a, b) => a.line - b.line);
}

type Band = { key: string; title: string; lo: number; hi: number };
type LadderEntry = { base: Pick; ladder: Ladder | undefined };

/** How many of each band's likeliest combinations the search keeps, to choose ladders that don't repeat each other's players. */
const LADDER_KEEP = 40;

/** For each band, the likeliest combination of 2-4 different players' rungs (one rung each) whose combined price lands in it.
 *  With `corr`, "likeliest" counts same-game correlation: a player's rungs share a market and side, so the players'
 *  correlations are looked up once and every candidate is priced from that matrix.
 *  Bands are filled in order, each from its likeliest combinations sharing at most MAX_SHARED_PLAYERS players with the ladders
 *  already chosen (and with `avoid`), so the tiles are different bets; a band falls back to its likeliest combination when
 *  every one of them would repeat. */
function searchLadders(players: ParlayLeg[][], bands: Band[], valid: (legs: ParlayLeg[]) => boolean = () => true, corr?: CorrTable,
  maxLegs = LADDER_MAX_LEGS, avoid: Pick[][] = []): (ParlayLeg[] | null)[] {
  const kept: { legs: ParlayLeg[]; p: number }[][] = bands.map(() => []);
  const top = Math.max(...bands.map((b) => b.hi));
  const rho = corr ? players.map((a) => players.map((b) => (a === b ? 1 : legRho(a[0], b[0], corr)))) : null;
  const correlated = !!rho && rho.some((row, i) => row.some((v, j) => i !== j && v !== 0));
  const idx: number[] = [];
  const chanceOf = (chosen: ParlayLeg[], product: number) => {
    if (!correlated) return product;
    const sub = idx.map((i) => idx.map((j) => rho![i][j]));
    return sub.every((row, a) => row.every((v, b) => a === b || v === 0)) ? product : jointProb(chosen.map((l) => l.prob), chosen.map(() => 1), sub);
  };
  const dfs = (from: number, chosen: ParlayLeg[], dec: number, prob: number) => {
    if (chosen.length >= 2) {
      const b = bands.findIndex((x) => dec >= x.lo && dec < x.hi);
      if (b >= 0 && valid(chosen)) {
        const p = chanceOf(chosen, prob), list = kept[b];
        if (list.length < LADDER_KEEP || p > list[list.length - 1].p) {
          const at = list.findIndex((x) => x.p < p);
          list.splice(at < 0 ? list.length : at, 0, { legs: [...chosen], p });
          if (list.length > LADDER_KEEP) list.pop();
        }
      }
    }
    if (chosen.length === maxLegs) return;
    for (let i = from; i < players.length; i++) {
      for (const leg of players[i]) {
        const next = dec * toDecimal(leg.odds);
        if (next >= top) continue; // prices only grow as legs are added
        chosen.push(leg);
        idx.push(i);
        dfs(i + 1, chosen, next, prob * leg.prob);
        idx.pop();
        chosen.pop();
      }
    }
  };
  dfs(0, [], 1, 1);
  const chosen: ParlayLeg[][] = [];
  return kept.map((list) => {
    const pick = list.find((c) => !overlapsAny(c.legs, [...avoid, ...chosen]))?.legs ?? list[0]?.legs ?? null;
    if (pick) chosen.push(pick);
    return pick;
  });
}

const ladderBlurb = (legs: ParlayLeg[] = []) => `Likely ${getLeague() === "nba" ? "points, rebounds, assists and threes" : "rushing and receiving"} overs${legs.some((l) => l.kind === "ml") ? " and favorites' moneylines" : ""}, on alternate lines where needed, stacked to a modest price.`;

/** Moneylines that can be a ladder rung at this book: likely winners at a price a ladder can use, with a betslip link (as every
 *  rung needs). Each is a "player" of its own with one rung, so a ladder takes at most one per team, and the opposing sides
 *  of a game are never both likely. */
export function moneylineRungs(picks: ParlayLeg[], book: string): ParlayLeg[][] {
  return picks.filter((p) => p.kind === "ml" && p.book === book && !p.flagged && p.prob >= LADDER_MIN_PROB && p.odds >= LADDER_MIN_ODDS
    && linkStatus({ link: p.link ?? null }, "xx") === "direct").map((p) => [p]);
}

/**
 * Ladder parlays at one book: 2-4 different players' overs whose combined price lands in each band, picking the likeliest combo per band.
 * Games may repeat (it's meant to work on a thin slate), so same-game legs are flagged on the result.
 */
export function buildLadderParlays(entries: LadderEntry[], book: string, corr?: CorrTable, moneylines: ParlayLeg[][] = []): Parlay[] {
  const players = [...entries.map((e) => ladderRungs(e.base, e.ladder, book)).filter((r) => r.length), ...moneylines];
  const best = searchLadders(players, LADDER_BANDS, undefined, corr);
  const seen = new Set<string>();
  const out: Parlay[] = [];
  LADDER_BANDS.forEach((band, i) => {
    const legs = best[i];
    if (!legs) return;
    const sig = legs.map(pickKey).sort().join("&");
    if (seen.has(sig)) return;
    seen.add(sig);
    out.push({ key: band.key, title: band.title, blurb: ladderBlurb(legs), legs, ...priceParlay(legs, corr) });
  });
  return out;
}

// ---------- NBA threes ladders: only made-threes overs, each rung a likely hit (mostly the 1+ and 2+ alternates) ----------
/** Players whose threes ladder is pulled for them. */
export const THREES_PLAYERS = 12;
/** Threes overs whose ladders are pulled to find THREES_PLAYERS usable ones: many of the likeliest have no rung that passes
 *  ladderRungs at the chosen book (no betslip link, 1+ shorter than -1000, 2+ under 60%). On Nov 18, 2026 one of FanDuel's
 *  top 12 had none and four had a single rung, so the tiles chose among about eight players and the same names kept coming up. */
export const THREES_CANDIDATES = 20;
/** Most threes tiles one player appears in on a slate. */
export const THREES_MAX_TILES_PER_PLAYER = 2;
/** Shortest price on a threes rung: 1+ made is usually -500 to -1000, so the usual -400 floor left nothing. */
export const THREES_MIN_ODDS = -1000;
/** One tile per combined-price band. The cheap band keeps every rung at 70%+, which means many short-priced 1+ legs;
 *  the dearer bands accept 2+ lines (usually 60-68%) so they reach the price in fewer legs. */
export const THREES_BANDS = [
  { key: "threes-1", title: "Threes ladder +100 to +150", lo: 2, hi: 2.5, minProb: 0.7, maxLegs: 6 },
  { key: "threes-2", title: "Threes ladder +150 to +225", lo: 2.5, hi: 3.25, minProb: 0.65, maxLegs: 5 },
  { key: "threes-3", title: "Threes ladder +225 to +300", lo: 3.25, hi: 4.0001, minProb: 0.6, maxLegs: 4 },
];

/** For each threes band, the likeliest combination of different players' threes overs at this book whose price lands in it,
 *  each rung at least the band's chance. Skips a tile that repeats an earlier one or one in `existing`. */
export function buildThreesLadders(entries: LadderEntry[], book: string, existing: Parlay[] = [], corr?: CorrTable): Parlay[] {
  // entries come likeliest first: the first THREES_PLAYERS with a usable rung at this book
  const rungs = entries.filter((e) => e.base.kind === "threes").map((e) => ladderRungs(e.base, e.ladder, book, THREES_MIN_ODDS))
    .filter((r) => r.length).slice(0, THREES_PLAYERS);
  const seen = new Set(existing.map((p) => p.legs.map(pickKey).sort().join("&")));
  const out: Parlay[] = [];
  const tiles = new Map<string, number>();   // player -> threes tiles he's in so far
  for (const band of THREES_BANDS) {
    const players = rungs.filter((r) => (tiles.get(r[0].player_id) ?? 0) < THREES_MAX_TILES_PER_PLAYER)
      .map((r) => r.filter((l) => l.prob >= band.minProb)).filter((r) => r.length);
    const [legs] = searchLadders(players, [band], undefined, corr, band.maxLegs, out.map((p) => p.legs));
    if (!legs) continue;
    const sig = legs.map(pickKey).sort().join("&");
    if (seen.has(sig)) continue;
    seen.add(sig);
    for (const l of legs) tiles.set(l.player_id, (tiles.get(l.player_id) ?? 0) + 1);
    out.push({ key: band.key, title: band.title, blurb: `Alternate threes overs, every leg at least ${Math.round(band.minProb * 100)}% likely.`,
      legs, ...priceParlay(legs, corr) });
  }
  return out;
}

/** "2026_05_TB_DAL" -> "TB @ DAL" */
export const gameLabel = (gameId: string) => { const [, , away, home] = gameId.split("_"); return `${away} @ ${home}`; };

/**
 * One ladder per game that uses both teams: the likeliest +100 to +300 combination with at least one leg from each side.
 * Plain ladders go to whichever team the model likes; this shows the other team's props too. Skips games where a side has no usable rung,
 * and any tile that repeats one in `existing`.
 */
export function buildBalancedLadders(entries: LadderEntry[], book: string, gameIds: string[], existing: Parlay[] = [], corr?: CorrTable,
  moneylines: ParlayLeg[][] = []): Parlay[] {
  const seen = new Set(existing.map((p) => p.legs.map(pickKey).sort().join("&")));
  const band: Band = { key: "", title: "", lo: LADDER_BANDS[0].lo, hi: LADDER_BANDS[LADDER_BANDS.length - 1].hi };
  const out: Parlay[] = [];
  for (const gameId of gameIds) {
    const players = [...entries.filter((e) => e.base.game_id === gameId).map((e) => ladderRungs(e.base, e.ladder, book)).filter((r) => r.length),
      ...moneylines.filter((r) => r[0].game_id === gameId)];
    const teams = [...new Set(players.map((r) => r[0].team))];
    if (teams.length < 2) continue;
    const [legs] = searchLadders(players, [band], (chosen) => teams.every((t) => chosen.some((l) => l.team === t)), corr);
    if (!legs) continue;
    const sig = legs.map(pickKey).sort().join("&");
    if (seen.has(sig)) continue;
    seen.add(sig);
    out.push({ key: `both-${gameId}`, title: `${gameLabel(gameId)}, both teams`, blurb: "The likeliest ladder with a player from each side of the game.",
      legs, ...priceParlay(legs, corr) });
  }
  return out;
}

// ---------- soft-matchup parlays: 4-6 longer-priced overs, every player facing a defense that gives up a lot to his position ----------
/** The markets soft-matchup parlays are built from: the graded yardage and box-score markets with alternate lines. Passing
 *  yards is the one NFL market where the grade has hinted at anything (engine/dvp.py). */
export const softKinds = (): Kind[] => (getLeague() === "nba" ? ["pts", "reb", "ast", "pra", "pr", "pa"] : ["rush", "rec", "rr", "pass"]);
/** Matchup grades a leg needs (MatchupGrade.tsx: A is the softest fifth of defenses against his position). */
export const SOFT_GRADES = "AB";
/** Grades let in when too few players have one of SOFT_GRADES and a usable rung: anything but a tough matchup. */
export const SOFT_FALLBACK_GRADES = "ABC";
/** Fewest players with a usable rung before the search widens to SOFT_FALLBACK_GRADES. */
export const SOFT_MIN_PLAYERS = 6;
/** Shortest price on a leg: these parlays are for payout, so heavy chalk is left to the ladders. */
export const SOFT_MIN_ODDS = -200;
/** Lowest chance a leg needs: a coin flip, so plus-money rungs can join. */
export const SOFT_MIN_PROB = 0.5;
/** Players whose ladders are pulled (preferred grades first, then the fallback's), and the most a tile's search looks at. */
export const SOFT_TARGETS = 16;
export const SOFT_SEARCH_PLAYERS = 12;
export const SOFT_PER_TEAM = 4;
/** Rungs kept per player, spread across his lines, so a 6-leg search stays quick. */
export const SOFT_RUNGS_PER_PLAYER = 3;
/** One tile per leg count, each capped in price (decimal odds) so a long rung can't turn it into a lottery ticket. At
 *  SOFT_MIN_ODDS or longer, 4 legs start near +400, 5 near +660 and 6 near +1040. */
export const SOFT_BANDS = [
  { key: "soft-4", title: "Soft matchups 4-leg", legs: 4, lo: 1, hi: 11.0001 },   // up to +1000
  { key: "soft-5", title: "Soft matchups 5-leg", legs: 5, lo: 1, hi: 21.0001 },   // up to +2000
  { key: "soft-6", title: "Soft matchups 6-leg", legs: 6, lo: 1, hi: 36.0001 },   // up to +3500
];
export const SOFT_MIN_LEGS = SOFT_BANDS[0].legs;
export const SOFT_MAX_LEGS = SOFT_BANDS[SOFT_BANDS.length - 1].legs;

const gradeOf = (p: Pick) => (p.dvp_rank != null && p.dvp_n ? matchupGrade(p.dvp_rank, p.dvp_n) : null);
const gradedIn = (grades: string) => (p: Pick) => { const g = gradeOf(p); return g != null && grades.includes(g); };

/** How big a player's role is in each market: where his projection ranks among the slate's overs in that market, 0 (the
 *  smallest) to 1 (the biggest). A percentile, not a ratio, so markets compare: every starting QB's passing yards dwarf a
 *  receiver's, and a center's rebounds a guard's. Ranking by chance instead fills the tiles with bench players' 2.5s, which
 *  are likely because they're small. */
export function roleScores(picks: Pick[], kinds: Kind[] = softKinds()): Map<string, number> {
  const out = new Map<string, number>();
  for (const k of kinds) {
    const mus = new Map<string, number>();
    for (const p of picks) if (p.kind === k && p.side === "Over") mus.set(p.player_id, p.mu);
    const sorted = [...mus].sort((a, b) => a[1] - b[1]);
    sorted.forEach(([id], i) => out.set(`${id}|${k}`, sorted.length > 1 ? i / (sorted.length - 1) : 1));
  }
  return out;
}

/** The players to pull ladders for: those with a soft-matchup over graded in SOFT_GRADES, biggest role first (roleScores),
 *  then (to widen to if those run short) the rest of SOFT_FALLBACK_GRADES the same way. Each player in the market where his
 *  role is biggest; at most SOFT_PER_TEAM per team. Flagged plays are left out. */
export function softTargets(picks: Pick[], n = SOFT_TARGETS): Pick[] {
  const kinds = softKinds();
  const role = roleScores(picks, kinds);
  const score = (p: Pick) => role.get(`${p.player_id}|${p.kind}`) ?? 0;
  const pickFrom = (pool: Pick[], max: number, taken: Set<string>, teams: Map<string, number>) => {
    const best = new Map<string, Pick>();
    for (const p of pool) {
      if (taken.has(p.player_id)) continue;
      const cur = best.get(p.player_id);
      if (!cur || score(p) > score(cur) || (score(p) === score(cur) && p.prob > cur.prob)) best.set(p.player_id, p);
    }
    const out: Pick[] = [];
    for (const p of [...best.values()].sort((a, b) => score(b) - score(a) || b.prob - a.prob)) {
      const team = `${p.game_id}|${p.team}`;
      if ((teams.get(team) ?? 0) >= SOFT_PER_TEAM) continue;
      teams.set(team, (teams.get(team) ?? 0) + 1);
      taken.add(p.player_id);
      out.push(p);
      if (out.length >= max) break;
    }
    return out;
  };
  const eligible = picks.filter((p) => p.side === "Over" && kinds.includes(p.kind) && !p.flagged);
  const taken = new Set<string>(), teams = new Map<string, number>();
  const soft = pickFrom(eligible.filter(gradedIn(SOFT_GRADES)), n, taken, teams);
  if (soft.length >= n) return soft;
  return [...soft, ...pickFrom(eligible.filter((p) => gradedIn(SOFT_FALLBACK_GRADES)(p) && !gradedIn(SOFT_GRADES)(p)), n - soft.length, taken, teams)];
}

/** At most `n` of a player's rungs, evenly spread from his shortest line to his longest. */
export function spreadRungs(rungs: ParlayLeg[], n = SOFT_RUNGS_PER_PLAYER): ParlayLeg[] {
  if (rungs.length <= n) return rungs;
  if (n <= 1) return rungs.slice(0, n);
  return [...new Set(Array.from({ length: n }, (_, i) => Math.round((i * (rungs.length - 1)) / (n - 1))))].map((i) => rungs[i]);
}

/** Soft-matchup parlays at one book: for each SOFT_BANDS leg count, the likeliest combination of that many different players'
 *  overs under its price cap, each leg at SOFT_MIN_ODDS or longer and facing a defense graded in SOFT_GRADES (widened to
 *  SOFT_FALLBACK_GRADES when fewer than SOFT_MIN_PLAYERS have a usable rung). Tiles share at most MAX_SHARED_PLAYERS players
 *  with each other and with `avoid` where the pool allows, else repeat the likeliest. */
export function buildSoftMatchupParlays(entries: LadderEntry[], book: string, corr?: CorrTable, avoid: Parlay[] = []): Parlay[] {
  const withRungs = entries.map((e) => ({ base: e.base, rungs: spreadRungs(ladderRungs(e.base, e.ladder, book, SOFT_MIN_ODDS, SOFT_MIN_PROB)) }))
    .filter((e) => e.rungs.length);
  const soft = withRungs.filter((e) => gradedIn(SOFT_GRADES)(e.base));
  const widened = soft.length < SOFT_MIN_PLAYERS;
  // entries come biggest role first (softTargets), so the search looks at the starters, not the likeliest small lines
  const pool = (widened ? withRungs : soft).slice(0, SOFT_SEARCH_PLAYERS);
  if (pool.length < SOFT_MIN_LEGS) return [];
  const players = pool.map((e) => e.rungs);
  const chosen: Pick[][] = avoid.map((p) => p.legs);
  const best = SOFT_BANDS.map((band) => {
    const [legs] = searchLadders(players, [band], (ls) => ls.length === band.legs, corr, band.legs, chosen);
    if (legs) chosen.push(legs);
    return legs;
  });
  const grades = widened ? SOFT_FALLBACK_GRADES : SOFT_GRADES;
  const which = `${grades.slice(0, -1).split("").join(", ")}${grades.length > 2 ? "," : ""} or ${grades.slice(-1)}`;
  const blurb = `Every player faces a defense graded ${which} against his position, on lines priced ${SOFT_MIN_ODDS} or longer.`
    + (widened ? ` Too few ${SOFT_GRADES.split("").join(" or ")} matchups had a usable line, so ${SOFT_FALLBACK_GRADES.replace(SOFT_GRADES, "").split("").join(" and ")} ones are in too.` : "");
  const seen = new Set<string>();
  const out: Parlay[] = [];
  SOFT_BANDS.forEach((band, i) => {
    const legs = best[i];
    if (!legs) return;
    const sig = legs.map(pickKey).sort().join("&");
    if (seen.has(sig)) return;
    seen.add(sig);
    // on a small pool the longer tile is often the shorter one plus a leg or two: say so, rather than let it read as a new bet
    const prev = out[out.length - 1];
    const ext = prev && prev.legs.every((l) => legs.some((m) => pickKey(m) === pickKey(l)));
    const added = ext ? legs.filter((m) => !prev.legs.some((l) => pickKey(l) === pickKey(m))).map((l) => l.name) : [];
    out.push({ key: band.key, title: band.title, legs, ...priceParlay(legs, corr), extends: ext ? prev.key : undefined,
      blurb: ext ? `The ${prev.legs.length}-leg plus ${added.join(" and ")}. ${blurb}` : blurb });
  });
  return out;
}

/** The likeliest single alternate-line overs at this book (each player's best rung), for browsing and adding one at a time. */
export function ladderProps(entries: LadderEntry[], book: string, minProb = LADDER_PROP_MIN_PROB, n = 12): ParlayLeg[] {
  const out: ParlayLeg[] = [];
  for (const e of entries) {
    const rungs = ladderRungs(e.base, e.ladder, book).filter((r) => r.prob >= minProb);
    if (rungs.length) out.push(rungs.reduce((a, b) => (b.prob > a.prob ? b : a)));
  }
  return out.sort((a, b) => b.prob - a.prob).slice(0, n);
}

// ---------- anytime touchdown parlays ----------
/** Most games' top scorers combined in one touchdown parlay; more than this is a lottery ticket. */
export const TD_MAX_GAMES = 5;
/** Most single-game "each team's top scorer" tiles shown at once. */
export const TD_MAX_GAME_TILES = 4;

const bestBy = <T,>(xs: T[], key: (x: T) => string, score: (x: T) => number): T[] => {
  const best = new Map<string, T>();
  for (const x of xs) {
    const k = key(x), cur = best.get(k);
    if (!cur || score(x) > score(cur)) best.set(k, x);
  }
  return [...best.values()];
};

/**
 * Anytime-TD parlays at one book, from that book's TD picks:
 *  - for each chosen game, the likeliest scorer on each team parlayed together (a same-game parlay);
 *  - the likeliest scorer in each game parlayed across games: every game when none is chosen, else the chosen ones (two or more).
 * The cross-game tile keeps the TD_MAX_GAMES most likely scorers. Flagged plays (model and market far apart) are left out.
 */
export function buildTdParlays(tdPicks: Pick[], book: string, gameIds: string[] = [], corr?: CorrTable): Parlay[] {
  const pool = tdPicks.filter((p) => p.kind === "td" && p.side === "Over" && p.book === book && !p.flagged);
  const inScope = gameIds.length ? pool.filter((p) => gameIds.includes(p.game_id)) : pool;
  const out: Parlay[] = [];
  const make = (key: string, title: string, blurb: string, legs: Pick[]) => out.push({ key, title, blurb, legs, ...priceParlay(legs, corr) });

  for (const gameId of gameIds.slice(0, TD_MAX_GAME_TILES)) {
    const legs = bestBy(pool.filter((p) => p.game_id === gameId), (p) => p.team, (p) => p.prob).sort((a, b) => b.prob - a.prob);
    if (legs.length >= 2) make(`td-${gameId}`, `${gameLabel(gameId)}: top TD each team`, "The likeliest anytime scorer on each side of the game.", legs);
  }
  const perGame = bestBy(inScope, (p) => p.game_id, (p) => p.prob).sort((a, b) => b.prob - a.prob);
  if (perGame.length >= 2 && (gameIds.length === 0 || gameIds.length >= 2)) {
    const legs = perGame.slice(0, TD_MAX_GAMES);
    make("td-games", gameIds.length ? "Top TD from each selected game" : "Top TD from each game",
      perGame.length > legs.length ? `The likeliest scorer in each game, limited to the ${legs.length} most likely.` : "The likeliest anytime scorer in each game.", legs);
  }
  return out;
}

/** A pick as a betslip leg. Links aren't part of a pick; the slip's "Get links" and book switcher fill those in. */
export function pickToLeg(p: ParlayLeg): Leg {
  const base = { playerId: p.player_id, kind: p.kind, side: p.side, line: p.line, book: p.book };
  return { id: legId(base), ...base, name: p.name, team: p.team, opp: p.opp, home: p.home, odds: p.odds,
    link: p.link ?? null, eventLink: p.eventLink ?? null, alt: p.alt ?? false, prob: p.prob, ev: p.ev, gameId: p.game_id };
}
