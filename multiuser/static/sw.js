// Service worker: makes the app installable and shows scan notifications.
// It never touches the network for anything dynamic: /api/*, sign-in pages (/__/) and the
// pages themselves always go straight to the server, so no private data is ever cached.
const CACHE = "job-tracker-shell-v1";
const SHELL = ["/manifest.json", "/static/icons/icon-192.png", "/static/icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((names) => Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (!SHELL.includes(url.pathname)) return;           // only the static icons and manifest are cached
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});
// Tapping a notification opens (or focuses) the app on the Applications tab.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ("focus" in c) return c.focus(); }
    return self.clients.openWindow("/");
  }));
});
