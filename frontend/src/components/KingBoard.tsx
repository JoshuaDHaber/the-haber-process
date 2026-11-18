import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api, type KingRow, type Pick } from "../api";
import { americanOdds, fmtProj, fmtSd, kickoff, matchup, pct, signedPct } from "../format";
import { toAmerican } from "../slip";
import { useSort } from "../useSort";
import Avatar from "./Avatar";
import { BookName } from "./BookBadge";
import HowToRead from "./HowToRead";
import Info from "./Info";
import MatchupGrade from "./MatchupGrade";
import SidePill from "./SidePill";
import Skeleton from "./Skeleton";
import SortBar from "./SortBar";

/** Rows on the main board; sleepers get their own list underneath. */
const TOP_ROWS = 15;

/** "+900": the price at which a chance breaks even. */
const fair = (p: number) => americanOdds(Math.round(toAmerican(1 / p)));

type Select = (r: KingRow) => void;

/** One player: who he is and his matchup, his PRA over at the book, the projection, and his chances to finish top three
 *  and to lead the slate. Opens his PRA drawer, like a row on Who's Hot. */
function Row({ r, onSelect, chip }: { r: KingRow; onSelect: Select; chip: boolean }) {
  const p = r.pick;
  return (
    <tr onClick={() => onSelect(r)} className="clickable" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onSelect(r)}>
      <td>
        <div className="pcell">
          <Avatar name={r.name} size={34} />
          <div>
            <b>{r.name}</b> <span className="pos-pill">{r.pos}</span> <span className="mut">{r.team}</span>
            {r.status && <span className="q"> {r.status}</span>}
            {chip && r.sleeper && <span className="flag with" title="Outside the slate's top five projections, with a soft matchup and a real chance to lead">Sleeper</span>}
            <br /><span className="mut">{matchup(r.opp, r.home)} · {kickoff(r.gameday, r.gametime)}</span>
            <MatchupGrade kind="pra" pos={r.pos} opp={r.opp} rank={r.dvp_rank} n={r.dvp_n} side="Over" quiet />
          </div>
        </div>
      </td>
      <td className="c-market">#{r.proj_rank} projected</td>
      <td>{p ? <><SidePill kind="pra" side="Over" line={p.line} /><br /><span className="mut">{americanOdds(p.odds)} · <BookName book={p.book} size={14} /></span></>
        : <span className="mut">no PRA line</span>}</td>
      <td data-label="Proj PRA"><b>{fmtProj("pra", r.mu)}</b><br /><span className="mut">{fmtSd("pra", r.sd)}</span></td>
      <td data-label="Top 3">{pct(r.p_top3, 0)}</td>
      <td data-label="Leads slate"><b>{pct(r.p_top, r.p_top < 0.1 ? 1 : 0)}</b><span className="meter" aria-hidden><i style={{ width: `${Math.min(100, r.p_top * 250)}%` }} /></span>
        <span className="mut">fair {fair(r.p_top)}</span></td>
      <td className="c-ev">{p ? <span className={`ev-pill${p.ev < 0 ? " neg" : ""}`} title="EV of his PRA over at this price">{signedPct(p.ev)}</span> : <span className="mut">–</span>}</td>
    </tr>
  );
}

/** chip: mark sleepers in the row (the main list; the sleepers list doesn't need telling) */
function Table({ rows, onSelect, chip = true }: { rows: KingRow[]; onSelect: Select; chip?: boolean }) {
  return (
    <div className="card scroll cards">
      <table className="t-hot">
        <thead>
          <tr>
            <th scope="col">Player</th><th scope="col">Rank</th><th scope="col">PRA line</th><th scope="col">Proj PRA</th>
            <th scope="col">Top 3</th><th scope="col">Leads slate</th><th scope="col">EV <Info term="ev" /></th>
          </tr>
        </thead>
        <tbody>{rows.map((r) => <Row key={r.player_id} r={r} onSelect={onSelect} chip={chip} />)}</tbody>
      </table>
    </div>
  );
}

/** King of the Court: who's most likely to have the most points + rebounds + assists on the day's slate, then the sleepers. */
export default function KingBoard({ gameday, book, pills, onSelect }: { gameday?: string; book: string; pills: ReactNode; onSelect: (pid: string, pick?: Pick | null) => void }) {
  const q = useQuery({ queryKey: ["king", gameday, book], queryFn: () => api.king(gameday, book), placeholderData: (p) => p });
  const players = q.data?.players ?? [];
  const { sorted, toggle, arrow } = useSort(players, "p_top");
  const sleepers = players.filter((r) => r.sleeper);
  const select: Select = (r) => onSelect(r.player_id, r.pick);
  return (
    <>
      <div className="hot-bar">{pills}</div>
      <HowToRead storageKey="how-to-read-king-dismissed">
        <ol>
          <li>King of the Court pays when your player has the most points + rebounds + assists (PRA) of anyone on the day's slate. Ties count.</li>
          <li><b>Leads slate</b> is how often he comes out on top across {(q.data?.sims ?? 20000).toLocaleString()} simulated nights, each player drawn from his projection and the model's record of past misses. <b>Fair</b> is the price where that chance breaks even.</li>
          <li><b>Sleepers</b> sit outside the top five projections but face a soft defense for their position (matchup A or B) and still lead often enough to matter. The projection already counts the defense, so the chance is the model's own.</li>
          <li><b>PRA line</b> and <b>EV</b> <Info term="ev" /> are his PRA over at this book, if it's posted. Tap a player for his full ladder.</li>
        </ol>
      </HowToRead>
      {q.isLoading ? <Skeleton kind="rows" /> : q.error ? <p className="err">Couldn't load King of the Court.</p> : !players.length ? (
        <div className="card empty">No PRA projections for this slate yet. They come with each sync once the day's games are set.</div>
      ) : <>
        <SortBar toggle={toggle} arrow={arrow} options={[["p_top", "Leads slate"], ["p_top3", "Top 3"], ["mu", "Projection"], ["name", "Name"]]} />
        <section className="hot-teams" aria-labelledby="king-top-h">
          <h3 id="king-top-h" className="sub-h">Most likely to lead <span className="mut">· {q.data?.games} game{q.data?.games === 1 ? "" : "s"}</span></h3>
          <Table rows={sorted.slice(0, TOP_ROWS)} onSelect={select} />
        </section>
        {sleepers.length > 0 && (
          <section className="hot-teams" aria-labelledby="king-sleepers-h">
            <h3 id="king-sleepers-h" className="sub-h">Sleepers <span className="mut">· {sleepers.length}</span></h3>
            <Table rows={sleepers} onSelect={select} chip={false} />
          </section>
        )}
      </>}
    </>
  );
}
