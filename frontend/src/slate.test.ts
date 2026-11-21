import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { slatePart } from "./api";
import { MAX_AGE, restoreCache, saveCache } from "./persistCache";

const calls: string[] = [];
function serve(body: (url: string) => unknown) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => { calls.push(url); return new Response(JSON.stringify(body(url))); }));
}
afterEach(() => vi.unstubAllGlobals());

describe("the games page's reads", () => {
  it("go out as one /slate request when asked for together, each part answered from it", async () => {
    serve(() => ({ meta: { books: ["FanDuel"], games: [] }, picks: [{ id: 1 }], moneylines: [], injuries: { teams: {} } }));
    const [meta, picks, ml, inj] = await Promise.all([slatePart("nba", "meta"), slatePart("nba", "picks", "FanDuel", null),
      slatePart("nba", "moneylines", "FanDuel", null), slatePart("nba", "injuries")]);
    expect(calls).toEqual(["/api/nba/slate?parts=meta%2Cpicks%2Cmoneylines%2Cinjuries&book=FanDuel"]);
    expect([meta.books, picks, ml, inj]).toEqual([["FanDuel"], [{ id: 1 }], [], { teams: {} }]);
  });
  it("use their own endpoint when asked for alone (a refetch), and keep another book's board apart", async () => {
    serve((url) => (url.includes("slate") ? { meta: { books: [] }, picks: [] } : []));
    await slatePart("nfl", "injuries");
    expect(calls).toEqual(["/api/injuries"]);
    await Promise.all([slatePart("nfl", "meta"), slatePart("nfl", "picks", "DraftKings", 0.5), slatePart("nfl", "moneylines", "FanDuel", 0.5)]);
    expect(calls.slice(1).sort()).toEqual(["/api/moneylines?book=FanDuel&market_weight=0.5",
      "/api/slate?parts=meta%2Cpicks&book=DraftKings&market_weight=0.5"]);
  });
  it("fail together when the request does", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("down", { status: 503 })));
    const res = await Promise.allSettled([slatePart("nfl", "meta"), slatePart("nfl", "injuries")]);
    expect(res.map((r) => r.status)).toEqual(["rejected", "rejected"]);
  });
});

describe("the saved games page", () => {
  const store = new Map<string, string>();
  const ls = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) };

  it("comes back on the next visit with the same books, but not when stale or priced for other books", async () => {
    vi.stubGlobal("localStorage", ls);
    vi.useFakeTimers();
    const a = new QueryClient();
    const stop = saveCache(a, "nba", () => "*");
    await a.fetchQuery({ queryKey: ["slate-picks", "all", null], queryFn: async () => [1] });
    vi.advanceTimersByTime(10);
    await a.fetchQuery({ queryKey: ["slate-picks", "FanDuel", null], queryFn: async () => [2] });
    await a.fetchQuery({ queryKey: ["projections"], queryFn: async () => [3] });   // not a games-page read: not saved
    vi.advanceTimersByTime(1100);
    stop();
    vi.useRealTimers();

    const b = new QueryClient();
    expect(restoreCache(b, "nba", "*")).toBe(true);
    expect(b.getQueryData(["slate-picks", "FanDuel", null])).toEqual([2]);   // only the newest board is kept
    expect(b.getQueryData(["slate-picks", "all", null])).toBeUndefined();
    expect(b.getQueryData(["projections"])).toBeUndefined();
    expect(restoreCache(new QueryClient(), "nba", "FanDuel")).toBe(false);   // another state's legal books
    expect(restoreCache(new QueryClient(), "nba", "*", Date.now() + MAX_AGE + 60_000)).toBe(false);
    expect(restoreCache(new QueryClient(), "nfl", "*")).toBe(false);
  });
});
