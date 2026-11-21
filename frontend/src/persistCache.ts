import { dehydrate, hydrate, type QueryClient } from "@tanstack/react-query";

/** The games page's queries: kept in this browser so a visit, or the first switch to the other league, shows the last
 *  slate at once while it refreshes. The rest of the cache is per visit. */
const KEEP = new Set(["meta", "slate-picks", "moneylines", "injuries"]);
/** Older than this, a saved slate is left out: games will have started and the odds moved. */
export const MAX_AGE = 6 * 3600_000;
/** A new build's data may not have the shape the old one saved (the bundle's address changes with every build). */
const BUILD = import.meta.url;
const key = (name: string) => `query-cache:${name}`;

interface Saved { build: string; books: string; at: number; state: ReturnType<typeof dehydrate> }

/** Put a saved cache back, if it's from this build, recent, and priced for the same sportsbooks (legal ones differ by state). */
export function restoreCache(client: QueryClient, name: string, books: string, now = Date.now()): boolean {
  try {
    const raw = localStorage.getItem(key(name));
    const saved: Saved | null = raw ? JSON.parse(raw) : null;
    if (!saved || saved.build !== BUILD || saved.books !== books || now - saved.at > MAX_AGE) return false;
    hydrate(client, saved.state);
    return true;
  } catch { return false; }   // storage blocked or a damaged copy: start empty
}

/** Keep `client`'s games-page queries saved as they change (a second after the last change). books(): the current
 *  sportsbooks key. Only each query's newest version is kept (one book's board, not every book looked at). */
export function saveCache(client: QueryClient, name: string, books: () => string): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const save = () => {
    const newest = new Map<unknown, number>();
    for (const q of client.getQueryCache().getAll()) {
      const k = q.queryKey[0];
      if (KEEP.has(k as string) && q.state.status === "success") newest.set(k, Math.max(newest.get(k) ?? 0, q.state.dataUpdatedAt));
    }
    const state = dehydrate(client, { shouldDehydrateQuery: (q) => q.state.status === "success" && newest.get(q.queryKey[0]) === q.state.dataUpdatedAt });
    try { localStorage.setItem(key(name), JSON.stringify({ build: BUILD, books: books(), at: Date.now(), state } satisfies Saved)); } catch { /* full or blocked: not saved */ }
  };
  return client.getQueryCache().subscribe((e) => {
    if (e.type !== "updated" || e.action.type !== "success" || !KEEP.has(e.query.queryKey[0] as string)) return;
    clearTimeout(timer);
    timer = setTimeout(save, 1000);
  });
}
