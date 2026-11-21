import type { QueryClient } from "@tanstack/react-query";
import { slatePart, type Meta, type NbaMeta } from "./api";
import { loadAuth } from "./auth";
import type { League } from "./league";
import { chosenGameBook } from "./prefs";
import { leagueClients } from "./queryClients";

/** The games page's queries for one league, shared by the page itself and the prefetch that warms the other league, so
 *  both use the same keys and the page finds what the prefetch fetched. book: the board's sportsbook ("all": every book);
 *  marketWeight: the board's market weight (null: fitted). */
export function slateQueries<M extends Meta | NbaMeta>(league: League, book: string, marketWeight: number | null) {
  return {
    meta: metaQuery<M>(league),
    picks: { queryKey: ["slate-picks", book, marketWeight], queryFn: () => slatePart(league, "picks", book, marketWeight) },
    moneylines: { queryKey: ["moneylines", book, marketWeight], queryFn: () => slatePart(league, "moneylines", book, marketWeight) },
    injuries: { queryKey: ["injuries"], queryFn: () => slatePart(league, "injuries") },
  } as const;
}

/** A league's slate (["meta"]): the drawer and ladders read it from the cache, so both leagues keep it under this key. */
export const metaQuery = <M extends Meta | NbaMeta>(league: League) =>
  ({ queryKey: ["meta"], queryFn: () => slatePart<"meta", M>(league, "meta") }) as const;

/** Fetch a league's games page into its cache ahead of a switch (nothing is fetched for what's still fresh). The board
 *  opens at the visitor's sportsbook and the fitted market weight, as a league's front page always does. */
export function prefetchSlate(client: QueryClient, league: League) {
  const q = slateQueries(league, chosenGameBook() ?? "all", null);
  void client.prefetchQuery(q.meta);
  void client.prefetchQuery(q.picks);
  void client.prefetchQuery(q.moneylines);
  void client.prefetchQuery(q.injuries);
}

/** Get a league ready, so switching to it shows its games page at once: its code (the NBA side is its own chunk) and
 *  its games page's data, once the visitor's sportsbook is known (an account's saved book prices the board). */
export function prefetchLeague(league: League) {
  if (league === "nba") void import("./NbaApp").catch(() => {});
  void loadAuth().then(() => prefetchSlate(leagueClients[league], league));
}
