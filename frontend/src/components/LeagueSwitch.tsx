import { useLeague } from "../LeagueContext";
import type { League } from "../league";
import { prefetchLeague } from "../slateQueries";
import { LeagueIcon } from "./icons";
import Menu, { MenuItem } from "./Menu";

const OPTIONS: [League, string][] = [["nfl", "NFL"], ["nba", "NBA"]];

/** The switch between the NFL and NBA sides of the app: a small menu in the top bar. */
export default function LeagueSwitch() {
  const { league, setLeague } = useLeague();
  const [, label] = OPTIONS.find(([l]) => l === league)!;
  // reaching for the switch: make sure the other league is ready (the page already fetched it once idle; this catches
  // a switch made before that, or after its data went stale)
  const warm = () => prefetchLeague(league === "nba" ? "nfl" : "nba");
  return (
    <span className="league-switch" onPointerEnter={warm} onFocus={warm}>
      <Menu label={<><LeagueIcon league={league} />{label}</>} ariaLabel={`League: ${label}`}>
        {(close) => OPTIONS.map(([l, name]) => (
          <MenuItem key={l} on={league === l} onClick={() => { close(); if (league !== l) setLeague(l); }}>
            <span className="with-icon"><LeagueIcon league={l} />{name}</span>
          </MenuItem>
        ))}
      </Menu>
    </span>
  );
}
