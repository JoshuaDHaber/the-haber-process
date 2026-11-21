import { IconCalendarOff, IconClock } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";
import { api, type MoneylineGame, type MoneylineSide } from "../api";
import { americanOdds, kickoff, matchup, pct, signedPct, slatePhrase } from "../format";
import type { League } from "../league";
import { useLeague } from "../LeagueContext";
import { sidePick } from "../moneyline";
import { checkBack, localWhen } from "../sync";
import { useSort } from "../useSort";
import { BookName } from "./BookBadge";
import EmptyState, { LoadError } from "./EmptyState";
import HowToRead from "./HowToRead";
import Info from "./Info";
import { MoneylineAdd } from "./Moneylines";
import Skeleton from "./Skeleton";
import SortBar from "./SortBar";
import { PickAvatar } from "./TeamLogo";

/** Per league: the leads an early-win promo might ask for, FanDuel's own (backend nba/lead.THRESHOLD, engine/early_win.THRESHOLD),
 *  and how far back the largest leads the chances are fitted on go. NFL leads under 14 run high (touchdowns jump the score by 7). */
const LEAD_SETUP: Record<League, { leads: number[]; fanduel: number; since: string; news: string }> = {
  nba: { leads: [5, 8, 10, 12, 15], fanduel: 10, since: "2018-19", news: "an injury or rest day" },
  nfl: { leads: [14, 17, 21], fanduel: 17, since: "2018", news: "an injury or a quarterback change" },
};
/** Model and market this far apart (in win chance) usually means news the model hasn't seen (LEAD_SETUP's news). */
const SPLIT = 0.12;

type Row = { g: MoneylineGame; s: MoneylineSide; opp: string; name: string; ev: number; p_cash: number; p_lead: number };

/** "Up 10 in 6 of 14", "3 lost": a team's season against the token's lead, on two lines like the price and its book. */
function SeasonRecord({ s }: { s: MoneylineSide }) {
  const t = s.token!;
  if (!t.games) return <>No games yet</>;
  return <>Up {t.threshold} in <b>{t.up}</b> of {t.games}{t.up > 0 && <><br />{t.up_lost} of them lost</>}</>;
}

/** Every side on the slate priced for an early-win token: the chance it goes up by the lead, the chance the bet cashes (up by
 *  the lead or a win) and that bet's EV at the board's book, best first. */
export default function TokenBoard({ book, marketWeight, gameIds, pills, nextUpdate }: { book: string; marketWeight: number | null; gameIds: string[]; pills: ReactNode; nextUpdate?: string | null }) {
  const { league } = useLeague();
  const { leads, fanduel, since, news } = LEAD_SETUP[league];
  const [lead, setLead] = useState<number>(fanduel);
  const q = useQuery({ queryKey: ["moneylines", book, marketWeight, lead], queryFn: () => api.moneylines(book, marketWeight, lead), placeholderData: (p) => p });
  const slate = useMemo(() => (q.data ?? []).filter((g) => gameIds.includes(g.game_id)), [q.data, gameIds]);
  // games with no spread yet, so no lead chances: the whole board when it's empty, or a note under it when it isn't
  const unpriced = useMemo(() => slate.filter((g) => !g.sides.some((s) => s.token)), [slate]);
  const rows = useMemo<Row[]>(() => slate.flatMap((g) => g.sides
    .filter((s) => s.token)
    .map((s) => ({ g, s, opp: g.sides.find((x) => x.team !== s.team)?.team ?? "", name: s.name,
      ev: s.token!.ev ?? -Infinity, p_cash: s.token!.p_cash, p_lead: s.token!.p_lead }))), [slate]);
  const { sorted, toggle, arrow } = useSort(rows, "ev");
  // the lead picker and primer explain columns, so they come with the rows: an empty board leads with its message, and a
  // cold load doesn't show them only to take them away
  const failed = !!q.error && !q.data;
  const empty = !q.isLoading && !failed && !sorted.length;
  const hasRows = sorted.length > 0;
  return (
    <>
      <div className="hot-bar">
        {pills}
        {hasRows && <div className="seg small" role="group" aria-label="Lead the token needs">
          <span className="mut seg-label">Pays once up by</span>
          {leads.map((n) => <button key={n} className={lead === n ? "on" : ""} aria-pressed={lead === n} onClick={() => setLead(n)}>{n}</button>)}
        </div>}
      </div>
      {hasRows && <HowToRead storageKey="how-to-read-token-dismissed">
        <ol>
          <li>An early-win token pays a moneyline as a winner once the team goes up by {lead}, whatever happens after. So the bet cashes if the team goes up {lead} <b>or</b> wins.</li>
          <li><b>Up {lead}</b> is the chance the team leads by {lead}+ at some point; <b>Cashes</b> adds the games it wins without ever getting there. Both come from the market spread, fitted on every game's largest lead since {since}.</li>
          <li><b>Token EV</b> <Info term="ev" /> is the bet's return with the token at the price shown. It's highest on underdogs: a favorite that goes up {lead} usually wins anyway, so the token adds little.</li>
          <li><b>Model ≠ market</b> marks a game where the team model and the books disagree by {Math.round(SPLIT * 100)}+ points, usually {news}. Check the news before trusting either.</li>
        </ol>
      </HowToRead>}
      {q.isLoading ? <Skeleton kind="rows" /> : failed ? <LoadError what="the early-win board" retry={q.refetch} /> : empty ? (
        unpriced.length ? (
          <EmptyState icon={IconClock} title={`${slatePhrase(unpriced.map((g) => g.gameday))} ${unpriced.length === 1 ? "isn't" : "aren't"} priced yet`}
            preview={unpriced} columns={["Spread", `Up ${lead}`, "Token EV"]} league={league}>
            Lead chances need a spread, and the books haven't posted one yet. {checkBack(nextUpdate)}
          </EmptyState>
        ) : (
          <EmptyState icon={IconCalendarOff} title="No games on the slate yet">They appear after the next scheduled update{nextUpdate ? `, ${localWhen(nextUpdate)}` : ""}.</EmptyState>
        )
      ) : <>
        <SortBar toggle={toggle} arrow={arrow} options={[["ev", "Token EV"], ["p_cash", "Cashes"], ["p_lead", `Up ${lead}`], ["name", "Team"]]} />
        <div className={`card scroll cards${q.isPlaceholderData ? " refreshing" : ""}`} aria-busy={q.isPlaceholderData}>
          <table className="t-hot t-hot-teams">
            <thead>
              <tr>
                <th scope="col">Team</th><th scope="col">This season</th><th scope="col">Price</th>
                <th scope="col">Up {lead}</th><th scope="col">Win prob <Info term="winProb" /></th><th scope="col">Cashes</th><th scope="col">Token EV <Info term="ev" /></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(({ g, s, opp }) => {
                const t = s.token!;
                const split = s.p_mkt != null && Math.abs(s.p_model - s.p_mkt) >= SPLIT;
                return (
                  <tr key={`${g.game_id}-${s.team}`}>
                    <td>
                      <div className="pcell">
                        <PickAvatar name={s.name} kind="ml" team={s.team} gameId={g.game_id} size={34} />
                        <div>
                          <b>{s.name}</b> <span className="mut">{s.team}</span>
                          {split && <span className="flag" title={`The team model gives ${s.team} ${pct(s.p_model, 0)} to win, the books ${pct(s.p_mkt!, 0)}: check the injury report`}>Model ≠ market</span>}
                          <br /><span className="mut">{matchup(opp, s.home)} · {kickoff(g.gameday, g.gametime)}</span>
                        </div>
                      </div>
                    </td>
                    <td data-label="This season" className="mut" title="Games this season, how many the team led by this much at some point, and how many of those it still lost"><SeasonRecord s={s} /></td>
                    <td>{s.odds == null ? <span className="mut">no line</span> : <><b>{americanOdds(s.odds)}</b><br /><span className="mut"><BookName book={s.book!} size={14} /></span></>}</td>
                    <td data-label={`Up ${lead}`}><b>{pct(t.p_lead, 0)}</b><span className="meter" aria-hidden><i style={{ width: `${Math.min(100, t.p_lead * 100)}%` }} /></span></td>
                    <td data-label="Win prob">{pct(s.prob, 0)}</td>
                    <td data-label="Cashes"><b>{pct(t.p_cash, 0)}</b></td>
                    <td className="c-ev">{t.ev == null ? <span className="mut">–</span> : <span className={`ev-pill${t.ev < 0 ? " neg" : ""}`}>{signedPct(t.ev)}</span>}
                      <MoneylineAdd pick={sidePick(g, s)} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {unpriced.length > 0 && <p className="mut board-note">Not priced yet: {unpriced.map((g) => g.label).join(", ")}. They join the board once the books post a spread.</p>}
      </>}
    </>
  );
}
