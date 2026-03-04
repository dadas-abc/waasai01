const CACHE_NAME = 'waasai-app-v1';
const CORE_ASSETS = [
  '/web/index.html',
  '/web/manifest.json',
  '/web/pwa-icon.svg'
];
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS))
  );
});
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.map(k => {
      if (k !== CACHE_NAME) return caches.delete(k);
    })))
  );
});
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  event.respondWith(
    caches.match(req).then(cached => {
      const url = new URL(req.url);
      const accept = req.headers.get('Accept') || '';
      const isHTML = accept.includes('text/html');
      const isAPI = url.pathname.startsWith('/api/');
      const shouldCache = !isHTML && !isAPI;
      const fetchPromise = fetch(req).then(resp => {
        try {
          const copy = resp.clone();
          if (shouldCache && copy.ok && copy.type === 'basic') {
            caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
          }
        } catch {}
        return resp;
      }).catch(() => cached);
      if (isHTML || isAPI) {
        return fetchPromise;
      }
      return cached || fetchPromise;
    })
  );
});
