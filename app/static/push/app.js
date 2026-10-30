/* 绑定凭据仅操作本设备及其短期通知全文，不保存 MoviePilot 登录凭据。 */
const element = id => document.getElementById(id);
/** 网站存储暂时不可读时仍继续恢复订阅，避免脚本在绑定页启动前中断。 */
function readLocal(key) { try { return localStorage.getItem(key); } catch { return null; } }
/** localStorage 不是通知订阅本身，写入受限时由私有备份承担恢复。 */
function writeLocal(key, value) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* 保留现存系统订阅。 */ }
}
/** 损坏或不完整的本地记录不能阻止已有系统订阅恢复。 */
function savedBinding(value) {
  return value && ['id', 'secret', 'origin', 'owner'].every(key => typeof value[key] === 'string' && value[key].length > 0) ? value : null;
}
let binding = null;
try { binding = savedBinding(JSON.parse(readLocal('moviepilot.push.binding') || 'null')); }
catch { writeLocal('moviepilot.push.binding', null); }
let installation = readLocal('moviepilot.push.installation');
if (!installation) { installation = crypto.randomUUID(); writeLocal('moviepilot.push.installation', installation); }

/** 以纯文本显示结果，通知和服务端响应不会作为 HTML 执行。 */
function status(text, error = false) { element('status').textContent = text; element('status').dataset.error = String(error); }
/** 请求同源接口，能力令牌不放进 URL、日志或共享缓存。 */
async function api(path, body, method = 'POST') {
  const response = await fetch('/push/' + path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store' });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '操作失败，请重试');
  return result;
}
/** 刷新本机绑定状态，地址采用文本渲染。 */
function render() {
  element('connected').hidden = !binding; element('setup').hidden = !!binding;
  if (!binding) { element('message').hidden = true; element('settings').hidden = false; }
  element('instance').textContent = binding ? '已连接 ' + binding.origin : '';
}
/** 将 VAPID 公钥转换为浏览器订阅需要的字节序列。 */
function applicationKey(value) {
  const raw = atob((value + '='.repeat((4 - value.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, char => char.charCodeAt(0));
}
/** 同一时刻只执行一项用户操作，完成或失败后均恢复按钮。 */
async function perform(action) {
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  try { await action(); } catch (error) { status(error.message || '操作失败，请重试', true); }
  finally { for (const button of document.querySelectorAll('button')) button.disabled = false; render(); }
}
element('bind').addEventListener('click', () => perform(async () => {
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) throw new Error('请在支持通知的浏览器中使用，iPhone 需要先添加到主屏幕');
  if (/iPhone|iPad/.test(navigator.userAgent) && !navigator.standalone && !matchMedia('(display-mode: standalone)').matches) throw new Error('请先添加到主屏幕，再从主屏幕打开并绑定');
  const code = element('code').value.trim();
  if (code.length < 32) throw new Error('请粘贴 App 中生成的完整绑定码');
  // iOS 必须在点击事件链中申请权限，不能先等待网络或加载配置。
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') throw new Error('通知权限未开启，请在系统设置中允许通知');
  const config = await fetch('/push/config', { cache: 'no-store' }).then(response => response.json());
  if (!config.webpush) throw new Error('通知服务尚未配置，请联系服务管理员');
  const registration = await navigator.serviceWorker.register('worker.js');
  await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: applicationKey(config.vapid_public_key) });
  binding = await api('subscriptions', { code, installation, subscription: subscription.toJSON() });
  messageRevision++;
  await notificationMessages.clear().catch(() => {});
  writeLocal('moviepilot.push.binding', JSON.stringify(binding));
  await notificationMessages.writeBinding(binding).catch(() => {}); element('code').value = '';
  status('已绑定，可以发送测试通知');
}));
element('test').addEventListener('click', () => perform(async () => {
  const result = await api('subscriptions/test', { id: binding.id, secret: binding.secret });
  status(result.accepted ? '测试通知已提交，请检查系统通知' : result.reason, !result.accepted);
}));
element('remove').addEventListener('click', () => perform(async () => {
  try { await api('subscriptions', { id: binding.id, secret: binding.secret }, 'DELETE'); }
  catch (error) { if (!error.message.includes('不存在')) throw error; }
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = await registration?.pushManager.getSubscription();
  if (subscription) await subscription.unsubscribe();
  messageRevision++; binding = null; writeLocal('moviepilot.push.binding', null);
  await notificationMessages.clear().catch(() => {}); status('已解除绑定');
}));
/** 仅加载网页图片；旧消息缺少图片或含不安全引用时继续显示正文和跳转入口。 */
function messageImage(value) {
  if (typeof value !== 'string' || value.length > 2048 || /[\\\s\p{Cc}\p{Cf}]/u.test(value)) return '';
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch { return ''; }
}
/** 图片失败不阻断消息阅读，也不影响用户返回 App。 */
element('message-image').addEventListener('error', () => { element('message-image').hidden = true; });
/** 新消息加载前清除上一条海报和跳转，错误也显示在消息界面中。 */
function clearMessage() {
  element('message').hidden = true;
  element('settings').hidden = false;
  element('message-title').textContent = ''; element('message-body').textContent = '';
  element('message-image').hidden = true; element('message-image').removeAttribute('src');
  element('open-app').removeAttribute('href'); element('open-web').removeAttribute('href');
  element('message-note').hidden = true; element('message-note').textContent = '';
  element('retry').hidden = true;
}

/** 通知只打开已绑定实例的相对路由，旧账号内容不在新绑定中展示。 */
function showMessage(data) {
  if (data.origin !== binding.origin || data.owner !== binding.owner || typeof data.route !== 'string' || data.route.length > 2048 || !data.route.startsWith('/') || data.route.startsWith('//') || /[\\\p{Cc}\p{Cf}]/u.test(data.route)) throw new Error('这条通知不属于当前绑定或跳转无效，请进入 MoviePilot App 查看');
  element('open-web').href = binding.origin + '/#' + data.route;
  const params = new URLSearchParams({ origin: binding.origin, route: data.route, owner: binding.owner });
  element('open-app').href = 'moviepilot-app://notification?' + params.toString();
  element('message-title').textContent = typeof data.title === 'string' && data.title ? data.title : 'MoviePilot 通知';
  element('message-body').textContent = typeof data.body === 'string' ? data.body : '';
  const image = messageImage(data.image);
  if (image) { element('message-image').src = image; element('message-image').hidden = false; }
}

let messageRevision = 0;
let messageId = '';
/** URL、worker 消息和页面恢复共用入口；点击参数丢失时仍能从私有暂存恢复。 */
async function openMessage(identifier = '') {
  const revision = ++messageRevision;
  if (!binding || new URLSearchParams(location.search).has('settings')) return;
  let id = identifier || new URLSearchParams(location.search).get('message') || '';
  let record = null;
  try {
    if (location.hash.startsWith('#open=')) {
      const data = JSON.parse(decodeURIComponent(location.hash.slice(6)));
      try { id = await notificationMessages.save(data, 'clicked', !/^[a-f0-9]{32}$/.test(data.id || '')); }
      catch { record = { data, complete: !/^[a-f0-9]{32}$/.test(data.id || '') }; id = data.id || ''; }
      history.replaceState(null, '', location.pathname + '?message=' + encodeURIComponent(id));
    }
    try { record ||= await notificationMessages.read(id); } catch { /* 带标识的消息仍可在线读取。 */ }
    if (!record && !id) {
      // 已安装的旧 worker 没有本地消息暂存时，仍可从当前绑定读取最近一条。
      try {
        const data = await api('subscriptions/latest', { id: binding.id, secret: binding.secret });
        if (data.id) record = { data, complete: true, reason: 'recent' };
      } catch { /* 没有点击意图且网络不可用时保留通知管理入口。 */ }
    }
    if (!record && !id) return;
    if (revision !== messageRevision) return;
    clearMessage();
    element('message').hidden = false; element('settings').hidden = true;
    element('message-title').textContent = '正在加载消息';
    id ||= record.data.id || '';
    messageId = id;
    let data = record?.data;
    let reason = '';
    let complete = record?.complete || false;
    if (!record?.complete && /^[a-f0-9]{32}$/.test(id)) {
      try {
        data = await api('subscriptions/message', { id: binding.id, secret: binding.secret, message: id });
        complete = true;
      } catch (error) { reason = error.message; }
    }
    if (revision !== messageRevision) return;
    if (!data || (record?.expired && !complete)) throw new Error(reason || '这条通知已过期，请进入 MoviePilot App 查看');
    showMessage(data);
    try {
      await notificationMessages.save(data, identifier || location.search ? 'clicked' : '', complete);
      // 点击记录已落盘后清理地址；旧参数不能在下一次恢复前台时覆盖新收到的通知。
      if (location.search) history.replaceState(null, '', location.pathname);
    } catch { /* 私有缓存受限时保留标识供重载在线读取，当前全文仍可阅读。 */ }
    if (reason || record?.reason === 'recent') {
      element('message-note').textContent = reason ? '当前显示通知预览。' + reason : '最近收到的通知';
      element('message-note').hidden = false;
      element('retry').hidden = !reason;
    }
  } catch (error) {
    if (revision !== messageRevision) return;
    clearMessage(); element('message').hidden = false; element('settings').hidden = true;
    element('message-title').textContent = '消息暂时无法打开';
    element('message-body').textContent = error.message || '请进入 MoviePilot App 查看消息';
    element('retry').hidden = false;
  }
}
element('retry').addEventListener('click', () => openMessage(messageId));
render();
/** 本地绑定缺失并不等于系统订阅消失，先恢复能力再决定是否显示输入绑定码。 */
async function initialize() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('worker.js').catch(() => {});
  if (!binding) {
    element('setup').hidden = true;
    status('正在检查已有通知订阅…');
    try { binding = savedBinding(await notificationMessages.readBinding()); } catch { /* 可继续从现存订阅证明归属。 */ }
    if (!binding && 'serviceWorker' in navigator) {
      try {
        const registration = await navigator.serviceWorker.getRegistration('./');
        const subscription = await registration?.pushManager.getSubscription();
        if (subscription) {
          const recovered = await api('subscriptions/restore', { subscription: subscription.toJSON() });
          const { message, ...restored } = recovered;
          binding = restored;
          if (message?.id) await notificationMessages.save(message, 'recent', true).catch(() => {});
        }
      } catch (error) { status(error.message || '无法恢复通知订阅，请稍后重试', true); }
    }
    if (binding) status('已恢复原通知绑定');
    else if (element('status').textContent === '正在检查已有通知订阅…') status('请从接收通知的主屏幕应用打开；未绑定时粘贴 App 中生成的绑定码');
  }
  if (binding) {
    writeLocal('moviepilot.push.binding', JSON.stringify(binding));
    await notificationMessages.writeBinding(binding).catch(() => {});
  }
  render();
  await openMessage();
}
const initialMessage = initialize();
window.addEventListener('hashchange', () => openMessage());
window.addEventListener('pageshow', () => openMessage());
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void openMessage(); });
if ('serviceWorker' in navigator) navigator.serviceWorker.addEventListener('message', event => {
  if (event.data?.type === 'moviepilot-notification') {
    history.replaceState(null, '', location.pathname);
    void openMessage(event.data.id || '');
  }
});
