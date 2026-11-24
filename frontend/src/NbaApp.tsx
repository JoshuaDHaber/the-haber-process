import { IconCalendarOff } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { api, type BacktestStat, type Filters, type Game, type InjuryReport, type Kind, type NbaMeta } from "./api";
import { useSettingsReady } from "./auth";
import { metaQuery, slateQueries } from "./slateQueries";
import BookSelect from "./components/BookSelect";
import GamePills from "./components/GamePills";
import Freshness, { StaleBanner, latestFetch, oddsLevel } from "./components/Freshness";
import Methodology from "./components/Methodology";
import { routeClick } from "./components/NavLink";
import { useBetSlip } from "./BetSlipContext";
import { type DrawerPick, useSelectedPlayer } from "./playerHash";
import HotBoard, { hotTiles, useHotBoard } from "./components/HotBoard";
import HowToRead from "./components/HowToRead";
import PageHead from "./components/PageHead";
import ResponsibleGaming from "./components/ResponsibleGaming";
import TopBar from "./components/TopBar";
import BetSlip from "./components/BetSlip";
import Controls from "./components/Controls";
import Games from "./components/Games";
import GradesCard, { TrackRecord } from "./components/GradesCard";
import { GameInjuries } from "./components/Injuries";
import NbaGameOverview from "./components/NbaGameOverview";
import ParlayTiles from "./components/ParlayTiles";
import PicksTable from "./components/PicksTable";
import PlayerCompare from "./components/PlayerCompare";
import ProjectionsTable from "./components/ProjectionsTable";
import SoftSpots from "./components/SoftSpots";
import EmptyState, { LoadError } from "./components/EmptyState";
import KingBoard from "./components/KingBoard";
import TokenBoard from "./components/TokenBoard";
import TopTiles, { type Tile } from "./components/TopTiles";
import { KIND_LABEL, NBA_KINDS, easternToday, isYesNo, americanOdds, fmtProj, fmtSd, matchup, playLabel, projCaption, signedPct, timeAgo } from "./format";
import { importOrReload } from "./staleChunk";
import { localWhen } from "./sync";
import { filterParams, filtersFromUrl, leaguePath, param, setParams, useDocumentTitle, useSection } from "./route";
import NotFound from "./components/NotFound";
import { useDebounced } from "./useDebounced";
import { useParlayBoard } from "./useParlayBoard";
import Skeleton from "./components/Skeleton";
import { BASE } from "./env";
import MyBets from "./components/MyBets";
import Preferences from "./components/Preferences";
import { orderTabs, PREFERRED_GAME_BOOK, useBoardBook, usePref } from "./prefs";

const PlayerDrawer = lazy(() => importOrReload(() => import("./components/PlayerDrawer")));
const AdminPage = lazy(() => importOrReload(() => import("./components/AdminPage")));

type Tab = "games" | "picks" | "hot" | "parlays" | "promos" | "injuries" | "spots" | "projections" | "compare" | "check" | "admin" | "account" | "bets";
const TABS: [Tab, string][] = [["games", "Games"], ["picks", "Best props"], ["parlays", "Parlays"], ["hot", "Who's Hot"], ["promos", "Promos"], ["injuries", "Injuries"], ["spots", "Soft spots"], ["projections", "Projections"], ["compare", "Compare"],
  ["check", "Model check"], ["admin", "Admin"]];
/** Sections under the top bar's More menu. */
const MORE: Tab[] = ["promos", "injuries", "spots", "compare", "check", "admin"];
/** Sections only the site owner sees (signed in with an ADMIN_EMAILS account): meta.can_write. */
const OWNER_ONLY: Tab[] = ["check", "admin"];
/** Each section's path under /nba: /nba/props, /nba/parlays, ... (Games is the front page). */
const SLUGS: Record<Tab, string> = { games: "", picks: "props", hot: "hot", parlays: "parlays", promos: "promos", injuries: "injuries", spots: "soft-spots", projections: "projections", compare: "compare",
  check: "model", admin: "admin", account: "account", bets: "bets" };
/** Each section's page title (Best props and Parlays add their sportsbook picker). */
const TITLE: Record<Tab, string> = { games: "Games", picks: "Best props", hot: "Who's Hot", parlays: "Parlays", promos: "Promos", injuries: "Injury report", spots: "Soft spots", projections: "Projections",
  compare: "Compare players", check: "Model check", admin: "Admin", account: "Preferences", bets: "My bets" };
/** The book promotions the Promos page prices, each behind a pill. */
type Promo = "early-win" | "king";
const PROMOS: [Promo, string][] = [["early-win", "Early win"], ["king", "King of the Court"]];
const gameFromHash = () => new URLSearchParams(window.location.hash.slice(1)).get("game");
/** "Tue Oct 20" from an Eastern date. */
const day = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

/** The NBA side: the next two game dates' moneylines, points/rebounds/assists props and projections, and injuries. */
export default function NbaApp() {
  const [tab, goSection, knownPath] = useSection<Tab>(SLUGS, "nba", "games");
  const [gameId, setGameId] = useState<string | null>(gameFromHash);
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
  const { selected, open: openPlayer, close: closePlayer, remember: rememberKind } = useSelectedPlayer();
  // the game page's sportsbook: a preference, remembered in this browser and the account (prefs.ts)
  const [gameBook, setGameBook] = usePref("game_book");
  const [tabOrder] = usePref("tab_order");
  // which game date to show: null follows today (or the next date with games), "all" shows both
  const [datePick, setDatePick] = useState<string | null>(() => param("date"));
  // which promotion the Promos page shows (Early win by default)
  const [promo, setPromo] = useState<Promo>(() => (param("promo") === "king" ? "king" : "early-win"));

  // the games page's four reads go out as one request (slateQueries.ts); on a signed-in visit they wait for the account's
  // sportsbook, so the board isn't fetched at a stale book first
  const settingsReady = useSettingsReady();
  const meta = useQuery({ ...metaQuery<NbaMeta>("nba"), enabled: settingsReady || tab !== "games" || !!gameId });
  // the slate as this visit fetched it, or still fresh from before (not a copy saved on an earlier visit, which may
  // lack a game added since): only that sends a game link or the owner's page elsewhere
  const metaFresh = meta.isFetchedAfterMount || (!!meta.data && !meta.isStale);
  useDocumentTitle(!knownPath ? "Page not found" : gameId ? meta.data?.games.find((g) => g.game_id === gameId)?.label : TITLE[tab], "NBA");
  // Model check is the owner's: anyone else following a /nba/model link lands on the games (Admin keeps its own sign-in card)
  const owner = meta.data?.can_write;
  useEffect(() => { if (tab === "check" && metaFresh && owner === false) goSection("games", { replace: true }); }, [tab, owner, metaFresh]); // eslint-disable-line react-hooks/exhaustive-deps
  // odds over six hours old: the slip asks the bettor to check prices before handing legs to a book
  const { setStaleSince } = useBetSlip();
  const oddsAt = meta.data ? latestFetch(meta.data.odds) : undefined;
  const stale = !!oddsAt && oddsLevel(oddsAt) === "stale";
  useEffect(() => setStaleSince(stale ? oddsAt! : null), [stale, oddsAt, setStaleSince]);
  const gameOpen = !!gameId && (!metaFresh || !meta.data || meta.data.games.some((g) => g.game_id === gameId));
  useEffect(() => {
    if (!gameId || !metaFresh || !meta.data || meta.data.games.some((g) => g.game_id === gameId)) return;
    pushedGame.current = false;
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    setGameId(null);
  }, [gameId, meta.data, metaFresh]);
  const dates = useMemo(() => [...new Set((meta.data?.games ?? []).map((g) => g.gameday))].sort(), [meta.data]);
  const today = easternToday();
  const date = datePick === "all" || (datePick && dates.includes(datePick)) ? datePick : dates.includes(today) ? today : dates[0] ?? "all";
  const dateGames = useMemo(() => (meta.data?.games ?? []).filter((g) => date === "all" || g.gameday === date), [meta.data, date]);
  const pickDate = (d: string) => { setDatePick(d); setParlayGames([]); if (filters.game !== "all") setFilters({ ...filters, game: "all" }); };
  const datedFilters = useMemo(() => ({ ...filters, date: date === "all" ? undefined : date }), [filters, date]);
  const debounced = useDebounced(datedFilters, 250);
  const onSlate = tab === "games" && !gameOpen;
  const [parlayGames, setParlayGames] = useState<string[]>(() => (tab === "parlays" && param("games")?.split(",").filter(Boolean)) || []);
  const chosenGames = useMemo(() => parlayGames.filter((id) => dateGames.some((g) => g.game_id === id)), [parlayGames, dateGames]);
  const board = useParlayBoard(meta.data?.books ?? [], chosenGames, tab === "parlays" && !gameOpen, undefined,
    date === "all" ? undefined : dateGames.map((g) => g.game_id));
  // a shared parlays link names its book
  useEffect(() => { const b = param("book"); if (tab === "parlays" && b) board.setBook(b); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // keep the query string in step with what's on screen, so the URL always reopens this view
  useEffect(() => {
    if (gameId) return;
    const dated = { date: datePick && datePick !== today ? datePick : null };
    setParams(tab === "picks" || tab === "projections" || tab === "hot" ? { ...filterParams(filters, tab !== "projections", boardBook), ...(tab === "hot" && { side: null }), ...dated }
      : tab === "parlays" ? { book: board.pickedBook, games: parlayGames.join(",") || null, ...dated }
      : tab === "games" || tab === "injuries" ? dated
      : tab === "promos" ? { ...dated, promo: promo === "early-win" ? null : promo } : {});
  }, [tab, gameId, filters, board.pickedBook, parlayGames, datePick, today, boardBook, promo]);
  const picks = useQuery({ queryKey: ["picks", debounced], queryFn: () => api.picks(debounced), enabled: tab === "picks", placeholderData: (p) => p });
  // the filters follow a new account book a render late (the effect above), so the board's book is worked out the same way
  const book = filters.book === lastBoardBook.current ? boardBook : filters.book;
  const slate = slateQueries<NbaMeta>("nba", book, debounced.marketWeight);
  const slatePicks = useQuery({ ...slate.picks, enabled: onSlate && settingsReady });
  const moneylines = useQuery({ ...slate.moneylines, enabled: onSlate && settingsReady, placeholderData: (p) => p });
  const injuries = useQuery({ ...slate.injuries, enabled: tab === "injuries" || (onSlate && settingsReady), refetchInterval: 15 * 60_000 });
  const hot = useHotBoard(debounced, tab === "hot");
  const projections = useQuery({ queryKey: ["projections", debounced], queryFn: () => api.projections(debounced), enabled: tab === "projections", placeholderData: (p) => p });

  if (meta.isLoading) return <div className="wrap"><Skeleton kind="page" /></div>;
  if (meta.error || !meta.data) return (
    <div className="wrap">
      <TopBar />
      <p className="err">The reaction stalled: can't reach the NBA API right now. Try again in a minute.</p>
    </div>
  );
  const m = meta.data;
  const select = (id: string, kind: Kind, pick?: DrawerPick) => openPlayer({ id, kind, pick });
  const tiles: Tile[] = tab === "picks"
    // by Kelly, not EV: EV makes a 1-point edge on a +2500 long shot look like the best play on the board
    ? [...(picks.data ?? [])].sort((a, b) => b.kelly - a.kelly).slice(0, 3).map((p) => ({
      key: `${p.player_id}-${p.kind}-${p.side}`, label: KIND_LABEL[p.kind], name: p.name, meta: `${p.pos} ${p.team} ${matchup(p.opp, p.home)}`,
      value: <>{playLabel(p.kind, p.side, p.line)} <em>{signedPct(p.ev)} EV</em></>, caption: `${americanOdds(p.odds)} at ${p.book}`,
      onClick: () => select(p.player_id, p.kind, { side: p.side, line: p.line, book: p.book }),
    }))
    : tab === "projections"
      ? (filters.kind === "all" ? NBA_KINDS.map((k) => (projections.data ?? []).filter((p) => p.kind === k).sort((a, b) => b.mu - a.mu)[0])
        : [...(projections.data ?? [])].sort((a, b) => b.mu - a.mu).slice(0, 3)).filter(Boolean).map((p) => ({
        key: `${p.player_id}-${p.kind}`, label: filters.kind === "all" ? `Top ${KIND_LABEL[p.kind].toLowerCase()}` : KIND_LABEL[p.kind], name: p.name,
        meta: `${p.pos} ${p.team} ${matchup(p.opp, p.home)}`, value: <>{fmtProj(p.kind, p.mu)} {!isYesNo(p.kind) && <em>{fmtSd(p.kind, p.sd)}</em>}</>, caption: projCaption(p.kind),
        onClick: () => select(p.player_id, p.kind),
      }))
      : tab === "hot" ? hotTiles(hot.query.data ?? [], (p) => select(p.player_id, p.kind, { side: p.side, line: p.line, book: p.book }))
      : [];
  const openedGame = gameId ? m.games.find((g) => g.game_id === gameId) : undefined;
  const pageBook = gameBook === "all" || m.books.includes(gameBook) ? gameBook : m.books.includes(PREFERRED_GAME_BOOK) ? PREFERRED_GAME_BOOK : m.books[0] ?? "all";
  const drawerBook = openedGame ? pageBook : filters.book;
  const setDrawerBook = (b: string) => (openedGame ? setGameBook(b) : setFilters({ ...filters, book: b }));
  const hasLines = Object.keys(m.odds).length > 0;
  const hasProps = NBA_KINDS.some((k) => m.odds[k]?.lines);
  const latestOdds = oddsAt;
  const pricedPage = knownPath && (!!openedGame || tab === "games" || tab === "picks" || tab === "hot" || tab === "parlays" || tab === "promos");
  const href = (t: Tab) => leaguePath("nba", SLUGS[t]);
  // when the next scheduled sync runs, for empty boards to say when to check back
  const nextUpdate = m.auto_sync?.enabled ? m.auto_sync.next_at : null;
  const promoPills = (
    <div className="seg small" role="group" aria-label="Promotion">
      {PROMOS.map(([k, label]) => <button key={k} className={promo === k ? "on" : ""} aria-pressed={promo === k} onClick={() => setPromo(k)}>{label}</button>)}
    </div>
  );
  const seasonLabel = m.run ? `${m.run.season - 1}-${String(m.run.season).slice(2)}` : "";

  return (
    <div className="wrap">
      <TopBar tabs={orderTabs(m.can_write ? TABS : TABS.filter(([k]) => !OWNER_ONLY.includes(k)), tabOrder)} home="games" more={MORE} tab={knownPath ? tab : undefined} onTab={goTab} href={href} />
      <main id="main" tabIndex={-1}>
      <p className="statusline">
        {m.run ? `${seasonLabel} · ${m.run.slate.map(day).join(" & ")} · projections ${timeAgo(m.run.created_at)}` : "No projections yet"}
        {latestOdds && <> · <Freshness at={latestOdds} /></>}
        {m.auto_sync?.enabled && m.auto_sync.next_at && ` · next update ${localWhen(m.auto_sync.next_at)}`}
      </p>

      {!m.run && <div className="banner">No NBA projections yet. They appear after the next scheduled sync{m.auto_sync?.next_at ? ` (${localWhen(m.auto_sync.next_at)})` : ""}.</div>}
      {m.run && !hasLines && (tab === "games" || tab === "picks" || tab === "parlays" || tab === "promos") && (
        <div className="banner">{m.has_odds_key ? "No sportsbook lines are loaded yet. Moneylines come with every sync; player props are fetched the day of each game, once books post them."
          : "No NBA odds key is set (ODDS_API_KEY_NBA), so there are projections but no lines."}</div>
      )}
      {m.run && hasLines && !hasProps && (tab === "picks" || tab === "hot" || tab === "parlays") && (
        <div className="banner">Player props aren't loaded yet. They're fetched the day of each game (10 AM, 2 PM and 6 PM ET syncs).</div>
      )}


      {stale && pricedPage && <StaleBanner at={latestOdds!} />}

      <div className={stale ? "odds-stale" : undefined}>
      {openedGame ? (
        <NbaGameOverview key={openedGame.game_id} game={openedGame} books={m.books} book={pageBook} onBook={setGameBook} marketWeight={debounced.marketWeight}
          onBack={() => (pushedGame.current ? window.history.back() : openGame(null))} onSelect={select} />
      ) : !knownPath ? <NotFound home={href("games")} homeLabel="Go to the games" onHome={() => goTab("games")} /> : <>
        <PageHead
          title={tab === "picks" ? <>Best props at <BookSelect inline books={m.books} book={filters.book} allLabel="every book" onChange={(b) => setFilters({ ...filters, book: b })} /></>
            : tab === "hot" ? <>Who's Hot at <BookSelect inline books={m.books} book={filters.book} allLabel="every book" onChange={(b) => setFilters({ ...filters, book: b })} /></>
            : tab === "promos" ? <>Promos at <BookSelect inline books={m.books} book={filters.book} allLabel="every book" onChange={(b) => setFilters({ ...filters, book: b })} /></>
            : tab === "parlays" && board.book ? <>Parlays at <BookSelect inline books={m.books} book={board.book} onChange={board.setBook} /></>
            : TITLE[tab]}
          aside={dates.length > 1 && tab !== "compare" && tab !== "check" && tab !== "account" && tab !== "bets" && (
            <select value={date} onChange={(e) => pickDate(e.target.value)} aria-label="Game date">
              {dates.map((d) => <option key={d} value={d}>{d === today ? `Today, ${day(d)}` : day(d)}</option>)}
              <option value="all">All dates</option>
            </select>
          )} />
        <TopTiles tiles={tiles} />
        {tab === "games" && <Games games={dateGames} picks={slatePicks.data ?? []} moneylines={moneylines.data ?? []} injuries={injuries.data} onOpen={openGame} nextUpdate={nextUpdate} />}
        {(tab === "picks" || tab === "projections" || tab === "hot") && <Controls filters={filters} onChange={setFilters} games={dateGames} showPickControls={tab === "picks"} allMarkets={tab === "hot" ? "All markets" : undefined} />}
      {tab === "picks" && !!picks.data?.length && <HowToRead />}
        {tab === "picks" && (picks.isLoading ? <Skeleton kind="rows" /> : picks.error && !picks.data ? <LoadError what="best props" retry={picks.refetch} /> :
          <PicksTable picks={picks.data ?? []} busy={picks.isPlaceholderData} filters={filters} onFilters={setFilters} onSelect={(p) => select(p.player_id, p.kind, { side: p.side, line: p.line, book: p.book })} />)}
        {tab === "hot" && <HotBoard board={hot} onSelect={(p) => select(p.player_id, p.kind, { side: p.side, line: p.line, book: p.book })} />}
        {tab === "promos" && (promo === "king"
          ? <KingBoard gameday={date === "all" ? undefined : date} book={debounced.book} pills={promoPills} games={dateGames} nextUpdate={nextUpdate}
            onSelect={(pid, p) => select(pid, "pra", p ? { side: p.side, line: p.line, book: p.book } : undefined)} />
          : <TokenBoard book={debounced.book} marketWeight={debounced.marketWeight} gameIds={dateGames.map((g) => g.game_id)} pills={promoPills} nextUpdate={nextUpdate} />)}
        {tab === "parlays" && (board.loading ? <Skeleton kind="cards" /> : board.failed ? <LoadError what="parlays" retry={board.retry} /> :
          <ParlayTiles book={board.book} parlayCounts={board.parlayCounts} onBook={board.setBook} games={dateGames} selectedGames={chosenGames} onGames={setParlayGames} parlays={board.parlays}
            tdParlays={board.tdParlays} ladders={board.ladders} altProps={board.altProps} laddersLoading={board.laddersLoading} starterParlays={board.starterParlays} startersLoading={board.startersLoading} onSelect={select} />)}
        {tab === "injuries" && (injuries.isLoading ? <Skeleton kind="cards" count={2} /> : injuries.error || !injuries.data ? <LoadError what="the injury report" retry={injuries.refetch} /> :
          <NbaInjuries report={injuries.data} games={dateGames} onSelect={select} />)}
        {tab === "spots" && <SoftSpots league="nba" onSelect={(id, kind) => select(id, kind)} />}
        {tab === "projections" && (projections.isLoading ? <Skeleton kind="rows" /> : projections.error && !projections.data ? <LoadError what="projections" retry={projections.refetch} /> :
          <ProjectionsTable nba rows={projections.data ?? []} busy={projections.isPlaceholderData} filters={filters} onSelect={(p) => select(p.player_id, p.kind)} />)}
        {tab === "compare" && <PlayerCompare onSelect={select} />}
        {tab === "check" && <div className="stack"><TrackRecord nba /><GradesCard nba /><NbaBacktest backtest={m.backtest} /><Methodology league="nba" /></div>}
        {tab === "admin" && <Suspense fallback={<Skeleton kind="cards" />}><AdminPage embedded /></Suspense>}
        {tab === "account" && <Preferences books={m.books} />}
        {tab === "bets" && <MyBets />}
      </>}

      </div>
      </main>

      <footer>
        Projections and win chances, not betting advice.{m.can_write && tab !== "check" && <> <a href={href("check")} onClick={routeClick(() => goTab("check"))}>How the numbers work →</a></>}
        <span className="signoff">Bread from air, picks from stats. Built by JD Haber. <a className="footer-link" href={`${BASE}privacy`}>Privacy</a></span>
        <ResponsibleGaming />
      </footer>

      {selected && (
        <Suspense fallback={null}>
          <PlayerDrawer playerId={selected.id} initialKind={selected.kind} pick={selected.pick} marketWeight={debounced.marketWeight} book={drawerBook} onBookChange={setDrawerBook}
            onKindChange={rememberKind} onClose={closePlayer} />
        </Suspense>
      )}
      <BetSlip />
    </div>
  );
}

/** Every slate game's injury report from ESPN, both teams side by side. */
function NbaInjuries({ report, games, onSelect }: { report: InjuryReport; games: Game[]; onSelect: (id: string, kind: Kind) => void }) {
  const [game, setGame] = useState("all");
  const shown = games.filter((g) => game === "all" || g.game_id === game);
  return (
    <div className="stack">
      <section className="controls" aria-label="Injury filters">
        <GamePills games={games} selected={game === "all" ? [] : [game]} onChange={(ids) => setGame(ids[0] ?? "all")} label="Game" />
        <p className="td-note nba-note">
          <b>Out</b> players aren't projected. <b>Day-to-day</b> players are, and are often decided close to tip-off.
          {report.fetched_at && <> Checked {timeAgo(report.fetched_at)} (ESPN).</>} Players with projections link to them.
        </p>
      </section>
      {!shown.length ? <EmptyState icon={IconCalendarOff} title="No games on the slate">Injury reports show up here once the games are set.</EmptyState> : (
        <div className="inj-grid">{shown.map((g) => <GameInjuries key={g.game_id} game={g} report={report} onSelect={onSelect} />)}</div>
      )}
    </div>
  );
}

/** The walk-forward backtest from the latest run (app/nba/model.py): the model's average miss against a plain last-10 average. */
function NbaBacktest({ backtest }: { backtest: Record<string, BacktestStat> }) {
  const rows = NBA_KINDS.filter((k) => backtest[k]);
  if (!rows.length) return null;
  return (
    <div className="card prose">
      <h3>Backtest</h3>
      <p>Every game since last October, projected from only the games before its week. The books' lines are sharper than either column; the graded picks above are the real test.</p>
      <table className="calib" aria-label="NBA backtest">
        <thead><tr><th>Stat</th><th>Games</th><th>Model MAE</th><th>Last-10 MAE</th><th>Bias</th></tr></thead>
        <tbody>
          {rows.map((k) => {
            const b = backtest[k];
            const d = b.metric === "brier" ? 4 : 2;   // double-doubles: Brier score of the chance, against the player's last-10 rate
            return (
              <tr key={k}><td>{KIND_LABEL[k]}{b.metric === "brier" && <span className="mut"> (Brier)</span>}</td><td>{b.n.toLocaleString()}</td>
                <td className={b.mae_model <= b.mae_naive ? "good" : ""}>{b.mae_model.toFixed(d)}</td><td>{b.mae_naive.toFixed(d)}</td>
                <td>{b.bias >= 0 ? "+" : "−"}{Math.abs(b.bias).toFixed(d)}</td></tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
