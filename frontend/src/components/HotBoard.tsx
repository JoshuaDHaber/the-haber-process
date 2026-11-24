import { IconFlame, IconSnowflake } from "@tabler/icons-react";
import EmptyState, { focusEmpty, LoadError } from "./EmptyState";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Filters, type GameWeather, type HotPick, type HotTeam } from "../api";
import { flagChip, isYesNo, KIND_LABEL, americanOdds, fmtProj, kickoff, matchup, pct, playLabel, signedPct } from "../format";
import { getLeague } from "../league";
import { sidePick } from "../moneyline";
import { useSort } from "../useSort";
import Avatar from "./Avatar";
import { BookName } from "./BookBadge";
import HowToRead from "./HowToRead";
import Info, { type Term } from "./Info";
import MatchupGrade from "./MatchupGrade";
import { MoneylineAdd } from "./Moneylines";
import { PickAvatar } from "./TeamLogo";
import SidePill from "./SidePill";
import Skeleton from "./Skeleton";
import SortBar, { SortTh } from "./SortBar";
import Spark from "./Spark";
import type { Tile } from "./TopTiles";
import WeatherChip from "./WeatherChip";

/** Wins in a row that make a team "on a streak" (backend engine/moneyline.MIN_STREAK). */
const MIN_TEAM_STREAK = 3;

/** How far back to look: an NFL season is short, so its windows are too. */
const WINDOWS = { nfl: [3, 5, 8], nba: [5, 10, 15] } as const;
const DEFAULT_WINDOW = { nfl: 5, nba: 10 } as const;

/** "Over 6 straight", "4 of 5": a row's run in words. */
export function runText(r: HotPick) {
  if (r.streak === r.window) return `${r.window} straight`;
  return `${r.hits} of ${r.window}${r.streak >= 2 ? ` · last ${r.streak} in a row` : ""}`;
}

/** "Won 6 straight", "Won 3 straight · 4-1 in his last 5": a team's run in words. */
export function teamRunText(t: HotTeam) {
  return `Won ${t.streak} straight${t.streak < t.window ? ` · ${t.wins}-${t.window - t.wins} in the last ${t.window}` : ""}`;
}

/** A team's last games as W/L marks, oldest first, the latest on the right. */
function Results({ t }: { t: HotTeam }) {
  return (
    <span className="wl" role="img" aria-label={`Last ${t.recent.length} games, oldest first: ${t.recent.map((m) => (m > 0 ? "won" : m < 0 ? "lost" : "tied")).join(", ")}`}>
      {t.recent.map((m, i) => <i key={i} className={m > 0 ? "w" : "l"} title={`${m > 0 ? "Won" : m < 0 ? "Lost" : "Tied"} by ${Math.abs(m)}`}>{m > 0 ? "W" : m < 0 ? "L" : "T"}</i>)}
    </span>
  );
}

/** Teams on a winning streak, each with its moneyline at the board's book and a button to put it on the slip. */
function HotTeams({ teams, weather, busy }: { teams: HotTeam[]; weather: Record<string, GameWeather | null | undefined>; busy: boolean }) {
  return (
    <section className="hot-teams" aria-labelledby="hot-teams-h">
      <h3 id="hot-teams-h" className="sub-h">Teams on a win streak <span className="mut">· {teams.length}</span></h3>
      <div className={`card scroll cards${busy ? " refreshing" : ""}`} aria-busy={busy}>
        <table className="t-hot t-hot-teams">
          <thead>
            <tr>
              <th scope="col">Team</th><th scope="col">Market</th><th scope="col">Price</th>
              <th scope="col">Last {teams[0].window}</th><th scope="col">Margin</th><th scope="col">Win prob <Info term="winProb" /></th><th scope="col">EV <Info term="ev" /></th>
            </tr>
          </thead>
          <tbody>
            {teams.map((t) => {
              const g = { game_id: t.game_id, label: t.label, gameday: t.gameday, gametime: t.gametime, margin: 0, spread: null,
                sides: [t, { ...t, team: t.opp, name: t.opp }] };
              return (
                <tr key={`${t.game_id}-${t.team}`}>
                  <td>
                    <div className="pcell">
                      <PickAvatar name={t.name} kind="ml" team={t.team} gameId={t.game_id} size={34} />
                      <div>
                        <b>{t.name}</b> <span className="mut">{t.team}</span>
                        <br /><span className="mut">{matchup(t.opp, t.home)} · {kickoff(t.gameday, t.gametime)}</span>
                        <WeatherChip weather={weather[t.game_id]} />
                      </div>
                    </div>
                  </td>
                  <td className="c-market">Moneyline</td>
                  <td>{t.odds == null ? <span className="mut">no line</span> : <><b>{americanOdds(t.odds)}</b><br /><span className="mut"><BookName book={t.book!} size={14} /></span></>}</td>
                  <td data-label={`Last ${t.window}`} className="c-run"><Results t={t} /><br /><b className={t.streak >= t.window ? "run-full" : undefined}>{teamRunText(t)}</b></td>
                  <td data-label="Margin"><b>{t.avg_margin > 0 ? "+" : ""}{t.avg_margin.toFixed(1)}</b><br /><span className="mut">a game</span></td>
                  <td data-label="Win prob"><b>{pct(t.prob)}</b><span className="meter" aria-hidden><i style={{ width: `${Math.min(100, t.prob * 100)}%` }} /></span>
                    <span className="mut">model {pct(t.p_model, 0)}</span></td>
                  <td className="c-ev">{t.ev == null ? <span className="mut">–</span> : <span className={`ev-pill${t.ev < 0 ? " neg" : ""}`}>{signedPct(t.ev)}</span>}
                    <MoneylineAdd pick={sidePick(g, t)} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Who's Hot: players whose last few games cleared the line they're posted at now (or, on the Cold side, stayed under it),
 *  with the board's price beside the run. A streak isn't an edge by itself: the projections already lean on recent games,
 *  so the EV says whether the run is in the price. */
export default function HotBoard({ board, onSelect, weather = {} }: { board: ReturnType<typeof useHotBoard>; onSelect: (r: HotPick) => void; weather?: Record<string, GameWeather | null | undefined> }) {
  const { league, games, setGames, side, setSide, query: hot, teams: teamsQuery } = board;
  const rows = hot.data ?? [];
  const teams = side === "Over" ? teamsQuery.data ?? [] : [];
  // the server's order (games cleared, then streak, then margin) is the default; "rank" keeps it
  const ranked = rows.map((r, i) => ({ ...r, rank: -i }));
  const { sorted, toggle, arrow, sortOf } = useSort(ranked, "rank");
  // an empty board's way out: the next shorter stretch, if there is one
  const shorter = [...WINDOWS[league]].reverse().find((n) => n < games);
  const th = (key: string, label: string, term?: Term) => (
    <SortTh k={key} label={label} extra={term && <Info term={term} />} toggle={toggle} arrow={arrow} sortOf={sortOf} />
  );
  return (
    <>
      <div className="hot-bar">
        <div className="seg small" role="group" aria-label="Side">
          <button className={side === "Over" ? "on" : ""} aria-pressed={side === "Over"} onClick={() => setSide("Over")}>Hot: over the line</button>
          <button className={side === "Under" ? "on" : ""} aria-pressed={side === "Under"} onClick={() => setSide("Under")}>Cold: under it</button>
        </div>
        <div className="seg small" role="group" aria-label="Games">
          <span className="mut seg-label">Last</span>
          {WINDOWS[league].map((n) => <button key={n} className={games === n ? "on" : ""} aria-pressed={games === n} onClick={() => setGames(n)}>{n} games</button>)}
        </div>
      </div>
      {(rows.length > 0 || teams.length > 0) && <HowToRead storageKey="how-to-read-hot-dismissed">
        <ol>
          <li>Each row is a player whose last games {side === "Over" ? "beat" : "stayed under"} the line he's posted at now: at least 6 in 10 of them{league === "nfl" ? ", reaching into last season when this one runs short" : ""}. Bars that cleared the line are green.</li>
          <li><b>Avg</b> is those games' average, with his average in the games before them underneath.</li>
          <li>A run isn't an edge on its own: the projections already weight recent games. <b>EV</b> <Info term="ev" /> says whether the run is in the price.</li>
          {side === "Over" && <li>Teams that have won {MIN_TEAM_STREAK} or more in a row this season sit on top, with their moneyline. The price already knows they've been winning, so read the EV the same way.</li>}
        </ol>
      </HowToRead>}
      {teams.length > 0 && <HotTeams teams={teams} weather={weather} busy={teamsQuery.isPlaceholderData} />}
      {hot.isLoading ? <Skeleton kind="rows" /> : hot.error && !hot.data ? <LoadError what="who's hot" retry={hot.refetch} /> : !rows.length ? (
        <EmptyState id="no-hot" icon={side === "Over" ? IconFlame : IconSnowflake} title={teams.length > 0 ? "No players on a run yet" : `Nobody's ${side === "Over" ? "hot" : "cold"} over the last ${games} games`}
          action={shorter && <button type="button" className="chip-btn" onClick={() => { setGames(shorter); focusEmpty("no-hot"); }}>Try the last {shorter} games</button>}>
          No one on the board has {side === "Over" ? "cleared" : "stayed under"} their line in at least 60% of those games.{shorter ? " A shorter stretch or another market may turn some up." : " Try another market."}
        </EmptyState>
      ) : <>
        {/* with the teams above, the players need their own header: otherwise their cards run on from the teams' and the
            sort chips (players only) read as sorting both */}
        {teams.length > 0 && <h3 className="sub-h hot-sub">Players on a run <span className="mut">· {rows.length}</span></h3>}
        <SortBar toggle={toggle} arrow={arrow} options={[["rank", "Hottest"], ["streak", "Streak"], ["ev", "EV"], ["name", "Name"]]} />
        <div className={`card scroll cards${hot.isPlaceholderData ? " refreshing" : ""}`} aria-busy={hot.isPlaceholderData}>
          <table className="t-hot">
            <thead>
              <tr>
                {th("name", "Player")}<th scope="col">Market</th><th scope="col">Line</th>
                {th("rank", `Last ${games}`, "hitRates")}{th("avg", "Avg")}{th("prob", "Win prob", "winProb")}{th("ev", "EV", "ev")}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={`${r.player_id}-${r.kind}-${r.side}`} onClick={() => onSelect(r)} className="clickable" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onSelect(r)}>
                  <td>
                    <div className="pcell">
                      <Avatar name={r.name} size={34} />
                      <div>
                        <b>{r.name}</b> <span className="pos-pill">{r.pos}</span> <span className="mut">{r.team}</span>
                        {r.status && <span className="q"> {r.status}</span>}
                        {r.flagged && <span className="flag" title={`${flagChip(r).title}, so Best props hides this price`}>{flagChip(r).label}</span>}
                        <br /><span className="mut">{matchup(r.opp, r.home)} · {kickoff(r.gameday, r.gametime)}</span>
                        {!isYesNo(r.kind) && <WeatherChip weather={weather[r.game_id]} wx={r.wx} />}
                        <MatchupGrade kind={r.kind} pos={r.pos} opp={r.opp} rank={r.dvp_rank} n={r.dvp_n} slot={r.dvp_slot} side={r.side} quiet />
                      </div>
                    </div>
                  </td>
                  <td className="c-market">{KIND_LABEL[r.kind]}</td>
                  <td><SidePill kind={r.kind} side={r.side} line={r.line} /><br /><span className="mut">{americanOdds(r.odds)} · <BookName book={r.book} size={14} /></span></td>
                  <td data-label={`Last ${games}`} className="c-run">
                    <Spark values={r.recent} line={isYesNo(r.kind) ? undefined : r.line} side={r.side} />
                    <br /><b className={r.streak === r.window ? "run-full" : undefined}>{runText(r)}</b>
                  </td>
                  <td data-label="Avg">{isYesNo(r.kind) ? <span className="mut">—</span> : <>
                    <b>{fmtProj(r.kind, r.avg)}</b>
                    {r.prior_avg != null && <><br /><span className="mut" title={`His average in the games before these ${games}`}>{fmtProj(r.kind, r.prior_avg)} before</span></>}
                  </>}</td>
                  <td data-label="Win prob"><b>{pct(r.prob)}</b><span className="meter" aria-hidden><i style={{ width: `${Math.min(100, r.prob * 100)}%` }} /></span>
                    <span className="mut">proj {fmtProj(r.kind, r.mu)}</span></td>
                  <td className="c-ev"><span className={`ev-pill${r.ev < 0 ? " neg" : ""}`}>{signedPct(r.ev)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>}
    </>
  );
}

/** The board's state (which side, how many games) and its rows, kept by the league shell so the top tiles can read them. */
export function useHotBoard(filters: Filters, enabled: boolean) {
  const league = getLeague();
  const [games, setGames] = useState<number>(DEFAULT_WINDOW[league]);
  const [side, setSide] = useState<"Over" | "Under">("Over");
  const query = useQuery({ queryKey: ["hot", league, filters, games, side], queryFn: () => api.hot(filters, games, side), enabled, placeholderData: (p) => p });
  // teams on a winning streak: only beside the hot side, every market (a team has no market of its own to filter by), and by search
  const teamsQuery = useQuery({ queryKey: ["hot-teams", league, filters.book, filters.game, filters.date, games], queryFn: () => api.hotTeams(filters, games),
    enabled: enabled && side === "Over" && filters.kind === "all", placeholderData: (p) => p,
    select: (rows: HotTeam[]) => { const q = filters.q.trim().toLowerCase(); return q ? rows.filter((t) => `${t.name} ${t.team}`.toLowerCase().includes(q)) : rows; } });
  const teams = filters.kind === "all" ? teamsQuery : { ...teamsQuery, data: [] as HotTeam[] };
  return { league, games, setGames, side, setSide, query, teams };
}

/** The three hottest runs, as the tiles above the board. */
export const hotTiles = (rows: HotPick[], onSelect: (r: HotPick) => void): Tile[] => rows.slice(0, 3).map((r) => ({
  key: `${r.player_id}-${r.kind}-${r.side}`, label: KIND_LABEL[r.kind], name: r.name, meta: `${r.pos} ${r.team} ${matchup(r.opp, r.home)}`,
  value: <>{playLabel(r.kind, r.side, r.line)} <em>{r.hits}/{r.window}</em></>, caption: `${runText(r)} · ${signedPct(r.ev)} EV`,
  onClick: () => onSelect(r),
}));
