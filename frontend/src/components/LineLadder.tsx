import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { sharedClient } from "../queryClients";
import { useMemo, useState } from "react";
import { api, type Kind, type LadderRow, type QuoteSide } from "../api";
import { useBetSlip } from "../BetSlipContext";
import { KIND_LABEL, isDouble, isYesNo, americanOdds, fmtProj, isNba, pct, playLabel, signedPct, timeAgo, unit } from "../format";
import { legId, type Leg } from "../slip";
import { BookName } from "./BookBadge";
import Info from "./Info";
import Skeleton from "./Skeleton";

export interface PlayerCtx { playerId: string; name: string; team: string; opp: string; home: boolean; gameId: string }

const LONG_SHOT_TITLE = "An alternate over longer than +250. These have hit well below their price (about 12% when the model said 14%), so the EV isn't counted as an edge";

/** A positive-EV side that isn't flagged: what the green highlight and "+EV only" count. */
const isEdge = (q: QuoteSide | null | undefined) => !!q && q.ev > 0 && !q.flag;

/** One side of one rung: the price, and the quote row it came from (its book, links and alt flag). */
export interface Cell { side: "Over" | "Under"; row: LadderRow; q: QuoteSide }

export const bestOf = (rows: LadderRow[], side: "over" | "under"): Cell | null => {
  let best: Cell | null = null;
  for (const row of rows) {
    const q = row[side];
    if (q && (!best || q.odds > best.q.odds)) best = { side: side === "over" ? "Over" : "Under", row, q };
  }
  return best;
};

/** A rung as a betslip leg. */
export const legFor = (ctx: PlayerCtx, kind: Kind, c: Cell): Leg => {
  const base = { playerId: ctx.playerId, kind, side: c.side, line: c.row.line, book: c.row.book };
  return { id: legId(base), ...base, name: ctx.name, team: ctx.team, opp: ctx.opp, home: ctx.home, odds: c.q.odds,
    link: c.q.link, eventLink: c.row.event_link, alt: c.row.alt, prob: c.q.prob, ev: c.q.ev, gameId: ctx.gameId };
};

/** Each line's best price per side, one row per line (and per book when `perBook`): a main line and its alternate twin at the
 *  same number collapse into one row, which keeps the better price on each side. */
export function ladderRows(quotes: LadderRow[], perBook: boolean) {
  const groups = new Map<string, LadderRow[]>();
  quotes.forEach((r) => { const k = perBook ? `${r.line}|${r.book}` : String(r.line); groups.set(k, [...(groups.get(k) ?? []), r]); });
  return [...groups.entries()].map(([key, rs]) => ({ key, line: rs[0].line, alt: rs.every((r) => r.alt), over: bestOf(rs, "over"), under: bestOf(rs, "under") }))
    .sort((a, b) => a.line - b.line || (a.over?.row.book ?? "").localeCompare(b.over?.row.book ?? ""));
}

export const ODDS_RANGE = 300; // prices shown by default: -300 to +300
const BEATS_LADDER = 0.03;      // a rung priced 3%+ better than the books' own ladder implies is marked

interface LadderProps { ctx: PlayerCtx; kind: Kind; mu: number; book: string; onBookChange: (b: string) => void; marketWeight?: number | null }

export default function LineLadder({ ctx, kind, mu, book, onBookChange, marketWeight = null }: LadderProps) {
  const isTd = isYesNo(kind); // anytime and first TD: one line (0.5), shown as Yes / No
  const qc = useQueryClient();
  const slip = useBetSlip();
  const meta = qc.getQueryData<{ has_odds_key: boolean; can_write: boolean; supports_alt?: boolean }>(["meta"]);
  const [allBooks, setAllBooks] = useState(false);
  const [onlyEv, setOnlyEv] = useState(false);
  const [limitOdds, setLimitOdds] = useState(true);
  const oddsRange = limitOdds ? ODDS_RANGE : 0;
  const ladder = useQuery({ queryKey: ["ladder", ctx.playerId, kind, oddsRange, marketWeight], queryFn: () => api.lines(ctx.playerId, kind, oddsRange, marketWeight) });
  const fetchAlt = useMutation({
    mutationFn: () => api.fetchAlt(ctx.playerId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ladder", ctx.playerId] });
      sharedClient.invalidateQueries({ queryKey: ["ladder", ctx.playerId] });   // the bet slip's copy of this player's lines
      qc.invalidateQueries({ queryKey: ["picks"] });
    },
  });

  const onlyBook = book !== "all";
  const rows = useMemo(() => ladderRows((ladder.data?.quotes ?? []).filter((q) => !onlyBook || q.book === book), allBooks || onlyBook),
    [ladder.data, allBooks, onlyBook, book]);
  const shown = onlyEv ? rows.filter((r) => isEdge(r.over?.q) || isEdge(r.under?.q)) : rows;
  const hasAlt = (ladder.data?.quotes ?? []).some((q) => q.alt);
  // alternate ladders are often priced on one side only: a column of dashes says nothing, so it's left out
  const showOver = !shown.length || shown.some((r) => r.over), showUnder = !shown.length || shown.some((r) => r.under);
  const toLeg = (c: Cell) => legFor(ctx, kind, c);

  const cost = 2; // main + alternate market for this stat
  const load = () => {
    if (window.confirm(`Fetch alternate lines and betslip links for this game?\n\nUses about ${cost} Odds API credits per stat this player is projected in.`)) fetchAlt.mutate();
  };

  const cell = (c: Cell | null) => {
    if (!c) return <td className="lcell none">–</td>;
    const id = legId({ playerId: ctx.playerId, kind, side: c.side, line: c.row.line, book: c.row.book });
    const inSlip = slip.has(id);
    const clash = !inSlip && slip.wouldClash(toLeg(c));
    const clashNote = `already on your slip at ${c.row.book}: books usually won't parlay both`;
    return (
      <td className={`lcell ${isEdge(c.q) ? "plus" : ""}`}>
        <button className={`add ${inSlip ? "on" : ""}${clash ? " clash" : ""}`} type="button" onClick={() => slip.toggle(toLeg(c))} title={clash ? `Another ${KIND_LABEL[kind]} line is ${clashNote}` : undefined}
          aria-label={`${inSlip ? "Remove" : "Add"} ${playLabel(kind, c.side, c.row.line)} at ${c.row.book}${clash ? `. Another line of this prop is ${clashNote}` : ""}`}>{inSlip ? "✓" : "+"}</button>
        <span className="lc-main"><span className="odds">{americanOdds(c.q.odds)}</span>
          <span className={isEdge(c.q) ? "pos" : "mut"}> {signedPct(c.q.ev)}</span>
          {c.q.flag && <span className="flag" title={LONG_SHOT_TITLE}>{c.q.flag}</span>}
          {!c.q.flag && (c.q.off_curve ?? 0) >= BEATS_LADDER && <span className="beats" title={`The books' own ladder puts this at ${pct(c.q.p_curve ?? 0)}; at ${americanOdds(c.q.odds)} that's ${signedPct(c.q.off_curve!)} EV before the model has a say.`}>beats ladder</span>}<br />
          <span className="mut"><BookName book={c.row.book} size={14} /></span></span>
      </td>
    );
  };

  return (
    <section>
      <div className="ladder-head">
        <h3>Lines &amp; alternates</h3>
        <div className="ladder-tools">
          {!isTd && <label className="check" title={`Hide prices beyond -${ODDS_RANGE} / +${ODDS_RANGE}`}>
            <input type="checkbox" checked={limitOdds} onChange={(e) => setLimitOdds(e.target.checked)} /> −{ODDS_RANGE} to +{ODDS_RANGE}
          </label>}
          <label className="check"><input type="checkbox" checked={onlyEv} onChange={(e) => setOnlyEv(e.target.checked)} /> +EV only</label>
          {!onlyBook && <label className="check"><input type="checkbox" checked={allBooks} onChange={(e) => setAllBooks(e.target.checked)} /> All books</label>}
        </div>
      </div>
      {ladder.isLoading && <Skeleton kind="rows" count={4} />}
      {ladder.error && <p className="err">Couldn't load lines.</p>}
      {ladder.data && (
        <>
          {onlyBook && (
            <div className="book-note">
              Showing <b>{book}</b> lines only (from your sportsbook filter).
              <button className="link" onClick={() => onBookChange("all")}>Show all books</button>
            </div>
          )}
          <details className="mut ladder-note">
            <summary>How these are priced</summary>
            {isDouble(kind) ? `Model chance of a ${KIND_LABEL[kind].toLowerCase()} ${(mu * 100).toFixed(0)}%. Win probability and EV here use the model alone (no market blend).`
              : isTd ? `Model chance ${kind === "ftd" ? "of the game's first TD" : "of a TD"} ${(mu * 100).toFixed(0)}%. Win probability and EV lean at least 60% on the market's chance, since the model is a weak signal here.`
              : ladder.data.curve && ladder.data.w != null
                ? <>Win probability and EV blend the model (projection {fmtProj(kind, mu)} {unit(kind)}) with the market curve <Info term="curve" /> read off {ladder.data.curve.n_lines} lines across books
                  (market median {fmtProj(kind, ladder.data.curve.median)}), {pct(ladder.data.w, 0)} market.</>
                : `Win probability and EV here use the model alone (no market blend). Projection ${fmtProj(kind, mu)} ${unit(kind)}.`}
            {ladder.data.alt_fetched_at && ` Alternates fetched ${timeAgo(ladder.data.alt_fetched_at)}.`}
          </details>
          <div className="scroll ladder">
            <table>
              <thead><tr><th>{isTd ? "Market" : "Line"}</th>{showOver && <th>{isTd ? "Yes" : "Over"}</th>}{showUnder && <th>{isTd ? "No" : "Under"}</th>}</tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.key} className={!isTd && Math.abs(r.line - mu) < (isNba(kind) ? 1.5 : 5) ? "near" : ""}>
                    <td><b>{isTd ? KIND_LABEL[kind] : r.line}</b>{r.alt && <span className="alt">alt</span>}</td>
                    {showOver && cell(r.over)}{showUnder && cell(r.under)}
                  </tr>
                ))}
                {!shown.length && <tr><td colSpan={3} className="mut">No {onlyBook ? `${book} ` : ""}quotes{onlyEv ? " with positive EV" : ""} for this player.</td></tr>}
              </tbody>
            </table>
          </div>
          {!hasAlt && !isTd && meta?.can_write && meta.supports_alt !== false && (
            <div className="alt-cta">
              <button className="primary" onClick={load} disabled={!meta?.has_odds_key || fetchAlt.isPending}>
                {fetchAlt.isPending ? "Fetching…" : "Load alternate lines"}
              </button>
              <span className="mut">{meta?.has_odds_key ? `Uses ~${cost} API credits · adds betslip links` : "Needs ODDS_API_KEY"}</span>
            </div>
          )}
          {fetchAlt.error && <p className="err">{(fetchAlt.error as Error).message}</p>}
          {fetchAlt.data && <p className="mut">Loaded {fetchAlt.data.alt_quotes} alternate quotes. Credits left: {fetchAlt.data.credits_remaining ?? "?"}.</p>}
        </>
      )}
    </section>
  );
}
