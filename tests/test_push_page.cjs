/** 执行实际页面、worker 和暂存脚本，覆盖交接失败；不替代真机系统推送验收。 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { randomUUID } = require('node:crypto');
const vm = require('node:vm');
const assets = resolve(__dirname, '../app/static/push');
const scope = 'https://relay.test/push/app/';
const binding = { id: 'd'.repeat(32), secret: 's'.repeat(43), origin: 'https://moviepilot.test/base', owner: 'a'.repeat(32) };
const message = { origin: binding.origin, owner: binding.owner, title: '电影下载完成', body: '第一行\n第二行', route: '/history?name=中文', image: 'https://images.test/poster.jpg' };

/** 只替换浏览器 CacheStorage 边界，页面与 worker 共用真实暂存代码。 */
function storage() {
  const stores = new Map();
  return {
    async open(name) {
      if (!stores.has(name)) stores.set(name, new Map());
      const values = stores.get(name);
      const key = value => typeof value === 'string' ? value : value.url;
      return {
        match: async request => values.get(key(request))?.clone(),
        put: async (request, response) => { values.set(key(request), response.clone()); },
        delete: async request => values.delete(key(request)),
        keys: async () => [...values.keys()].map(url => ({ url })),
      };
    },
    delete: async name => stores.delete(name),
    keys: async () => [...stores.keys()],
  };
}

/** 最小 DOM 禁止 HTML 渲染，保留实际属性和事件。 */
class Element {
  /** 每个元素独立保存状态。 */
  constructor() { this.hidden = true; this.attributes = {}; this.events = {}; this.dataset = {}; this.textContent = ''; }
  /** 保存实际深链。 */
  set href(value) { this.attributes.href = value; }
  /** 读取实际深链。 */
  get href() { return this.attributes.href; }
  /** 保存实际图片。 */
  set src(value) { this.attributes.src = value; }
  /** 验证旧图片已清理。 */
  get src() { return this.attributes.src; }
  /** 纯文本消息不能按 HTML 执行。 */
  set innerHTML(_value) { throw new Error('消息不能作为 HTML 执行'); }
  /** 保存实际事件入口。 */
  addEventListener(name, listener) { this.events[name] = listener; }
  /** 删除浏览器属性。 */
  removeAttribute(name) { delete this.attributes[name]; }
}

/** 加载实际文件，不另写一份消息处理实现。 */
function script(context, file) { return vm.runInContext(readFileSync(resolve(assets, file), 'utf8'), context); }

/** 按真实 HTML 建立 DOM，并等待真实启动读取结束。 */
async function page({ data, bound = binding, cache = storage(), url = scope, fetcher, subscription = null, blockedLocal = false } = {}) {
  const html = readFileSync(resolve(assets, 'index.html'), 'utf8');
  const elements = Object.fromEntries([...html.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].map(match => {
    const element = new Element(); element.hidden = /\bhidden\b/.test(match[0]); return [match[1], element];
  }));
  const windowEvents = {}, documentEvents = {}, workerEvents = {};
  const location = new URL(url);
  if (data !== undefined) location.hash = 'open=' + encodeURIComponent(JSON.stringify(data));
  const updates = [], requests = [];
  const local = new Map([['moviepilot.push.binding', JSON.stringify(bound)], ['moviepilot.push.installation', 'installation-001']]);
  const context = vm.createContext({
    URL, URLSearchParams, location, caches: cache, Response, crypto: { randomUUID }, self: {},
    fetch: async (url, options) => {
      requests.push({ url, ...options, body: JSON.parse(options.body) });
      if (!fetcher) throw new Error('离线');
      return fetcher(url, JSON.parse(options.body));
    },
    history: { replaceState: (_state, _title, path) => { location.href = new URL(path, location).href; } },
    localStorage: {
      getItem: key => { if (blockedLocal) throw new Error('localStorage unavailable'); return local.get(key); },
      setItem: (key, value) => { if (blockedLocal) throw new Error('localStorage unavailable'); local.set(key, value); },
      removeItem: key => local.delete(key),
    },
    document: { visibilityState: 'visible', addEventListener: (name, listener) => { documentEvents[name] = listener; }, getElementById: id => { assert.ok(elements[id], id); return elements[id]; } },
    window: { addEventListener: (name, listener) => { windowEvents[name] = listener; } },
    navigator: { serviceWorker: {
      register: script => { updates.push(script); return Promise.resolve({}); },
      getRegistration: async () => subscription ? { pushManager: { getSubscription: async () => ({ toJSON: () => subscription }) } } : undefined,
      addEventListener: (name, listener) => { workerEvents[name] = listener; },
    } },
  });
  script(context, 'messages.js'); script(context, 'app.js');
  await vm.runInContext('initialMessage', context);
  return { elements, context, location, windowEvents, documentEvents, workerEvents, updates, requests, local };
}

/** worker 使用页面同源缓存，窗口行为按用例注入。 */
function worker(cache = storage(), clients = {}) {
  const handlers = {}, shown = [];
  const context = vm.createContext({ URL, caches: cache, Response, crypto: { randomUUID }, self: {
    addEventListener: (name, callback) => { handlers[name] = callback; },
    registration: { scope, showNotification: async (title, options) => { shown.push({ title, ...options }); } },
    clients: { matchAll: async () => [], openWindow: async () => null, ...clients },
  } });
  context.importScripts = file => script(context, file);
  script(context, 'worker.js');
  return { handlers, shown, context };
}

/** 等待 worker 实际生命周期的 waitUntil。 */
async function dispatch(view, name, values) {
  let completion;
  if (name === 'push') { const source = values.data; values = { ...values, data: { json: () => structuredClone(source.json()) } }; }
  view.handlers[name]({ ...values, waitUntil: promise => { completion = promise; } });
  await completion;
}

/** 等待前台异步事件的实际结果，超限有明确断言。 */
async function until(predicate) {
  for (let turn = 0; turn < 40 && !predicate(); turn++) await new Promise(setImmediate);
  assert.ok(predicate(), '页面事件没有完成消息展示');
}

test('旧通知显示纯文本、海报和正确 App 链接，重载仍可恢复', async () => {
  const cache = storage();
  const { elements, location, updates } = await page({ data: message, cache });
  assert.equal(elements.message.hidden, false);
  assert.equal(elements.settings.hidden, true);
  assert.equal(elements['message-title'].textContent, message.title);
  assert.equal(elements['message-body'].textContent, message.body);
  assert.equal(elements['message-image'].src, message.image);
  assert.equal(elements['message-image'].hidden, false);
  const app = new URL(elements['open-app'].href);
  assert.equal(app.protocol, 'moviepilot-app:');
  assert.equal(app.hostname, 'notification');
  for (const key of ['origin', 'owner', 'route']) assert.equal(app.searchParams.get(key), message[key]);
  assert.equal(elements['open-web'].href, binding.origin + '/#' + message.route);
  assert.equal(location.hash, '');
  assert.equal(location.search, '');
  assert.deepEqual(updates, ['worker.js']);
  assert.equal((await page({ cache })).elements['message-body'].textContent, message.body);
});

test('没有消息时显示绑定设置，详情可显式进入通知设置', async () => {
  const connected = (await page()).elements;
  assert.equal(connected.settings.hidden, false);
  assert.equal(connected.connected.hidden, false);
  assert.equal(connected.message.hidden, true);
  const setup = (await page({ bound: null })).elements;
  assert.equal(setup.setup.hidden, false);
  assert.equal(setup.connected.hidden, true);
  const settings = (await page({ data: message, url: scope + '?settings=1' })).elements;
  assert.equal(settings.settings.hidden, false);
  assert.equal(settings.message.hidden, true);
});

test('没有点击事件且系统丢弃全部 URL 参数，冷启动仍显示收到消息的全文', async () => {
  const cache = storage();
  const view = worker(cache);
  const id = 'c'.repeat(32);
  await dispatch(view, 'push', { data: { json: () => ({ ...message, id, body: '系统预览' }) } });
  const full = { ...message, id, body: '完整中文与换行\n' + '内容'.repeat(10000) };
  const loaded = await page({ cache, fetcher: async (url, request) => {
    assert.equal(url, '/push/subscriptions/message');
    assert.deepEqual(request, { id: binding.id, secret: binding.secret, message: id });
    return Response.json(full);
  } });
  assert.equal(loaded.location.href, scope);
  assert.equal(loaded.elements.settings.hidden, true);
  assert.equal(loaded.elements['message-body'].textContent, full.body);
  assert.equal(loaded.elements['message-note'].textContent, '最近收到的通知');
  assert.equal(loaded.elements['message-image'].src, full.image);
  const offline = await page({ cache });
  assert.equal(offline.elements['message-body'].textContent, full.body);
  assert.equal(offline.requests.length, 0);
});

test('点击指定消息先落盘，已有窗口导航和 focus 失败、首次窗口丢 URL 均能恢复', async () => {
  for (const existing of [true, false]) {
    const cache = storage(), targets = [], sent = [];
    const current = { url: scope, postMessage: data => sent.push(data), navigate: async target => { targets.push(target); throw new Error('导航失败'); }, focus: async () => { throw new Error('focus failed'); } };
    const view = worker(cache, { matchAll: async () => existing ? [current] : [], openWindow: async target => { targets.push(target); return null; } });
    await dispatch(view, 'push', { data: { json: () => message } });
    const first = view.shown[0];
    await dispatch(view, 'push', { data: { json: () => ({ ...message, body: '另一条新通知' }) } });
    let closed = false;
    await dispatch(view, 'notificationclick', { notification: { ...first, close: () => { closed = true; } } });
    assert.equal(closed, true);
    assert.equal(targets.length, 1);
    assert.equal(new URL(targets[0]).searchParams.get('message'), first.data.id);
    assert.equal(new URL(targets[0]).hash, '');
    if (existing) assert.equal(sent.at(-1).type, 'moviepilot-notification');
    const loaded = await page({ cache });
    assert.equal(loaded.elements['message-body'].textContent, message.body);
    assert.equal(loaded.elements.settings.hidden, true);
    assert.equal(loaded.elements['message-note'].hidden, true);
  }
});

test('已有页面恢复前台和收到 worker 消息时读取详情，无需重新导航', async () => {
  const cache = storage();
  const loaded = await page({ cache });
  assert.equal(loaded.elements.message.hidden, true);
  const view = worker(cache);
  await dispatch(view, 'push', { data: { json: () => message } });
  loaded.documentEvents.visibilitychange();
  await until(() => loaded.elements['message-body'].textContent === message.body);
  await dispatch(view, 'push', { data: { json: () => ({ ...message, body: '点击另一条通知' }) } });
  loaded.workerEvents.message({ data: { type: 'moviepilot-notification', id: view.shown.at(-1).data.id } });
  await until(() => loaded.elements['message-body'].textContent === '点击另一条通知');
  const settings = await page({ cache, url: scope + '?settings=1' });
  settings.workerEvents.message({ data: { type: 'moviepilot-notification', id: view.shown.at(-1).data.id } });
  await until(() => settings.elements['message-body'].textContent === '点击另一条通知');
  assert.equal(settings.elements.settings.hidden, true);
});

test('全文过期或设备无权读取时显示明确消息界面，不无声退回绑定页', async () => {
  const loaded = await page({ url: scope + '?message=' + 'f'.repeat(32), fetcher: async () => Response.json({ detail: '消息全文已过期' }, { status: 404 }) });
  assert.equal(loaded.elements.message.hidden, false);
  assert.equal(loaded.elements.settings.hidden, true);
  assert.equal(loaded.elements['message-title'].textContent, '消息暂时无法打开');
  assert.match(loaded.elements['message-body'].textContent, /已过期/);
  assert.equal(loaded.elements.retry.hidden, false);
  assert.equal(loaded.elements['open-app'].href, undefined);
});

test('其他账号、实例和非法路由不保留上一条正文、海报和跳转', async () => {
  for (const invalid of [{ owner: 'b'.repeat(32) }, { origin: 'https://other.test' }, { route: '//other.test' }, { route: '/\\other.test' }]) {
    const loaded = await page({ data: message });
    loaded.location.hash = 'open=' + encodeURIComponent(JSON.stringify({ ...message, ...invalid }));
    await loaded.windowEvents.hashchange();
    assert.equal(loaded.elements.settings.hidden, true);
    assert.equal(loaded.elements['message-title'].textContent, '消息暂时无法打开');
    assert.equal(loaded.elements['message-image'].src, undefined);
    assert.equal(loaded.elements['open-app'].href, undefined);
    assert.equal(loaded.elements['open-web'].href, undefined);
  }
});

test('网页与系统通知拒绝不安全图片，图片失败和 HTML 正文不阻断阅读', async () => {
  const view = worker();
  for (const image of ['javascript:alert(1)', 'data:image/png;base64,AA', 'file:///secret.png', '//other.test/x', 'https://user:secret@images.test/x', 'https://images.test/\\x', 'https://images.test/\nx']) {
    const loaded = await page({ data: { ...message, image, body: '<img src=x onerror=alert(1)>' } });
    assert.equal(loaded.elements['message-image'].hidden, true);
    assert.equal(loaded.elements['message-body'].textContent, '<img src=x onerror=alert(1)>');
    await dispatch(view, 'push', { data: { json: () => ({ ...message, image }) } });
    assert.equal(view.shown.at(-1).icon, 'icon.png');
    assert.equal(view.shown.at(-1).image, undefined);
  }
  await dispatch(view, 'push', { data: { json: () => message } });
  assert.equal(view.shown.at(-1).icon, message.image);
  assert.equal(view.shown.at(-1).image, message.image);
  const loaded = await page({ data: message });
  loaded.elements['message-image'].events.error();
  assert.equal(loaded.elements['message-image'].hidden, true);
  assert.ok(loaded.elements['open-app'].href);
});

test('本地暂存限制二十条和二十四小时，解绑清除后不残留内容', async () => {
  const view = worker();
  let now = Date.now();
  view.context.Date = class extends Date { static now() { return now; } };
  const store = vm.runInContext('notificationMessages', view.context);
  const ids = [];
  for (let index = 0; index < 21; index++) { now++; ids.push(await store.save({ ...message, body: String(index) }, 'recent')); }
  assert.equal(await store.read(ids[0]), null);
  assert.equal((await store.read()).data.body, '20');
  now += 86400000;
  assert.equal((await store.read()).expired, true);
  await store.save(message, 'clicked');
  await store.clear();
  assert.equal(await store.read(), null);
});

test('缓存受限仍显示系统通知，旧片段和在线全文仍可打开', async () => {
  const cache = { open: async () => { throw new Error('存储受限'); } };
  const view = worker(cache);
  await dispatch(view, 'push', { data: { json: () => message } });
  assert.equal(view.shown[0].body, message.body);
  assert.equal((await page({ cache, data: message })).elements['message-body'].textContent, message.body);
  const online = await page({ cache, url: scope + '?message=' + 'f'.repeat(32), fetcher: async () => Response.json(message) });
  assert.equal(online.elements['message-body'].textContent, message.body);
});

test('本地绑定记录为空或 localStorage 不可读时，以现存系统订阅恢复，不要求重新输入绑定码', async () => {
  for (const blockedLocal of [false, true]) {
    const subscription = { endpoint: 'https://web.push.apple.com/test', keys: { p256dh: 'c'.repeat(87), auth: 'd'.repeat(22) } };
    const full = { ...message, id: 'c'.repeat(32), body: '本地绑定丢失后仍完整显示\n' + '正文'.repeat(3000) };
    const loaded = await page({ bound: null, blockedLocal, subscription, fetcher: async (url, request) => {
      assert.equal(url, '/push/subscriptions/restore');
      assert.deepEqual(request, { subscription });
      return Response.json({ ...binding, message: full });
    } });
    assert.equal(loaded.elements['message-body'].textContent, full.body);
    assert.equal(loaded.elements['message-image'].src, full.image);
    assert.equal(loaded.elements.settings.hidden, true);
    assert.equal(loaded.requests.length, 1);
    assert.deepEqual(loaded.updates, ['worker.js']);
  }
});

test('绑定备份可以独立恢复，已安装旧 worker 无本地消息时也能读取当前设备最近通知', async () => {
  const cache = storage();
  await page({ cache, data: message });
  const restored = await page({ cache, bound: null });
  assert.equal(restored.elements['message-body'].textContent, message.body);
  assert.equal(restored.requests.length, 0);
  const latest = await page({ fetcher: async (url, request) => {
    assert.equal(url, '/push/subscriptions/latest');
    assert.deepEqual(request, { id: binding.id, secret: binding.secret });
    return Response.json({ ...message, id: 'c'.repeat(32) });
  } });
  assert.equal(latest.elements.settings.hidden, true);
  assert.equal(latest.elements['message-body'].textContent, message.body);
});
