import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { api, type BacktestStat, type Filters, type Game, type InjuryReport, type Kind } from "./api";
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
import TokenBoard from "./components/TokenBoard";
import TopTiles, { type Tile } from "./components/TopTiles";
import { KIND_LABEL, NBA_KINDS, easternToday, isYesNo, americanOdds, fmtProj, fmtSd, matchup, playLabel, projCaption, signedPct, timeAgo } from "./format";
import { importOrReload } from "./staleChunk";
import { localWhen } from "./sync";
import { filterParams, filtersFromUrl, leaguePath, param, setParams, useSection } from "./route";
import { useDebounced } from "./useDebounced";
import { useParlayBoard } from "./useParlayBoard";
import Skeleton from "./components/Skeleton";
import { BASE } from "./env";
import MyBets from "./components/MyBets";
import Preferences from "./components/Preferences";
import { orderTabs, PREFERRED_GAME_BOOK, useBoardBook, usePref } from "./prefs";

const PlayerDrawer = lazy(() => importOrReload(() => import("./components/PlayerDrawer")));
const AdminPage = lazy(() => importOrReload(() => import("./components/AdminPage")));

type Tab = "games" | "picks" | "hot" | "parlays" | "token" | "injuries" | "projections" | "compare" | "check" | "admin" | "account" | "bets";
const TABS: [Tab, string][] = [["games", "Games"], ["picks", "Best props"], ["parlays", "Parlays"], ["hot", "Who's Hot"], ["token", "Early win"], ["injuries", "Injuries"], ["projections", "Projections"], ["compare", "Compare"],
  ["check", "Model check"], ["admin", "Admin"]];
/** Sections under the top bar's More menu. */
const MORE: Tab[] = ["token", "injuries", "compare", "check", "admin"];
/** Sections only the site owner sees (signed in with an ADMIN_EMAILS account): meta.can_write. */
const OWNER_ONLY: Tab[] = ["check", "admin"];
/** Each section's path under /nba: /nba/props, /nba/parlays, ... (Games is the front page). */
const SLUGS: Record<Tab, string> = { games: "", picks: "props", hot: "hot", parlays: "parlays", token: "early-win", injuries: "injuries", projections: "projections", compare: "compare",
  check: "model", admin: "admin", account: "account", bets: "bets" };
/** Each section's page title (Best props and Parlays add their sportsbook picker). */
const TITLE: Record<Tab, string> = { games: "Games", picks: "Best props", hot: "Who's Hot", parlays: "Parlays", token: "Early-win token", injuries: "Injury report", projections: "Projections",
  compare: "Compare players", check: "Model check", admin: "Admin", account: "Preferences", bets: "My bets" };
const gameFromHash = () => new URLSearchParams(window.location.hash.slice(1)).get("game");
/** "Tue Oct 20" from an Eastern date. */
const day = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

/** The NBA side: the next two game dates' moneylines, points/rebounds/assists props and projections, and injuries. */
export default function NbaApp() {
  const [tab, goSection] = useSection<Tab>(SLUGS, "nba", "games");
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

  // the drawer and ladder read ["meta"] from the cache, so the NBA meta lives under the same key (this league has its own cache)
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.nbaMeta });
  // Model check is the owner's: anyone else following a /nba/model link lands on the games (Admin keeps its own sign-in card)
  const owner = meta.data?.can_write;
  useEffect(() => { if (tab === "check" && owner === false) goSection("games", { replace: true }); }, [tab, owner]); // eslint-disable-line react-hooks/exhaustive-deps
  // odds over six hours old: the slip asks the bettor to check prices before handing legs to a book
  const { setStaleSince } = useBetSlip();
  const oddsAt = meta.data ? latestFetch(meta.data.odds) : undefined;
  const stale = !!oddsAt && oddsLevel(oddsAt) === "stale";
  useEffect(() => setStaleSince(stale ? oddsAt! : null), [stale, oddsAt, setStaleSince]);
  const gameOpen = !!gameId && (!meta.data || meta.data.games.some((g) => g.game_id === gameId));
  useEffect(() => {
    if (!gameId || !meta.data || meta.data.games.some((g) => g.game_id === gameId)) return;
    pushedGame.current = false;
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    setGameId(null);
  }, [gameId, meta.data]);
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
      : tab === "games" || tab === "injuries" || tab === "token" ? dated : {});
  }, [tab, gameId, filters, board.pickedBook, parlayGames, datePick, today, boardBook]);
  const picks = useQuery({ queryKey: ["picks", debounced], queryFn: () => api.picks(debounced), enabled: tab === "picks", placeholderData: (p) => p });
  const slatePicks = useQuery({ queryKey: ["slate-picks", debounced.book, debounced.marketWeight],
    queryFn: () => api.picks({ ...debounced, kind: "all", side: "all", game: "all", q: "", minEv: 0, flagged: false, date: undefined }, 1000), enabled: onSlate });
  const moneylines = useQuery({ queryKey: ["moneylines", debounced.book, debounced.marketWeight], queryFn: () => api.moneylines(debounced.book, debounced.marketWeight),
    enabled: onSlate, placeholderData: (p) => p });
  const injuries = useQuery({ queryKey: ["injuries"], queryFn: api.injuries, enabled: tab === "injuries" || onSlate, refetchInterval: 15 * 60_000 });
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
  const pricedPage = !!openedGame || tab === "games" || tab === "picks" || tab === "hot" || tab === "parlays" || tab === "token";
  const href = (t: Tab) => leaguePath("nba", SLUGS[t]);
  const seasonLabel = m.run ? `${m.run.season - 1}-${String(m.run.season).slice(2)}` : "";

  return (
    <div className="wrap">
      <TopBar tabs={orderTabs(m.can_write ? TABS : TABS.filter(([k]) => !OWNER_ONLY.includes(k)), tabOrder)} home="games" more={MORE} tab={tab} onTab={goTab} href={href} />
      <p className="statusline">
        {m.run ? `${seasonLabel} · ${m.run.slate.map(day).join(" & ")} · projections ${timeAgo(m.run.created_at)}` : "No projections yet"}
        {latestOdds && <> · <Freshness at={latestOdds} /></>}
        {m.auto_sync?.enabled && m.auto_sync.next_at && ` · next update ${localWhen(m.auto_sync.next_at)}`}
      </p>

      {!m.run && <div className="banner">No NBA projections yet. They appear after the next scheduled sync{m.auto_sync?.next_at ? ` (${localWhen(m.auto_sync.next_at)})` : ""}.</div>}
      {m.run && !hasLines && (tab === "games" || tab === "picks" || tab === "parlays" || tab === "token") && (
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
      ) : <>
        <PageHead
          title={tab === "picks" ? <>Best props at <BookSelect inline books={m.books} book={filters.book} allLabel="every book" onChange={(b) => setFilters({ ...filters, book: b })} /></>
            : tab === "hot" ? <>Who's Hot at <BookSelect inline books={m.books} book={filters.book} allLabel="every book" onChange={(b) => setFilters({ ...filters, book: b })} /></>
            : tab === "token" ? <>Early win at <BookSelect inline books={m.books} book={filters.book} allLabel="every book" onChange={(b) => setFilters({ ...filters, book: b })} /></>
            : tab === "parlays" && board.book ? <>Parlays at <BookSelect inline books={m.books} book={board.book} onChange={board.setBook} /></>
            : TITLE[tab]}
          aside={dates.length > 1 && tab !== "compare" && tab !== "check" && tab !== "account" && tab !== "bets" && (
            <select value={date} onChange={(e) => pickDate(e.target.value)} aria-label="Game date">
              {dates.map((d) => <option key={d} value={d}>{d === today ? `Today, ${day(d)}` : day(d)}</option>)}
              <option value="all">All dates</option>
            </select>
          )} />
        <TopTiles tiles={tiles} />
        {tab === "games" && <Games games={dateGames} picks={slatePicks.data ?? []} moneylines={moneylines.data ?? []} injuries={injuries.data} onOpen={openGame} />}
        {(tab === "picks" || tab === "projections" || tab === "hot") && <Controls filters={filters} onChange={setFilters} games={dateGames} showPickControls={tab === "picks"} allMarkets={tab === "hot" ? "All markets" : undefined} />}
      {tab === "picks" && <HowToRead />}
        {tab === "picks" && (picks.isLoading ? <Skeleton kind="rows" /> :
          <PicksTable picks={picks.data ?? []} onSelect={(p) => select(p.player_id, p.kind, { side: p.side, line: p.line, book: p.book })} />)}
        {tab === "hot" && <HotBoard board={hot} onSelect={(p) => select(p.player_id, p.kind, { side: p.side, line: p.line, book: p.book })} />}
        {tab === "token" && <TokenBoard book={debounced.book} marketWeight={debounced.marketWeight} gameIds={dateGames.map((g) => g.game_id)} />}
        {tab === "parlays" && (board.loading ? <Skeleton kind="cards" /> :
          <ParlayTiles book={board.book} parlayCounts={board.parlayCounts} onBook={board.setBook} games={dateGames} selectedGames={chosenGames} onGames={setParlayGames} parlays={board.parlays}
            tdParlays={board.tdParlays} ladders={board.ladders} altProps={board.altProps} laddersLoading={board.laddersLoading} onSelect={select} />)}
        {tab === "injuries" && (injuries.isLoading ? <Skeleton kind="cards" count={2} /> : injuries.error || !injuries.data ? <p className="err">Couldn't load the injury report.</p> :
          <NbaInjuries report={injuries.data} games={dateGames} onSelect={select} />)}
        {tab === "projections" && (projections.isLoading ? <Skeleton kind="rows" /> :
          <ProjectionsTable nba rows={projections.data ?? []} onSelect={(p) => select(p.player_id, p.kind)} />)}
        {tab === "compare" && <PlayerCompare onSelect={select} />}
        {tab === "check" && <div className="stack"><TrackRecord nba /><GradesCard nba /><NbaBacktest backtest={m.backtest} /><Methodology league="nba" /></div>}
        {tab === "admin" && <Suspense fallback={<Skeleton kind="cards" />}><AdminPage embedded /></Suspense>}
        {tab === "account" && <Preferences books={m.books} />}
        {tab === "bets" && <MyBets />}
      </>}

      </div>

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
      {!shown.length ? <div className="card empty">No games on the slate.</div> : (
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
