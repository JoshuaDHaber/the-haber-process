import { useQueries, useQuery } from "@tanstack/react-query";
import { sharedClient } from "./queryClients";
import { useMemo, useState } from "react";
import { api, type Filters } from "./api";
import { getLeague } from "./league";
import { moneylinePicks } from "./moneyline";
import { buildBalancedLadders, buildLadderParlays, buildStarterParlays, starterTargets, buildTdParlays, buildThreesLadders, defaultBook, filterGames, ladderProps, ladderTargets, LADDER_PLAYERS_PER_TEAM, moneylineRungs, parlaysByBook, THREES_CANDIDATES, type ParlayLeg } from "./parlays";
import { usePref } from "./prefs";

// Parlays rank by win probability, not EV, so they price every play (any EV) at the default market weight
export const PARLAY_FILTERS: Filters = { kind: "all", side: "all", game: "all", book: "all", q: "", minEv: -1, marketWeight: null, flagged: false };

/**
 * Every parlay tile for the chosen games (none = every game) at one sportsbook: likely plays, touchdowns, ladders and single alternate props.
 * `slateGames` narrows "every game" when none are chosen (the NBA's date filter).
 * The per-book picks and line ladders are fetched for the whole slate and shared through the query cache, so the Parlays tab and a game page reuse them.
 */
export function useParlayBoard(books: string[], chosenGames: string[], enabled: boolean, preferredBook?: string, slateGames?: string[]) {
  // one pricing per sportsbook, so every parlay's legs sit at a single book
  const parlayQueries = useQueries({
    queries: books.map((book) => ({ queryKey: ["parlay-picks", book], queryFn: () => api.picks({ ...PARLAY_FILTERS, book }, 1000), enabled })),
  });
  // each book's moneylines, which join its props as legs (a likely winner is as good a leg as a likely over)
  const mlQueries = useQueries({
    queries: books.map((book) => ({ queryKey: ["parlay-ml", book], queryFn: () => api.moneylines(book, null), enabled })),
  });
  // anytime-TD picks per book (the plain picks above leave TDs out); the NBA has no touchdown market
  const hasTd = getLeague() !== "nba";
  const tdQueries = useQueries({
    queries: books.map((book) => ({ queryKey: ["parlay-td", book], queryFn: () => api.picks({ ...PARLAY_FILTERS, kind: "td", book }, 1000), enabled: enabled && hasTd })),
  });
  // same-game correlations from the league's latest run (without them, legs are priced as independent)
  const corrQuery = useQuery({ queryKey: [hasTd ? "correlations" : "nba-correlations"], queryFn: hasTd ? api.correlations : api.nbaCorrelations,
    enabled, staleTime: 10 * 60_000 }, sharedClient);   // shared with the bet slip, which reads both leagues
  const corr = corrQuery.data?.pairs;
  const loading = parlayQueries.some((q) => q.isLoading);
  // no book's props came back: the page says the load failed, rather than that there aren't enough likely plays
  const failed = parlayQueries.length > 0 && parlayQueries.every((q) => q.error && !q.data);
  const retry = () => Promise.all(parlayQueries.map((q) => q.refetch())).then((rs) => ({ error: rs.every((r) => r.error) ? rs[0].error : null }));
  const [book, setBook] = useState<string | null>(null);
  const parlayStamp = parlayQueries.map((q) => q.dataUpdatedAt).join() + mlQueries.map((q) => q.dataUpdatedAt).join();
  const allPicksByBook = useMemo(() => Object.fromEntries(books.map((b, i) => [b, [...parlayQueries[i]?.data ?? [],
    ...moneylinePicks(mlQueries[i]?.data ?? []).filter((p) => p.book === b)] as ParlayLeg[]])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [books.join(), parlayStamp]);
  const pool = chosenGames.length ? chosenGames : slateGames ?? [];
  const poolKey = pool.join();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const gamePicks = useMemo(() => filterGames(allPicksByBook, pool), [allPicksByBook, poolKey]);
  const byBook = useMemo(() => parlaysByBook(gamePicks, corr), [gamePicks, corr]);
  // a page-wide sportsbook choice wins over the tab's own picker
  const chosenBook = preferredBook && books.includes(preferredBook) ? preferredBook : book;
  // opening the tab: the visitor's default sportsbook from Preferences ("every book" isn't one book, so FanDuel then)
  const [savedBook] = usePref("game_book");
  const activeBook = chosenBook && books.includes(chosenBook) ? chosenBook : defaultBook(byBook, gamePicks, savedBook === "all" ? undefined : savedBook);
  // ladder parlays need each likely player's line ladder (alternate lines); shares the drawer's and slip's query cache
  const targets = useMemo(() => ladderTargets(activeBook ? gamePicks[activeBook] ?? [] : [], Math.min(24, Math.max(12, chosenGames.length * 8))), [activeBook, gamePicks, chosenGames]);
  const ladderQueries = useQueries({
    queries: targets.map((t) => ({ queryKey: ["ladder", t.player_id, t.kind, 0], queryFn: () => api.lines(t.player_id, t.kind, 0), enabled })),
  });
  const ladderStamp = ladderQueries.map((q) => q.dataUpdatedAt).join();
  const ladderEntries = useMemo(() => targets.map((base, i) => ({ base, ladder: ladderQueries[i]?.data })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [targets, ladderStamp]);
  const mlRungs = useMemo(() => activeBook ? moneylineRungs(gamePicks[activeBook] ?? [], activeBook) : [], [activeBook, gamePicks]);
  const ladderParlays = useMemo(() => activeBook ? buildLadderParlays(ladderEntries, activeBook, corr, mlRungs) : [], [activeBook, ladderEntries, corr, mlRungs]);
  // with games chosen, one more ladder per game that uses both teams
  const balancedLadders = useMemo(() => activeBook ? buildBalancedLadders(ladderEntries, activeBook, chosenGames.slice(0, 4), ladderParlays, corr, mlRungs) : [],
    [activeBook, ladderEntries, chosenGames, ladderParlays, corr, mlRungs]);
  // NBA: threes-only ladders from the likeliest threes overs (their ladders share the cache with the ones above)
  const threesTargets = useMemo(() => (!hasTd && activeBook ? ladderTargets(gamePicks[activeBook] ?? [], THREES_CANDIDATES, LADDER_PLAYERS_PER_TEAM, ["threes"]) : []),
    [hasTd, activeBook, gamePicks]);
  const threesQueries = useQueries({
    queries: threesTargets.map((t) => ({ queryKey: ["ladder", t.player_id, t.kind, 0], queryFn: () => api.lines(t.player_id, t.kind, 0), enabled })),
  });
  const threesStamp = threesQueries.map((q) => q.dataUpdatedAt).join();
  const threesLadders = useMemo(() => activeBook ? buildThreesLadders(threesTargets.map((base, i) => ({ base, ladder: threesQueries[i]?.data })), activeBook, ladderParlays, corr) : [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeBook, threesTargets, threesStamp, ladderParlays, corr]);
  const tdStamp = tdQueries.map((q) => q.dataUpdatedAt).join();
  const tdParlays = useMemo(() => {
    const i = books.indexOf(activeBook ?? "");
    return activeBook && i >= 0 ? buildTdParlays(tdQueries[i]?.data ?? [], activeBook, chosenGames, corr) : [];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBook, books, chosenGames, tdStamp, corr]);
  // starter parlays: the biggest roles' ladders, pulled separately (most aren't among the likeliest overs above); shares the ladder cache
  const starterTargetList = useMemo(() => (activeBook ? starterTargets(gamePicks[activeBook] ?? []) : []), [activeBook, gamePicks]);
  const starterQueries = useQueries({
    queries: starterTargetList.map((t) => ({ queryKey: ["ladder", t.player_id, t.kind, 0], queryFn: () => api.lines(t.player_id, t.kind, 0), enabled })),
  });
  const starterStamp = starterQueries.map((q) => q.dataUpdatedAt).join();
  const starterParlays = useMemo(() => activeBook ? buildStarterParlays(starterTargetList.map((base, i) => ({ base, ladder: starterQueries[i]?.data })), activeBook, corr) : [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeBook, starterTargetList, starterStamp, corr]);
  const altProps = useMemo(() => activeBook ? ladderProps(ladderEntries, activeBook) : [], [activeBook, ladderEntries]);
  const ladders = useMemo(() => [...ladderParlays, ...threesLadders, ...balancedLadders], [ladderParlays, threesLadders, balancedLadders]);

  // how many likely-play parlays each book has, so an empty book can point at one that isn't
  const parlayCounts = useMemo(() => Object.fromEntries(Object.entries(byBook).map(([b, ps]) => [b, ps.length])), [byBook]);

  return {
    // pickedBook: only a book the visitor chose (or a link carried), which is what goes in the address; a default left there
    // would beat their saved sportsbook on the next visit
    parlayCounts, loading, failed, retry, book: activeBook, pickedBook: book && books.includes(book) ? book : null, setBook, parlays: (activeBook && byBook[activeBook]) || [], tdParlays, ladders, altProps,
    laddersLoading: ladderQueries.some((q) => q.isLoading) || threesQueries.some((q) => q.isLoading),
    starterParlays, startersLoading: starterQueries.some((q) => q.isLoading),
  };
}
