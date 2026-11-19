import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useAuth } from '@/stores/auth'
import Features from '@/pages/Features.vue'

/** 构造后台功能读取结果，仅包含合成值。 */
function response(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => { document.body.innerHTML = ''; vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('Server 中心知识总开关', () => {
  it('loads the effective value and persists an explicit change', async () => {
    setActivePinia(createPinia())
    useAuth().user = {
      id: 1, username: 'synthetic', display_name: 'Synthetic', role: 'super_admin', enabled: true,
      must_change_password: false, created_at: '', last_login_at: null,
    }
    let saved: Record<string, unknown> | undefined
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        saved = JSON.parse(String(init.body)) as Record<string, unknown>
        return response({ ok: true })
      }
      return response({
        central_knowledge_enabled: true, community_medals_enabled: false,
        community_medals_preview_users: [], github_admin_users: [],
      })
    }))
    const wrapper = mount(Features, { attachTo: document.body })
    await flushPromises()
    const control = wrapper.get('#central-knowledge-enabled')
    expect(control.attributes('aria-checked')).toBe('true')
    await control.trigger('click')
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(saved).toMatchObject({ central_knowledge_enabled: false })
    wrapper.unmount()
  })
})
