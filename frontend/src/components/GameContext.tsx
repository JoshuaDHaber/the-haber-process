import { useQuery } from "@tanstack/react-query";
import { api, type GameWeather, type Kind, type MoneylineGame, type Pick, type ShareRow, type TeamContext } from "../api";
import { gameRundown } from "../read";
import { pct } from "../format";
import type { League } from "../league";
import EmptyState, { LoadError } from "./EmptyState";
import Info from "./Info";
import Skeleton from "./Skeleton";
import TeamLogo from "./TeamLogo";

const share = (x: number) => `${Math.round(x * 100)}%`;
const arrow = (t: number, now: number, before: number | null) => t === 0 ? null : (
  <span className={`ctx-trend ${t > 0 ? "up" : "down"}`} title={`${share(now)} in his recent games vs ${share(before ?? 0)} before`}
    aria-label={t > 0 ? "up from earlier this season" : "down from earlier this season"}>{t > 0 ? "▲" : "▼"}</span>
);

/** "BUF 27.3 · KC 20.8": implied points from the spread and total, favorite first. */
export function impliedLine(teams: TeamContext[]): string | null {
  const t = teams.filter((x) => x.implied != null);
  if (t.length < 2) return null;
  return [...t].sort((a, b) => b.implied! - a.implied!).map((x) => `${x.team} ${x.implied!.toFixed(1)}`).join(" · ");
}

function ShareTable({ rows, team, onSelect }: { rows: ShareRow[]; team: string; onSelect: (id: string, kind: Kind) => void }) {
  if (!rows.length) return <p className="mut board-note">No targets or carries logged for {team} this season yet.</p>;
  const games = rows[0].games;
  return (
    <div className="ctx-table">
      <table>
        <caption className="sr-only">{team}: share of targets and carries in the games each played of the team's last {games}, inside the 20 this season, and touchdown chances</caption>
        <thead><tr><th scope="col">Player</th><th scope="col">Targets</th><th scope="col">Carries</th><th scope="col" title="Share of the team's targets and carries inside the 20 this season">Red zone</th><th scope="col">2+ TDs</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.player_id}>
              <td><button type="button" className="link strong" onClick={() => onSelect(r.player_id, r.tgt_l5 >= r.car_l5 ? "rec" : "rush")}>{r.name}</button>
                <small className="mut ctx-meta">{r.pos}{r.played < r.games ? ` · ${r.played} of last ${r.games}` : ""}</small></td>
              <td>{r.tgt_l5 >= 0.005 ? <>{share(r.tgt_l5)}{arrow(r.tgt_trend, r.tgt_l5, r.tgt_prior)}</> : <span className="mut">–</span>}</td>
              <td>{r.car_l5 >= 0.005 ? <>{share(r.car_l5)}{arrow(r.car_trend, r.car_l5, r.car_prior)}</> : <span className="mut">–</span>}</td>
              <td>{r.rz_share != null ? <>{share(r.rz_share)}<small className="mut ctx-meta">{r.rz} of {r.rz_team}</small></> : <span className="mut">–</span>}</td>
              <td>{r.p_td2 != null ? <>{pct(r.p_td2, 0)}<small className="mut ctx-meta">any {pct(r.p_td!, 0)}</small></> : <span className="mut">–</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The game page's "Game script and roles": each team's implied points and what the spread and total mean for it, and
 *  (NFL) who gets the ball: target and carry shares over the team's last 5 games with ▲/▼ against the rest of the
 *  season, share of the touches inside the 20, and the chance of 2+ touchdowns. Numbers the app already has, as context. */
export default function GameContext({ gameId, league, onSelect, ml, picks, weather }: { gameId: string; league: League; onSelect: (id: string, kind: Kind) => void;
  /** For the rundown: the game's win chances, its priced plays and its forecast, as the page already loaded them. */
  ml?: MoneylineGame | null; picks?: Pick[] | null; weather?: GameWeather | null }) {
  const q = useQuery({ queryKey: ["game-context", league, gameId], queryFn: () => api.gameContext(gameId) });
  const head = (
    <h2 className="parlay-h">Game script{league === "nfl" ? " and roles" : ""} <small>from the spread and total{league === "nfl" ? ", and each team's last 5 games" : ""}</small> <Info term="gameScript" /></h2>
  );
  const rundown = (sentences: string[]) => sentences.length > 0 && (
    <div className="card ctx-rundown" aria-live="polite"><h3>The rundown</h3><p>{sentences.join(" ")}</p></div>
  );
  if (q.isPending) return <section id="go-ctx" className="go-sec">{head}<Skeleton kind="card" label="Loading the game script…" /></section>;
  if (q.isError && !q.data) return (
    <section id="go-ctx" className="go-sec">{head}{rundown(gameRundown({ league, ml, picks, weather }))}<LoadError compact what="the game script" retry={q.refetch} /></section>
  );
  const teams = q.data.teams;
  const said = gameRundown({ league, ml, ctx: q.data, picks, weather });
  const line = impliedLine(teams);
  if (!line && !teams.some((t) => t.players.length)) return (
    <section id="go-ctx" className="go-sec">{head}{rundown(said)}
      <EmptyState compact title="No spread or total for this game yet">Implied points and the script read show once the books post a line.</EmptyState>
    </section>
  );
  return (
    <section id="go-ctx" className={`go-sec${q.isRefetching ? " refreshing" : ""}`} aria-busy={q.isRefetching}>
      {head}
      {rundown(said)}
      {!line && <p className="mut board-note">No spread or total yet, so no implied points: they show once the books post a line.</p>}
      {league === "nba" ? (
        <div className="card ctx-team">
          <header className="ctx-h ctx-both">
            {teams.map((t) => (
              <span key={t.team} className="ctx-side"><TeamLogo team={t.team} league={league} size={28} /><b>{t.team}</b>
                {t.implied != null && <span className="ctx-pts"><b>{t.implied.toFixed(1)}</b> <span className="mut">implied</span></span>}</span>
            ))}
          </header>
          {teams[0].script.length > 0 && <ul className="ctx-script">{teams[0].script.map((s) => <li key={s}>{s}</li>)}</ul>}
        </div>
      ) : <div className="ctx-grid">
        {teams.map((t) => (
          <div key={t.team} className="card ctx-team">
            <header className="ctx-h">
              <TeamLogo team={t.team} league={league} size={28} />
              <b>{t.team}</b>
              {t.implied != null && <span className="ctx-pts"><b>{t.implied.toFixed(1)}</b> <span className="mut">implied pts</span></span>}
            </header>
            {t.script.length > 0 && <ul className="ctx-script">{t.script.map((s) => <li key={s}>{s}</li>)}</ul>}
            {league === "nfl" && <ShareTable rows={t.players} team={t.team} onSelect={onSelect} />}
          </div>
        ))}
      </div>}
      {league === "nfl" && teams.some((t) => t.players.length) && (
        <p className="mut board-note">Shares count the games each player played among his team's last 5 (▲/▼: 3+ points above or below his earlier games this season).
          In the 20: his share of the team's targets and carries inside the opponent's 20 this season. 2+ TDs: from his anytime-TD chance, as a Poisson count.</p>
      )}
    </section>
  );
}
