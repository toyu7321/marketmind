/* MarketMind app-shell worker. API responses are intentionally never cached as live data. */
const VERSION = 'marketmind-shell-v2';
const SHELL = [
  '/', '/offline', '/manifest.webmanifest',
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

function cacheResponse(request, response) {
  if (response && response.ok && new URL(request.url).origin === self.location.origin) {
    caches.open(VERSION).then(cache => cache.put(request, response.clone()));
  }
  return response;
}

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then(response => cacheResponse(request, response)).catch(async () => (await caches.match('/offline')) || (await caches.match('/'))));
    return;
  }

  event.respondWith(caches.match(request).then(cached => {
    const network = fetch(request).then(response => cacheResponse(request, response)).catch(() => cached || Response.error());
    return cached || network;
  }));
});
