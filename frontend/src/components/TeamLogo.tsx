import { useState } from "react";
import type { League } from "../league";
import { mlLeague } from "../moneyline";
import { teamLogo } from "../teams";
import Avatar from "./Avatar";

/** A team's logo, or nothing if it is unknown or fails to load (the abbreviation beside it still names the team). Loaded
 *  at once, not lazily: logos sit on the game tiles at the top of the page, where lazy loading only held them back. */
export default function TeamLogo({ team, size = 36, league }: { team: string; size?: number; league?: League }) {
  const [failed, setFailed] = useState(false);
  const src = teamLogo(team, league, size * 2);
  if (!src || failed) return null;
  return <img className="team-logo" src={src} alt="" width={size} height={size} decoding="async" onError={() => setFailed(true)} />;
}

/** The face of a parlay or board row: a team's logo for its moneyline, else the player's avatar. */
export function PickAvatar({ name, kind, team, gameId, size = 26 }: { name: string; kind: string; team: string; gameId: string; size?: number }) {
  if (kind === "ml" && teamLogo(team, mlLeague(gameId))) {
    return <span className="pick-logo" style={{ width: size, height: size }}><TeamLogo team={team} league={mlLeague(gameId)} size={size} /></span>;
  }
  return <Avatar name={name} size={size} />;
}
