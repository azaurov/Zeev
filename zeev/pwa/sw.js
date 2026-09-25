// Deliberately cache-free: Zeev is a live chat behind a login, and a cached page or
// API reply would show stale or another session's content. This exists so the browser
// treats the site as installable; every request goes straight to the network.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
