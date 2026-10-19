import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { api, ApiError, setCsrf, write } from '@/api'
import PasswordForm from '@/components/PasswordForm.vue'

/** 构造最小 HTTP 响应，保留前端错误分支和 Cookie 请求选项。 */
function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}})
}
afterEach(() => { vi.unstubAllGlobals(); setCsrf('') })

describe('后台认证传输边界', () => {
  it('写操作携带内存 CSRF 标识并使用同源 Cookie', async () => {
    const fetch = vi.fn().mockResolvedValue(response({ok: true})); vi.stubGlobal('fetch', fetch)
    setCsrf('csrf-token'); await write('/users', 'POST', {username: 'staff'})
    expect(fetch).toHaveBeenCalledWith('/admin/api/users', expect.objectContaining({credentials: 'same-origin', method: 'POST', headers: expect.objectContaining({'X-CSRF-Token': 'csrf-token'})}))
  })
  it('会话失效发出事件，权限不足保持当前会话', async () => {
    const expired = vi.fn(); window.addEventListener('admin-session-expired', expired)
    const fetch = vi.fn().mockResolvedValueOnce(response({detail: '无权限'}, 403)).mockResolvedValueOnce(response({detail: '会话失效'}, 401)); vi.stubGlobal('fetch', fetch)
    await expect(api('/users')).rejects.toMatchObject({status: 403}); expect(expired).not.toHaveBeenCalled()
    await expect(api('/users')).rejects.toMatchObject({status: 401}); expect(expired).toHaveBeenCalledOnce()
    window.removeEventListener('admin-session-expired', expired)
  })
  it('登录密码错误不会广播会话过期', async () => {
    const expired = vi.fn(); window.addEventListener('admin-session-expired', expired)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({detail: '用户名或密码错误'}, 401)))
    await expect(write('/auth/login', 'POST', {})).rejects.toBeInstanceOf(ApiError); expect(expired).not.toHaveBeenCalled()
    window.removeEventListener('admin-session-expired', expired)
  })
  it('网络故障以可读错误呈现', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
    await expect(api('/overview')).rejects.toMatchObject({status: 0, message: '无法连接服务，请检查网络后重试'})
  })
})

describe('首次登录改密表单', () => {
  it('两次新密码不同不能提交', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    const wrapper = mount(PasswordForm)
    await wrapper.get('#old-password').setValue('old-password-123')
    await wrapper.get('#new-password').setValue('new-password-123')
    await wrapper.get('#repeat-password').setValue('different-password')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(fetch).not.toHaveBeenCalled(); expect(wrapper.get('[role=alert]').text()).toContain('不一致'); wrapper.unmount()
  })
  it('提交旧密码与一致的新密码，成功后通知会话重新登录', async () => {
    const fetch = vi.fn().mockResolvedValue(response({ok: true})); vi.stubGlobal('fetch', fetch)
    const wrapper = mount(PasswordForm)
    await wrapper.get('#old-password').setValue('old-password-123')
    await wrapper.get('#new-password').setValue('new-password-123')
    await wrapper.get('#repeat-password').setValue('new-password-123')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({current_password: 'old-password-123', password: 'new-password-123'})
    expect(wrapper.emitted('changed')).toHaveLength(1); wrapper.unmount()
  })
})
