/* 静态缓存不含账号与绑定响应；通知交接使用独立、有界的私有暂存。 */
importScripts('messages.js');
const CACHE = 'moviepilot-notifications-v6';
const ASSETS = ['./', './style.css', './app.js', './messages.js', './icon.svg', './icon.png', './manifest.webmanifest'];
// 新旧消息格式兼容；静态更新接管现有绑定，不要求用户重新订阅。
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const key of await caches.keys()) if (key.startsWith('moviepilot-notifications-') && key !== CACHE) await caches.delete(key);
  await self.clients.claim();
})()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method === 'GET' && url.origin === self.location.origin && ASSETS.some(asset => new URL(asset, self.registration.scope).pathname === url.pathname)) {
    event.respondWith(fetch(event.request).catch(() => caches.match(url.origin + url.pathname)));
  }
});
self.addEventListener('push', event => event.waitUntil((async () => {
  let data;
  try { data = event.data.json(); } catch { data = { title: 'MoviePilot', body: '你有一条新消息' }; }
  if (!data || typeof data !== 'object' || Array.isArray(data)) data = { title: 'MoviePilot', body: '你有一条新消息' };
  // 部分主屏幕启动只恢复 start_url，先保存接收记录，不把详情读取寄托在跳转参数上。
  try { data.id = await notificationMessages.save(data, 'recent'); } catch { /* 存储不可用时仍展示系统通知。 */ }
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
    const data = { title: event.notification.title, body: event.notification.body, ...event.notification.data };
    try { target.searchParams.set('message', await notificationMessages.save(data, 'clicked')); }
    catch { target.hash = 'open=' + encodeURIComponent(JSON.stringify(data)); }
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const current = windows.find(client => client.url.startsWith(self.registration.scope));
    const intent = { type: 'moviepilot-notification', id: target.searchParams.get('message') };
    if (current) {
      current.postMessage(intent);
      // 旧页面需要重新加载脚本；导航或 focus 拒绝不能抹掉已经持久化的点击记录。
      let opened = current;
      try { opened = await current.navigate(target.href) || current; } catch { /* 页面恢复时自行读取暂存。 */ }
      try { await opened.focus(); } catch { /* WebKit 恢复窗口期间可能暂时不可聚焦。 */ }
      opened.postMessage(intent);
    } else {
      const opened = await self.clients.openWindow(target.href);
      opened?.postMessage(intent);
    }
  })());
});
