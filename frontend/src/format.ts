import type { GameWeather, Kind } from "./api";

export const KIND_LABEL: Record<Kind, string> = {
  rush: "Rushing yds", rec: "Receiving yds", pass: "Passing yds", rr: "Rush+Rec yds", td: "Anytime TD", recs: "Receptions",
  ptd: "Passing TDs", lng: "Longest reception", rlng: "Longest rush", ftd: "First TD scorer", pts: "Points", reb: "Rebounds", ast: "Assists", threes: "3-pointers made",
  pra: "Pts+Reb+Ast", pr: "Pts+Reb", pa: "Pts+Ast", stl: "Steals", blk: "Blocks", dd: "Double-double", tpl: "Triple-double", ml: "Moneyline",
};
export const KIND_SHORT: Record<Kind, string> = { rush: "Rushing", rec: "Receiving", pass: "Passing", rr: "Rush+Rec", td: "Anytime TD", recs: "Receptions",
  ptd: "Passing TDs", lng: "Longest rec", rlng: "Longest rush", ftd: "First TD", pts: "Points", reb: "Rebounds", ast: "Assists", threes: "Threes",
  pra: "PRA", pr: "PR", pa: "PA", stl: "Steals", blk: "Blocks", dd: "Double-double", tpl: "Triple-double", ml: "Moneyline" };
/** The NBA's markets: projected minutes x a per-minute rate. */
export const NBA_KINDS = ["pts", "reb", "ast", "threes", "pra", "pr", "pa", "stl", "blk", "dd", "tpl"] as const;
export const isNba = (kind: Kind) => (NBA_KINDS as readonly Kind[]).includes(kind);
/** Touchdown markets: a lone "Yes" price per player (stored as an over 0.5), and mu is the chance. */
export const isYesNo = (kind?: Kind | "all") => kind === "td" || kind === "ftd" || isDouble(kind);
/** NBA double- and triple-doubles: yes/no, and how many stats must reach double figures. */
export const DOUBLE_NEED: Partial<Record<Kind, number>> = { dd: 2, tpl: 3 };
export const isDouble = (kind?: Kind | "all") => kind === "dd" || kind === "tpl";
/** Plays whose label says it all ("Anytime TD", "Moneyline"), so no market name follows it. */
export const namesItself = (kind?: Kind | "all") => isYesNo(kind) || kind === "ml";

/** Grouped markets: one sub tab each, with a pill per market inside. */
/** icon: the market whose icon stands for the group (components/icons.tsx). */
export interface MarketGroup { label: string; icon: Kind; kinds: Kind[] }
export const PROJ_GROUPS: MarketGroup[] = [
  { label: "Rushing", icon: "rush", kinds: ["rush", "rlng"] },
  { label: "Receiving", icon: "rec", kinds: ["rec", "recs", "lng"] },
  { label: "Passing", icon: "pass", kinds: ["pass", "ptd"] },
  { label: "Touchdowns", icon: "td", kinds: ["td", "ftd"] },
];
/** The market picker's top row (props and projections alike): single markets, and the groups above. */
export const MARKET_TABS: (Kind | "all" | MarketGroup)[] = ["all", ...PROJ_GROUPS.slice(0, 3), "rr", PROJ_GROUPS[3]];
/** The NBA's combo markets, under one tab. */
export const NBA_COMBOS: MarketGroup = { label: "Combos", icon: "pra", kinds: ["pra", "pr", "pa", "dd", "tpl"] };
/** Steals and blocks, under one tab. */
export const NBA_DEFENSE: MarketGroup = { label: "Defense", icon: "stl", kinds: ["stl", "blk"] };
/** The NBA market picker's top row. */
export const NBA_MARKET_TABS: (Kind | "all" | MarketGroup)[] = ["all", "pts", "reb", "ast", "threes", NBA_COMBOS, NBA_DEFENSE];
/** The group a market belongs to, if any. */
export const groupOf = (kind: Kind | "all") => [...PROJ_GROUPS, NBA_COMBOS, NBA_DEFENSE].find((g) => (g.kinds as string[]).includes(kind));
/** A market's name inside its group's sub tab. */
export const SUB_LABEL: Partial<Record<Kind, string>> = { rush: "Yards", rlng: "Longest", rec: "Yards", recs: "Receptions", lng: "Longest",
  pass: "Yards", ptd: "TDs", td: "Anytime", ftd: "First TD" };
/** Any market key the API reports (props, plus the team moneyline). */
export const marketLabel = (k: string) => (k === "ml" ? "Moneyline" : KIND_LABEL[k as Kind] ?? k);

/** Receptions are counted in catches (small numbers, so one decimal); the other over/under markets in yards. */
export const isCount = (kind: Kind) => kind === "recs";
/** Markets counted in small whole numbers (catches, passing TDs) show one decimal; yardage none. */
export const decimals = (kind: Kind) => (kind === "recs" || kind === "ptd" || isNba(kind) ? 1 : 0);
export const unit = (kind: Kind) => (kind === "threes" ? "3PM" : kind === "stl" || kind === "blk" ? kind.toUpperCase() : isNba(kind) ? (kind.startsWith("p") && kind !== "pts" ? kind.toUpperCase() : kind) : isCount(kind) ? "rec" : kind === "ptd" ? "TD" : "yds");
/** What a projection's volume is: carries / targets / attempts, for longest reception the expected catches, for the NBA minutes. */
export const volLabel = (kind: Kind) => (isNba(kind) ? "Minutes" : isCount(kind) ? "Targets" : kind === "ptd" ? "Attempts" : kind === "lng" ? "Catches" : kind === "rlng" ? "Carries" : "Volume");
/** A projection's efficiency, labelled: catch rate, TD rate, yards per catch, yards per attempt, or (NBA) per 36 minutes. */
export const effText = (kind: Kind, eff: number): [string, string] =>
  isNba(kind) ? ["Per 36 min", (eff * 36).toFixed(1)]
  : isCount(kind) ? ["Catch rate", `${(eff * 100).toFixed(0)}%`]
  : kind === "ptd" ? ["TD / att", `${(eff * 100).toFixed(1)}%`]
  : kind === "lng" ? ["Yds / catch", eff.toFixed(1)]
  : kind === "rlng" ? ["Yds / carry", eff.toFixed(1)]
  : ["Yds / att", eff.toFixed(2)];
/** The caption under a projection: what the number counts. */
export const projCaption = (kind: Kind) =>
  isDouble(kind) ? `chance of a ${KIND_LABEL[kind].toLowerCase()}`
  : isNba(kind) ? `projected ${KIND_LABEL[kind].toLowerCase()}`
  : kind === "td" ? "chance to score" : kind === "ftd" ? "chance to score first" : isCount(kind) ? "projected receptions" : kind === "ptd" ? "projected passing TDs"
  : kind === "lng" ? "projected longest catch" : kind === "rlng" ? "projected longest rush" : "projected yards";

/** A projection as shown to a reader: yards (catches for receptions), or for anytime TD the chance of scoring (mu is a probability there). */
export const fmtProj = (kind: Kind, mu: number) => (isYesNo(kind) ? `${(mu * 100).toFixed(0)}%` : mu.toFixed(decimals(kind)));
/** The typical miss around a projection. */
export const fmtSd = (kind: Kind, sd: number) => `±${sd.toFixed(decimals(kind))}`;

/** What a play is called. Yardage is "Over 54.5"; anytime TD is stored as an over 0.5, shown as "Anytime TD" / "No TD" (double-doubles alike). */
export const playLabel = (kind: Kind, side: "Over" | "Under", line: number) =>
  kind === "ml" ? "Moneyline" : kind === "td" ? (side === "Over" ? "Anytime TD" : "No TD") : kind === "ftd" ? (side === "Over" ? "First TD" : "Not first TD")
  : isDouble(kind) ? (side === "Over" ? KIND_LABEL[kind] : `No ${KIND_LABEL[kind].toLowerCase()}`) : `${side} ${line}`;

/** Edge between the projection and the line: yards (catches for receptions), percentage points for anytime TD. */
export const fmtEdge = (kind: Kind, edge: number) => `${edge >= 0 ? "+" : ""}${edge.toFixed(1)}${isYesNo(kind) ? " pts" : ""}`;

export const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;
export const signedPct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
export const signed = (x: number, digits = 1) => `${x >= 0 ? "+" : ""}${x.toFixed(digits)}`;
export const americanOdds = (o: number) => (o > 0 ? `+${o}` : `${o}`);
export const matchup = (opp: string, home: boolean) => `${home ? "vs" : "@"} ${opp}`;

/** The UTC instant of a US Eastern wall-clock time (the nflverse schedule's gameday/gametime are Eastern). */
export function easternToDate(gameday: string, gametime: string): Date | null {
  const [y, mo, d] = gameday.split("-").map(Number);
  const [h, mi] = gametime.split(":").map(Number);
  if ([y, mo, d, h, mi].some((n) => n === undefined || isNaN(n))) return null;
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  // What Eastern wall time is that instant? The difference is Eastern's UTC offset on that date.
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric",
  }).formatToParts(guess).map((p) => [p.type, p.value]));
  const asEastern = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute);
  return new Date(guess + (guess - asEastern));
}

/** Today's US Eastern date as "YYYY-MM-DD", the schedule's gameday format (or `now`'s, for tests). */
export function easternToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** "Sun 10:00 AM": kickoff in the viewer's own time zone (or `timeZone`, for tests). */
export function kickoff(gameday: string, gametime: string, timeZone?: string): string {
  const at = easternToDate(gameday, gametime);
  if (at) return at.toLocaleString(undefined, { timeZone, weekday: "short", hour: "numeric", minute: "2-digit" });
  const d = new Date(`${gameday}T12:00:00`);
  const day = isNaN(d.getTime()) ? "" : d.toLocaleDateString("en-US", { weekday: "short" });
  return [day, gametime].filter(Boolean).join(" ");
}

export function timeAgo(iso: string, now = Date.now()): string {
  const mins = Math.max(0, Math.round((now - new Date(iso + "Z").getTime()) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 48) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} d ago`;
}

/** "Dome", or the kickoff forecast: "78°F · wind 19 mph (gusts 27) · 40% rain". null when there is nothing to say. */
export function weatherText(w: GameWeather | null | undefined): string | null {
  if (!w) return null;
  if (w.indoor) return w.roof === "dome" ? "Dome" : w.roof === "closed" ? "Roof closed" : w.roof === "retractable" ? "Retractable roof" : null;
  if (w.source !== "forecast" || w.temp == null || w.wind == null) return null;
  const parts = [`${Math.round(w.temp)}°F`, `wind ${Math.round(w.wind)} mph${w.gust != null && w.gust >= w.wind + 5 ? ` (gusts ${Math.round(w.gust)})` : ""}`];
  if ((w.snow ?? 0) > 0.05) parts.push(`snow ${w.snow!.toFixed(1)}"`);
  else if ((w.precip_prob ?? 0) >= 30) parts.push(`${Math.round(w.precip_prob!)}% rain`);
  else if (w.precip_prob == null && (w.precip ?? 0) >= 0.05) parts.push(`rain ${w.precip!.toFixed(2)}"`);
  return parts.join(" · ");
}

/** Worth flagging on a row: wind, cold, or wet enough to matter. */
export function notableWeather(w: GameWeather | null | undefined): boolean {
  if (!w || w.indoor || w.source !== "forecast") return false;
  return (w.wind ?? 0) >= 12 || (w.temp ?? 60) <= 40 || (w.precip_prob ?? 0) >= 60 || (w.snow ?? 0) > 0.05;
}

export type WxIcon = "wind" | "snow" | "rain" | "cold";
/** The one condition that stands out, and its icon: wind "19 mph", snow "snow", rain "70% rain", cold "31°F". */
export function weatherShort(w: GameWeather): { icon: WxIcon; text: string } {
  if ((w.snow ?? 0) > 0.05) return { icon: "snow", text: "snow" };
  if ((w.wind ?? 0) >= 12) return { icon: "wind", text: `${Math.round(w.wind!)} mph` };
  if ((w.precip_prob ?? 0) >= 60) return { icon: "rain", text: `${Math.round(w.precip_prob!)}% rain` };
  if ((w.temp ?? 60) <= 40) return { icon: "cold", text: `${Math.round(w.temp!)}°F` };
  return { icon: "wind", text: `${Math.round(w.wind ?? 0)} mph` };
}

/** The weather adjustment as a percentage ("-22%"), or null when it is (effectively) none. */
export const wxEffect = (wx: number | null | undefined) =>
  wx != null && Math.abs(wx - 1) >= 0.005 ? `${wx > 1 ? "+" : "−"}${Math.abs((wx - 1) * 100).toFixed(0)}%` : null;

const POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
/** Compass point for a bearing: 300 -> "WNW". */
export const compassPoint = (deg: number) => POINTS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];

export type SkyIcon = "storm" | "snow" | "rain" | "fog" | "cloud" | "partly" | "moon" | "sun";
/** The icon for a sky description; clear skies at night kickoffs (7 pm ET or later) get a moon. */
export function skyIcon(sky: string | null | undefined, gametime = ""): SkyIcon {
  const s = (sky ?? "").toLowerCase();
  if (s.includes("thunder")) return "storm";
  if (s.includes("snow") || s.includes("flurr")) return "snow";
  if (/rain|shower|drizzle/.test(s)) return "rain";
  if (s.includes("fog")) return "fog";
  if (s.includes("overcast") || /^(mostly )?cloudy/.test(s)) return "cloud";
  if (s.includes("partly")) return "partly";
  return parseInt(gametime, 10) >= 19 ? "moon" : "sun";
}

/** Smooth scrolling, unless the reader asked their device for less motion. */
export const scrollBehavior = (): ScrollBehavior =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";

/** A change in chance as signed percentage points: 0.031 -> "+3 pts". */
export const signedPts = (d: number) => { const r = Math.round(d * 100); return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r)} pts`; };

const FLAG_TITLE: Record<string, string> = {
  "large gap": "Model and market disagree strongly",
  "long shot": "A double- or triple-double longer than +400: these have hit well below their price",
  "edge too big": "EV over 20%: an edge this big is usually something the model is missing",
};

/** A flagged pick's chip: its reason (older snapshots without one were all large gaps) and the reason's tooltip. */
export function flagChip(p: { flag?: string }): { label: string; title: string } {
  const label = p.flag || "large gap";
  return { label, title: FLAG_TITLE[label] ?? FLAG_TITLE["large gap"] };
}
