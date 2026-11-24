import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiError } from '@/api'

export type Kind = 'collections' | 'sites' | 'sources'
export type Value = string | number | boolean | null | string[]
export type Form = Record<string, Value>
export interface Entity { id: string; version: number; name: string; enabled: boolean; updated_at: string }
export interface Collection extends Entity { slug: string; description: string; domain: 'mp' | 'pt'; product: string; has_sources?: boolean }
export interface Site extends Entity { domain: string; aliases: string[]; has_sources?: boolean }
export interface Source extends Entity {
  collection_id: string; site_id: string | null; connector: 'manual' | 'jsonl' | 'github' | 'http' | 'nexusphp'
  url: string; repository: string; branch: string; paths: string[]; selector: string; refresh_minutes: number
  has_documents?: boolean; has_credential?: boolean
}
export type CatalogItem = Entity & Partial<Omit<Collection, 'domain'> & Site & Source>
export interface Page<T> { items: T[]; total: number; page: number; size: number }
export interface Job {
  id: string; version: number; kind: string; source_id: string | null; status: string; outcome: string
  cancel_requested: boolean; attempts: number; max_attempts: number; created_at: string; updated_at: string
  available_at: number; processed_documents: number; processed_chunks: number
}
export interface Policy { version_scope: 'unknown' | 'all' | 'range'; min_version?: string | null; max_version?: string | null }
export interface CollectionState { source_version: number; policy: Policy; next_at: number | null; job: Job | null; result: { counts?: Record<string, number>; flags?: Record<string, number> } }
export interface Credential { configured: boolean; state: string; source_version: number; version: number; kind: 'cookie' | 'bearer' | null; host: string | null }
export interface Revision {
  number: number; title: string; content?: string; state: string; product: string; version_scope: Policy['version_scope']
  min_version: string | null; max_version: string | null; source_url: string; source_commit: string
  fetched_at: string | null; valid_until: string | null; created_at: string; created_by: string
}
export interface DocumentUsage { search_hits: number; read_hits: number; total_hits: number; last_hit_at: string | null }
export interface KnowledgeStatusUsage { total_calls: number; today_calls: number; total_hits: number; hit_documents: number }
export interface KnowledgeUsageChannel {
  total_calls: number; search_calls: number; search_matched_calls: number; search_hits: number; read_calls: number; read_hits: number
}
export interface KnowledgeUsageDaily extends KnowledgeUsageChannel { day: string }
export interface KnowledgeTopDocument {
  document_id: string; external_id: string; title: string; source_id: string; source_name: string; collection_name: string
  domain: string; published_revision: number | null; search_hits: number; read_hits: number; total_hits: number; last_hit_at: string | null
}
export interface KnowledgeUsageSummary {
  totals: {
    total_calls: number; search_calls: number; search_matched_calls: number; search_hits: number
    read_calls: number; read_hits: number; total_hits: number; hit_documents: number; published_documents: number
  }
  windows: { today_calls: number; last_7_days_calls: number; last_30_days_calls: number }
  channels: { agent: KnowledgeUsageChannel; client: KnowledgeUsageChannel }
  daily: KnowledgeUsageDaily[]
  top_documents: KnowledgeTopDocument[]
}
export interface Document extends Omit<Entity, 'name' | 'enabled'> {
  source_id: string; external_id: string; head_revision: number; published_revision: number | null
  usage?: DocumentUsage; revision: Revision
}
export interface Client extends Entity { expires_at: string | null; collection_ids: string[]; created_at: string }
export interface IssuedClient extends Client { token: string }
export interface ImportSummary { rows?: number; actions?: Record<string, number>; flags?: Record<string, number> }
export interface ImportBatch {
  id: string; version: number; source_id: string; source_version: number; status: string; size_bytes: number
  plan_hash: string; error_code: string; current_job_id: string | null; expires_at: number; created_at: string
  summary: ImportSummary; result: Record<string, number>; job?: Job | null; configuration?: Record<string, unknown>
}
export interface ImportRow {
  line: number; external_id?: string; title?: string; document_id: string | null; base_revision?: number | null
  action: string; flags: string[]; revision?: number | null
  base?: { title: string; content: string } | null; candidate?: { title: string; content: string } | null
}
export const kinds: Record<Kind, string> = { sources: '来源', sites: '站点', collections: '集合' }
export const connectors = { manual: '手工', jsonl: 'JSONL 导入', github: 'GitHub', http: 'HTTP / Wiki', nexusphp: 'NexusPHP' }
export const labels: Record<string, string> = {
  name: '名称', slug: '稳定标识', description: '说明', domain: '领域 / 域名', product: '产品标识', enabled: '启用',
  aliases: '别名', collection_id: '所属集合', site_id: '所属站点', connector: '来源类型', url: '页面地址',
  repository: '仓库', branch: '分支', paths: '允许路径', selector: '正文选择器', refresh_minutes: '刷新间隔',
  title: '标题', content: '正文', version_scope: '版本范围', min_version: '最低版本', max_version: '最高版本',
  source_url: '出处', source_commit: '来源提交', fetched_at: '采集时间', valid_until: '有效截止时间',
  collection_ids: '授权集合', expires_at: '到期时间',
}
const codes: Record<string, string> = {
  queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败', cancelled: '已取消',
  draft: '草稿', review: '待审', published: '已发布', superseded: '历史发布版', withdrawn: '已撤回',
  index: '索引', import: '导入', collect: '采集', activated: '索引已就绪', collected: '采集完成', collected_partial: '部分采集完成',
  collect_blocked: '采集被阻止', collect_retry: '等待采集重试', new: '新增', updated: '更新', unchanged: '未变化',
  protected: '人工修订受保护', excluded: '已排除', appended: '已追加待审', discarded: '已忽略', pending: '待处理',
  authentication_rejected: '上游拒绝认证', authentication_page: '登录或验证页面', target_rejected: '目标超出批准范围',
  address_rejected: '解析地址不允许', dns_rejected: 'DNS 解析被拒绝', fetch_timeout: '请求超时', fetch_failed: '网络请求失败',
  upstream_rate_limited: '上游限流', content_shrank: '正文异常缩水', suspected_secret: '疑似敏感信息', content_instruction: '正文包含指令文本',
  knowledge_source_changed: '来源已更新，请重新打开', knowledge_source_unavailable: '请检查来源、集合和站点的启用状态',
  knowledge_credential_changed: '来源或凭据已更新，请重新打开', knowledge_product_required: '版本范围需要明确的产品标识',
  knowledge_candidate_changed: '候选已更新，请重新打开', knowledge_candidate_document_changed: '文档已更新，请重新审阅候选',
  uploading: '上传中', uploaded: '已上传，待预检', upload_failed: '上传未完成', preflight_queued: '预检排队中', ready: '预检完成',
  blocked: '需处理', apply_queued: '导入排队中', imported: '已导入待审', expired: '已到期', conflict: '人工修订冲突', skipped: '已跳过',
  preflight_ready: '预检完成', preflight_blocked: '预检被阻止', filtered_origin: '外部来源未被选中', scope_unknown: '版本适用范围未知',
  scope_mismatch: '知识归属或范围不一致', invalid_record: '记录字段无效', invalid_json: 'JSON 格式无效', invalid_identity: '文档身份无效',
  unmapped_origin: '外部来源未明确映射', unmapped_fields: '存在未映射字段', duplicate_identity: '文档身份重复', duplicate_content: '正文重复',
  manual_revision: '保护人工修订', empty_selection: '没有选中的记录', plan_stale: '预检计划已失效', source_changed: '来源已改变', document_changed: '文档已改变',
}
/** 服务端固定代码转为短提示，未知代码仍保留以便查阅任务回执。 */
export function text(value: string | null | undefined) { return value ? codes[value] || value : '—' }
/** 维护错误不输出请求体；业务冲突与失效会话仍由现有传输处理。 */
export function message(error: unknown) { return error instanceof Error ? text(error.message) : '操作失败，请重试' }
/** 仅拆分列表字段；正文和秘密从不经过此规范化。 */
export function lines(value: Value | undefined): string[] { return String(value || '').split('\n').map(item => item.trim()).filter(Boolean) }
/** 只选可编辑字段，防止把锁标记、密文或并发版本随表单回传。 */
export function formFor(kind: Kind, item?: Partial<CatalogItem>): Form {
  if (kind === 'collections') return { slug: item?.slug || '', name: item?.name || '', description: item?.description || '', domain: item?.domain || 'mp', product: item?.product || '', enabled: item?.enabled ?? true }
  if (kind === 'sites') return { name: item?.name || '', domain: item?.domain || '', aliases: item?.aliases?.join('\n') || '', enabled: item?.enabled ?? true }
  return { name: item?.name || '', collection_id: item?.collection_id || '', site_id: item?.site_id || null,
    connector: item?.connector || 'manual', url: item?.url || '', repository: item?.repository || '', branch: item?.branch || 'main',
    paths: item?.paths?.join('\n') || '', selector: item?.selector || '', refresh_minutes: item?.refresh_minutes || 0, enabled: item?.enabled ?? false }
}
/** 服务端分页保留最新请求，卸载和切换筛选后旧响应不能覆盖当前视图。 */
export function usePage<T>(endpoint: () => string, filters: () => Record<string, string> = () => ({}), size = 30, allowed: () => boolean = () => true) {
  const rows = ref<T[]>([]), query = ref(''), page = ref(1), total = ref(0), loading = ref(false), error = ref('')
  let sequence = 0, disposed = false, controller: AbortController | undefined
  const pages = computed(() => Math.max(1, Math.ceil(total.value / size)))
  /** 每次读取有独立序号，即使服务器未及时取消也不接纳过时结果。 */
  async function load(reset = false): Promise<void> {
    if (disposed) return
    if (!allowed()) { sequence++; controller?.abort(); rows.value = []; total.value = 0; loading.value = false; return }
    if (reset) page.value = 1
    const current = ++sequence
    controller?.abort(); controller = new AbortController(); loading.value = true; error.value = ''; rows.value = []
    try {
      const params = new URLSearchParams({ q: query.value, page: String(page.value), size: String(size), ...filters() })
      const result = await api<Page<T>>(`${endpoint()}?${params}`, { signal: controller.signal })
      if (current !== sequence) return
      total.value = result.total
      if (page.value > pages.value) { page.value = pages.value; await load(); return }
      rows.value = result.items as typeof rows.value
    } catch (reason) { if (current === sequence) error.value = message(reason) }
    finally { if (current === sequence) loading.value = false }
  }
  watch(endpoint, () => load(true)); watch(allowed, () => load(true)); onMounted(() => load())
  onBeforeUnmount(() => { disposed = true; sequence++; controller?.abort(); rows.value = [] })
  return { rows, query, page, total, pages, loading, error, load }
}
export interface Difference { key: string; mine: Value; latest: Value; choice: 'mine' | 'latest' | '' }
/** 三方对照只自动接纳未改字段；双方都改动时必须逐字段选择，之后仍需再次保存。 */
export function differences(base: Form, mine: Form, latest: Form): Difference[] {
  /** 表单标量和数组按值比较，不把新对象引用误判成内容冲突。 */
  const same = (left: Value | undefined, right: Value | undefined) => JSON.stringify(left) === JSON.stringify(right)
  return Object.keys(base).filter(key => !same(base[key], latest[key])).map(key => ({
    key, mine: mine[key] ?? null, latest: latest[key] ?? null,
    choice: same(mine[key], base[key]) || same(mine[key], latest[key]) ? 'latest' : '',
  }))
}
/** 合并只发生在明确审阅后，不在捕获 409 时自动带新版本重试写入。 */
export function merged(mine: Form, changes: Difference[]): Form {
  if (changes.some(item => !item.choice)) throw new Error('请逐项选择要保留的值')
  return { ...mine, ...Object.fromEntries(changes.map(item => [item.key, item[item.choice as 'mine' | 'latest']])) }
}
/** 只有确定的版本冲突进入对照；其他 409 仍作为原业务错误展示。 */
export function isConflict(error: unknown) { return error instanceof ApiError && error.status === 409 }
