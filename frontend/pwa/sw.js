// Makes the app installable and lets it open offline. Pages are fetched from the network first (so a deploy shows up
// on the next launch) with the last copy as the offline fallback; built assets have hashed names, so a cached copy is
// always correct. API calls are never cached: stale odds are worse than an error.
const CACHE = "props-v2";
// A bet slip shared from a sportsbook app (Android's share menu, via the manifest's share_target): a screenshot, or the
// text and link a share button sends (FanDuel's "Check out my bet" link, a DraftKings Social post). Held here until My bets opens it in Add a bet
// (frontend bets.takeSharedSlip)
const SHARED = "shared-slip";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== SHARED).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method === "POST" && url.origin === self.location.origin && url.pathname.endsWith("/share-slip")) {
    e.respondWith((async () => {
      const form = await req.formData();
      const slip = form.get("slip");
      const c = await caches.open(SHARED);
      if (slip instanceof File) {
        await c.put("slip", new Response(slip, { headers: { "content-type": slip.type, "x-name": encodeURIComponent(slip.name), "x-at": String(Date.now()) } }));
      } else {
        const text = ["title", "text", "url"].map((k) => form.get(k)).filter((v) => typeof v === "string" && v.trim()).join("\n").slice(0, 20000);
        if (text) await c.put("slip", new Response(text, { headers: { "content-type": "text/plain", "x-at": String(Date.now()) } }));
      }
      return Response.redirect(new URL("bets", self.registration.scope).href, 303);
    })());
    return;
  }
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.includes("/api/")) return;
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put("shell", copy)); }
      return res;
    }).catch(() => caches.match("shell").then((r) => r || Response.error())));
  } else if (url.pathname.includes("/assets/")) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
      return res;
    })));
  }
});
