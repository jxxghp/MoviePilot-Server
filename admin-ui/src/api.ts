import { ref } from 'vue'

export class ApiError extends Error {
  /** 保留 HTTP 状态供登录失效和权限不足分别处理。 */
  constructor(public status: number, message: string) { super(message) }
}
export const notice = ref('')
export const noticeError = ref(false)
let noticeTimer: ReturnType<typeof setTimeout>
/** 通过统一可访问提示区反馈操作结果，不输出原始服务端异常。 */
export function notify(message: string, error = false) {
  notice.value = message
  noticeError.value = error
  clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value = '' }, 7000)
}
let csrf = ''
/** CSRF 标识仅存于内存，页面刷新后从会话接口恢复。 */
export function setCsrf(value: string) { csrf = value }
/** 同源调用后台接口；会话只使用 HttpOnly Cookie。 */
export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/admin/api${path}`, {
      ...options, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...options.headers },
    })
  } catch { throw new ApiError(0, '无法连接服务，请检查网络后重试') }
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('admin-session-expired'))
    const detail = typeof body.detail === 'string' ? body.detail : response.status === 422 ? '请检查输入内容是否符合要求' : '操作失败，请稍后重试'
    throw new ApiError(response.status, detail)
  }
  return body as T
}
/** 统一序列化写请求，避免重复拼接 JSON 和认证头。 */
export function write<T = any>(path: string, method: string, payload: unknown = {}) {
  return api<T>(path, { method, body: JSON.stringify(payload) })
}
/** 展示统计计数，缺失数据与零值有明确区分。 */
export function number(value: unknown) { return value == null ? '—' : Number(value).toLocaleString('zh-CN') }
/** 服务端已有时间字符串按原值展示，ISO UTC 时间转换为浏览器本地时间。 */
export function date(value: unknown) {
  if (!value) return '—'
  const raw = String(value)
  return raw.includes('T') ? new Date(raw).toLocaleString('zh-CN', { hour12: false }) : raw
}
