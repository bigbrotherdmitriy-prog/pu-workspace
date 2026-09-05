const CACHE = "pu-workspace-shell-v3";
const APP_SHELL = [
  "/new/", "/new/manifest.webmanifest", "/new/pu-icon.svg",
  "/new/pu-icon-192.png", "/new/pu-icon-512.png", "/new/pu-icon-maskable-512.png",
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith("/new/")) return;
  if (url.pathname.startsWith("/new/api/")) return;
  // Never use an old HTML shell after a release: it can reference chunks that
  // no longer exist and leave the user with an entirely blank application.
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok) caches.open(CACHE).then(cache => cache.put(event.request, response.clone()));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
