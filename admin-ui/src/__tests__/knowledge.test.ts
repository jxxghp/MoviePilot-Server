import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { differences, formFor, merged, usePage } from '@/knowledge'
import { useAuth } from '@/stores/auth'
import SourceSettings from '@/components/knowledge/SourceSettings.vue'
import Catalog from '@/components/knowledge/Catalog.vue'
import Documents from '@/components/knowledge/Documents.vue'
import Jobs from '@/components/knowledge/Jobs.vue'
import Usage from '@/components/knowledge/Usage.vue'

const id = 'a'.repeat(32), parent = 'b'.repeat(32)
const origin = { id, version: 7, name: '合成来源', connector: 'http' as const, enabled: true, collection_id: parent,
  url: 'https://docs.example/rules', repository: '', branch: 'main', paths: [], selector: '', refresh_minutes: 0, site_id: null }
const collection = { id: parent, version: 1, name: '原集合', slug: 'synthetic', description: '', domain: 'mp', product: 'moviepilot', enabled: true, has_sources: false }
const doc = { id, version: 5, source_id: parent, external_id: 'synthetic', head_revision: 3, published_revision: null,
  revision: { number: 1, title: '所见历史修订', content: '<script>window.knowledgeExecuted = true</script>合成内容', state: 'draft', product: 'moviepilot', version_scope: 'unknown', min_version: null, max_version: null, source_url: '', source_commit: '', fetched_at: null, valid_until: null } }
let wrapper: VueWrapper | undefined

/** 请求和权限测试不依赖真实布局测量，观测器只提供可关闭的浏览器接口。 */
class SyntheticObserver {
  /** 无几何回调，避免将 jsdom 尺寸当作浏览器布局结果。 */
  observe() {}
  /** 允许组件正常解除观测，不在测试间保留目标。 */
  unobserve() {}
  /** 卸载清理不创建后续异步回调。 */
  disconnect() {}
}

/** 仅合成 JSON 响应，不读取运行服务或外部站点。 */
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }) }
/** Portal 弹窗中的按钮按可见文案定位，不依赖组件内部实现。 */
function button(label: string) { const item = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(node => node.textContent?.trim() === label); if (!item) throw new Error(`missing button ${label}`); return item }
/** 原生输入事件驱动 Vue 模型，保持与浏览器表单相同的写入边界。 */
async function fill(selector: string, value: string) { const node = document.querySelector<HTMLInputElement>(selector)!; node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); await nextTick() }
/** 提交真实表单并等待异步读取和 Vue 更新。 */
async function submit(selector: string) { document.querySelector(selector)!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await flushPromises() }
beforeEach(() => {
  setActivePinia(createPinia())
  useAuth().user = { id: 1, username: 'synthetic', display_name: '合成管理员', role: 'super_admin', enabled: true, must_change_password: false, created_at: '', last_login_at: null }
  vi.stubGlobal('ResizeObserver', SyntheticObserver)
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = ''; vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('知识编辑与请求生命周期', () => {
  it('只投影表单字段，列表和凭据元数据不会混入写请求', () => {
    const form = formFor('sources', { ...origin, has_credential: true, has_documents: true })
    expect(form).not.toHaveProperty('id'); expect(form).not.toHaveProperty('version'); expect(form).not.toHaveProperty('has_credential')
    expect(Object.keys(form)).toEqual(['name', 'collection_id', 'site_id', 'connector', 'url', 'repository', 'branch', 'paths', 'selector', 'refresh_minutes', 'enabled'])
  })
  it('三方对照保留单方输入，双方更改同一字段时必须选择', () => {
    const mine = { name: '我的标题', description: '我的说明', enabled: true }
    const changes = differences({ name: '旧标题', description: '', enabled: true }, mine, { name: '别人的标题', description: '', enabled: false })
    expect(changes).toMatchObject([{ key: 'name', choice: '' }, { key: 'enabled', choice: 'latest' }])
    expect(() => merged(mine, changes)).toThrow('逐项选择')
    changes[0].choice = 'latest'
    expect(merged(mine, changes)).toEqual({ name: '别人的标题', description: '我的说明', enabled: false })
  })
  it('旧分页响应不能覆盖新查询，卸载后不会重新读网络', async () => {
    const pending: ((value: Response) => void)[] = []
    const fetch = vi.fn().mockImplementation(() => new Promise<Response>(resolve => pending.push(resolve))); vi.stubGlobal('fetch', fetch)
    let state!: ReturnType<typeof usePage<{ id: string }>>
    wrapper = mount(defineComponent({ setup() { state = usePage(() => '/knowledge/sources'); return () => h('p') } }))
    state.query.value = '新条件'; const latest = state.load(true)
    pending[1](response({ items: [{ id: 'latest' }], total: 1 })); await latest
    pending[0](response({ items: [{ id: 'old' }], total: 20 })); await flushPromises()
    expect(state.rows.value).toEqual([{ id: 'latest' }]); expect(state.total.value).toBe(1)
    wrapper.unmount(); wrapper = undefined; await state.load(); expect(fetch).toHaveBeenCalledTimes(2)
  })
  it('读取失败展示错误，不把失败伪装成没有匹配数据', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ detail: '服务暂不可用' }, 503)))
    wrapper = mount(Catalog, { props: { kind: 'collections' } }); await flushPromises()
    expect(wrapper.text()).toContain('读取失败，请重试'); expect(wrapper.text()).not.toContain('暂无符合条件')
    expect(wrapper.text()).not.toContain('共 0 条')
  })
  it('并发冲突保留表单，采用差异之后仍需明确再次提交', async () => {
    let reads = 0, writes = 0
    const payloads: any[] = []
    vi.stubGlobal('fetch', vi.fn(async (path: string, options: RequestInit) => {
      if (options.method === 'PUT') { payloads.push(JSON.parse(String(options.body))); writes++; return writes === 1 ? response({ detail: '知识记录已被其他操作修改，请刷新后重试' }, 409) : response({ ...collection, version: 3 }) }
      if (path === `/admin/api/knowledge/collections/${parent}`) { reads++; return response(reads === 1 ? collection : { ...collection, version: 2, name: '远端标题' }) }
      return response({ items: [collection], total: 1 })
    }))
    wrapper = mount(Catalog, { props: { kind: 'collections' }, attachTo: document.body }); await flushPromises()
    button('编辑').click(); await flushPromises(); await fill('#catalog-name', '我的标题'); await fill('#catalog-description', '我的说明')
    await submit('#knowledge-catalog-form')
    expect(writes).toBe(1); expect((document.querySelector('#catalog-name') as HTMLInputElement).value).toBe('我的标题')
    expect(button('保存').disabled).toBe(true)
    const choice = document.querySelector<HTMLInputElement>('input[name="change-name"][value="latest"]')!; choice.checked = true; choice.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
    button('采用对照结果').click(); await nextTick(); expect(writes).toBe(1)
    await submit('#knowledge-catalog-form')
    expect(payloads[1]).toMatchObject({ expected_version: 2, name: '远端标题', description: '我的说明' })
  })
})

describe('采集凭据与范围', () => {
  it('普通管理员只能查看配置状态，角色失去超管后清空输入', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => response(path.endsWith('/credential')
      ? { configured: true, state: 'configured', version: 3, source_version: 7, kind: 'cookie', host: 'docs.example' } : origin)))
    wrapper = mount(SourceSettings, { props: { sourceId: id, mode: 'credential' }, attachTo: document.body }); await flushPromises()
    expect(document.body.textContent).toContain('目标：https://docs.example/rules')
    expect((document.querySelector('#knowledge-secret') as HTMLInputElement).value).toBe('')
    await fill('#knowledge-secret', 'synthetic=session')
    useAuth().user!.role = 'admin'; await nextTick()
    expect(document.querySelector('#knowledge-secret')).toBeNull(); expect(document.body.textContent).toContain('只有超级管理员')
    useAuth().user!.role = 'super_admin'; await nextTick()
    expect((document.querySelector('#knowledge-secret') as HTMLInputElement).value).toBe('')
  })
  it('凭据替换发送双版本，失败后清空认证值且不自动重试', async () => {
    let writeCount = 0, sent: any
    vi.stubGlobal('fetch', vi.fn(async (path: string, options: RequestInit) => {
      if (options.method === 'PUT') { writeCount++; sent = JSON.parse(String(options.body)); return response({ detail: 'knowledge_credential_changed' }, 409) }
      return response(path.endsWith('/credential') ? { configured: true, state: 'configured', version: 3, source_version: 7, kind: 'cookie', host: 'docs.example' } : origin)
    }))
    wrapper = mount(SourceSettings, { props: { sourceId: id, mode: 'credential' }, attachTo: document.body }); await flushPromises()
    await fill('#knowledge-secret', 'synthetic=session'); await submit('#knowledge-secret-form')
    expect(sent).toEqual({ expected_source_version: 7, expected_credential_version: 3, kind: 'cookie', secret: 'synthetic=session' })
    expect(writeCount).toBe(1); expect((document.querySelector('#knowledge-secret') as HTMLInputElement).value).toBe('')
    expect(button('替换凭据').disabled).toBe(true)
  })
  it('未保存的版本范围不能直接触发采集', async () => {
    const fetch = vi.fn(async (path: string, options: RequestInit) => {
      if (options.method === 'PUT') return response({ source_version: 8, policy: { version_scope: 'all' }, job: null, next_at: null })
      return response(path.endsWith('/collection') ? { source_version: 7, policy: { version_scope: 'unknown' }, job: null, next_at: null } : origin)
    }); vi.stubGlobal('fetch', fetch)
    wrapper = mount(SourceSettings, { props: { sourceId: id, mode: 'collection' }, attachTo: document.body }); await flushPromises()
    const scope = document.querySelector<HTMLSelectElement>('#knowledge-policy')!; scope.value = 'all'; scope.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
    expect(button('立即采集').disabled).toBe(true)
    await submit('#knowledge-policy-form'); expect(button('立即采集').disabled).toBe(false)
    expect(JSON.parse(String(fetch.mock.calls.find(call => call[1].method === 'PUT')![1].body))).toMatchObject({ expected_version: 7, version_scope: 'all' })
  })
})

describe('审核与任务', () => {
  it('任务深链读取实际索引文档和分块统计', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => response(path === `/admin/api/knowledge/jobs/${id}`
      ? { id, version: 1, kind: 'index', status: 'succeeded', attempts: 1, max_attempts: 3, processed_documents: 3, processed_chunks: 7, outcome: 'activated' }
      : { items: [], total: 0 })))
    wrapper = mount(Jobs, { props: { jobId: id }, attachTo: document.body }); await flushPromises()
    expect(document.body.textContent).toContain('已处理文档 3 · 索引分块 7')
  })
  it('只发布当前所见历史修订，正文按文本展示而不会执行 HTML', async () => {
    let published: any
    vi.stubGlobal('fetch', vi.fn(async (path: string, options: RequestInit) => {
      if (options.method === 'POST') { published = JSON.parse(String(options.body)); return response({ document: { ...doc, version: 6, published_revision: 1 }, job: null, index_pending: false }) }
      return response(path.includes('/documents/' + id) ? doc : { items: [], total: 0 })
    }))
    wrapper = mount(Documents, { props: { documentId: id }, attachTo: document.body }); await flushPromises()
    expect(document.querySelector('pre')?.textContent).toContain('<script>')
    expect(document.querySelector('script')).toBeNull()
    button('发布所见 r1').click(); await nextTick(); await fill('#confirm-input', '发布'); await submit('#confirm-action')
    expect(published).toEqual({ expected_version: 5, revision: 1 })
  })
  it('关闭功能时不轮询，启用后的活动任务轮询在卸载时终止', async () => {
    vi.useFakeTimers(); vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    const fetch = vi.fn(async () => response({ items: [{ id, version: 1, kind: 'index', status: 'queued', attempts: 0, max_attempts: 3, outcome: '' }], total: 1 })); vi.stubGlobal('fetch', fetch)
    wrapper = mount(Jobs, { props: { enabled: false } }); await flushPromises()
    await vi.advanceTimersByTimeAsync(10000); expect(fetch).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ enabled: true }); await vi.advanceTimersByTimeAsync(5000); expect(fetch).toHaveBeenCalledTimes(2)
    wrapper.unmount(); wrapper = undefined; await vi.advanceTimersByTimeAsync(15000); expect(fetch).toHaveBeenCalledTimes(2)
  })
})

describe('知识使用统计与长标题防溢出', () => {
  it('展示调用汇总、渠道分布、热门命中排行并支持跳转知识详情与切换天数', async () => {
    const fetch = vi.fn(async (_path: string) => response({
      totals: { total_calls: 42, search_calls: 30, search_matched_calls: 24, search_hits: 51, read_calls: 12, read_hits: 11, total_hits: 62, hit_documents: 9, published_documents: 20 },
      windows: { today_calls: 6, last_7_days_calls: 25, last_30_days_calls: 42 },
      channels: {
        agent: { total_calls: 35, search_calls: 25, search_matched_calls: 20, search_hits: 43, read_calls: 10, read_hits: 9 },
        client: { total_calls: 7, search_calls: 5, search_matched_calls: 4, search_hits: 8, read_calls: 2, read_hits: 2 },
      },
      daily: [{ day: '2026-11-24', total_calls: 6, search_calls: 5, search_matched_calls: 4, search_hits: 8, read_calls: 1, read_hits: 1 }],
      top_documents: [{
        document_id: id, external_id: 'page:3bfcdb1e7c5f5578103f2f8071131a7790fa0a3d08d84f164a4fb56f0b908628',
        title: 'https://wiki.hhanclub.net/zh/%E7%AB%99%E7%82%B9%E8%A7%84%E5%88%99/%E5%AD%97%E5%B9%95%E8%A7%84%E5%88%99',
        source_id: parent, source_name: '站点规则来源', collection_name: 'PT 规则', domain: 'pt',
        published_revision: 1, search_hits: 15, read_hits: 5, total_hits: 20, last_hit_at: '2026-11-24T12:00:00+00:00',
      }],
    }))
    vi.stubGlobal('fetch', fetch)
    wrapper = mount(Usage, { attachTo: document.body }); await flushPromises()
    expect(wrapper.text()).toContain('42'); expect(wrapper.text()).toContain('80%'); expect(wrapper.text()).toContain('62')
    expect(wrapper.text()).toContain('公共 Agent 接口'); expect(wrapper.text()).toContain('独立授权客户端')
    expect(wrapper.text()).toContain('2026-11-24')
    const titleCell = wrapper.find('td.whitespace-normal')
    expect(titleCell.exists()).toBe(true)
    expect(titleCell.classes()).not.toContain('whitespace-nowrap')
    button('查看与审核').click(); await nextTick()
    expect(wrapper.emitted('document')).toEqual([[id]])
    button('近 30 天').click(); await flushPromises()
    expect(fetch.mock.calls.some(call => String(call[0]).includes('days=30'))).toBe(true)
  })
  it('知识列表显示命中次数、支持按命中排序并对超长无空格标题应用换行保护', async () => {
    const longUrl = 'https://wiki.hhanclub.net/zh/%E7%AB%99%E7%82%B9%E8%A7%84%E5%88%99/%E5%AD%97%E5%B9%95%E5%8C%BA%E8%A7%84%E5%88%99'
    const fetch = vi.fn(async (_path: string) => response({
      items: [{
        ...doc, published_revision: 1,
        external_id: 'page:3bfcdb1e7c5f5578103f2f8071131a7790fa0a3d08d84f164a4fb56f0b908628',
        usage: { search_hits: 7, read_hits: 2, total_hits: 9, last_hit_at: '2026-11-24T12:00:00+00:00' },
        revision: { ...doc.revision, title: longUrl, state: 'published' },
      }],
      total: 1,
    }))
    vi.stubGlobal('fetch', fetch)
    wrapper = mount(Documents, { attachTo: document.body }); await flushPromises()
    expect(wrapper.text()).toContain('命中次数'); expect(wrapper.text()).toContain('9')
    const titleCell = wrapper.find('tbody tr td')
    expect(titleCell.classes()).toContain('whitespace-normal')
    expect(titleCell.classes()).not.toContain('whitespace-nowrap')
    const titleParagraph = titleCell.find('p')
    expect(titleParagraph.attributes('title')).toBe(longUrl)
    expect(titleParagraph.classes()).toContain('line-clamp-2')
    const sortSelect = wrapper.find<HTMLSelectElement>('#knowledge-documents-sort')
    await sortSelect.setValue('hits'); await flushPromises()
    expect(fetch.mock.calls.some(call => String(call[0]).includes('sort=hits'))).toBe(true)
  })
})
