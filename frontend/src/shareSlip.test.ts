import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { holdSharedText, takeSharedSlip } from "./bets";

// The share menu's hand-off: manifest share_target → service worker (pwa/sw.js) → Cache Storage → My bets
function fakeCaches() {
  const store = new Map<string, Response>();
  const cache = { match: async (k: string) => store.get(k)?.clone(), delete: async (k: string) => store.delete(k),
    put: async (k: string, r: Response) => { store.set(k, r); } };
  (globalThis as { caches?: unknown }).caches = { open: async () => cache };
  return cache;
}
const slip = (at: number) => new Response(new Blob(["png"], { type: "image/png" }),
  { headers: { "content-type": "image/png", "x-name": encodeURIComponent("bet slip.png"), "x-at": String(at) } });

describe("a screenshot shared to the app", () => {
  afterEach(() => { delete (globalThis as { caches?: unknown }).caches; });

  it("is taken once, with its name and type", async () => {
    await fakeCaches().put("slip", slip(Date.now()));
    const f = await takeSharedSlip() as File;
    expect(f?.name).toBe("bet slip.png");
    expect(f?.type).toBe("image/png");
    expect(await takeSharedSlip()).toBeNull();
  });

  it("isn't opened an hour later, and nothing happens without Cache Storage", async () => {
    await fakeCaches().put("slip", slip(Date.now() - 2 * 3600_000));
    expect(await takeSharedSlip()).toBeNull();
    delete (globalThis as { caches?: unknown }).caches;
    expect(await takeSharedSlip()).toBeNull();
  });

  it("uses the same names in the manifest and the service worker", () => {
    const m = JSON.parse(readFileSync(new URL("../pwa/manifest.webmanifest", import.meta.url), "utf8"));
    const sw = readFileSync(new URL("../pwa/sw.js", import.meta.url), "utf8");
    expect(m.share_target).toMatchObject({ action: "share-slip", method: "POST", enctype: "multipart/form-data" });
    expect(m.share_target.params.files[0].name).toBe("slip");
    expect(sw).toContain('endsWith("/share-slip")');
    expect(sw).toContain('.get("slip")');
  });
});

describe("the service worker's share hand-off", () => {
  afterEach(() => { delete (globalThis as { caches?: unknown }).caches; });

  it("keeps the shared image and sends the app to My bets", async () => {
    const cache = fakeCaches();
    const listeners: Record<string, (e: unknown) => void> = {};
    const self = { location: { origin: "https://process.example" }, registration: { scope: "https://process.example/" },
      addEventListener: (type: string, fn: (e: unknown) => void) => { listeners[type] = fn; }, skipWaiting() {}, clients: { claim() {} } };
    new Function("self", readFileSync(new URL("../pwa/sw.js", import.meta.url), "utf8"))(self);
    const form = new FormData();
    form.append("slip", new File(["png"], "slip.png", { type: "image/png" }));
    let answer: Promise<Response> | undefined;
    listeners.fetch({ request: new Request("https://process.example/share-slip", { method: "POST", body: form }), respondWith: (p: Promise<Response>) => { answer = p; } });
    const res = await answer!;
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("https://process.example/bets");
    const kept = await cache.match("slip");
    expect(kept?.headers.get("content-type")).toBe("image/png");
    expect(await kept?.text()).toBe("png");
  });
});

describe("a share link shared to the app (FanDuel's share button sends text, not an image)", () => {
  afterEach(() => { delete (globalThis as { caches?: unknown }).caches; });

  it("keeps the text and link, and My bets takes them as text", async () => {
    const cache = fakeCaches();
    const listeners: Record<string, (e: unknown) => void> = {};
    const self = { location: { origin: "https://process.example" }, registration: { scope: "https://process.example/" },
      addEventListener: (type: string, fn: (e: unknown) => void) => { listeners[type] = fn; }, skipWaiting() {}, clients: { claim() {} } };
    new Function("self", readFileSync(new URL("../pwa/sw.js", import.meta.url), "utf8"))(self);
    const form = new FormData();
    form.append("text", "Check out my bet on FanDuel Sportsbook!");
    form.append("url", "https://sportsbook.fanduel.com/addToBetslip?shareCode=Ii1CDAF");
    let answer: Promise<Response> | undefined;
    listeners.fetch({ request: new Request("https://process.example/share-slip", { method: "POST", body: form }), respondWith: (p: Promise<Response>) => { answer = p; } });
    expect((await answer!).status).toBe(303);
    expect((await cache.match("slip"))?.headers.get("content-type")).toBe("text/plain");
    expect(await takeSharedSlip()).toBe("Check out my bet on FanDuel Sportsbook!\nhttps://sportsbook.fanduel.com/addToBetslip?shareCode=Ii1CDAF");
    expect(await takeSharedSlip()).toBeNull();
  });

  it("is in the manifest's share target", () => {
    const m = JSON.parse(readFileSync(new URL("../pwa/manifest.webmanifest", import.meta.url), "utf8"));
    expect(m.share_target.params).toMatchObject({ title: "title", text: "text", url: "url" });
  });
});

describe("signed out", () => {
  afterEach(() => { delete (globalThis as { caches?: unknown }).caches; });
  it("can see a shared screenshot is waiting without taking it", async () => {
    await fakeCaches().put("slip", slip(Date.now()));
    expect(await takeSharedSlip(true)).not.toBeNull();
    expect(await takeSharedSlip()).not.toBeNull();
    expect(await takeSharedSlip()).toBeNull();
  });
});

describe("a slip's text from the iPhone Shortcut", () => {
  const g = globalThis as { window?: unknown; localStorage?: unknown };
  let replaced = "";
  function browser(hash: string) {
    const store = new Map<string, string>();
    g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); },
      removeItem: (k: string) => { store.delete(k); } };
    g.window = { location: { hash, pathname: "/bets", search: "" }, history: { replaceState: (_: unknown, __: string, url: string) => { replaced = url; } } };
  }
  afterEach(() => { delete g.window; delete g.localStorage; replaced = ""; });

  it("is held from /bets#slip=..., the address tidied, and taken once", async () => {
    const text = "Same Game Parlay +2638\nAJ Barner 3+ Receptions\nAJ BARNER - ALT RECEPTIONS";
    browser("#slip=" + encodeURIComponent(text));
    holdSharedText();
    expect(replaced).toBe("/bets");
    expect(await takeSharedSlip(true)).toBe(text);
    expect(await takeSharedSlip()).toBe(text);
    expect(await takeSharedSlip()).toBeNull();
  });

  it("ignores other links and garbled text", async () => {
    browser("#game=2026_09_CHI_SEA");
    holdSharedText();
    expect(replaced).toBe("");
    browser("#slip=%E0%A4%A");
    holdSharedText();
    expect(await takeSharedSlip()).toBeNull();
  });
});
