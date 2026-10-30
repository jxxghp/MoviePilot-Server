/* 系统推送不依赖前台页面运行；只缓存公开静态资源，不缓存账号与绑定响应。 */
const CACHE = 'moviepilot-notifications-v4';
const ASSETS = ['./', './style.css', './app.js', './icon.svg', './icon.png', './manifest.webmanifest'];
// 新旧消息格式兼容；静态更新接管现有绑定，不要求用户重新订阅。
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting())));
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
  const image = notificationImage(data.image);
  await self.registration.showNotification(String(data.title || 'MoviePilot'), {
    body: String(data.body || ''), icon: image || 'icon.png', ...(image ? { image } : {}), data,
  });
})()));
/** 浏览器按自身能力展示海报；不将脚本、凭据地址或本地文件交给系统通知。 */
function notificationImage(value) {
  if (typeof value !== 'string' || value.length > 2048 || /[\\\s\p{Cc}\p{Cf}]/u.test(value)) return '';
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch { return ''; }
}
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
