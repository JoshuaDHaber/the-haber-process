import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { api, type Filters, type Kind, type Meta } from "./api";
import { useSettingsReady } from "./auth";
import { slateQueries } from "./slateQueries";
import BetSlip from "./components/BetSlip";
import Controls from "./components/Controls";
import GameOverview from "./components/GameOverview";
import GameWeather from "./components/GameWeather";
import Games from "./components/Games";
import Injuries from "./components/Injuries";
import ModelCheck from "./components/ModelCheck";
import ParlayTiles from "./components/ParlayTiles";
import PicksTable from "./components/PicksTable";
import PlayerCompare from "./components/PlayerCompare";
import ProjectionsTable from "./components/ProjectionsTable";
import SoftSpots from "./components/SoftSpots";
import EndzoneBoard from "./components/EndzoneBoard";
import TokenBoard from "./components/TokenBoard";
import { isYesNo, KIND_LABEL, americanOdds, fmtProj, fmtSd, marketLabel, projCaption, matchup, playLabel, signedPct, timeAgo } from "./format";
import { useLeague } from "./LeagueContext";
import BookSelect from "./components/BookSelect";
import Freshness, { StaleBanner, latestFetch, oddsLevel } from "./components/Freshness";
import HotBoard, { hotTiles, useHotBoard } from "./components/HotBoard";
import HowToRead from "./components/HowToRead";
import PageHead from "./components/PageHead";
import MyBets from "./components/MyBets";
import Preferences from "./components/Preferences";
import { orderTabs, PREFERRED_GAME_BOOK, useBoardBook, usePref } from "./prefs";
import ResponsibleGaming from "./components/ResponsibleGaming";
import TopBar from "./components/TopBar";
import TopTiles, { type Tile } from "./components/TopTiles";
import { importOrReload } from "./staleChunk";
import { localWhen } from "./sync";
import { filterParams, filtersFromUrl, leaguePath, param, setParams, useDocumentTitle, useSection } from "./route";
import NotFound from "./components/NotFound";
import { useBetSlip } from "./BetSlipContext";
import { useSelectedPlayer } from "./playerHash";
import Methodology from "./components/Methodology";
import { useDebounced } from "./useDebounced";
import { routeClick } from "./components/NavLink";
import { useParlayBoard } from "./useParlayBoard";
import Skeleton from "./components/Skeleton";
import { LoadError } from "./components/EmptyState";
import { BASE } from "./env";

// keeps the charting library out of the first load; a tab left open across a deploy reloads to the new build
const PlayerDrawer = lazy(() => importOrReload(() => import("./components/PlayerDrawer")));
const AdminPage = lazy(() => importOrReload(() => import("./components/AdminPage")));
const NbaApp = lazy(() => importOrReload(() => import("./NbaApp")));
type Tab = "games" | "picks" | "hot" | "parlays" | "promos" | "injuries" | "spots" | "projections" | "compare" | "model" | "admin" | "account" | "bets";
const TABS: [Tab, string][] = [["games", "Games"], ["picks", "Best props"], ["parlays", "Parlays"], ["hot", "Who's Hot"], ["promos", "Promos"], ["injuries", "Injuries"], ["spots", "Soft spots"], ["projections", "Projections"],
  ["compare", "Compare"], ["model", "Model check"], ["admin", "Admin"]];
/** Sections under the top bar's More menu. */
const MORE: Tab[] = ["promos", "injuries", "spots", "compare", "model", "admin"];
/** Sections only the site owner sees (signed in with an ADMIN_EMAILS account): meta.can_write. */
const OWNER_ONLY: Tab[] = ["model", "admin"];
/** Each section's page title (Best props and Parlays add their sportsbook picker). */
const TITLE: Record<Tab, string> = { games: "This week's games", picks: "Best props", hot: "Who's Hot", parlays: "Parlays", promos: "Promos", injuries: "Injury report", spots: "Soft spots", projections: "Projections",
  compare: "Compare players", model: "Model check", admin: "Admin", account: "Preferences", bets: "My bets" };
/** Each section's path: /props, /parlays, ... (Games is the front page). */
const SLUGS: Record<Tab, string> = { games: "", picks: "props", hot: "hot", parlays: "parlays", promos: "promos", injuries: "injuries", spots: "soft-spots", projections: "projections", compare: "compare",
  model: "model", admin: "admin", account: "account", bets: "bets" };
/** The book promotions the Promos page prices, each behind a pill. */
type Promo = "early-win" | "endzone";
const PROMOS: [Promo, string][] = [["early-win", "Early win"], ["endzone", "King of the Endzone"]];
/** A game's overview page lives at #game=<game_id>, so the browser's back button returns to the slate and the page can be linked. */
const gameFromHash = () => new URLSearchParams(window.location.hash.slice(1)).get("game");

export default function App() {
  const { league } = useLeague();
  return league === "nba" ? <Suspense fallback={<div className="wrap"><Skeleton kind="page" /></div>}><NbaApp /></Suspense> : <PublicApp />;
}

function PublicApp() {
  const [tab, goSection, knownPath] = useSection<Tab>(SLUGS, "nfl", "games");
  const [gameId, setGameId] = useState<string | null>(gameFromHash);
  // true while a game page sits on top of the slate in history, so "All games" can go back instead of stacking another entry
  const pushedGame = useRef(false);
  useEffect(() => {
    const sync = () => { const id = gameFromHash(); pushedGame.current = !!id; setGameId(id); };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const openGame = (id: string | null) => {
    if (id === gameId) return;
    pushedGame.current = !!id;
    const url = window.location.pathname + window.location.search + (id ? `#game=${encodeURIComponent(id)}` : "");
    if (id) window.history.pushState(null, "", url); else window.history.replaceState(null, "", url);
    setGameId(id);
    window.scrollTo({ top: 0 });
  };
  const goTab = (t: Tab) => { pushedGame.current = false; setGameId(null); goSection(t); window.scrollTo({ top: 0 }); };
  const boardBook = useBoardBook();
  const [filters, setFilters] = useState<Filters>(() => filtersFromUrl(boardBook));
  // the account's sportsbook can arrive after the page opens (signing in on a new device): follow it, unless the board's book
  // was changed here or came from a link
  const lastBoardBook = useRef(boardBook);
  useEffect(() => {
    const was = lastBoardBook.current;
    lastBoardBook.current = boardBook;
    if (was !== boardBook) setFilters((f) => (f.book === was ? { ...f, book: boardBook } : f));
  }, [boardBook]);
  const { selected, open: select, close: closePlayer, remember: rememberKind } = useSelectedPlayer();
  // the game page's sportsbook: a preference, remembered in this browser and the account (prefs.ts)
  const [gameBook, setGameBook] = usePref("game_book");
  const [tabOrder] = usePref("tab_order");
  const debounced = useDebounced(filters, 250);
  // which promotion the Promos page shows (Early win by default), and King of the Endzone's game (the next to kick off by default)
  const [promo, setPromo] = useState<Promo>(() => (param("promo") === "endzone" ? "endzone" : "early-win"));
  const [endzoneGame, setEndzoneGame] = useState<string | null>(() => param("promo_game"));

  // the games page's four reads go out as one request (slateQueries.ts); on a signed-in visit they wait for the account's
  // sportsbook, so the board isn't fetched at a stale book first. The filters follow a new account book a render late
  // (the effect above), so the board's book is worked out here the same way.
  const settingsReady = useSettingsReady();
  const book = filters.book === lastBoardBook.current ? boardBook : filters.book;
  const slate = slateQueries<Meta>("nfl", book, debounced.marketWeight);
  const meta = useQuery({ ...slate.meta, enabled: settingsReady || tab !== "games" || !!gameId });
  // the slate as this visit fetched it, or still fresh from before (not a copy saved on an earlier visit, which may
  // lack a game added since): only that sends a game link or the owner's page elsewhere
  const metaFresh = meta.isFetchedAfterMount || (!!meta.data && !meta.isStale);
  useDocumentTitle(!knownPath ? "Page not found" : gameId ? meta.data?.games.find((g) => g.game_id === gameId)?.label : TITLE[tab]);
  // Model check is the owner's: anyone else following a /model link lands on the games (Admin keeps its own sign-in card)
  const owner = meta.data?.can_write;
  useEffect(() => { if (tab === "model" && metaFresh && owner === false) goSection("games", { replace: true }); }, [tab, owner, metaFresh]); // eslint-disable-line react-hooks/exhaustive-deps
  // odds over six hours old: the slip asks the bettor to check prices before handing legs to a book
  const { setStaleSince } = useBetSlip();
  const oddsAt = meta.data ? latestFetch(meta.data.odds) : undefined;
  const stale = !!oddsAt && oddsLevel(oddsAt) === "stale";
  useEffect(() => setStaleSince(stale ? oddsAt! : null), [stale, oddsAt, setStaleSince]);
  // a game page counts as open only for a game still on the slate (assumed so until the slate loads)
  const gameOpen = !!gameId && (!metaFresh || !meta.data || meta.data.games.some((g) => g.game_id === gameId));
  // an old link (#game=<a game no longer on the slate>) drops the hash and lands on the slate
  useEffect(() => {
    if (!gameId || !metaFresh || !meta.data || meta.data.games.some((g) => g.game_id === gameId)) return;
    pushedGame.current = false;
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    setGameId(null);
  }, [gameId, meta.data, metaFresh]);
  const picks = useQuery({ queryKey: ["picks", debounced], queryFn: () => api.picks(debounced), enabled: tab === "picks", placeholderData: (p) => p });
  const [parlayGames, setParlayGames] = useState<string[]>(() => (tab === "parlays" && param("games")?.split(",").filter(Boolean)) || []);
  // only games still on the slate count; every parlay and ladder is built from the chosen games' picks
  const chosenGames = useMemo(() => parlayGames.filter((id) => meta.data?.games.some((g) => g.game_id === id)), [parlayGames, meta.data]);
  const board = useParlayBoard(meta.data?.books ?? [], chosenGames, tab === "parlays" && !gameOpen);
  // a shared parlays link names its book
  useEffect(() => { const b = param("book"); if (tab === "parlays" && b) board.setBook(b); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // keep the query string in step with what's on screen, so the URL always reopens this view
  useEffect(() => {
    if (gameId) return;
    const f = filters;
    setParams(tab === "picks" || tab === "projections" || tab === "hot"
      ? { ...filterParams(f, tab !== "projections", boardBook), ...(tab === "hot" && { side: null }) }
      : tab === "parlays" ? { book: board.pickedBook, games: parlayGames.join(",") || null }
      : tab === "promos" ? { promo: promo === "early-win" ? null : promo, promo_game: promo === "endzone" ? endzoneGame : null } : {});
  }, [tab, gameId, filters, board.pickedBook, parlayGames, boardBook, promo, endzoneGame]);
  // positive-EV plays across the slate, for the game tiles' top prop and counts
  const onSlate = tab === "games" && !gameOpen;
  const slatePicks = useQuery({ ...slate.picks, enabled: onSlate && settingsReady });
  const moneylines = useQuery({ ...slate.moneylines, enabled: onSlate && settingsReady, placeholderData: (p) => p });
  const injuries = useQuery({ ...slate.injuries, enabled: tab === "injuries" || (onSlate && settingsReady), refetchInterval: 15 * 60_000 });
  const hot = useHotBoard(debounced, tab === "hot");
  const projections = useQuery({ queryKey: ["projections", debounced], queryFn: () => api.projections(debounced), enabled: tab === "projections", placeholderData: (p) => p });

  if (meta.isLoading) return (
    <div className="wrap">
      <Skeleton kind="page" />
    </div>
  );
  if (meta.error || !meta.data) return (
    <div className="wrap">
      <p className="err">{window.location.hostname === "localhost" ? "Can't reach the API. Is the backend running on port 8000?" : "The reaction stalled: can't reach the API right now. Try again in a minute."}</p>
    </div>
  );
  const m = meta.data;
  const tiles: Tile[] = tab === "picks"
    ? [...(picks.data ?? [])].sort((a, b) => b.ev - a.ev).slice(0, 3).map((p) => ({
      key: `${p.player_id}-${p.kind}-${p.side}`, label: KIND_LABEL[p.kind], name: p.name, meta: `${p.pos} ${p.team} ${matchup(p.opp, p.home)}`,
      value: <>{playLabel(p.kind, p.side, p.line)} <em>{signedPct(p.ev)} EV</em></>, caption: `${americanOdds(p.odds)} at ${p.book}`,
      onClick: () => select({ id: p.player_id, kind: p.kind, pick: { side: p.side, line: p.line, book: p.book } }),
    }))
    : tab === "hot" ? hotTiles(hot.query.data ?? [], (p) => select({ id: p.player_id, kind: p.kind, pick: { side: p.side, line: p.line, book: p.book } }))
    : tab === "games" || tab === "parlays" || tab === "promos" || tab === "admin" || tab === "account" || tab === "bets" || tab === "injuries" || tab === "spots" || tab === "compare" ? []
    : tab === "projections"
      ? (filters.kind === "all" ? (["pass", "rush", "rec", "recs"] as Kind[]).map((k) => (projections.data ?? []).filter((p) => p.kind === k).sort((a, b) => b.mu - a.mu)[0])
        : [...(projections.data ?? [])].sort((a, b) => b.mu - a.mu).slice(0, 3)).filter(Boolean).map((p) => ({
        key: `${p.player_id}-${p.kind}`, label: filters.kind === "all" ? `Top ${KIND_LABEL[p.kind]}` : KIND_LABEL[p.kind], name: p.name, meta: `${p.pos} ${p.team} ${matchup(p.opp, p.home)}`,
        value: <>{fmtProj(p.kind, p.mu)} {!isYesNo(p.kind) && <em>{fmtSd(p.kind, p.sd)}</em>}</>, caption: projCaption(p.kind),
        onClick: () => select({ id: p.player_id, kind: p.kind }),
      }))
      // the moneyline beats a naive baseline but not the market, so it isn't held up as a "best" market
      : Object.entries(m.backtest).filter(([k]) => k !== "ml").map(([k, b]) => ({ k, b, gain: (b.mae_naive - b.mae_model) / b.mae_naive * 100 }))
        .sort((a, b) => b.gain - a.gain).slice(0, 3).map(({ k, b, gain }) => ({
          key: k, label: "Best backtested market", name: marketLabel(k), meta: `${b.n.toLocaleString()} games`,
          value: <>{gain.toFixed(1)}% <em>better</em></>, caption: "than the naive baseline",
        }));
  const weatherByGame = Object.fromEntries(m.games.map((g) => [g.game_id, g.weather]));
  const selectedGame = m.games.find((g) => g.game_id === filters.game);
  const openedGame = gameId ? m.games.find((g) => g.game_id === gameId) : undefined;
  // a remembered book that has no lines this week falls back to FanDuel, then the first book loaded
  const pageBook = gameBook === "all" || m.books.includes(gameBook) ? gameBook
    : m.books.includes(PREFERRED_GAME_BOOK) ? PREFERRED_GAME_BOOK : m.books[0] ?? "all";
  // the player drawer follows the game page's book while one is open, else the tabs' filter
  const drawerBook = openedGame ? pageBook : filters.book;
  const setDrawerBook = (b: string) => (openedGame ? setGameBook(b) : setFilters({ ...filters, book: b }));
  const hasOdds = Object.keys(m.odds).length > 0;
  const latestOdds = oddsAt;
  const pricedPage = knownPath && (!!openedGame || tab === "games" || tab === "picks" || tab === "hot" || tab === "parlays" || tab === "promos");
  const href = (t: Tab) => leaguePath("nfl", SLUGS[t]);
  // when the next scheduled sync runs, for empty boards to say when to check back
  const nextUpdate = m.auto_sync?.enabled ? m.auto_sync.next_at : null;
  const promoPills = (
    <div className="seg small" role="group" aria-label="Promotion">
      {PROMOS.map(([k, label]) => <button key={k} className={promo === k ? "on" : ""} aria-pressed={promo === k} onClick={() => setPromo(k)}>{label}</button>)}
    </div>
  );

  return (
    <div className="wrap">
      <TopBar tabs={orderTabs(m.can_write ? TABS : TABS.filter(([k]) => !OWNER_ONLY.includes(k)), tabOrder)} home="games" more={MORE} tab={knownPath ? tab : undefined} onTab={goTab} href={href} />
      <main id="main" tabIndex={-1}>
      <p className="statusline">
        {m.run ? `${m.run.season} Week ${m.run.week} · projections ${timeAgo(m.run.created_at)}` : "No projections yet"}
        {latestOdds && <> · <Freshness at={latestOdds} /></>}
        {m.auto_sync?.enabled && m.auto_sync.next_at && ` · next update ${localWhen(m.auto_sync.next_at)}`}
      </p>

      {!m.run && <div className="banner">No projections yet. They appear after the next scheduled sync{m.auto_sync?.next_at ? ` (${localWhen(m.auto_sync.next_at)})` : ""}.</div>}
      {m.run && !hasOdds && (tab === "games" || tab === "picks" || tab === "hot" || tab === "parlays" || tab === "promos") && (
        <div className="banner">No sportsbook lines are loaded yet, so there are no picks. Books post props mid-week; the scheduled syncs fetch them as they appear.</div>
      )}
      {stale && pricedPage && <StaleBanner at={latestOdds!} />}

      <div className={stale ? "odds-stale" : undefined}>

      {openedGame ? (
        <GameOverview key={openedGame.game_id} game={openedGame} books={m.books} book={pageBook} onBook={setGameBook} marketWeight={debounced.marketWeight} fit={m.weather}
          onBack={() => (pushedGame.current ? window.history.back() : openGame(null))}
          onSelect={(id, kind, pick) => select({ id, kind, pick })} />
      ) : !knownPath ? <NotFound home={href("games")} homeLabel="Go to this week's games" onHome={() => goTab("games")} /> : <>
      <PageHead title={
        tab === "picks" ? <>Best props at <BookSelect inline books={m.books} book={filters.book} allLabel="every book" onChange={(b) => setFilters({ ...filters, book: b })} /></>
          : tab === "hot" ? <>Who's Hot at <BookSelect inline books={m.books} book={filters.book} allLabel="every book" onChange={(b) => setFilters({ ...filters, book: b })} /></>
          : tab === "promos" ? <>Promos at <BookSelect inline books={m.books} book={filters.book} allLabel="every book" onChange={(b) => setFilters({ ...filters, book: b })} /></>
          : tab === "parlays" && board.book ? <>Parlays at <BookSelect inline books={m.books} book={board.book} onChange={board.setBook} /></>
          : TITLE[tab]} />
      <TopTiles tiles={tiles} />

      {tab === "games" && <Games games={m.games} nextGames={m.next_games} nextByes={m.next_byes} picks={slatePicks.data ?? []} moneylines={moneylines.data ?? []} injuries={injuries.data} onOpen={openGame} nextUpdate={nextUpdate} />}
      {tab !== "games" && tab !== "model" && tab !== "parlays" && tab !== "promos" && tab !== "admin" && tab !== "account" && tab !== "bets" && tab !== "injuries" && tab !== "spots" && tab !== "compare" && <Controls filters={filters} onChange={setFilters} games={m.games} showPickControls={tab === "picks"} allMarkets={tab === "hot" ? "All markets" : undefined} />}
      {tab === "picks" && !!picks.data?.length && <HowToRead />}
      {(tab === "picks" || tab === "projections") && selectedGame && <GameWeather game={selectedGame} fit={m.weather} />}

      {tab === "picks" && (picks.isLoading ? <Skeleton kind="rows" /> : picks.error && !picks.data ? <LoadError what="best props" retry={picks.refetch} /> :
        <PicksTable picks={picks.data ?? []} busy={picks.isPlaceholderData} weather={weatherByGame} filters={filters} onFilters={setFilters} onSelect={(p) => select({ id: p.player_id, kind: p.kind, pick: { side: p.side, line: p.line, book: p.book } })} />)}
      {tab === "hot" && <HotBoard board={hot} weather={weatherByGame} onSelect={(p) => select({ id: p.player_id, kind: p.kind, pick: { side: p.side, line: p.line, book: p.book } })} />}
      {tab === "parlays" && (board.loading ? <Skeleton kind="cards" /> : board.failed ? <LoadError what="parlays" retry={board.retry} /> :
        <ParlayTiles book={board.book} parlayCounts={board.parlayCounts} onBook={board.setBook} games={m.games} selectedGames={chosenGames} onGames={setParlayGames} parlays={board.parlays} tdParlays={board.tdParlays}
          ladders={board.ladders} altProps={board.altProps} laddersLoading={board.laddersLoading} starterParlays={board.starterParlays} startersLoading={board.startersLoading} onSelect={(id, kind, pick) => select({ id, kind, pick })} />)}
      {tab === "promos" && (promo === "endzone"
        ? <EndzoneBoard games={m.games} gameId={endzoneGame} onGame={setEndzoneGame} book={debounced.book} pills={promoPills} nextUpdate={nextUpdate}
          onSelect={(id, p) => select({ id, kind: "td", pick: p ? { side: p.side, line: p.line, book: p.book } : undefined })} />
        : <TokenBoard book={debounced.book} marketWeight={debounced.marketWeight} gameIds={m.games.map((g) => g.game_id)} pills={promoPills} nextUpdate={nextUpdate} />)}
      {tab === "injuries" && (injuries.isLoading ? <Skeleton kind="cards" count={2} /> : injuries.error || !injuries.data ? <LoadError what="the injury report" retry={injuries.refetch} /> :
        <Injuries report={injuries.data} games={m.games} onSelect={(id, kind) => select({ id, kind })} />)}
      {tab === "projections" && (projections.isLoading ? <Skeleton kind="rows" /> : projections.error && !projections.data ? <LoadError what="projections" retry={projections.refetch} /> :
        <ProjectionsTable rows={projections.data ?? []} busy={projections.isPlaceholderData} weather={weatherByGame} filters={filters} onSelect={(p) => select({ id: p.player_id, kind: p.kind })} />)}
      {tab === "spots" && <SoftSpots league="nfl" onSelect={(id, kind) => select({ id, kind })} />}
      {tab === "compare" && <PlayerCompare weather={weatherByGame} onSelect={(id, kind) => select({ id, kind })} />}
      {tab === "model" && <ModelCheck meta={m} />}
      {tab === "model" && <Methodology />}
      {tab === "admin" && <Suspense fallback={<Skeleton kind="cards" />}><AdminPage embedded /></Suspense>}
      {tab === "account" && <Preferences books={m.books} />}
      {tab === "bets" && <MyBets />}
      </>}
      </div>
      </main>

      <footer>
        Projections and win chances, not betting advice.{m.can_write && tab !== "model" && <> <a href={href("model")} onClick={routeClick(() => goTab("model"))}>How the numbers work →</a></>}
        <span className="signoff">Bread from air, picks from stats. Built by JD Haber. <a className="footer-link" href={`${BASE}privacy`}>Privacy</a></span>
        <ResponsibleGaming />
      </footer>

      {selected && (
        <Suspense fallback={null}>
          <PlayerDrawer playerId={selected.id} initialKind={selected.kind} pick={selected.pick} marketWeight={debounced.marketWeight}
            book={drawerBook} onBookChange={setDrawerBook} onKindChange={rememberKind}
            onClose={closePlayer} />
        </Suspense>
      )}
      <BetSlip />
    </div>
  );
}
