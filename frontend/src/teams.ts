import type { CSSProperties } from "react";
import { getLeague, type League } from "./league";

/** Team colors (primary, secondary) and logos, keyed by the nflverse abbreviations the API uses. Logos are ESPN's, as linked by nflverse. */
const TEAMS: Record<string, [string, string]> = {
  ARI: ["#97233F", "#FFB612"], ATL: ["#A71930", "#000000"], BAL: ["#241773", "#9E7C0C"], BUF: ["#00338D", "#C60C30"],
  CAR: ["#0085CA", "#101820"], CHI: ["#0B162A", "#C83803"], CIN: ["#FB4F14", "#000000"], CLE: ["#FF3C00", "#311D00"],
  DAL: ["#003594", "#869397"], DEN: ["#FB4F14", "#002244"], DET: ["#0076B6", "#B0B7BC"], GB: ["#203731", "#FFB612"],
  HOU: ["#03202F", "#A71930"], IND: ["#002C5F", "#A2AAAD"], JAX: ["#006778", "#D7A22A"], KC: ["#E31837", "#FFB81C"],
  LA: ["#003594", "#FFA300"], LAC: ["#0080C6", "#FFC20E"], LV: ["#000000", "#A5ACAF"], MIA: ["#008E97", "#FC4C02"],
  MIN: ["#4F2683", "#FFC62F"], NE: ["#002244", "#C60C30"], NO: ["#D3BC8D", "#101820"], NYG: ["#0B2265", "#A71930"],
  NYJ: ["#125740", "#000000"], PHI: ["#004C54", "#A5ACAF"], PIT: ["#FFB612", "#101820"], SEA: ["#002244", "#69BE28"],
  SF: ["#AA0000", "#B3995D"], TB: ["#D50A0A", "#34302B"], TEN: ["#0C2340", "#4B92DB"], WAS: ["#5A1414", "#FFB612"],
};
/** NBA team colors, keyed by ESPN's abbreviations (the NBA API's); from ESPN's team list. */
const NBA_TEAMS: Record<string, [string, string]> = {
  ATL: ["#C8102E", "#FDB927"], BOS: ["#008348", "#BA9653"], BKN: ["#000000", "#FFFFFF"], CHA: ["#008CA8", "#1D1060"],
  CHI: ["#CE1141", "#000000"], CLE: ["#860038", "#BC945C"], DAL: ["#0064B1", "#BBC4CA"], DEN: ["#0E2240", "#FEC524"],
  DET: ["#1D428A", "#C8102E"], GS: ["#FDB927", "#1D428A"], HOU: ["#CE0E2D", "#000000"], IND: ["#0C2340", "#FFD520"],
  LAC: ["#12173F", "#C8102E"], LAL: ["#552583", "#FDB927"], MEM: ["#5D76A9", "#12173F"], MIA: ["#98002E", "#000000"],
  MIL: ["#00471B", "#EEE1C6"], MIN: ["#266092", "#79BC43"], NO: ["#0A2240", "#B4975A"], NY: ["#1D428A", "#F58426"],
  OKC: ["#007AC1", "#EF3B24"], ORL: ["#0150B5", "#9CA0A3"], PHI: ["#1D428A", "#E01234"], PHX: ["#29127A", "#E56020"],
  POR: ["#E03A3E", "#000000"], SAC: ["#5A2D81", "#6A7A82"], SA: ["#000000", "#C4CED4"], TOR: ["#D91244", "#000000"],
  UTAH: ["#4E008E", "#79A3DC"], WSH: ["#E31837", "#002B5C"],
};
/** The current league's colors (NFL and NBA abbreviations overlap: DAL, DEN, DET, MIA, ...). */
const colors = () => (getLeague() === "nba" ? NBA_TEAMS : TEAMS);
/** ESPN's file names where they differ from nflverse's abbreviations. */
const ESPN_SLUG: Record<string, string> = { LA: "lar", WAS: "wsh" };

/** Every team in a league, alphabetical by abbreviation (the favorite-team picker). */
export const teamsOf = (league: League) => Object.keys(league === "nba" ? NBA_TEAMS : TEAMS).sort();

/** A team's logo from ESPN, scaled down by ESPN's image service to `px` (pass about twice the shown size, for sharp
 *  screens): the original files are 500 px, ~40 KB each, against ~4 KB at 80 px. Sizes snap to a few so every logo of
 *  a size shares one cached file. */
export const teamLogo = (team: string, league: League = getLeague(), px = 80) => {
  const file = league === "nba" ? (team in NBA_TEAMS ? `nba/500/${team.toLowerCase()}` : null)
    : team in TEAMS ? `nfl/500/${ESPN_SLUG[team] ?? team.toLowerCase()}` : null;
  if (!file) return null;
  const w = LOGO_SIZES.find((s) => s >= px);
  return w ? `https://a.espncdn.com/combiner/i?img=/i/teamlogos/${file}.png&w=${w}&h=${w}` : `https://a.espncdn.com/i/teamlogos/${file}.png`;
};
const LOGO_SIZES = [40, 80, 160];

const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
/** Relative luminance (0 black .. 1 white). */
export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const distance = (a: string, b: string) => { const [x, y] = [rgb(a), rgb(b)]; return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]); };
/** Below this, two colors read as the same team next to each other (navy vs navy). */
const CLASH = 90;
/** Darker than this disappears on the dark theme's navy cards. */
const TOO_DARK = 0.03;

export interface TeamShade { light: string; dark: string; ink: string; inkDark: string }
const shade = (light: string, dark: string): TeamShade => ({ light, dark, ink: luminance(light) > 0.45 ? "#101820" : "#fff", inkDark: luminance(dark) > 0.45 ? "#101820" : "#fff" });
const FALLBACK: [string, string] = ["#5b6472", "#9aa3b2"];

/**
 * Each side's bar color in light and dark themes, plus a readable text color on it. The home team switches to its secondary color when the two
 * primaries would look alike, and a near-black color is swapped for the secondary on the dark theme.
 */
export function matchupColors(away: string, home: string): [TeamShade, TeamShade] {
  const [a1, a2] = colors()[away] ?? FALLBACK;
  const [h1, h2] = colors()[home] ?? FALLBACK;
  const pick = (first: string, second: string, other: string) => (distance(first, other) < CLASH && distance(second, other) >= CLASH ? second : first);
  const darkOf = (c: string, alt: string) => (luminance(c) < TOO_DARK ? alt : c);
  const aDark = darkOf(a1, a2);
  const hLight = pick(h1, h2, a1);
  // on the dark theme a clash never switches to a near-black secondary (ATL next to CHI would turn invisible)
  const hDark = pick(darkOf(h1, h2), darkOf(h2, h1), aDark);
  return [shade(a1, aDark), shade(hLight, hDark)];
}

/** CSS custom properties for one side of a team-colored bar (see .tbar in styles.css). */
export const shadeVars = (s: TeamShade) => ({ "--tc": s.light, "--tc-dark": s.dark, "--ti": s.ink, "--ti-dark": s.inkDark }) as CSSProperties;
