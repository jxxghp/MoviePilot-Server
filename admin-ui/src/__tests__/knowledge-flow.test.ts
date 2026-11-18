import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { setCsrf } from '@/api'
import { useAuth } from '@/stores/auth'
import { approval, checkUpload, mappingFor, mappingPayload, planKey, type Decisions } from '@/importing'
import { retrieve } from '@/retrieval'
import type { ImportBatch, Source } from '@/knowledge'
import Clients from '@/components/knowledge/Clients.vue'
import Imports from '@/components/knowledge/Imports.vue'
import Search from '@/components/knowledge/Search.vue'
import Grants from '@/components/knowledge/Grants.vue'

const id = 'a'.repeat(32), parent = 'b'.repeat(32), token = 'mpk_' + 's'.repeat(43), evidenceId = 'ke_' + 'x'.repeat(43)
const collection = { id: parent, name: '合成授权集合', version: 1, enabled: true }
const source: Source = { id, version: 7, name: '合成导入', connector: 'jsonl', enabled: false, collection_id: parent, site_id: null, url: '', repository: '', branch: 'main', paths: [], selector: '', refresh_minutes: 0, updated_at: '' }
const batch: ImportBatch = { id, version: 4, source_id: id, source_version: 7, status: 'ready', size_bytes: 100, plan_hash: 'hash', error_code: '', current_job_id: parent, expires_at: 1795100000, created_at: '', summary: { rows: 22, actions: { new: 20, blocked: 1, conflict: 1 }, flags: { scope_unknown: 22 } }, result: {} }
const client = { id, version: 1, name: '合成客户端', enabled: true, expires_at: null, collection_ids: [], created_at: '', updated_at: '' }
let wrapper: VueWrapper | undefined

/** 仅为没有布局引擎的单元环境提供观测接口，不参与真实浏览器验收。 */
class Observer {
  /** 单元测试不以虚构尺寸触发观测回调。 */
  observe() {}
  /** 解除观测不保留节点。 */
  unobserve() {}
  /** 卸载时不产生延迟任务。 */
  disconnect() {}
}
/** 构造合成响应，不连接本机运行服务或外部知识来源。 */
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }) }
/** 弹窗按钮通过用户可见的完整文案定位。 */
function button(label: string) { const item = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(node => node.textContent?.trim() === label); if (!item) throw new Error('missing button ' + label); return item }
/** 输入事件更新真实 Vue 模型，不直接修改组件内部状态。 */
async function fill(selector: string, value: string) { const node = document.querySelector<HTMLInputElement>(selector)!; node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); await nextTick() }
/** 提交实际表单并等待所有可观察界面更新。 */
async function submit(selector: string) { document.querySelector(selector)!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await flushPromises() }
/** 构造有限证据，保留 HTML 字面量以测试原文转义。 */
function matched(title = '旧证据') { return { protocol_version: 1, status: 'matched', evidence: [{ evidence_id: evidenceId, document_id: id, revision: 1, title, excerpt: '<script>not-executed</script>有限正文', collection_label: '合成集合', source_label: '合成来源', source_url: '' }], notices: [], retention: 'ephemeral' } }
beforeEach(() => {
  setActivePinia(createPinia()); setCsrf('synthetic-csrf')
  useAuth().user = { id: 1, username: 'synthetic', display_name: '合成管理员', role: 'super_admin', enabled: true, must_change_password: false, created_at: '', last_login_at: null }
  vi.stubGlobal('ResizeObserver', Observer)
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = ''; setCsrf(''); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('原始 JSONL 与明确计划', () => {
  it('拒绝压缩包、空包和超限，不读取文件正文', () => {
    expect(() => checkUpload({ name: 'knowledge.zip', size: 10 })).toThrow('不接受 ZIP')
    expect(() => checkUpload({ name: 'knowledge.jsonl', size: 0 })).toThrow('非空')
    expect(() => checkUpload({ name: 'knowledge.jsonl', size: 64 * 1024 * 1024 + 1 })).toThrow('64 MiB')
    expect(() => checkUpload({ name: 'knowledge.ndjson', size: 100 })).not.toThrow()
  })
  it('映射只投影标准字段，未知时间保持 null，来源使用当前版本', () => {
    const form = mappingFor({ identity_fields: ['id'], title_field: 'heading', metadata: { version_scope: 'unknown' }, unexpected: 'ignored' })
    const value = mappingPayload(form, { ...batch, source_version: 1 }, source)
    expect(value).toMatchObject({ expected_version: 4, expected_source_version: 7, identity_fields: ['id'], title_field: 'heading', metadata: { version_scope: 'unknown', fetched_at: null, valid_until: null } })
    expect(value).not.toHaveProperty('unexpected')
  })
  it('跨页决定必须覆盖所有阻止 / 冲突并明确确认全部警告', () => {
    const decisions: Decisions = { 21: { action: 'blocked', value: 'skip' }, 22: { action: 'conflict', value: 'append' } }
    expect(() => approval(batch, {}, true)).toThrow('阻止行')
    expect(() => approval(batch, { 21: decisions[21] }, true)).toThrow('人工修订冲突')
    expect(() => approval(batch, decisions, false)).toThrow('全部预检警告')
    expect(approval(batch, decisions, true)).toEqual({ expected_version: 4, acknowledge_warnings: true, skip_lines: [21], append_lines: [22] })
    expect(planKey(batch)).not.toBe(planKey({ ...batch, current_job_id: id }))
  })
  it('不接受全部跳过、越界追加或超过一千条的决定', () => {
    const simple = { ...batch, summary: { rows: 1, actions: { new: 1 }, flags: {} } }
    expect(() => approval(simple, { 1: { action: 'new', value: 'skip' } }, true)).toThrow('没有明确选择')
    expect(() => approval(simple, { 1: { action: 'new', value: 'append' } }, true)).toThrow('有效的当前计划')
    const many = Object.fromEntries(Array.from({ length: 1001 }, (_, index) => [index + 1, { action: 'new', value: 'skip' as const }]))
    expect(() => approval(simple, many, true)).toThrow('1000')
  })
  it('上传直接发送 File Blob 和 NDJSON 头，成功后没有自动预检或导入', async () => {
    let sent: RequestInit | undefined, path = ''
    const fetch = vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') { sent = options; path = url; return response({ ...batch, status: 'uploaded', plan_hash: '', configuration: {} }, 201) }
      if (url.endsWith('/sources/' + id)) return response(source)
      if (url.includes('/sources?')) return response({ items: [source], total: 1 })
      if (url.endsWith('/imports/' + id)) return response({ ...batch, status: 'uploaded', plan_hash: '', configuration: {}, job: null })
      return response({ items: [], total: 0 })
    }); vi.stubGlobal('fetch', fetch)
    wrapper = mount(Imports, { attachTo: document.body }); await flushPromises(); button('上传 JSONL').click(); await nextTick(); button('选择来源').click(); await flushPromises()
    const choice = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(node => node.textContent?.includes('合成导入'))!; choice.click(); await flushPromises()
    const file = new File(['{"external_id":"x","title":"合成","content":"私有合成正文"}\n'], 'synthetic.jsonl', { type: 'application/octet-stream' })
    const input = document.querySelector<HTMLInputElement>('#knowledge-upload-file')!; Object.defineProperty(input, 'files', { value: [file], configurable: true }); input.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
    await submit('#knowledge-upload-form')
    expect(sent?.body).toBe(file); expect(sent?.headers).toMatchObject({ 'Content-Type': 'application/x-ndjson', 'X-CSRF-Token': 'synthetic-csrf' })
    expect(path).toContain(`source_id=${id}&source_version=7`)
    expect(fetch.mock.calls.filter(call => call[1].method === 'POST')).toHaveLength(1)
  })
})

describe('独立令牌管理边界', () => {
  it('普通管理员不发起客户端管理请求', async () => {
    useAuth().user!.role = 'admin'; const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    wrapper = mount(Clients); await flushPromises(); expect(fetch).not.toHaveBeenCalled(); expect(wrapper.text()).toContain('只有超级管理员')
  })
  it('首显令牌关闭即清空，角色失去超管后同样移除', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_path: string, options: RequestInit) => options.method === 'POST'
      ? response({ ...client, token }, 201) : response({ items: [], total: 0 })))
    wrapper = mount(Clients, { attachTo: document.body }); await flushPromises(); button('签发客户端').click(); await nextTick(); await fill('#knowledge-client-name', '合成客户端'); await submit('#knowledge-client-form')
    expect(document.querySelector<HTMLInputElement>('[aria-label="一次性知识令牌"]')?.value).toBe(token)
    button('关闭并清空').click(); await nextTick(); expect(document.querySelector('[aria-label="一次性知识令牌"]')).toBeNull()
    button('签发客户端').click(); await nextTick(); await fill('#knowledge-client-name', '合成客户端'); await submit('#knowledge-client-form')
    expect(document.querySelector<HTMLInputElement>('[aria-label="一次性知识令牌"]')?.value).toBe(token)
    useAuth().user!.role = 'admin'; await nextTick(); expect(document.querySelector('[aria-label="一次性知识令牌"]')).toBeNull()
    useAuth().user!.role = 'super_admin'; await nextTick(); expect(document.querySelector('[aria-label="一次性知识令牌"]')).toBeNull()
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
  })
  it('签发响应丢失后不自动重试，必须先返回列表核对', async () => {
    let writes = 0
    vi.stubGlobal('fetch', vi.fn(async (_path: string, options: RequestInit) => {
      if (options.method === 'POST') { writes++; throw new TypeError('simulated lost response') }
      return response({ items: [client], total: 1 })
    }))
    wrapper = mount(Clients, { attachTo: document.body }); await flushPromises(); button('签发客户端').click(); await nextTick(); await fill('#knowledge-client-name', '合成客户端'); await submit('#knowledge-client-form')
    expect(writes).toBe(1); expect(button('签发').disabled).toBe(true); expect(document.body.textContent).toContain('结果未确认')
    button('返回列表核对').click(); await flushPromises(); expect(writes).toBe(1)
  })
  it('相同角色换账号后，旧签发响应不能重新展示密钥', async () => {
    let resolve!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn((_path: string, options: RequestInit) => options.method === 'POST'
      ? new Promise<Response>(done => { resolve = done }) : Promise.resolve(response({ items: [], total: 0 }))))
    wrapper = mount(Clients, { attachTo: document.body }); await flushPromises(); button('签发客户端').click(); await nextTick()
    await fill('#knowledge-client-name', '合成客户端'); await submit('#knowledge-client-form')
    useAuth().user = { ...useAuth().user!, id: 2 }; await nextTick()
    resolve(response({ ...client, token }, 201)); await flushPromises()
    expect(document.querySelector('[aria-label="一次性知识令牌"]')).toBeNull()
    expect(button('签发客户端').disabled).toBe(false)
  })
  it('保留已停用集合授权，新增选择器禁用该集合，撤销需要明确移除', async () => {
    const disabled = { ...collection, enabled: false }
    vi.stubGlobal('fetch', vi.fn(async (path: string) => path.includes('/collections?')
      ? response({ items: [disabled], total: 1 }) : response(disabled)))
    wrapper = mount(Grants, { props: { modelValue: [parent] }, attachTo: document.body }); await flushPromises()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined(); expect(wrapper.text()).toContain('已停用，授权暂不生效')
    button('选择集合').click(); await flushPromises()
    const choice = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(node => node.textContent?.includes('合成授权集合') && node.hasAttribute('aria-pressed'))!
    expect(choice.disabled).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flushPromises()
    button('移除').click(); await flushPromises(); expect(wrapper.emitted('update:modelValue')).toEqual([[[]]])
  })
})

describe('公开只读检索测试', () => {
  it('固定地址使用独立 Bearer，无后台 Cookie / CSRF 或后台过期事件', async () => {
    const expired = vi.fn(); window.addEventListener('admin-session-expired', expired)
    const fetch = vi.fn(async (_path: string, _options: RequestInit) => response({ protocol_version: 1, status: 'unauthorized', detail: 'knowledge_invalid' }, 401)); vi.stubGlobal('fetch', fetch)
    const result = await retrieve('search', token, { query: '分享率', product: 'moviepilot' })
    expect(result.status).toBe('unauthorized'); expect(expired).not.toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledWith('/knowledge/v1/search', expect.objectContaining({ credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer' }))
    const headers = fetch.mock.calls[0][1].headers as Record<string, string>; expect(headers.Authorization).toBe('Bearer ' + token); expect(headers).not.toHaveProperty('X-CSRF-Token')
    window.removeEventListener('admin-session-expired', expired)
  })
  it('拒绝无效令牌、未知协议和超出有限证据预算的响应', async () => {
    const fetch = vi.fn(async () => response({ protocol_version: 2, status: 'matched' })); vi.stubGlobal('fetch', fetch)
    await expect(retrieve('search', 'invalid', {})).rejects.toThrow('独立知识令牌'); expect(fetch).not.toHaveBeenCalled()
    await expect(retrieve('search', token, {})).rejects.toThrow('协议不兼容')
    fetch.mockResolvedValue(response({ ...matched(), evidence: [{ ...matched().evidence[0], excerpt: 'x'.repeat(6001) }] }))
    await expect(retrieve('search', token, {})).rejects.toThrow('有限证据')
    fetch.mockResolvedValue(response({ ...matched(), evidence: [{ ...matched().evidence[0], excerpt: '🎬'.repeat(4000) }] }))
    await expect(retrieve('search', token, {})).resolves.toMatchObject({ status: 'matched' })
    fetch.mockResolvedValue(response({ ...matched(), evidence: [{ ...matched().evidence[0], excerpt: '🎬'.repeat(4001) }] }))
    await expect(retrieve('read', token, {})).rejects.toThrow('有限证据')
  })
  it('更改问题会丢弃迟到响应，旧证据不会显示成新问题的答案', async () => {
    let resolve!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(done => { resolve = done })))
    wrapper = mount(Search, { attachTo: document.body }); await fill('#knowledge-test-token', token); await fill('#knowledge-test-query', '原问题')
    document.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await nextTick()
    await fill('#knowledge-test-query', '新问题'); resolve(response(matched())); await flushPromises()
    expect(document.body.textContent).not.toContain('旧证据')
  })
  it('同角色切换账号后清空令牌并拒绝迟到的检索证据', async () => {
    let resolve!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(done => { resolve = done })))
    wrapper = mount(Search, { attachTo: document.body }); await fill('#knowledge-test-token', token); await fill('#knowledge-test-query', '分享率')
    document.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await nextTick()
    useAuth().user = { ...useAuth().user!, id: 2 }; await nextTick(); resolve(response(matched())); await flushPromises()
    expect(document.querySelector<HTMLInputElement>('#knowledge-test-token')?.value).toBe('')
    expect(document.body.textContent).not.toContain('旧证据')
  })
  it('证据按原文显示，补读权限拒绝会清除旧正文', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => path.endsWith('/read') ? response({ protocol_version: 1, status: 'forbidden' }, 403) : response(matched())))
    wrapper = mount(Search, { attachTo: document.body }); await fill('#knowledge-test-token', token); await fill('#knowledge-test-query', '分享率'); await submit('form')
    expect(document.querySelector('pre')?.textContent).toContain('<script>'); expect(document.querySelector('script')).toBeNull()
    button('补读相邻上下文').click(); await flushPromises(); expect(document.body.textContent).not.toContain('旧证据'); expect(document.body.textContent).toContain('没有当前范围的读取权限')
    wrapper.unmount(); wrapper = undefined; expect(document.querySelector('#knowledge-test-token')).toBeNull()
  })
})
