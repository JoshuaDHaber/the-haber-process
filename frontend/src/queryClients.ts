import { QueryClient } from "@tanstack/react-query";
import type { League } from "./league";

// a league's data stays cached an hour after its side is left, so switching back shows it at once (refreshed if over 30 s old)
const newClient = () => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 30_000, gcTime: 60 * 60_000 } } });

/** One cache per league: both sides use the same query keys (["meta"], ["picks", ...]) for different endpoints. */
export const leagueClients: Record<League, QueryClient> = { nfl: newClient(), nba: newClient() };

/** What both leagues read the same way, so a switch doesn't fetch it again: the bet slip's line ladders, projections and
 *  correlations (a slip holds legs from either league). Pass it as useQuery's second argument. Its ladders are reset
 *  with the leagues' caches when the legal books change (main.tsx), and refreshed with theirs after an alt-line fetch. */
export const sharedClient = newClient();
