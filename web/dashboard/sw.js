// Mycelium dashboard service worker.
//
// Two strategies, deliberately different per resource class:
//  - App shell (index.html, dist/main.js + map, styles, manifest, icons):
//    cache-first. These are versioned by CACHE_NAME below and only change
//    on a redeploy, so serving stale-while-a-fetch-happens is unnecessary
//    complexity -- bump CACHE_NAME when dist/ changes.
//  - GET /api/* (status, traces, findings, miners, provenance): stale-
//    while-revalidate. Shows last-known state instantly if the gateway is
//    briefly unreachable, then updates from the network in the background.
//  - Everything else (SSE's /api/stream, all POSTs): untouched, network
//    only. SSE is explicitly NOT offline-capable -- the dashboard's own
//    connection-state badge shows "offline" rather than this worker trying
//    to fake a live stream from cache.
//
// Scope is this file's own directory, /web/dashboard/ (a browser refuses a
// wider scope without a Service-Worker-Allowed response header) -- so the
// bare /web/ document URL itself is NOT intercepted here, only requests
// actually under /web/dashboard/ (everything the app shell needs) and
// /api/*.
const CACHE_NAME = "mycelium-dashboard-v1";
const APP_SHELL = [
  "/web/dashboard/index.html",
  "/web/dashboard/manifest.json",
  "/web/dashboard/dist/main.js",
  "/web/dashboard/src/styles/app.css",
  "/web/dashboard/icons/icon-192.png",
  "/web/dashboard/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // POSTs (apply/dismiss/mine/trace) always hit the network

  const url = new URL(req.url);

  if (url.pathname === "/api/stream") return; // SSE: network only, never intercepted

  if (url.pathname.startsWith("/api/")) {
    event.respondWith(staleWhileRevalidate(req));
    return;
  }

  if (url.pathname.startsWith("/web/dashboard/")) {
    event.respondWith(cacheFirst(req));
  }
});

async function cacheFirst(req) {
  const cached = await caches.match(req);
  if (cached) return cached;
  const res = await fetch(req);
  if (res.ok) {
    const cache = await caches.open(CACHE_NAME);
    cache.put(req, res.clone());
  }
  return res;
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(req);
  const network = fetch(req)
    .then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    })
    .catch(() => null);
  return cached || (await network) || Response.error();
}
