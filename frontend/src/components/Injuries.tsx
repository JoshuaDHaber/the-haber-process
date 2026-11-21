import { IconCalendarOff } from "@tabler/icons-react";
import EmptyState from "./EmptyState";
import { useState } from "react";
import type { Game, InjuredPlayer, InjuryReport, Kind } from "../api";
import { kickoff, timeAgo } from "../format";
import { getLeague } from "../league";
import GamePills from "./GamePills";

interface Props {
  report: InjuryReport;
  games: Game[];
  onSelect: (id: string, kind: Kind) => void;
}

type Side = "all" | "off" | "def";
const SIDES: [Side, string][] = [["all", "Both sides"], ["off", "Offense"], ["def", "Defense"]];
const GROUPS: [InjuredPlayer["side"], string][] = [["off", "Offense"], ["def", "Defense"], ["st", "Special teams"]];
const RESERVE = new Set(["IR", "PUP", "NFI", "Reserve"]);

/** The badge: game status if there is one, else how he practiced ("DNP", "Limited"), or "Rest" for a veteran's day off. */
function badge(p: InjuredPlayer): { text: string; cls: string; title: string } {
  if (p.status === "Out") return { text: "Out", cls: "out", title: "Ruled out" };
  if (p.status === "Doubtful") return { text: "Doubtful", cls: "doubt", title: "Doubtful: rarely plays" };
  if (p.status === "Questionable") return { text: "Questionable", cls: "quest", title: "Questionable: a game-time decision" };
  // NBA statuses (ESPN)
  if (p.status === "Day-To-Day") return { text: "Day-to-day", cls: "quest", title: "Day-to-day: often a game-time decision" };
  if (p.status === "Probable") return { text: "Probable", cls: "rest", title: "Probable: expected to play" };
  if (p.status === "Suspension") return { text: "Suspended", cls: "out", title: "Suspended" };
  if (RESERVE.has(p.status)) return { text: p.status, cls: "out", title: p.status === "IR" ? "Injured reserve" : p.status === "PUP" ? "Physically unable to perform list" : p.status === "NFI" ? "Non-football injury list" : "Reserve list" };
  if (p.rest) return { text: "Rest", cls: "rest", title: "Not injury related: a veteran's rest day" };
  return { text: p.practice === "DNP" ? "DNP" : "Limited", cls: "banged", title: `No game status yet; ${p.practice === "DNP" ? "did not practice" : "limited in practice"}` };
}

/** "Nov 1", or "Oct 1, 2027" when it isn't this year (a season-ending injury); null when the date isn't after today
 * (ESPN dates a questionable player's return to game day, which says nothing). */
function returnText(d: string): string | null {
  const at = new Date(`${d}T12:00:00`), today = new Date();
  if (at.toDateString() === today.toDateString() || at < today) return null;
  return at.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(at.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) });
}

function summary(players: InjuredPlayer[]): string {
  const n = (f: (p: InjuredPlayer) => boolean) => players.filter(f).length;
  const parts: [number, string][] = [
    [n((p) => p.status === "Out" || p.status === "Suspension"), "out"], [n((p) => p.status === "Doubtful"), "doubtful"], [n((p) => p.status === "Questionable"), "questionable"],
    [n((p) => p.status === "Day-To-Day"), "day-to-day"], [n((p) => !p.status && !p.rest), "limited/DNP"],
  ];
  return parts.filter(([c]) => c).map(([c, l]) => `${c} ${l}`).join(" · ") || "Nobody on the report";
}

function Row({ p, onSelect }: { p: InjuredPlayer; onSelect: Props["onSelect"] }) {
  const b = badge(p);
  const practice = p.status && !RESERVE.has(p.status) && p.practice ? p.practice : "";
  const back = p.return_date && !["Day-To-Day", "Questionable", "Probable"].includes(p.status) ? returnText(p.return_date) : null;
  return (
    <li className="inj-row">
      <span className={`inj-badge ${b.cls}`} title={b.title}>{b.text}</span>
      <span className="pos-pill">{p.pos}</span>
      <span className="inj-name">
        {p.kind ? <button className="link strong" onClick={() => onSelect(p.player_id, p.kind as Kind)} title="Open his projections and props">{p.name}</button> : p.name}
        {(p.injury || practice) && <span className="mut"> {[p.rest ? "" : p.injury, practice && `${practice} in practice`].filter(Boolean).join(" · ")}</span>}
        {back && <span className="mut"> · back ~{back}</span>}
        {p.comment && <span className="inj-note mut" title={p.updated ? `ESPN, ${timeAgo(p.updated.replace("Z", ""))}` : "ESPN"}>{p.comment}</span>}
      </span>
    </li>
  );
}

function Team({ team, home, players, side, showReserve, onSelect }: {
  team: string; home: boolean; players: InjuredPlayer[]; side: Side; showReserve: boolean; onSelect: Props["onSelect"];
}) {
  const bySide = players.filter((p) => side === "all" || p.side === side);
  const report = bySide.filter((p) => !RESERVE.has(p.status));
  const reserve = bySide.filter((p) => RESERVE.has(p.status));
  const shown = showReserve ? bySide : report;
  return (
    <section className="inj-team">
      <h3><b>{team}</b> <span className="mut">{home ? "home" : "away"}</span></h3>
      <p className="mut inj-sum">{summary(report)}{!showReserve && reserve.length > 0 && ` · ${reserve.length} on IR/reserve`}</p>
      {GROUPS.filter(([g]) => side === "all" || g === side).map(([g, label]) => {
        const list = shown.filter((p) => p.side === g);
        if (!list.length) return null;
        return (
          <div key={g}>
            {side === "all" && getLeague() !== "nba" && <h4>{label}</h4>}
            <ul className="inj-list">{list.map((p) => <Row key={p.player_id} p={p} onSelect={onSelect} />)}</ul>
          </div>
        );
      })}
    </section>
  );
}

/** One game's card: both teams' injury reports side by side. */
export function GameInjuries({ game, report, side = "all", showReserve = false, onSelect }: {
  game: Game; report: InjuryReport; side?: Side; showReserve?: boolean; onSelect: Props["onSelect"];
}) {
  const [, , away, home] = game.game_id.split("_");
  return (
    <article className="card inj-card">
      <header className="ml-head"><b>{game.label}</b><span className="mut">{kickoff(game.gameday, game.gametime)}</span></header>
      <div className="inj-teams">
        <Team team={away} home={false} players={report.teams[away] ?? []} side={side} showReserve={showReserve} onSelect={onSelect} />
        <Team team={home} home players={report.teams[home] ?? []} side={side} showReserve={showReserve} onSelect={onSelect} />
      </div>
    </article>
  );
}

/** Players listed Out, Doubtful or Questionable (the reserve lists left out). */
export const gameStatusCounts = (players: InjuredPlayer[]) => ({
  out: players.filter((p) => p.status === "Out" || p.status === "Suspension").length,
  doubtful: players.filter((p) => p.status === "Doubtful").length,
  questionable: players.filter((p) => p.status === "Questionable").length,
  dayToDay: players.filter((p) => p.status === "Day-To-Day").length,   // NBA
});

export default function Injuries({ report, games, onSelect }: Props) {
  const [game, setGame] = useState("all");
  const [side, setSide] = useState<Side>("all");
  const [showReserve, setShowReserve] = useState(false);
  const shown = games.filter((g) => game === "all" || g.game_id === game);
  const stale = report.report_week != null && report.week != null && report.report_week < report.week;
  return (
    <div className="stack">
      <section className="controls" aria-label="Injury filters">
        <GamePills games={games} selected={game === "all" ? [] : [game]} onChange={(ids) => setGame(ids[0] ?? "all")} label="Game" />
        <div className="row">
          <div className="seg small">
            {SIDES.map(([k, label]) => <button key={k} className={side === k ? "on" : ""} onClick={() => setSide(k)}>{label}</button>)}
          </div>
          <label className="check"><input type="checkbox" checked={showReserve} onChange={(e) => setShowReserve(e.target.checked)} /> Show IR / PUP</label>
        </div>
        <p className="td-note">
          {report.report_week == null ? <>No injury report is available yet.</>
            : stale ? <><b>Week {report.week}'s report isn't out yet</b> (teams start reporting Wednesday). Showing week {report.report_week}'s final statuses{report.news_fetched_at ? <>, updated with ESPN's latest where it has one.</> : <>, which may have changed.</>}</>
            : <><b>Week {report.week} injury report:</b> game statuses come out Friday (Saturday for Monday games); until then, practice participation is the tell.</>}
          {report.fetched_at && <> Checked {timeAgo(report.fetched_at)} (nflverse).</>}
          {report.news_fetched_at && <> News lines and estimated returns from ESPN, checked {timeAgo(report.news_fetched_at)}.</>} Players with props link to their projections.
        </p>
      </section>
      {!shown.length ? <EmptyState icon={IconCalendarOff} title="No games on the slate">Injury reports show up here once the games are set.</EmptyState> : (
        <div className="inj-grid">
          {shown.map((g) => <GameInjuries key={g.game_id} game={g} report={report} side={side} showReserve={showReserve} onSelect={onSelect} />)}
        </div>
      )}
    </div>
  );
}
