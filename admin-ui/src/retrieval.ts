export interface Evidence {
  evidence_id: string; document_id: string; revision: number; title: string; excerpt: string; source_label: string
  collection_label: string; source_url: string; source_commit?: string; fetched_at?: string | null; valid_until?: string | null
  applicable_versions?: { scope: string; min: string | null; max: string | null }; kind?: string; freshness?: string
}
export interface RetrievalResult { protocol_version: 1; status: string; evidence: Evidence[]; notices: string[]; detail?: string; retention?: string; http: number }
/** 固定同源公开只读接口，不附带后台 Cookie / CSRF，不广播后台会话失效事件。 */
export async function retrieve(operation: 'search' | 'read', token: string, payload: Record<string, unknown>, signal?: AbortSignal): Promise<RetrievalResult> {
  if (!/^mpk_[A-Za-z0-9_-]{43}$/.test(token.trim())) throw new Error('请填写中心签发的独立知识令牌')
  let response: Response
  try {
    response = await fetch(`/knowledge/v1/${operation}`, { method: 'POST', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer', signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token.trim()}` }, body: JSON.stringify({ ...payload, protocol_version: 1 }) })
  } catch { throw new Error('知识服务请求未完成，请检查连接或重新检索') }
  const body = await response.json().catch(() => null)
  if (!body || body.protocol_version !== 1 || typeof body.status !== 'string') throw new Error('知识服务响应格式无效或协议不兼容')
  const items = body.evidence ?? []
  if (!Array.isArray(items) || items.length > (operation === 'read' ? 1 : 5) || items.some(item => !item
    || typeof item.title !== 'string' || typeof item.excerpt !== 'string' || typeof item.evidence_id !== 'string'
    || !/^ke_[A-Za-z0-9_-]{43}$/.test(item.evidence_id))
    || items.reduce((sum, item) => sum + Array.from(item.excerpt).length, 0) > (operation === 'read' ? 4000 : 6000)) throw new Error('知识响应超出预期的有限证据范围')
  return { protocol_version: 1, status: body.status, evidence: items, notices: Array.isArray(body.notices) ? body.notices.filter((item: unknown) => typeof item === 'string') : [],
    detail: typeof body.detail === 'string' ? body.detail : undefined, retention: body.retention, http: response.status }
}
const descriptions: Record<string, string> = {
  matched: '已匹配', no_match: '没有匹配知识', not_applicable: '当前范围不适用', conflicting: '来源存在差异，请核对',
  unauthorized: '令牌无效、过期或已被轮换', forbidden: '此令牌没有当前范围的读取权限', reference_expired: '引用已过期或不可用，请重新检索',
  invalid_request: '检索参数无效，请检查输入', rate_limited: '请求过于频繁，请稍后再试', unavailable: '知识服务或索引暂不可用',
  applicability_unknown: '适用版本尚未确认', fetched_time_unknown: '来源采集时间未知', index_updating: '索引仍在更新',
  unknown_or_unavailable_site: '站点不存在或不在当前授权范围', site_unspecified: '未指定站点，包含通用知识与站点示例',
  pt_site_examples_only: '站点资料仅作示例，不能代表所有站点的规则',
  no_site_specific_match: '没有本站专属知识，当前为通用参考', scope_unavailable: '当前领域或产品不在授权范围',
  query_too_general: '关键词过于宽泛', version_not_applicable: '版本不适用', product_version_required: '需要明确产品版本',
  knowledge_expired: '资料已过有效期', source_variants_need_review: '同一知识存在多个来源版本，需人工核对', test_reference_only: '测试代码只供参考',
}
/** 状态与未知代码分开显示，不能把拒绝访问或服务故障伪装成无匹配。 */
export function retrievalText(value: string) { return descriptions[value] || value }
