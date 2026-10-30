/* 绑定凭据仅能测试和解除本网页的通知；不保存 MoviePilot 登录凭据。 */
const element = id => document.getElementById(id);
let binding = null;
try { binding = JSON.parse(localStorage.getItem('moviepilot.push.binding') || 'null'); }
catch { localStorage.removeItem('moviepilot.push.binding'); }
let installation = localStorage.getItem('moviepilot.push.installation');
if (!installation) { installation = crypto.randomUUID(); localStorage.setItem('moviepilot.push.installation', installation); }

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
  if (!binding) element('message').hidden = true;
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
  localStorage.setItem('moviepilot.push.binding', JSON.stringify(binding)); element('code').value = '';
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
  binding = null; localStorage.removeItem('moviepilot.push.binding'); status('已解除绑定');
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
/** 通知点击只交接已绑定实例的相对路由，其他来源或其他账号的旧消息不能打开。 */
function openMessage() {
  element('message').hidden = true;
  element('message-title').textContent = ''; element('message-body').textContent = '';
  element('message-image').hidden = true; element('message-image').removeAttribute('src');
  element('open-app').removeAttribute('href'); element('open-web').removeAttribute('href');
  if (!location.hash.startsWith('#open=') || !binding) return;
  try {
    const data = JSON.parse(decodeURIComponent(location.hash.slice(6)));
    history.replaceState(null, '', location.pathname);
    if (data.origin !== binding.origin || data.owner !== binding.owner || typeof data.route !== 'string' || data.route.length > 2048 || !data.route.startsWith('/') || data.route.startsWith('//') || /[\\\p{Cc}\p{Cf}]/u.test(data.route)) return;
    element('open-web').href = binding.origin + '/#' + data.route;
    const params = new URLSearchParams({ origin: binding.origin, route: data.route, owner: binding.owner });
    element('open-app').href = 'moviepilot-app://notification?' + params.toString();
    element('message-title').textContent = typeof data.title === 'string' && data.title ? data.title : 'MoviePilot 通知';
    element('message-body').textContent = typeof data.body === 'string' ? data.body : '';
    const image = messageImage(data.image);
    if (image) { element('message-image').src = image; element('message-image').hidden = false; }
    element('message').hidden = false;
  } catch { status('这条通知无法打开，请进入 App 查看消息', true); }
}
render(); openMessage();
window.addEventListener('hashchange', openMessage);
// 已绑定网页也检查 worker 更新，补齐海报展示时无需解除绑定或生成新绑定码。
if (binding && 'serviceWorker' in navigator) navigator.serviceWorker.register('worker.js').catch(() => {});
