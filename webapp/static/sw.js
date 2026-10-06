// Minimal service worker: exists mainly to satisfy browser installability
// criteria (Add to Home Screen) rather than to provide real offline
// support -- every page in this app needs a live API call to be useful,
// so there's nothing meaningful to show offline anyway.
//
// Strategy: cache the static "app shell" (the HTML/manifest/icons) so the
// app opens instantly on a flaky connection, but NEVER cache /api/* --
// that data must always be fresh, and some of it (CV text, application
// history) is sensitive enough that caching it would be the wrong
// tradeoff even if it were allowed to go stale.

const CACHE_NAME = "job-tracker-shell-v1";
// Deliberately excludes "/" itself: that route is behind HTTP Basic Auth,
// and precaching an authenticated route during install is an easy way to
// make install silently fail depending on the browser. These three are
// genuinely public/static, so they're safe (and useful) to precache.
const SHELL_FILES = ["/manifest.json", "/static/icons/icon-192.png", "/static/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never intercept API calls -- always go to the network.
  if (url.pathname.startsWith("/api/")) return;

  // Network-first for the shell: prefer a fresh copy, fall back to the
  // cache only if the network is unavailable.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
