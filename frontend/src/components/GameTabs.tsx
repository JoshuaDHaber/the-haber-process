import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Game } from "../api";
import { kickoff } from "../format";
import { useOverflowFade } from "../useOverflowFade";
import BookSelect from "./BookSelect";

// A game page is split into tabs (Overview, Props, Parlays, ...) under a header that sticks below the top bar, so the page
// is a few screens per tab instead of one long scroll. The tab lives in the hash next to #game (#game=<id>&view=props), so
// a tab can be linked and a reload keeps it; switching tabs replaces the history entry, so Back still returns to the slate.

export type GameView = "overview" | "props" | "parlays" | "proj" | "script" | "inj";
export const VIEW_LABEL: Record<GameView, string> = {
  overview: "Overview", props: "Props", parlays: "Parlays", proj: "Projections", script: "Script", inj: "Injuries",
};

export function viewFromHash(views: readonly GameView[], hash = window.location.hash): GameView {
  const v = new URLSearchParams(hash.slice(1)).get("view") as GameView | null;
  return v && views.includes(v) ? v : "overview";
}

/** The hash with the view swapped in (Overview, the default, drops the key), keeping #game and the player drawer's keys. */
export function hashWithView(view: GameView, hash = window.location.hash) {
  const h = new URLSearchParams(hash.slice(1));
  if (view === "overview") h.delete("view"); else h.set("view", view);
  const str = h.toString();
  return str ? `#${str}` : "";
}

/** The open tab, kept in the URL. Back and forward (the player drawer opening and closing) re-read it. */
export function useGameView(views: readonly GameView[]): [GameView, (v: GameView) => void] {
  const [view, setView] = useState<GameView>(() => viewFromHash(views));
  useEffect(() => {
    const sync = () => setView(viewFromHash(views));
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, [views]);
  const go = (v: GameView) => {
    window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search + hashWithView(v));
    setView(v);
  };
  return [view, go];
}

/** Keeps --topbar-h on the page at the sticky top bar's height (it wraps to two lines on tablets and grows with a phone's
 *  notch), so the game header sticks right under it. */
function useTopbarHeight() {
  useEffect(() => {
    const bar = document.querySelector<HTMLElement>(".topbar");
    if (!bar) return;
    const set = () => document.documentElement.style.setProperty("--topbar-h", `${bar.offsetHeight}px`);
    set();
    const ro = new ResizeObserver(set);
    ro.observe(bar);
    return () => ro.disconnect();
  }, []);
}

interface HeaderProps {
  game: Game;
  books: string[];
  book: string;
  onBook: (book: string) => void;
  onBack: () => void;
  views: readonly GameView[];
  view: GameView;
  onView: (v: GameView) => void;
  /** A count beside a tab's label ("Props 8"), when there is one worth showing. */
  counts?: Partial<Record<GameView, number>>;
}

/** The game page's sticky header: back to the slate, the game and its kickoff, the page's sportsbook, and the tabs.
 *  On a phone only the tabs stick, so the header costs one row of the screen once scrolled. */
export function GameHeader({ game, books, book, onBook, onBack, views, view, onView, counts }: HeaderProps) {
  useTopbarHeight();
  const { ref, onScroll, className: fade } = useOverflowFade<HTMLDivElement>("[aria-selected=true]", [view]);
  const keys = (e: KeyboardEvent) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    const to = e.key === "Home" ? 0 : e.key === "End" ? views.length - 1 : step ? (views.indexOf(view) + step + views.length) % views.length : -1;
    if (to < 0) return;
    e.preventDefault();
    onView(views[to]);
  };
  // arrow keys move focus with the selection, once the new tab has rendered (never when a tab changed some other way)
  useEffect(() => {
    if (ref.current?.contains(document.activeElement)) ref.current.querySelector<HTMLElement>("[aria-selected=true]")?.focus();
  }, [view, ref]);
  return (
    <div className="go-bar">
      <div className="go-head">
        <button className="link go-back" onClick={onBack}>← All games</button>
        <h2 className="go-title"><b>{game.label}</b> <span className="mut">{kickoff(game.gameday, game.gametime)}</span></h2>
        <BookSelect books={books} book={book} onChange={onBook} allLabel="All books (best price)" ariaLabel="Sportsbook for this page" />
      </div>
      <div ref={ref} className={`go-tabs${fade}`} role="tablist" aria-label={`${game.label} sections`} onScroll={onScroll} onKeyDown={keys}>
        {views.map((v) => (
          <button key={v} id={`go-tab-${v}`} role="tab" aria-selected={v === view} aria-controls="go-panel" tabIndex={v === view ? 0 : -1}
            className={v === view ? "on" : ""} onClick={() => onView(v)}>
            {VIEW_LABEL[v]}{counts?.[v] ? <small className="go-tab-n"><span className="sr-only">, </span>{counts[v]}</small> : null}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The open tab's panel. Switching tabs from further down the page brings the new tab's top up under the sticky header,
 *  instead of leaving the reader midway down a different list. */
export function GamePanel({ view, children }: { view: GameView; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  useLayoutEffect(() => {
    if (first.current) { first.current = false; return; }
    const el = ref.current, tabs = document.querySelector<HTMLElement>(".go-tabs");
    if (!el || !tabs) return;
    const stuckAt = tabs.getBoundingClientRect().bottom;
    const top = el.getBoundingClientRect().top + window.scrollY - stuckAt - 8;
    if (window.scrollY > top) window.scrollTo({ top });
  }, [view]);
  return <div ref={ref} id="go-panel" className="go-panel stack" role="tabpanel" aria-labelledby={`go-tab-${view}`}>{children}</div>;
}

/** A block of the Overview: a heading, a short preview, and a link to the tab with all of it. */
export function OverviewBlock({ title, sub, more, onMore, children }: { title: string; sub?: ReactNode; more?: string; onMore?: () => void; children: ReactNode }) {
  return (
    <section className="go-sec go-ov">
      <div className="go-ov-h">
        <h2 className="parlay-h">{title}{sub && <> <small>{sub}</small></>}</h2>
        {more && onMore && <button type="button" className="link go-see" onClick={onMore}>{more} →</button>}
      </div>
      {children}
    </section>
  );
}
