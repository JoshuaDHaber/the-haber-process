import { useMemo, useState } from "react";
import type { GameWeather, Pick } from "../api";
import { onePerPlayer, type GroupedPick } from "../onePerPlayer";
import { usePref } from "../prefs";
import { flagChip, isNba, isYesNo, KIND_LABEL, americanOdds, fmtEdge, fmtProj, fmtSd, kickoff, matchup, pct, playLabel, signedPct, signedPts } from "../format";
import { useSort } from "../useSort";
import Avatar from "./Avatar";
import SortBar from "./SortBar";
import Spark from "./Spark";
import MatchupGrade from "./MatchupGrade";
import WeatherChip from "./WeatherChip";
import { BookName } from "./BookBadge";
import Info, { type Term } from "./Info";
import SidePill from "./SidePill";


/** A side the projection seems to argue against (an Under with the average over the line): the model's typical game
 *  (median) explains it when a few long plays pull the average up. */
function againstProjection(p: Pick): string | null {
  if (isYesNo(p.kind) || p.median == null) return null;
  const against = p.side === "Under" ? p.mu > p.line : p.mu < p.line;
  if (!against) return null;
  const typical = fmtProj(p.kind, p.median);
  return p.side === "Under"
    ? `The ${fmtProj(p.kind, p.mu)} projection is an average that a few big games pull up. His typical game is ${typical}, under the line.`
    : `The ${fmtProj(p.kind, p.mu)} projection is an average that a few quiet games pull down. His typical game is ${typical}, over the line.`;
}

/** picks arrive best EV first. limit shows only the top rows (after grouping) with a "Show all" button, as on the game pages. */
export default function PicksTable({ picks, onSelect, weather = {}, empty, limit }: { picks: Pick[]; onSelect: (p: Pick) => void; weather?: Record<string, GameWeather | null | undefined>; empty?: string; limit?: number }) {
  const [grouped, setGrouped] = usePref("one_per_player");
  // phones: compact cards (player, pick, win prob, EV) unless the reader asks for every number on the card
  const [detailed, setDetailed] = usePref("detailed_cards");
  const [showAll, setShowAll] = useState(false);
  const rows: GroupedPick[] = useMemo(() => grouped ? onePerPlayer(picks) : picks.map((p) => ({ ...p, related: [] })), [picks, grouped]);
  const shown = useMemo(() => (limit && !showAll ? rows.slice(0, limit) : rows), [rows, limit, showAll]);
  const { sorted, toggle, arrow } = useSort(shown, "ev");
  const th = (key: string, label: string, term?: Term) => (
    <th scope="col" onClick={() => toggle(key)} aria-sort="none" className="sortable">{label}{arrow(key)}{term && <> <Info term={term} /></>}</th>
  );
  if (!picks.length) return <div className="card empty">{empty ?? <>No plays clear the current filters. Try lowering Min EV (it can go below 0) to see the closest ones.</>}</div>;
  return (
    <>
    <label className="group-toggle mut" title="A player's picks win or lose together, so the board shows his best one and tucks the rest behind it">
      <input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} /> One pick per player
      {grouped && rows.length < picks.length && <> · {picks.length - rows.length} related picks tucked away</>}
    </label>
    <label className="group-toggle mut cards-toggle">
      <input type="checkbox" checked={detailed} onChange={(e) => setDetailed(e.target.checked)} /> Every number on the cards
    </label>
    <SortBar toggle={toggle} arrow={arrow} options={[["ev", "EV"], ["prob", "Win prob"], ["edge_yds", "Edge"], ["mu", "Proj"], ["name", "Name"]]} />
    <div className={`card scroll cards${detailed ? "" : " compact"}`}>
      <table className="t-picks">
        <thead>
          <tr>
            {th("name", "Player")}<th scope="col">Market</th><th scope="col">Pick</th>
            {th("mu", "Proj", "proj")}{th("edge_yds", "Edge", "edge")}
            {th("prob", "Win prob", "winProb")}{th("ev", "EV", "ev")}{th("kelly", "¼ Kelly", "kelly")}
            <th scope="col">Last 5</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => { const why = againstProjection(p); return (
            <tr key={`${p.player_id}-${p.kind}-${p.side}`} onClick={() => onSelect(p)} className="clickable"
              tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onSelect(p)}>
              <td>
                <div className="pcell">
                  <Avatar name={p.name} size={34} />
                  <div>
                    <b>{p.name}</b> <span className="pos-pill">{p.pos}</span> <span className="mut">{p.team}</span>
                    {p.status && <span className="q"> {p.status}</span>}
                    {p.flagged && <span className="flag" title={flagChip(p).title}>{flagChip(p).label}</span>}
                    {p.related.length > 0 && <span className="flag related" title={`Moves with this pick: ${p.related.map((r) => `${KIND_LABEL[r.kind]} ${playLabel(r.kind, r.side, r.line)} (${signedPct(r.ev)} EV)`).join(", ")}. Open the player for all his plays.`}>+{p.related.length} related</span>}
                    {!!p.steam && <span className={`flag ${p.steam > 0 ? "with" : "against"}`} title={`Between the last two odds fetches, three or more books moved this line ${p.steam > 0 ? "toward" : "away from"} this side and none moved the other way.`}>{p.steam > 0 ? "steam with" : "steam against"}</span>}
                    {p.teammate_out && <span className="flag" title={isNba(p.kind)
                      ? `${p.teammate_out} out: the projection already hands this player his share of their minutes and production (see "Why" in the player drawer).`
                      : `${p.teammate_out} out (first game): the model doesn't hand his targets to teammates. In past games like this, receivers averaged a few more yards than projected, from occasional big games, but beat their projection no more often than usual.`}>teammate out</span>}
                    <br /><span className="mut">{matchup(p.opp, p.home)} · {kickoff(p.gameday, p.gametime)}</span>
                    {!isYesNo(p.kind) && <WeatherChip weather={weather[p.game_id]} wx={p.wx} />}
                    <MatchupGrade kind={p.kind} pos={p.pos} opp={p.opp} rank={p.dvp_rank} n={p.dvp_n} side={p.side} quiet />
                  </div>
                </div>
              </td>
              <td className="c-market">{KIND_LABEL[p.kind]}</td>
              <td>
                <SidePill kind={p.kind} side={p.side} line={p.line} /><br /><span className="mut">{americanOdds(p.odds)} · <BookName book={p.book} size={14} /></span>
                {why && <span className="pick-why">{why}</span>}
              </td>
              <td data-label="Proj">{fmtProj(p.kind, p.mu)}{!isYesNo(p.kind) && <><br /><span className="mut">{fmtSd(p.kind, p.sd)}</span></>}
                {why && p.median != null && <><br /><span className="mut">typical {fmtProj(p.kind, p.median)}</span></>}</td>
              <td data-label="Edge">{fmtEdge(p.kind, p.edge_yds)}</td>
              <td data-label="Win prob"><b>{pct(p.prob)}</b><span className="meter" aria-hidden><i style={{ width: `${Math.min(100, p.prob * 100)}%` }} /></span>
                {p.prob < 0.5 && p.ev > 0 && <span className="wp-note">Loses more often than not, but {americanOdds(p.odds)} pays enough to be worth it</span>}
                <span className="mut wp-blend">model {pct(p.p_model, 0)} · market {pct(p.p_mkt, 0)}<br />{p.w != null && <>{Math.round((1 - p.w) * 100)}/{Math.round(p.w * 100)} blend</>} <Info term="blend" /></span>
                {p.move != null && Math.abs(p.move) >= 0.02 && <span className={`move ${p.move > 0 ? "with" : "against"}`} title={`The books' fair chance of this side at ${p.line} has moved ${signedPts(p.move)} since the week's first odds fetch${p.open_line != null && p.open_line !== p.line ? ` (the line opened at ${p.open_line})` : ""}.`}>mkt {signedPts(p.move)} since open</span>}</td>
              <td className="c-ev"><span className="ev-pill">{signedPct(p.ev)}</span></td>
              <td data-label="¼ Kelly" className="c-kelly">{pct(p.kelly)}</td>
              <td data-label="Last 5"><Spark values={p.last5} line={isYesNo(p.kind) ? undefined : p.line} /></td>
            </tr>
          ); })}
        </tbody>
      </table>
    </div>
    {limit !== undefined && rows.length > limit && (
      <button className="link go-more" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${rows.length} ${grouped ? "players" : "props"}`}</button>
    )}
    </>
  );
}
