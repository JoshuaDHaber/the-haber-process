// Plain-English summaries written from templates, from numbers the app already has: no model calls, no LLM. The
// drawer's "read" on one prop and the game page's "rundown". Each function returns sentences; the caller joins them.
import type { GameContext, GameWeather, Kind, MoneylineGame, Pick, Projection, QuoteSide } from "./api";
import { KIND_LABEL, americanOdds, fmtProj, isYesNo, pct, playLabel, signedPct } from "./format";

const breakEven = (odds: number) => 1 / (odds > 0 ? 1 + odds / 100 : 1 + 100 / -odds);
const pctOff = (x: number) => `${Math.abs(Math.round((x - 1) * 100))}%`;
/** A gap this big between the model's chance and the market's usually means the model is missing something. */
export const DISAGREE = 0.15;

export interface ReadInput {
  kind: Kind;
  proj: Projection;
  /** The play being read: side, line, book and its quote. Null with no price (the read stays on the projection). */
  play: { side: "Over" | "Under"; line: number; book: string; q: QuoteSide } | null;
  /** His record at this line over a recent slice of games, e.g. { label: "Last 10", hit: 6, n: 10 }. */
  form?: { label: string; hit: number; n: number } | null;
  /** Matchup grade in words (MatchupGrade.matchupText), with its letter. */
  matchup?: { letter: string; text: string } | null;
}

/** The drawer's read on one prop: the projection against the line, the price against the chances, then whatever
 *  context moves it (matchup, weather, a QB change, a teammate out), his recent form at the line, and a warning when
 *  the model and the market are far apart. */
export function propRead({ kind, proj, play, form, matchup }: ReadInput): string[] {
  const out: string[] = [];
  const yes = isYesNo(kind);
  const stat = KIND_LABEL[kind].toLowerCase();
  if (yes) {
    out.push(`The model gives ${proj.name} a ${pct(proj.mu, 0)} chance${kind === "td" ? " to score a touchdown" : kind === "ftd" ? " to score the game's first touchdown" : ""}.`);
  } else if (play) {
    const gap = proj.mu - play.line;
    const near = Math.abs(gap) < (isCount(kind) ? 0.25 : 1);
    out.push(near
      ? `The model projects ${fmtProj(kind, proj.mu)} ${stat}, right at the ${play.line} line.`
      : `The model projects ${fmtProj(kind, proj.mu)} ${stat}, ${fmtProj(kind, Math.abs(gap))} ${gap > 0 ? "above" : "below"} the ${play.line} line.`);
  } else {
    out.push(`The model projects ${fmtProj(kind, proj.mu)} ${stat}.`);
  }
  if (play) {
    const q = play.q;
    const be = breakEven(q.odds);
    const what = playLabel(kind, play.side, play.line);
    const chances = [q.p_model != null ? `the model ${pct(q.p_model, 0)}` : null, q.p_curve != null ? `the market ${pct(q.p_curve, 0)}` : null]
      .filter(Boolean).join(", ");
    out.push(`${what} at ${americanOdds(q.odds)} (${play.book}) breaks even at ${pct(be, 0)}; ${chances ? `${chances}, ` : ""}`
      + `blended ${pct(q.prob, 0)}: ${q.ev > 0 ? `${signedPct(q.ev)} EV` : `not enough to beat the price (${signedPct(q.ev)} EV)`}.`);
    if (q.p_model != null && q.p_curve != null && Math.abs(q.p_model - q.p_curve) >= DISAGREE) {
      out.push(`The model and the books are ${Math.round(Math.abs(q.p_model - q.p_curve) * 100)} points apart, which more often means the model is missing something (a role change, an injury) than a gift.`);
    }
  }
  const ctx: string[] = [];
  if (matchup && !yes) {
    const soft = "AB".includes(matchup.letter), tough = "DF".includes(matchup.letter);
    const effect = play && (soft || tough) ? ((soft === (play.side === "Over")) ? ", which helps the " : ", which works against the ") + play.side : "";
    ctx.push(`Matchup ${matchup.letter}: ${matchup.text.replace(/\.$/, "").replace(/^(\S+): /, "$1 is the ")}${effect}`);
  }
  if (proj.wx != null && Math.abs(proj.wx - 1) >= 0.03) ctx.push(`the weather ${proj.wx < 1 ? "takes" : "adds"} ${pctOff(proj.wx)} ${proj.wx < 1 ? "off" : ""}`.trim());
  if (proj.qb_adj != null && proj.qb_adj <= 0.97) ctx.push(`${proj.qb_name || "a backup"} starting at QB takes ${pctOff(proj.qb_adj)} off`);
  if (proj.teammate_out) ctx.push(`${proj.teammate_out} out, so there's more volume to go around`);
  if (ctx.length) out.push(`${ctx[0][0].toUpperCase()}${ctx[0].slice(1)}${ctx.length > 1 ? `; ${ctx.slice(1).join("; ")}` : ""}.`);
  if (form && form.n >= 3 && play) {
    const r = form.hit / form.n;
    out.push(`${playLabel(kind, play.side, play.line)} hit in ${form.hit} of his ${form.label.toLowerCase()} (${pct(r, 0)})${r >= 0.7 || r <= 0.3 ? ", but the model already weights his recent games" : ""}.`);
  }
  return out;
}

const isCount = (k: Kind) => k === "recs" || k === "ptd" || k === "pts" || k === "reb" || k === "ast" || k === "threes" || k === "stl" || k === "blk";

export interface RundownInput {
  league: "nfl" | "nba";
  ml?: MoneylineGame | null;
  ctx?: GameContext | null;
  /** The game's priced plays, any EV (the rundown names the best and counts the positive ones). */
  picks?: Pick[] | null;
  weather?: GameWeather | null;
}

/** The game page's rundown: who's favored and by how much (win chances, spread, implied points), the weather when it
 *  matters, the roles worth knowing (a rising target share, the red-zone leader), and the board's best play here. */
export function gameRundown({ league, ml, ctx, picks, weather }: RundownInput): string[] {
  const out: string[] = [];
  const sides = ml?.sides ?? [];
  if (sides.length === 2) {
    const [fav, dog] = [...sides].sort((a, b) => b.prob - a.prob);
    const teams = ctx?.teams ?? [];
    const imp = teams.filter((t) => t.implied != null);
    const spread = ml?.spread != null ? Math.abs(ml.spread) : teams.find((t) => t.team === fav.team)?.spread ?? null;
    const lead = `${fav.team} ${fav.prob >= 0.55 && spread ? `is a ${spread}-point favorite` : "has the edge"} over ${dog.team}: ${pct(fav.prob, 0)} to win`;
    const parts = imp.length === 2
      ? `, and the total of ${imp[0].total} splits ${[...imp].sort((a, b) => b.implied! - a.implied!).map((t) => `${t.team} ${t.implied!.toFixed(1)}`).join(", ")}`
      : "";
    out.push(`${lead}${parts}.`);
    if (fav.p_mkt != null && Math.abs(fav.p_model - fav.p_mkt) >= 0.06) {
      out.push(`The model is ${fav.p_model > fav.p_mkt ? "higher" : "lower"} on ${fav.team} than the books (${pct(fav.p_model, 0)} vs ${pct(fav.p_mkt, 0)}).`);
    }
  }
  if (league === "nba") {
    const b20 = ml?.blowout?.chances.find((c) => c.margin === 20);
    if (b20 && b20.p >= 0.25) out.push(`A ${pct(b20.p, 0)} chance it's decided by 20+, when starters can sit the fourth quarter.`);
  }
  if (weather && !weather.indoor && weather.source === "forecast" && weather.wind != null && weather.wind >= 15) {
    const f = weather.factors ?? {};
    out.push(`Wind of ${Math.round(weather.wind)} mph at kickoff${f.pass != null && f.pass < 0.99 ? ` takes ${pctOff(f.pass)} off the passing projections` : ""}.`);
  }
  const rows = (ctx?.teams ?? []).flatMap((t) => t.players);
  const riser = rows.filter((r) => r.tgt_trend > 0 && r.tgt_prior != null).sort((a, b) => (b.tgt_l5 - b.tgt_prior!) - (a.tgt_l5 - a.tgt_prior!))[0];
  if (riser) out.push(`${riser.name}'s target share is up to ${pct(riser.tgt_l5, 0)} in ${riser.team}'s recent games, from ${pct(riser.tgt_prior!, 0)} before.`);
  const rz = rows.filter((r) => r.rz_share != null && r.rz >= 5).sort((a, b) => b.rz_share! - a.rz_share!)[0];
  if (rz) out.push(`${rz.name} gets ${pct(rz.rz_share!, 0)} of ${rz.team}'s touches inside the 20${rz.p_td2 != null ? `, with a ${pct(rz.p_td2, 0)} chance of 2+ TDs` : ""}.`);
  const priced = (picks ?? []).filter((p) => !p.flagged);
  const plus = priced.filter((p) => p.ev > 0);
  if (plus.length) {
    const best = [...plus].sort((a, b) => b.kelly - a.kelly)[0];
    out.push(`The board's best play here: ${best.name} ${playLabel(best.kind, best.side, best.line)}${isYesNo(best.kind) ? "" : ` ${KIND_LABEL[best.kind].toLowerCase()}`} at ${americanOdds(best.odds)} (${best.book}), ${signedPct(best.ev)} EV`
      + `${plus.length > 1 ? `, one of ${plus.length} +EV props for this game` : ""}.`);
  } else if (picks && priced.length) {
    out.push("No prop in this game beats its price right now.");
  }
  return out;
}
