import { IconFilterOff } from "@tabler/icons-react";
import { useState } from "react";
import type { Filters, GameWeather, Projection } from "../api";
import { isYesNo, KIND_LABEL, effText, fmtProj, fmtSd, matchup, signed } from "../format";
import { useIncremental } from "../useIncremental";
import { filtersNarrowed } from "../route";
import { useSort } from "../useSort";
import EmptyState from "./EmptyState";
import Avatar from "./Avatar";
import SortBar, { SortTh } from "./SortBar";
import Spark from "./Spark";
import WeatherChip from "./WeatherChip";
import MatchupGrade from "./MatchupGrade";

export const THIN_TITLE = "Only a game or two of history (a rookie, or back from IR): a rough guess, shown for reference and never priced";

/** filters: the board's, so an empty board can point at the ones hiding rows (a game page has none).
 *  limit: show only the top rows in the current sort, with a "Show all" button (a game page; the boards scroll on instead). */
export default function ProjectionsTable({ rows, onSelect, weather = {}, nba = false, filters, busy = false, limit }: { rows: Projection[]; onSelect: (p: Projection) => void; busy?: boolean;
  weather?: Record<string, GameWeather | null | undefined>; nba?: boolean; filters?: Filters; limit?: number }) {
  const { sorted, toggle, arrow, sortOf } = useSort(rows, "mu");
  const [showAll, setShowAll] = useState(false);
  const capped = limit !== undefined && !showAll && sorted.length > limit;
  const { visible: incremental, more: moreRows, showMore, sentinel } = useIncremental(sorted);
  const visible = capped ? sorted.slice(0, limit) : incremental;
  const more = !capped && moreRows;
  const th = (key: string, label: string, title?: string) => (
    <SortTh k={key} label={label} title={title} toggle={toggle} arrow={arrow} sortOf={sortOf} />
  );
  // the filter chips above already clear each filter (and "Clear all"), so the message points there instead of repeating them
  if (!rows.length) return (
    <EmptyState icon={IconFilterOff} title="No projections match these filters">
      {filters && filtersNarrowed(filters) ? "Remove a filter above, or try another market or game." : "Try another market."}
    </EmptyState>
  );
  return (
    <>
    <SortBar toggle={toggle} arrow={arrow} options={[["mu", "Proj"], ["vol", "Volume"], ["eff", "Efficiency"], ["spread", "Spread"], ["name", "Name"]]} />
    <div className={`card scroll cards${busy ? " refreshing" : ""}`} aria-busy={busy}>
      <table className="t-proj">
        <thead>
          <tr>
            {th("name", "Player")}<th scope="col">Market</th><th scope="col">Game</th>
            {th("mu", "Proj")}{th("sd", "SD")}{nba ? th("vol", "Minutes", "Projected minutes") : th("vol", "Volume", "Projected carries / targets / attempts; for longest reception, expected catches")}
            {nba ? th("eff", "Per 36", "Per 36 minutes, after the opponent adjustment") : th("eff", "Yds/att · E[TD]", "Yards per carry/target/attempt; for receptions, catch rate; for passing TDs, TDs per attempt; for longest reception, yards per catch; for anytime TD, expected touchdowns")}{th("spread", "Spread", "Team spread (positive = favorite)")}<th scope="col">Last 5</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((p) => (
            <tr key={`${p.player_id}-${p.kind}`} className="clickable" onClick={() => onSelect(p)} tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && onSelect(p)}>
              <td>
                <div className="pcell">
                  <Avatar name={p.name} size={34} />
                  <div><b>{p.name}</b> <span className="pos-pill">{p.pos}</span> <span className="mut">{p.team}</span>{p.status && <span className="q"> {p.status}</span>}{p.thin && <> <span className="thin-pill" title={THIN_TITLE}>Thin history</span></>}</div>
                </div>
              </td>
              <td>{KIND_LABEL[p.kind]}</td>
              <td>{matchup(p.opp, p.home)}{!isYesNo(p.kind) && <WeatherChip weather={weather[p.game_id]} wx={p.wx} />}
                <MatchupGrade kind={p.kind} pos={p.pos} opp={p.opp} rank={p.dvp_rank} n={p.dvp_n} factor={p.dvp} slot={p.dvp_slot} /></td>
              <td data-label="Proj"><b>{fmtProj(p.kind, p.mu)}</b></td>
              <td data-label="SD">{isYesNo(p.kind) ? "–" : fmtSd(p.kind, p.sd)}</td>
              <td data-label={nba ? "Minutes" : "Volume"}>{p.vol.toFixed(1)}</td>
              <td data-label={nba ? "Per 36" : "Yds/att · E[TD]"}>{isYesNo(p.kind) ? (nba ? "–" : p.eff.toFixed(2)) : effText(p.kind, p.eff)[1]}</td>
              <td data-label="Spread">{signed(p.spread)}</td>
              <td data-label="Last 5"><Spark values={p.last5} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    {limit !== undefined && sorted.length > limit && (
      <button className="link go-more" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${sorted.length} projections`}</button>
    )}
    {more && (
      <div ref={sentinel} className="more-rows">
        <button className="primary outline" onClick={showMore}>Show more ({visible.length.toLocaleString()} of {sorted.length.toLocaleString()})</button>
      </div>
    )}
    </>
  );
}
