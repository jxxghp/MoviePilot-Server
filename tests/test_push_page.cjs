/** 执行真实网页脚本，校验消息、海报、深链和账号隔离；不模拟系统投递成功。 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assets = resolve(__dirname, '../app/static/push');
const binding = { origin: 'https://moviepilot.test/base', owner: 'a'.repeat(32) };
const message = { ...binding, title: '电影下载完成', body: '第一行\n第二行', route: '/history?name=中文', image: 'https://images.test/poster.jpg' };

/** 只实现脚本使用的 DOM 属性，禁止通过 HTML 渲染消息正文。 */
class Element {
  /** 每个页面元素独立保存属性和事件。 */
  constructor() { this.hidden = true; this.attributes = {}; this.events = {}; this.dataset = {}; this.textContent = ''; }
  /** 保留可点击的原始深链，不替脚本补写或转义。 */
  set href(value) { this.attributes.href = value; }
  /** 读取脚本实际设置的深链。 */
  get href() { return this.attributes.href; }
  /** 保留实际图片来源。 */
  set src(value) { this.attributes.src = value; }
  /** 读取图片来源，验证旧图已清理。 */
  get src() { return this.attributes.src; }
  /** 安全渲染必须始终使用纯文本。 */
  set innerHTML(_value) { throw new Error('消息不能作为 HTML 执行'); }
  /** 同一个元素注册的用户操作独立保存。 */
  addEventListener(name, listener) { this.events[name] = listener; }
  /** 清除图片或链接，模拟浏览器的属性删除。 */
  removeAttribute(name) { delete this.attributes[name]; }
}

/** 以实际 HTML 中的元素建立最小 DOM，直接运行完整 app.js。 */
function page(data, bound = binding) {
  const html = readFileSync(resolve(assets, 'index.html'), 'utf8');
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const windowEvents = {};
  const location = new URL('https://relay.test/push/app/');
  location.hash = 'open=' + encodeURIComponent(JSON.stringify(data));
  const updates = [];
  const context = vm.createContext({
    URL, URLSearchParams, location,
    history: { replaceState: (_state, _title, path) => { location.href = new URL(path, location).href; } },
    localStorage: { getItem: key => key.endsWith('binding') ? JSON.stringify(bound) : 'installation-001' },
    document: { getElementById: id => { assert.ok(elements[id], id); return elements[id]; } },
    window: { addEventListener: (name, listener) => { windowEvents[name] = listener; } },
    navigator: { serviceWorker: { register: script => { updates.push(script); return Promise.resolve({}); } } },
  });
  vm.runInContext(readFileSync(resolve(assets, 'app.js'), 'utf8'), context);
  return { elements, context, location, windowEvents, updates };
}

test('点击通知显示本条标题、换行正文、海报及相同实例和账号的 App 链接', () => {
  const html = readFileSync(resolve(assets, 'index.html'), 'utf8');
  assert.match(html, />在 MoviePilot App 中打开<\/a>/);
  const { elements, location, updates } = page(message);
  assert.equal(elements.message.hidden, false);
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
  assert.deepEqual(updates, ['worker.js']);
});

test('系统通知点击将本条内容交给详情页，兼容已有窗口和首次打开', async () => {
  for (const existing of [true, false]) {
    const handlers = {};
    const targets = [];
    let closed = false;
    let focused = false;
    const scope = 'https://relay.test/push/app/';
    const current = { url: scope, navigate: async target => targets.push(target), focus: async () => { focused = true; } };
    const worker = vm.createContext({ URL, self: {
      addEventListener: (name, callback) => { handlers[name] = callback; },
      registration: { scope },
      clients: { matchAll: async () => existing ? [current] : [], openWindow: async target => targets.push(target) },
    } });
    vm.runInContext(readFileSync(resolve(assets, 'worker.js'), 'utf8'), worker);
    let completed;
    handlers.notificationclick({ notification: { data: message, close: () => { closed = true; } }, waitUntil: promise => { completed = promise; } });
    await completed;
    assert.equal(closed, true);
    assert.equal(focused, existing);
    assert.equal(targets.length, 1);
    const target = new URL(targets[0]);
    assert.equal(target.origin + target.pathname, scope);
    const received = JSON.parse(decodeURIComponent(target.hash.slice(6)));
    assert.deepEqual(received, message);
    assert.equal(page(received).elements['message-image'].src, message.image);
  }
});

test('旧通知无海报、加载失败、正文含 HTML 均不影响阅读和 App 入口', () => {
  const { elements } = page({ ...message, image: undefined, body: '<img src=x onerror=alert(1)>' });
  assert.equal(elements['message-image'].hidden, true);
  assert.equal(elements['message-body'].textContent, '<img src=x onerror=alert(1)>');
  assert.ok(elements['open-app'].href);
  const poster = page(message).elements;
  poster['message-image'].events.error();
  assert.equal(poster['message-image'].hidden, true);
  assert.equal(poster.message.hidden, false);
  assert.ok(poster['open-app'].href);
});

test('其他账号、实例、外站路由及反斜线不能显示内容或保留上一条跳转', () => {
  for (const invalid of [{ owner: 'b'.repeat(32) }, { origin: 'https://other.test' }, { route: '//other.test' }, { route: '/\\other.test' }]) {
    const view = page(message);
    view.location.hash = 'open=' + encodeURIComponent(JSON.stringify({ ...message, ...invalid }));
    view.windowEvents.hashchange();
    assert.equal(view.elements.message.hidden, true);
    assert.equal(view.elements['message-title'].textContent, '');
    assert.equal(view.elements['message-body'].textContent, '');
    assert.equal(view.elements['message-image'].src, undefined);
    assert.equal(view.elements['open-app'].href, undefined);
    assert.equal(view.elements['open-web'].href, undefined);
  }
  assert.equal(page(message, null).elements.message.hidden, true);
});

test('网页与系统通知均拒绝脚本、本地文件、凭据地址和畸形图片', async () => {
  const handlers = {};
  const shown = [];
  const scope = 'https://relay.test/push/app/';
  const worker = vm.createContext({ URL, self: {
    addEventListener: (name, callback) => { handlers[name] = callback; },
    registration: { scope, showNotification: async (title, options) => { shown.push({ title, options }); } },
  } });
  vm.runInContext(readFileSync(resolve(assets, 'worker.js'), 'utf8'), worker);
  for (const image of ['javascript:alert(1)', 'data:image/png;base64,AA', 'file:///secret.png', '//other.test/image.jpg', 'https://user:secret@images.test/x', 'https://images.test/\\x', 'https://images.test/\nx']) {
    assert.equal(page({ ...message, image }).elements['message-image'].hidden, true);
    let completed;
    handlers.push({ data: { json: () => ({ ...message, image }) }, waitUntil: promise => { completed = promise; } });
    await completed;
    const { options } = shown.at(-1);
    assert.equal(options.icon, 'icon.png');
    assert.equal(options.image, undefined);
  }
  let completed;
  handlers.push({ data: { json: () => message }, waitUntil: promise => { completed = promise; } });
  await completed;
  assert.equal(shown.at(-1).options.image, message.image);
  assert.equal(shown.at(-1).options.icon, message.image);
  assert.equal(shown.at(-1).options.data.body, message.body);
  assert.equal(shown.at(-1).options.data.route, message.route);
});
