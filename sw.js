// Voice Split AI — Service Worker
const CACHE = 'vsai-v1';
const ASSETS = [
  '/',
  '/index.html',
  '/manifest.json'
];

// Install — cache app shell
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(ASSETS))
  );
  self.skipWaiting();
});

// Activate — clean old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch — serve from cache, fallback to network
self.addEventListener('fetch', e => {
  // Don't cache API calls to backend server
  if (e.request.url.includes('/process') || 
      e.request.url.includes('/download') ||
      e.request.url.includes('/health')) {
    return; // Let network handle API calls
  }
  e.respondWith(
    caches.match(e.request).then(cached => {
      return cached || fetch(e.request).then(resp => {
        // Cache new static resources
        if (e.request.url.startsWith(self.location.origin)) {
          caches.open(CACHE).then(c => c.put(e.request, resp.clone()));
        }
        return resp;
      });
    }).catch(() => caches.match('/index.html'))
  );
});
