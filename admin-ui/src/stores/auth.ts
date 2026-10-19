import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, ApiError, setCsrf, write } from '@/api'

export interface User {
  id: number; username: string; display_name: string; role: 'admin' | 'super_admin'
  enabled: boolean; must_change_password: boolean; created_at: string; last_login_at: string | null
}
export const useAuth = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const ready = ref(false)
  /** 刷新后恢复会话，网络故障与未登录分别处理。 */
  async function load() {
    try {
      const result = await api<{ user: User; csrf_token: string }>('/auth/me')
      user.value = result.user; setCsrf(result.csrf_token)
    } catch (error) {
      user.value = null
      if (!(error instanceof ApiError && error.status === 401)) throw error
    } finally { ready.value = true }
  }
  /** 登录成功后只在内存保留用户资料与 CSRF 标识。 */
  async function login(username: string, password: string) {
    const result = await write<{ user: User; csrf_token: string }>('/auth/login', 'POST', { username, password })
    user.value = result.user; setCsrf(result.csrf_token); ready.value = true
  }
  /** 清理当前页面状态，服务端撤销由退出接口负责。 */
  function clear() { user.value = null; setCsrf(''); ready.value = true }
  /** 只有服务端成功撤销会话后才完成正常退出。 */
  async function logout() { await write('/auth/logout', 'POST'); clear() }
  return { user, ready, load, login, clear, logout }
})
