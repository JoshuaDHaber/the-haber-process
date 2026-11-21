import React, { Suspense, lazy, useEffect, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import AuthLayer from "./components/SignIn";
import SignInLink from "./components/SignInLink";
import { BASE } from "./env";
import { importOrReload } from "./staleChunk";
import { setAllowedBooks } from "./api";
import { leagueFromPath, leaguePath } from "./route";
import { BetSlipProvider, useBetSlip } from "./BetSlipContext";
import { holdSharedText } from "./bets";
import { allowedBooks } from "./legalBooks";
import { LeagueContext } from "./LeagueContext";
import { getLeague, setLeague as storeLeague, type League } from "./league";
import { leagueClients as clients } from "./queryClients";
import { restoreCache, saveCache } from "./persistCache";
import { prefetchLeague } from "./slateQueries";
import { loadAuth } from "./auth";
import "./styles.css";

const Privacy = lazy(() => importOrReload(() => import("./components/Privacy")));
/** Pages outside both leagues: where the emailed sign-in link lands, and the privacy policy. */
const PAGES: Record<string, () => React.ReactNode> = {
  [`${BASE}signin`]: () => <SignInLink />,
  [`${BASE}privacy`]: () => <Suspense fallback={null}><Privacy /></Suspense>,
};

const other = (l: League): League => (l === "nba" ? "nfl" : "nba");
const whenIdle = (f: () => void) => ("requestIdleCallback" in window ? window.requestIdleCallback(f, { timeout: 4000 }) : setTimeout(f, 2000));

function Root() {
  const [league, setLeagueState] = useState<League>(getLeague);
  // only the sportsbooks legal where the bettor is: set before any query runs, and every cached query is refetched when it changes
  const { state, allBooks } = useBetSlip();
  const allowed = allowedBooks(state, allBooks);
  setAllowedBooks(allowed);
  const booksKey = allowed?.join() ?? "*";
  const books = useRef(booksKey);
  books.current = booksKey;
  // the last games pages this browser saw, shown at once while they refresh; then kept saved as they change
  useState(() => (["nfl", "nba"] as League[]).forEach((l) => restoreCache(clients[l], l, booksKey)));
  useEffect(() => {
    const stops = (["nfl", "nba"] as League[]).map((l) => saveCache(clients[l], l, () => books.current));
    return () => stops.forEach((s) => s());
  }, []);
  // once this page has loaded, the other league in the background
  useEffect(() => {
    const ready = () => whenIdle(() => prefetchLeague(other(getLeague())));
    if (document.readyState === "complete") ready(); else window.addEventListener("load", ready, { once: true });
  }, []);
  const lastBooks = useRef(booksKey);
  useEffect(() => {
    if (lastBooks.current === booksKey) return;
    lastBooks.current = booksKey;
    Object.values(clients).forEach((c) => c.resetQueries());
  }, [booksKey]);
  const setLeague = (l: League) => {
    storeLeague(l);
    // each league has its own sections and games: switching lands on the other league's front page
    window.history.pushState(null, "", leaguePath(l));
    window.scrollTo({ top: 0 });
    setLeagueState(l);
  };
  // back and forward can cross between /nba/... and the NFL's pages
  useEffect(() => {
    const sync = () => {
      const l = leagueFromPath();
      if (l && l !== getLeague()) { storeLeague(l); setLeagueState(l); }
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  return (
    <LeagueContext.Provider value={{ league, setLeague }}>
      <QueryClientProvider client={clients[league]} key={league}>
        {PAGES[window.location.pathname.replace(/\/$/, "")]?.() ?? <App />}
        <AuthLayer />
      </QueryClientProvider>
    </LeagueContext.Provider>
  );
}

void loadAuth();    // now, alongside the first render: a signed-in visit's board waits for the account's sportsbook
holdSharedText();   // a slip from the iPhone Shortcut (/bets#slip=...), before the app reads the address
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BetSlipProvider>
      <Root />
    </BetSlipProvider>
  </React.StrictMode>,
);

// installable on Chrome/Android (and lets it open offline); dev skips it so the service worker never serves stale code
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => { navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`).catch(() => {}); });
}
