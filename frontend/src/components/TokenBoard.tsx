import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";
import { api, type MoneylineGame, type MoneylineSide } from "../api";
import { americanOdds, kickoff, matchup, pct, signedPct } from "../format";
import type { League } from "../league";
import { useLeague } from "../LeagueContext";
import { sidePick } from "../moneyline";
import { useSort } from "../useSort";
import { BookName } from "./BookBadge";
import HowToRead from "./HowToRead";
import Info from "./Info";
import { MoneylineAdd } from "./Moneylines";
import Skeleton from "./Skeleton";
import SortBar from "./SortBar";
import { PickAvatar } from "./TeamLogo";

/** Per league: the leads an early-win promo might ask for, FanDuel's own (backend nba/lead.THRESHOLD, engine/early_win.THRESHOLD),
 *  and how far back the largest leads the chances are fitted on go. NFL leads under 14 run high (touchdowns jump the score by 7). */
const LEAD_SETUP: Record<League, { leads: number[]; fanduel: number; since: string }> = {
  nba: { leads: [5, 8, 10, 12, 15], fanduel: 10, since: "2018-19" },
  nfl: { leads: [14, 17, 21], fanduel: 17, since: "2018" },
};
/** Model and market this far apart (in win chance) usually means news the model hasn't seen: an injury or a rest day. */
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
export default function TokenBoard({ book, marketWeight, gameIds, pills }: { book: string; marketWeight: number | null; gameIds: string[]; pills: ReactNode }) {
  const { league } = useLeague();
  const { leads, fanduel, since } = LEAD_SETUP[league];
  const [lead, setLead] = useState<number>(fanduel);
  const q = useQuery({ queryKey: ["moneylines", book, marketWeight, lead], queryFn: () => api.moneylines(book, marketWeight, lead), placeholderData: (p) => p });
  const rows = useMemo<Row[]>(() => (q.data ?? []).filter((g) => gameIds.includes(g.game_id)).flatMap((g) => g.sides
    .filter((s) => s.token)
    .map((s) => ({ g, s, opp: g.sides.find((x) => x.team !== s.team)?.team ?? "", name: s.name,
      ev: s.token!.ev ?? -Infinity, p_cash: s.token!.p_cash, p_lead: s.token!.p_lead }))), [q.data, gameIds]);
  const { sorted, toggle, arrow } = useSort(rows, "ev");
  return (
    <>
      <div className="hot-bar">
        {pills}
        <div className="seg small" role="group" aria-label="Lead the token needs">
          <span className="mut seg-label">Pays once up by</span>
          {leads.map((n) => <button key={n} className={lead === n ? "on" : ""} aria-pressed={lead === n} onClick={() => setLead(n)}>{n}</button>)}
        </div>
      </div>
      <HowToRead storageKey="how-to-read-token-dismissed">
        <ol>
          <li>An early-win token pays a moneyline as a winner once the team goes up by {lead}, whatever happens after. So the bet cashes if the team goes up {lead} <b>or</b> wins.</li>
          <li><b>Up {lead}</b> is the chance the team leads by {lead}+ at some point; <b>Cashes</b> adds the games it wins without ever getting there. Both come from the market spread, fitted on every game's largest lead since {since}.</li>
          <li><b>Token EV</b> <Info term="ev" /> is the bet's return with the token at the price shown. It's highest on underdogs: a favorite that goes up {lead} usually wins anyway, so the token adds little.</li>
          <li><b>Model ≠ market</b> marks a game where the team model and the books disagree by {Math.round(SPLIT * 100)}+ points, usually an injury or rest day. Check the news before trusting either.</li>
        </ol>
      </HowToRead>
      {q.isLoading ? <Skeleton kind="rows" /> : q.error ? <p className="err">Couldn't load the moneylines.</p> : !sorted.length ? (
        <div className="card empty">No games to price yet. Lead chances come with each sync once a game has a spread.</div>
      ) : <>
        <SortBar toggle={toggle} arrow={arrow} options={[["ev", "Token EV"], ["p_cash", "Cashes"], ["p_lead", `Up ${lead}`], ["name", "Team"]]} />
        <div className="card scroll cards">
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
      </>}
    </>
  );
}
