{% load static %}// WaiverWise service worker: offline-readable pages, cached static assets.
const VERSION = "{{ version }}";
const CACHE = `waiverwise-${VERSION}`;
const OFFLINE_URL = "{% url 'offline' %}";
const PRECACHE = [
  OFFLINE_URL,
  "{% static 'vendor/pico-2.0.6.min.css' %}",
  "{% static 'css/app.css' %}",
  "{% static 'vendor/htmx-2.0.3.min.js' %}",
  "{% static 'icons/icon-192.png' %}",
];
const SLOW_NETWORK_MS = 4000;
// pages worth keeping a last-seen copy of for offline reading
const PAGES = ["/", "{% url 'startsit' %}", "{% url 'waivers' %}", "{% url 'trades' %}"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// the login page asks us to forget cached pages so a signed-out device doesn't keep league data
self.addEventListener("message", (event) => {
  if (event.data === "clear-pages") {
    caches.open(CACHE).then((cache) =>
      cache.keys().then((reqs) => reqs.forEach((r) => {
        if (PAGES.includes(new URL(r.url).pathname)) cache.delete(r);
      }))
    );
  }
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("{% get_static_prefix %}")) {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
    return;
  }

  if (req.mode === "navigate" && PAGES.includes(url.pathname)) {
    const network = fetch(req).then((res) => {
      // don't cache the login redirect or errors as if they were the page
      if (res.ok && !res.redirected) {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(req, copy));
      }
      return res;
    });
    event.waitUntil(network.catch(() => {}));  // keep refreshing the cache after we answer
    event.respondWith(
      new Promise((resolve) => {
        let settled = false;
        const answer = (res) => { if (!settled && res) { settled = true; resolve(res); } };
        network.then(answer, () =>
          caches.match(req).then((hit) => hit || caches.match(OFFLINE_URL)).then(answer)
        );
        // a sleeping free-tier server takes ~1 min to wake: show the last copy instead of a blank wait
        // (if nothing is cached, `answer` ignores the miss and we keep waiting for the network)
        setTimeout(() => caches.match(req).then(answer), SLOW_NETWORK_MS);
      })
    );
    return;
  }

  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
  }
  // everything else (HTMX partials, explanations, admin) goes straight to the network
});
