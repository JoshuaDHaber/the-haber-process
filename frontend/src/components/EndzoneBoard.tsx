import { IconCalendarOff, IconClock } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api, type EndzoneRow, type Game, type Pick } from "../api";
import { americanOdds, kickoff, pct, signedPct } from "../format";
import { chance, fair, meterWidth } from "../promo";
import { useSort } from "../useSort";
import { BookName } from "./BookBadge";
import HowToRead from "./HowToRead";
import Info from "./Info";
import Menu, { MenuItem } from "./Menu";
import { localWhen } from "../sync";
import EmptyState, { LoadError } from "./EmptyState";
import Skeleton from "./Skeleton";
import SortBar from "./SortBar";
import { PickAvatar } from "./TeamLogo";

type Select = (r: EndzoneRow) => void;

/** One entry: who (a player, the rest of an offense, or a defense), his anytime TD at the book, how long his touchdowns run, how
 *  often a touchdown of his is the game's longest, and his chance of the longest TD. A player's row opens his anytime-TD drawer. */
function Row({ r, gameId, onSelect, top }: { r: EndzoneRow; gameId: string; onSelect: Select; top: number }) {
  const p = r.pick;
  const player = r.kind === "player";
  const open = player ? () => onSelect(r) : undefined;
  return (
    <tr onClick={open} className={player ? "clickable" : "no-tap"} tabIndex={player ? 0 : undefined} onKeyDown={player ? (e) => e.key === "Enter" && onSelect(r) : undefined}>
      <td>
        <div className="pcell">
          <PickAvatar name={r.name} kind={player ? "td" : "ml"} team={r.team} gameId={gameId} size={34} />
          <div>
            <b>{r.name}</b> {player && <><span className="pos-pill">{r.pos}</span> <span className="mut">{r.team}</span></>}
            {r.status && <span className="q"> {r.status}</span>}
            <br /><span className="mut">{player ? (r.n_tds ? `${r.n_tds} TD${r.n_tds === 1 ? "" : "s"} in three seasons` : "No recent TDs: his position's lengths")
              : r.kind === "other" ? "Everyone not listed, at league-wide lengths" : "Defensive and return TDs"}</span>
          </div>
        </div>
      </td>
      <td data-label="Anytime TD"><b>{pct(r.p_td, 0)}</b>
        {p && <><br /><span className="mut">{americanOdds(p.odds)} · <BookName book={p.book} size={14} /></span></>}</td>
      <td data-label="Median TD">{r.median_td == null ? <span className="mut">–</span> : <>{Math.round(r.median_td)} yds</>}</td>
      <td data-label="If he scores">{pct(r.p_long_if_td, 0)}</td>
      <td data-label="Longest TD"><b>{chance(r.p_long)}</b><span className="meter" aria-hidden><i style={{ width: meterWidth(r.p_long, top) }} /></span>
        <span className="mut">fair {fair(r.p_long)}</span></td>
      <td className={`c-ev${p ? "" : " no-price"}`}>{p ? <span className={`ev-pill${p.ev < 0 ? " neg" : ""}`} title="EV of his anytime TD at this price">{signedPct(p.ev)}</span> : <span className="mut">–</span>}</td>
    </tr>
  );
}

/** King of the Endzone: who's most likely to score one game's longest touchdown, with the game picker beside the promo pills. */
export default function EndzoneBoard({ games, gameId, onGame, book, pills, onSelect, nextUpdate }: {
  games: Game[]; gameId: string | null; onGame: (id: string) => void; book: string; pills: ReactNode; onSelect: (pid: string, pick?: Pick | null) => void; nextUpdate?: string | null;
}) {
  const q = useQuery({ queryKey: ["endzone", gameId, book], queryFn: () => api.endzone(gameId ?? undefined, book), placeholderData: (p) => p });
  const b = q.data;
  const rows = b?.rows ?? [];
  const { sorted, toggle, arrow } = useSort(rows, "p_long");
  // the board's game, or (before it loads, or if it failed) the one asked for
  const shown = games.find((g) => g.game_id === (b?.game_id ?? gameId));
  const select: Select = (r) => r.player_id && onSelect(r.player_id, r.pick);
  const top = Math.max(0, ...rows.map((r) => r.p_long));
  return (
    <>
      <div className="hot-bar">
        {pills}
        {games.length > 0 && (
          <Menu align="start" ariaLabel={`Game: ${shown?.label ?? "next game"}`} label={shown?.label ?? "Next game"}>
            {(close) => games.map((g) => (
              <MenuItem key={g.game_id} on={shown?.game_id === g.game_id} onClick={() => { close(); onGame(g.game_id); }}>
                {g.label} <span className="mut">{kickoff(g.gameday, g.gametime)}</span>
              </MenuItem>
            ))}
          </Menu>
        )}
      </div>
      {rows.length > 0 && <HowToRead storageKey="how-to-read-endzone-dismissed">
        <ol>
          <li>King of the Endzone pays when your player scores the game's longest touchdown. Ties are usually paid as a dead heat, so they count here as a share.</li>
          <li><b>Longest TD</b> is that chance across {(b?.sims ?? 100000).toLocaleString()} simulated games. Each player scores from his anytime-TD chance, and each of his TDs is as long as one of his own from the last three seasons, pulled toward his position's. <b>Fair</b> is the price where the chance breaks even.</li>
          <li><b>If he scores</b> is how often his TD is the longest when he gets one. Deep threats rank high here, goal-line backs low, so a big anytime chance alone doesn't win this.</li>
          <li><b>Rest of offense</b> covers everyone not listed, and <b>defense & returns</b> covers pick-sixes and return TDs, which are nearly always long. Team TDs come from the game total and spread.</li>
        </ol>
      </HowToRead>}
      {q.isLoading ? <Skeleton kind="rows" /> : q.error && !b ? <LoadError what="King of the Endzone" retry={q.refetch} /> : !rows.length ? (
        games.length ? (
          <EmptyState icon={IconClock} title={`No touchdown chances for ${shown?.label ?? "this game"} yet`}>
            They come once the week's projections are in. Check back after the next update{nextUpdate ? `, ${localWhen(nextUpdate)}` : ""}{games.length > 1 ? ", or pick another game above" : ""}.
          </EmptyState>
        ) : <EmptyState icon={IconCalendarOff} title="No games on the slate yet">They appear after the next scheduled update{nextUpdate ? `, ${localWhen(nextUpdate)}` : ""}.</EmptyState>
      ) : <>
        <SortBar toggle={toggle} arrow={arrow} options={[["p_long", "Longest TD"], ["p_long_if_td", "If he scores"], ["p_td", "Anytime TD"], ["name", "Name"]]} />
        <section className="hot-teams" aria-labelledby="endzone-h">
          <h3 id="endzone-h" className="sub-h">{b!.label} <span className="mut">· {kickoff(b!.gameday, b!.gametime)}
            {b!.median_longest != null && <> · longest TD usually {Math.round(b!.median_longest)} yds</>}
            {b!.total != null && <> · total {b!.total_assumed ? <span title="No total posted for this game yet: the league's average game">{Math.round(b!.total)} (league average)</span> : b!.total}</>}</span></h3>
          <div className={`card scroll cards${q.isPlaceholderData ? " refreshing" : ""}`} aria-busy={q.isPlaceholderData}>
            <table className="t-hot t-endzone">
              <thead>
                <tr>
                  <th scope="col">Player</th><th scope="col">Anytime TD</th><th scope="col">Median TD</th>
                  <th scope="col">If he scores</th><th scope="col">Longest TD</th><th scope="col">TD EV <Info term="ev" /></th>
                </tr>
              </thead>
              <tbody>{sorted.map((r) => <Row key={`${r.kind}-${r.player_id ?? r.team}`} r={r} gameId={b!.game_id!} onSelect={select} top={top} />)}</tbody>
            </table>
          </div>
        </section>
      </>}
    </>
  );
}
