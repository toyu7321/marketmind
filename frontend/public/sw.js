/* MarketMind worker: cache only public static assets, never authenticated screens or API data. */
// Bump on a cache-policy change so activation deletes every prior shell cache.
const VERSION = 'marketmind-shell-v6';
const SHELL = [
  '/offline', '/manifest.webmanifest',
  '/icons/marketmind.svg', '/icons/marketmind-maskable.svg',
  '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-512-maskable.png',
  '/icons/apple-touch-icon.png',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(VERSION).then(cache => cache.addAll(SHELL)));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== VERSION).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});

self.addEventListener('message', event => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  // Never persist protected HTML. A logout or revoked session must not leave a
  // previously rendered dashboard, portfolio, settings, or broker view offline.
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(async () => (await caches.match('/offline')) || Response.error()));
    return;
  }
  const safeStatic = url.pathname.startsWith('/_next/static/') || url.pathname.startsWith('/icons/') || url.pathname === '/manifest.webmanifest' || url.pathname === '/favicon.ico';
  if (!safeStatic) return;
  event.respondWith(caches.match(request).then(cached => cached || fetch(request).then(response => {
    if (response && response.ok) {
      // Clone synchronously, before returning the original response to the
      // browser. Deferring clone() until caches.open() resolves races with the
      // browser consuming that original body and causes a used-body failure.
      const cacheCopy = response.clone();
      event.waitUntil(caches.open(VERSION).then(cache => cache.put(request, cacheCopy)).catch(() => undefined));
    }
    return response;
  }).catch(() => Response.error())));
});
