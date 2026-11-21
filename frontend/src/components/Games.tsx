import { IconCalendarOff } from "@tabler/icons-react";
import type { ReactNode } from "react";
import type { Game, InjuryReport, MoneylineGame, Pick } from "../api";
import { KIND_LABEL, americanOdds, kickoff, pct, signedPct, weatherShort, weatherText, notableWeather } from "../format";
import { gameStatusCounts } from "./Injuries";
import { favored } from "./Moneylines";
import { blowoutAt } from "./Blowout";
import TeamLogo from "./TeamLogo";
import { getLeague } from "../league";
import { matchupColors, shadeVars } from "../teams";
import { BookName } from "./BookBadge";
import { FavStar, InjuryIcon, WeatherIcon } from "./icons";
import EmptyState from "./EmptyState";
import { localWhen } from "../sync";
import { favoritesFirst, useFavorites } from "../favorites";
import SidePill from "./SidePill";
import { routeClick } from "./NavLink";

interface Props {
  games: Game[];
  /** Next week's schedule, previewed under a slate that's down to its last game or two. */
  nextGames?: Game[];
  /** Teams on bye that week, shown under its games. */
  nextByes?: { team: string; record?: string | null }[];
  /** Positive-EV plays across the slate, any order. */
  picks: Pick[];
  moneylines: MoneylineGame[];
  injuries?: InjuryReport;
  onOpen: (gameId: string) => void;
  /** When the next scheduled sync runs, for an empty slate to say when to check back. */
  nextUpdate?: string | null;
}

/** "Dome", "19 mph" (with a wind icon), "72°F": the one weather fact worth a glance on a tile. */
function weatherBit(g: Game): ReactNode {
  const w = g.weather;
  if (!w) return null;
  if (w.indoor) return weatherText(w);
  if (notableWeather(w)) { const { icon, text } = weatherShort(w); return <span className="with-icon"><WeatherIcon name={icon} />{text}</span>; }
  return w.temp != null ? `${Math.round(w.temp)}°F` : null;
}

function Tile({ g, picks, ml, injuries, onOpen, favs }: { g: Game; picks: Pick[]; ml?: MoneylineGame; injuries?: InjuryReport; onOpen: Props["onOpen"]; favs: string[] }) {
  const [, , away, home] = g.game_id.split("_");
  const top = picks[0];
  const inj = injuries ? gameStatusCounts([...(injuries.teams[away] ?? []), ...(injuries.teams[home] ?? [])]) : null;
  const injText = inj && [inj.out && `${inj.out} out`, inj.doubtful && `${inj.doubtful} doubtful`, inj.questionable && `${inj.questionable} questionable`, inj.dayToDay && `${inj.dayToDay} day-to-day`].filter(Boolean).join(" · ");
  const [a, h] = ml?.sides ?? [];
  const wx = weatherBit(g);
  const blow = blowoutAt(ml);
  const [awayColor, homeColor] = matchupColors(away, home);
  const fav = (team: string) => favs.includes(team) && <span className="gt-fav" title="Your team" aria-label="Your team"><FavStar on size={14} /></span>;
  const rec = (team: string, at: "home" | "road") => {
    const r = g.records?.[team];
    return r && <span className="gt-rec" title={`${r.overall} this season${r[at] ? ` · ${r[at]} ${at === "home" ? "at home" : "on the road"}` : ""}`}>{r.overall}</span>;
  };
  return (
    <a className="gt" href={`#game=${encodeURIComponent(g.game_id)}`} onClick={routeClick(() => onOpen(g.game_id))} aria-label={`Open ${g.label}`}>
      <span className="gt-top"><span>{kickoff(g.gameday, g.gametime)}</span>{wx && <span>{wx}</span>}</span>
      <span className="gt-match">
        <span className="gt-team"><TeamLogo team={away} /><span><span className="gt-abbr">{fav(away)}<b>{away}</b>{rec(away, "road")}</span>{a && <small>{pct(a.prob, 0)}</small>}</span></span>
        <span className="gt-at">@</span>
        <span className="gt-team home"><span><span className="gt-abbr">{rec(home, "home")}<b>{home}</b>{fav(home)}</span>{h && <small>{pct(h.prob, 0)}</small>}</span><TeamLogo team={home} /></span>
      </span>
      {a && h
        ? <span className="ml-bar gt-bar" aria-hidden><i className="tbar" style={{ width: `${a.prob * 100}%`, ...shadeVars(awayColor) }} /><i className="tbar" style={{ width: `${h.prob * 100}%`, ...shadeVars(homeColor) }} /></span>
        : <span className="gt-bar gt-bar-empty" aria-hidden />}
      <span className="gt-line mut">{ml ? (ml.spread != null ? `Spread: ${favored(ml, ml.spread)}` : `Model: ${favored(ml, ml.margin)}`) : "No moneyline yet"}{ml?.total != null && ` · Total ${ml.total}`}</span>
      {blow && <span className={`gt-line gt-blow mut${blow.p >= 0.3 ? " hot" : ""}`} title="Chance the game is decided by 20 or more points, either way">20+ pt blowout: <b>{pct(blow.p, 0)}</b></span>}
      <span className="gt-prop">
        <span className="tile-label">Top prop</span>
        {top ? <>
          <span className="gt-prop-main"><b>{top.name}</b> <SidePill kind={top.kind} side={top.side} line={top.line} /></span>
          <span className="mut">{KIND_LABEL[top.kind]} · {americanOdds(top.odds)} <BookName book={top.book} size={14} /> · <span className="ev-text">{signedPct(top.ev)} EV</span></span>
        </> : <span className="mut">No +EV props yet</span>}
      </span>
      <span className="gt-foot">
        <span>{picks.length ? `${picks.length} +EV prop${picks.length === 1 ? "" : "s"}` : ""}</span>
        <span>{inj == null ? "" : injText ? <span className="with-icon"><InjuryIcon size={14} stroke={1.75} aria-hidden />{injText}</span> : getLeague() === "nba" ? "No injuries listed" : "No game statuses yet"}</span>
      </span>
      <span className="gt-go">Game overview →</span>
    </a>
  );
}

/** A slate this small is the tail of the week (Monday night): next week's schedule fills the page under it. */
const PREVIEW_UNDER = 3;

/** Next week's games, kickoff and matchup only: no projections or lines until the week's first sync. */
function NextWeek({ games, byes = [] }: { games: Game[]; byes?: Props["nextByes"] }) {
  const { teams: favs } = useFavorites();
  const days = new Map<string, Game[]>();
  for (const g of games) days.set(g.gameday, [...(days.get(g.gameday) ?? []), g]);
  const dayName = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  return (
    <section className="next-week" aria-label="Next week's games">
      <h2 className="parlay-h">Next week <small>projections and props arrive with the first sync after this week's games</small></h2>
      <div className="card next-week-list">
        {[...days].map(([d, gs]) => (
          <div key={d} className="nw-day">
            <h3>{dayName(d)}</h3>
            <ul>
              {gs.map((g) => {
                const [, , away, home] = g.game_id.split("_");
                return (
                  <li key={g.game_id}>
                    <span className="nw-time mut">{g.gametime ? kickoff(g.gameday, g.gametime).replace(/^\S+\s/, "") : ""}</span>
                    <span className="nw-team"><TeamLogo team={away} size={22} /><b>{away}</b>{favs.includes(away) && <span className="gt-fav" title="Your team"><FavStar on size={12} /></span>}<small className="mut">{g.records?.[away]?.overall}</small></span>
                    <span className="mut">@</span>
                    <span className="nw-team"><TeamLogo team={home} size={22} /><b>{home}</b>{favs.includes(home) && <span className="gt-fav" title="Your team"><FavStar on size={12} /></span>}<small className="mut">{g.records?.[home]?.overall}</small></span>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
        {byes.length > 0 && (
          <div className="nw-day nw-byes">
            <h3>Bye</h3>
            <ul>
              {byes.map((b) => (
                <li key={b.team}><span className="nw-team"><TeamLogo team={b.team} size={22} /><b>{b.team}</b>{b.record && <small className="mut">{b.record}</small>}</span></li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

/** The slate: one tile per game with its win chances, top prop and injury count. A tile opens that game's overview. */
export default function Games({ games, nextGames = [], nextByes = [], picks, moneylines, injuries, onOpen, nextUpdate }: Props) {
  const { teams: favs } = useFavorites();
  if (!games.length) return <>
    <EmptyState icon={IconCalendarOff} title="No games on the slate yet">They appear after the next scheduled update{nextUpdate ? `, ${localWhen(nextUpdate)}` : ""}.</EmptyState>
    {nextGames.length > 0 && <NextWeek games={nextGames} byes={nextByes} />}
  </>;
  const nba = getLeague() === "nba";
  const byGame = new Map<string, Pick[]>();
  for (const p of [...picks].filter((p) => !p.flagged).sort((x, y) => y.ev - x.ev)) byGame.set(p.game_id, [...(byGame.get(p.game_id) ?? []), p]);
  const mlByGame = new Map(moneylines.map((m) => [m.game_id, m]));
  return (
    <>
    <section className="gt-grid" aria-label={nba ? "Upcoming games" : "This week's games"}>
      {favoritesFirst(games, favs).map((g) => <Tile key={g.game_id} g={g} picks={byGame.get(g.game_id) ?? []} ml={mlByGame.get(g.game_id)} injuries={injuries} onOpen={onOpen} favs={favs} />)}
    </section>
    {games.length < PREVIEW_UNDER && nextGames.length > 0 && <NextWeek games={nextGames} byes={nextByes} />}
    </>
  );
}
