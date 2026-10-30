/* 页面与 worker 共用的私有暂存，不经 fetch 事件或 HTTP 接口返回，不保存 MoviePilot 登录凭据。 */
const notificationMessages = (() => {
  const name = 'moviepilot-notification-messages-v1';
  const base = new URL('_messages/', self.registration?.scope || new URL('./', location.href)).href;
  const ttl = 86400000;

  /** 仅读写随机消息标识，通知正文与设备能力均不进入 URL。 */
  function valid(id) { return typeof id === 'string' && !['selected', 'binding'].includes(id) && /^[a-zA-Z0-9_-]{1,64}$/.test(id); }
  /** 读取尚未过期的单条本地记录。 */
  async function entry(cache, id) {
    if (!valid(id)) return null;
    const response = await cache.match(base + id);
    if (!response) return null;
    const value = await response.json();
    if (Date.now() - value.at >= ttl) { await cache.delete(base + id); return null; }
    return value;
  }
  /** 显式写入私有缓存，不与静态离线文件、网络响应或绑定令牌混用。 */
  async function put(cache, key, value) {
    await cache.put(base + key, new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } }));
  }
  /** 收到及点击通知都先落盘；系统只打开起始页时仍可恢复最近收到的消息。 */
  async function save(data, reason = '', complete = false) {
    const cache = await caches.open(name);
    const id = valid(data.id) ? data.id : crypto.randomUUID();
    const previous = await entry(cache, id);
    // 全文到达后，迟到的系统预览不能把它覆盖回截断正文。
    await put(cache, id, previous?.complete && !complete ? previous : { at: previous?.at || Date.now(), data: { ...data, id }, complete });
    if (reason) await put(cache, 'selected', { id, reason });
    const records = [];
    for (const key of await cache.keys()) {
      const keyId = new URL(key.url).pathname.split('/').pop();
      if (['selected', 'binding'].includes(keyId)) continue;
      const value = await entry(cache, keyId);
      if (value) records.push({ key, at: value.at });
    }
    records.sort((a, b) => b.at - a.at);
    for (const record of records.slice(20)) await cache.delete(record.key);
    return id;
  }
  /** URL 未携带点击参数或页面重载时，读取 worker 留下的选择。 */
  async function read(id = '') {
    const cache = await caches.open(name);
    const selected = await cache.match(base + 'selected').then(response => response?.json());
    const record = await entry(cache, id || selected?.id);
    if (record) return { ...record, reason: id ? 'clicked' : selected?.reason };
    // 保留失效的点击意图以显示明确提示，过期正文已经删除，不退回绑定输入界面。
    return selected && (!id || id === selected.id) ? { expired: true, data: { id: selected.id }, reason: selected.reason } : null;
  }
  /** 解绑或重新绑定时清除前一账号的本地通知内容。 */
  async function clear() { await caches.delete(name); }
  /** 绑定能力另存一份，不受消息条数及有效期清理影响；不保存 MoviePilot 登录凭据。 */
  async function writeBinding(binding) { await put(await caches.open(name), 'binding', binding); }
  /** localStorage 未读到时尝试本设备的绑定备份。 */
  async function readBinding() {
    const response = await (await caches.open(name)).match(base + 'binding');
    return response ? response.json() : null;
  }
  return { save, read, clear, writeBinding, readBinding };
})();
