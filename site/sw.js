// Service worker: web push-varsler og enkel frakoblet visning.
// data.json hentes alltid fra nettet først (ferske tall), resten fra cache.
const CACHE = "momentum-v1";
const SKALL = ["./", "index.html", "manifest.webmanifest", "ikon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SKALL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // Nett først, cache som reserve: alltid ferske data når du er på nett.
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        const kopi = r.clone();
        caches.open(CACHE).then((c) => c.put(e.request, kopi));
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = {body: e.data ? e.data.text() : ""}; }
  e.waitUntil(
    self.registration.showNotification(d.title || "Momentum-varsling", {
      body: d.body || "",
      icon: "ikon-192.png",
      badge: "ikon-192.png",
      data: {url: d.url || "./"},
      tag: d.title || "momentum",
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const mål = new URL(e.notification.data.url || "./", self.registration.scope).href;
  e.waitUntil(
    clients.matchAll({type: "window", includeUncontrolled: true}).then((vs) => {
      for (const v of vs) if (v.url.startsWith(self.registration.scope)) return v.focus();
      return clients.openWindow(mål);
    })
  );
});
