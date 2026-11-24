import { useQuery } from "@tanstack/react-query";
import { IconChartLine } from "@tabler/icons-react";
import { useState } from "react";
import { api, type Kind, type MarketMove } from "../api";
import { KIND_SHORT, americanOdds, isYesNo, pct, signedPct, signedPts, timeAgo } from "../format";
import type { League } from "../league";
import EmptyState, { LoadError } from "./EmptyState";
import Info from "./Info";
import Skeleton from "./Skeleton";

type View = "movers" | "steam" | "flat";
/** Rows of each table before "Show all". */
const FIRST = 15;
/** As the backend counts them (market_fit.MOVER_PTS / FLAT_PTS). */
const MOVER = 0.04, FLAT = 0.015;

const when = (iso: string) => new Date(iso.endsWith("Z") ? iso : `${iso}Z`).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
const lineText = (m: MarketMove) => (isYesNo(m.kind) ? "Yes" : m.open_line != null && m.open_line !== m.line ? `${m.open_line} → ${m.line}` : `${m.line}`);

/** Which rows a view shows, biggest first (the backend sorts by size of move). */
export function viewRows(moves: MarketMove[], view: View): MarketMove[] {
  if (view === "steam") return moves.filter((m) => m.steam !== 0);
  if (view === "flat") return moves.filter((m) => m.move != null && Math.abs(m.move) < FLAT && m.open_line === m.line && m.fetches >= 3)
    .sort((a, b) => b.fetches - a.fetches || b.books - a.books);
  return moves.filter((m) => m.move != null && Math.abs(m.move) >= MOVER);
}

/** "Market": the slate's prop market in one place, read from odds already stored (it never spends Odds API credits):
 *  the biggest moves since the first fetch, steam, the lines that haven't budged, and quotes far from the consensus. */
export default function MarketBoard({ league, onSelect, gameIds }: { league: League; onSelect: (id: string, kind: Kind) => void;
  /** NBA: the games of the date picked at the top of the page (all of them when omitted). */
  gameIds?: string[] }) {
  const q = useQuery({ queryKey: ["market", league], queryFn: api.market });
  const [view, setView] = useState<View>("movers");
  const [allMoves, setAllMoves] = useState(false);
  const [allOut, setAllOut] = useState(false);
  if (q.isPending) return <Skeleton kind="rows" label="Loading the market…" />;
  if (q.isError && !q.data) return <LoadError what="the market" retry={q.refetch} />;
  const keep = gameIds ? new Set(gameIds) : null;
  const d = keep ? { ...q.data, moves: q.data.moves.filter((m) => keep.has(m.game_id)), outliers: q.data.outliers.filter((o) => keep.has(o.game_id)) } : q.data;
  if (!d.moves.length && !d.outliers.length) return (
    <EmptyState icon={IconChartLine} title="No prop lines yet">
      The market board reads the odds fetches already stored for this {league === "nba" ? "slate" : "week"}. It fills in after the next scheduled odds sync.
    </EmptyState>
  );
  const steamN = d.moves.filter((m) => m.steam !== 0).length;
  const movers = viewRows(d.moves, "movers").length, flat = viewRows(d.moves, "flat").length;
  const rows = viewRows(d.moves, view);
  const shown = allMoves ? rows : rows.slice(0, FIRST);
  const outs = allOut ? d.outliers : d.outliers.slice(0, FIRST);
  return (
    <div className={`market${q.isRefetching ? " refreshing" : ""}`} aria-busy={q.isRefetching}>
      <div className="mkt-tiles">
        <div className="card mkt-tile"><span className="mut">Odds fetches</span><b>{d.fetches}</b>
          <small className="mut">{d.first_at && d.last_at ? (d.fetches > 1 ? `${when(d.first_at)} to ${when(d.last_at)}` : when(d.last_at)) : ""}</small></div>
        <div className="card mkt-tile"><span className="mut">Movers</span><b>{movers}</b><small className="mut">fair chance moved {Math.round(MOVER * 100)}+ pts</small></div>
        <div className="card mkt-tile"><span className="mut">Steam</span><b>{steamN}</b><small className="mut">3+ books moved together, last fetch</small></div>
        <div className="card mkt-tile"><span className="mut">Flat</span><b>{flat}</b><small className="mut">same line, under {FLAT * 100} pts, 3+ fetches</small></div>
      </div>
      {d.fetches < 2 && <p className="mut board-note">One odds fetch so far ({d.last_at ? timeAgo(d.last_at) : ""}), so nothing has moved yet. Moves show after the next sync.</p>}

      <section className="mkt-section" aria-labelledby="mkt-moves-h">
        <h3 id="mkt-moves-h" className="sub-h">Line moves since the first fetch <span className="mut">· {rows.length}</span> <Info term="move" /></h3>
        <div className="seg small" role="group" aria-label="Which lines">
          {(["movers", "steam", "flat"] as View[]).map((v) => (
            <button type="button" key={v} className={view === v ? "on" : ""} aria-pressed={view === v} onClick={() => { setView(v); setAllMoves(false); }}>
              {v === "movers" ? `Movers ${movers}` : v === "steam" ? `Steam ${steamN}` : `Flat ${flat}`}
            </button>
          ))}
        </div>
        {rows.length === 0 ? (
          <EmptyState compact title={view === "steam" ? "No steam at the last fetch" : view === "flat" ? "No flat lines yet" : "Nothing has moved 4+ points"}>
            {view === "flat" ? "A line counts as flat once three fetches have quoted it at the same number." : "Check back after the next odds sync."}
          </EmptyState>
        ) : <>
          <div className="card mkt-card">
            <table>
              <thead><tr><th scope="col">Player</th><th scope="col">Line</th><th scope="col" title="The books' fair chance of the over (Yes) now">Over</th><th scope="col">Move</th></tr></thead>
              <tbody>
                {shown.map((m) => (
                  <tr key={`${m.game_id}-${m.player_id}-${m.kind}`}>
                    <td><button type="button" className="link strong" onClick={() => onSelect(m.player_id, m.kind)}>{m.name}</button>
                      <small className="mut mkt-meta">{m.team} · {KIND_SHORT[m.kind]} · {m.books} book{m.books === 1 ? "" : "s"}</small></td>
                    <td>{lineText(m)}</td>
                    <td>{m.p_over != null ? pct(m.p_over, 0) : "–"}</td>
                    <td className={m.move == null || Math.abs(m.move) < 0.005 ? "" : m.move > 0 ? "over" : "under"}>
                      {m.move != null ? signedPts(m.move) : "–"}
                      {m.steam !== 0 && <small className={`mkt-steam ${m.steam > 0 ? "over" : "under"}`}>steam {m.steam > 0 ? (isYesNo(m.kind) ? "Yes" : "Over") : (isYesNo(m.kind) ? "No" : "Under")}</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > FIRST && <p className="mkt-more"><button type="button" className="chip-btn" onClick={() => setAllMoves(!allMoves)}>{allMoves ? `Show the top ${FIRST}` : `Show all ${rows.length}`}</button></p>}
          <p className="mut board-note">Move: the books' fair chance of the over at today's line, now against the first fetch (an earlier line is moved to today's along the model's curve), so a moved line and a moved price read on one scale.</p>
        </>}
      </section>

      <section className="mkt-section" aria-labelledby="mkt-out-h">
        <h3 id="mkt-out-h" className="sub-h">Off the consensus <span className="mut">· {d.outliers.length}</span></h3>
        {d.outliers.length === 0 ? (
          <EmptyState compact title="Every book is close to the market">No main line at the last fetch sat far from the consensus or priced 4%+ better than it's worth.</EmptyState>
        ) : <>
          <div className="card mkt-card">
            <table>
              <thead><tr><th scope="col">Player</th><th scope="col">Book's line</th><th scope="col">vs market</th></tr></thead>
              <tbody>
                {outs.map((o) => (
                  <tr key={`${o.game_id}-${o.player_id}-${o.kind}-${o.book}`}>
                    <td><button type="button" className="link strong" onClick={() => onSelect(o.player_id, o.kind)}>{o.name}</button>
                      <small className="mut mkt-meta">{o.team} · {KIND_SHORT[o.kind]} · {o.book}</small></td>
                    <td>{o.side} {o.line} <small className="mut mkt-meta">{americanOdds(o.odds)} · consensus {o.consensus}</small></td>
                    <td><b className={o.ev > 0 ? "pos" : "neg"}>{signedPct(o.ev)}</b><small className="mut mkt-meta">fair {pct(o.fair, 0)}, {o.books} books</small></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.outliers.length > FIRST && <p className="mkt-more"><button type="button" className="chip-btn" onClick={() => setAllOut(!allOut)}>{allOut ? `Show the top ${FIRST}` : `Show all ${d.outliers.length}`}</button></p>}
          <p className="mut board-note">A book's main line far from the consensus line, or a price 4%+ better than the market's own fair chance at that line (prices from −300 to +300, over/under markets). Often a stale line about to move. The market's view only: the model isn't in this number.</p>
        </>}
      </section>
    </div>
  );
}
