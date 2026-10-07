// Only public application files may be cached. Sessions, API responses and media
// always go directly to the server; offline mode must never expose private data.
const CACHE = 'treasure-shell-v2-brand-034';
const PUBLIC_FILES = [
  '/offline.html',
  '/app-icon.svg',
  '/favicon.ico',
  '/brand/logo-lockup-256.png',
  '/brand/logo-lockup-512.png',
  '/brand/app-icon-192.png',
  '/brand/app-icon-512.png',
  '/brand/app-icon-maskable-512.png',
  '/brand/apple-touch-icon.png',
  '/brand/favicon-32.png',
];
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(PUBLIC_FILES))
      .then(() => self.skipWaiting()),
  );
});
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith('treasure-shell-') && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (
    request.method !== 'GET' ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith('/api/') ||
    request.headers.has('range')
  )
    return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/offline.html')));
  } else if (PUBLIC_FILES.includes(url.pathname)) {
    event.respondWith(fetch(request).catch(() => caches.match(url.pathname)));
  }
});
