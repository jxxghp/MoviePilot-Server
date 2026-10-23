/* 系统推送不依赖前台页面运行；只缓存公开静态资源，不缓存账号与绑定响应。 */
const CACHE = 'moviepilot-notifications-v2';
const ASSETS = ['./', './style.css', './app.js', './icon.svg', './icon.png', './manifest.webmanifest'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS))));
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const key of await caches.keys()) if (key.startsWith('moviepilot-notifications-') && key !== CACHE) await caches.delete(key);
  await self.clients.claim();
})()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method === 'GET' && url.origin === self.location.origin && ASSETS.some(asset => new URL(asset, self.registration.scope).pathname === url.pathname)) {
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
  }
});
self.addEventListener('push', event => event.waitUntil((async () => {
  let data;
  try { data = event.data.json(); } catch { data = { title: 'MoviePilot', body: '你有一条新消息' }; }
  await self.registration.showNotification(String(data.title || 'MoviePilot'), {
    body: String(data.body || ''), icon: 'icon.png', data,
  });
})()));
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const target = new URL('./', self.registration.scope);
    target.hash = 'open=' + encodeURIComponent(JSON.stringify(event.notification.data || {}));
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const current = windows.find(client => client.url.startsWith(self.registration.scope));
    if (current) { await current.navigate(target.href); await current.focus(); }
    else await self.clients.openWindow(target.href);
  })());
});
