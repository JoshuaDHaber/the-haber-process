import type { Kind } from "./api";
import { legLeague } from "./bets";
import type { Leg } from "./slip";

/**
 * "Send to Gambly": Gambly (gambly.com) turns a plain-English bet description into a slip priced at every major book it
 * covers (FanDuel, DraftKings, BetMGM, Caesars, Fanatics, bet365, theScore, Kalshi, Novig and more), so the bettor can
 * shop the price and open it at a book our own links don't reach. Its public Ask-Gambly page takes the description in the
 * URL and submits it on load, with no account or key. Gambly's API (through Unabated) needs a partner signup; this
 * doesn't use it.
 */
export const GAMBLY_CHAT = "https://gambly.com/chat";

/** How each market reads in a prompt. Over/under markets follow "Over 54.5"; yes/no markets are a whole phrase. */
const MARKET_TEXT: Record<Exclude<Kind, "td" | "ftd" | "dd" | "tpl" | "ml">, string> = {
  rush: "rushing yards", rec: "receiving yards", pass: "passing yards", rr: "rushing + receiving yards", recs: "receptions",
  ptd: "passing touchdowns", lng: "longest reception", rlng: "longest rush", pts: "points", reb: "rebounds", ast: "assists",
  threes: "threes made", pra: "points + rebounds + assists", pr: "points + rebounds", pa: "points + assists", stl: "steals", blk: "blocks",
};
const YES_TEXT: Record<"td" | "ftd" | "dd" | "tpl", string> = {
  td: "anytime touchdown", ftd: "first touchdown scorer", dd: "to record a double-double", tpl: "to record a triple-double",
};

export type Unmapped = { leg: Leg; reason: string };

/** One leg as Gambly reads it, or why it can't go: books (and so Gambly) don't sell the "no" side of a yes/no market. */
export function gamblyLeg(l: Leg): string | { reason: string } {
  const league = legLeague(l).toUpperCase();
  if (l.kind === "ml") return `${l.name} moneyline (${league})`;
  if (l.kind in YES_TEXT) {
    if (l.side === "Under") return { reason: "Gambly can't build the \"no\" side of this market" };
    return `${l.name} ${YES_TEXT[l.kind as keyof typeof YES_TEXT]} (${league})`;
  }
  return `${l.name} ${l.side} ${l.line} ${MARKET_TEXT[l.kind as keyof typeof MARKET_TEXT]} (${league})`;
}

/** The same player's same market (or a game's moneyline) twice can't sit in one parlay, even from different books here. */
const marketKey = (l: Leg) => (l.kind === "ml" ? `ml|${l.gameId}` : `${l.playerId}|${l.kind}`);

/**
 * The slip as one Gambly parlay: the prompt, the legs that went into it, and the ones that couldn't (each with why).
 * Every book's legs go into a single parlay, since Gambly prices the whole slip at each book itself.
 */
export function gamblyPrompt(legs: Leg[]): { prompt: string; mapped: Leg[]; unmapped: Unmapped[] } {
  const lines: string[] = [];
  const mapped: Leg[] = [];
  const unmapped: Unmapped[] = [];
  const seen = new Set<string>();
  for (const l of legs) {
    const text = gamblyLeg(l);
    if (typeof text !== "string") { unmapped.push({ leg: l, reason: text.reason }); continue; }
    if (seen.has(marketKey(l))) { unmapped.push({ leg: l, reason: "already in the parlay at another line or side" }); continue; }
    seen.add(marketKey(l));
    lines.push(text);
    mapped.push(l);
  }
  const prompt = lines.length === 0 ? ""
    : lines.length === 1 ? `Build me a betslip: ${lines[0]}`
    : `Build me a ${lines.length}-leg parlay:\n${lines.map((t, i) => `${i + 1}. ${t}`).join("\n")}`;
  return { prompt, mapped, unmapped };
}

/** The Ask-Gambly link that opens with the slip already submitted, or null when no leg can go. */
export function gamblyUrl(legs: Leg[]): { url: string | null; mapped: Leg[]; unmapped: Unmapped[] } {
  const { prompt, mapped, unmapped } = gamblyPrompt(legs);
  if (!prompt) return { url: null, mapped, unmapped };
  const q = new URLSearchParams({ entry: "ask-gambly", prompt, autoSubmit: "1" });
  return { url: `${GAMBLY_CHAT}?${q.toString()}`, mapped, unmapped };
}
