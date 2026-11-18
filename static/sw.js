/* NeuLearning service worker: makes the app installable and gives a friendly page when offline.
   Pages are always fetched fresh from the server (they show live, private data), so nothing
   personal is cached. Only the app's own CSS, JS and icons are cached, for a faster start. */
const VERSION = "nl-v1";
const SHELL = [
  "/offline",
  "/static/style.css",
  "/static/game.js",
  "/static/logo.svg",
  "/static/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;

  // Pages: always the network; if there's no connection, show the offline page.
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/offline")));
    return;
  }
  // App files (CSS, JS, icons): network first so updates show up, cache as a fallback.
  if (new URL(req.url).pathname.startsWith("/static/")) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req))
    );
  }
});
