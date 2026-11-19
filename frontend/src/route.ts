import { useEffect, useState } from "react";
import type { Filters, Kind } from "./api";
import { BASE } from "./env";
import { KIND_LABEL } from "./format";
import type { League } from "./league";

// Where you are lives in the URL, so a refresh keeps your place, back and forward step through sections, and any view can
// be shared: /<section>?<filters> for the NFL, /nba/<section>?<filters> for the NBA, with #game=<id> on top for a game page.

const NBA = "nba";
/** The path after BASE in parts: "/nba/props" -> ["nba", "props"]. */
const parts = () => window.location.pathname.slice(BASE.length).split("/").filter(Boolean);

/** The league the path names, or null on the site's front page (which shows the league last viewed). */
export const leagueFromPath = (): League | null => {
  const p = parts();
  return p[0] === NBA ? "nba" : p.length ? "nfl" : null;
};
/** The section's slug in the path ("" on a league's front page). */
export const sectionFromPath = () => {
  const p = parts();
  return (p[0] === NBA ? p[1] : p[0]) ?? "";
};
export const leaguePath = (l: League, section = "") => BASE + [l === "nba" ? NBA : "", section].filter(Boolean).join("/");
export const param = (k: string) => new URLSearchParams(window.location.search).get(k);

/** Go to a path on this site from anywhere (a sheet, a toast): both league shells follow popstate, so this lands on the
 *  section as a click on its link would. */
export function goToPath(path: string) {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo({ top: 0 });
}

/** Swap the query string for `params` (empty values dropped), keeping the path and any #game. Doesn't add a history entry. */
export function setParams(params: Record<string, string | null | undefined>) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) qs.set(k, v);
  const search = qs.toString() ? `?${qs}` : "";
  if (search !== window.location.search) window.history.replaceState(null, "", window.location.pathname + search + window.location.hash);
}

/** The section on screen, read from the path, and a way to go to another one (a new history entry, filters and #game cleared). */
export function useSection<T extends string>(slugs: Record<T, string>, league: League, fallback: T) {
  const fromPath = () => (Object.keys(slugs) as T[]).find((t) => slugs[t] === sectionFromPath()) ?? fallback;
  // a path that names no section (a typo, an old link): the shell says so instead of quietly showing the fallback
  const knownPath = () => (Object.values(slugs) as string[]).includes(sectionFromPath());
  const [section, setSection] = useState<T>(fromPath);
  const [known, setKnown] = useState(knownPath);
  useEffect(() => {
    const sync = () => { setSection(fromPath()); setKnown(knownPath()); };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  /** replace: no new history entry (a section this visitor can't open sends them on without leaving it behind) */
  const go = (t: T, { replace = false } = {}) => {
    const path = leaguePath(league, slugs[t]);
    if (path !== window.location.pathname || window.location.search || window.location.hash) window.history[replace ? "replaceState" : "pushState"](null, "", path);
    setSection(t);
    setKnown(true);
  };
  return [section, go, known] as const;
}

/** The browser tab's title for what's on screen ("Best props · The Haber Process"), so tabs, history and screen readers'
 *  page announcements tell the sections apart. */
export function useDocumentTitle(...parts: (string | null | undefined | false)[]) {
  const title = [...parts.filter(Boolean), "The Haber Process"].join(" · ");
  useEffect(() => { document.title = title; }, [title]);
}

export const DEFAULT_FILTERS: Filters = { kind: "all", side: "all", game: "all", book: "all", q: "", minEv: 0.03, marketWeight: null, flagged: false };

/** A shared link's Best props / Projections filters: ?market=rec&side=Over&game=<id>&book=DraftKings&q=name. With no book in
 *  the link, the visitor's own (prefs.ts useBoardBook). */
export function filtersFromUrl(defaultBook = "all"): Filters {
  const kind = param("market");
  const side = param("side");
  return { ...DEFAULT_FILTERS, kind: kind && kind !== "ml" && kind in KIND_LABEL ? (kind as Kind) : "all", side: side === "Over" || side === "Under" ? side : "all",
    game: param("game") ?? "all", book: param("book") ?? defaultBook, q: param("q") ?? "" };
}

/** The query string for those filters (side and book only mean something on Best props). The visitor's own default book is
 *  left out, so the address never pins it: a later change in Preferences still applies on the next visit. */
export const filterParams = (f: Filters, picks: boolean, defaultBook = "all") => ({
  market: f.kind !== "all" ? f.kind : null, side: picks && f.side !== "all" ? f.side : null, game: f.game !== "all" ? f.game : null,
  book: picks && f.book !== defaultBook ? f.book : null, q: f.q.trim() || null,
});
