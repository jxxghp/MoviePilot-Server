import type { Form, ImportBatch, Source } from '@/knowledge'
import { lines } from '@/knowledge'
export interface MappingForm extends Form {
  identity_fields: string; title_field: string; content_field: string; url_field: string; origin_field: string; origin_values: string
  metadata_mode: 'fixed' | 'record'; product: string; version_scope: 'unknown' | 'all' | 'range'; min_version: string; max_version: string
  source_commit: string; fetched_at: string; valid_until: string
}
export interface Decision { action: string; value: 'skip' | 'append' }
export type Decisions = Record<number, Decision>
/** 只读取既有顶层映射字段，未知参数不会被重新提交。 */
export function mappingFor(value: Record<string, unknown> = {}): MappingForm {
  const metadata = value.metadata && typeof value.metadata === 'object' ? value.metadata as Record<string, unknown> : {}
  /** 元数据未知值保持空白，不从上传日期补造来源时间。 */
  const string = (record: Record<string, unknown>, key: string, fallback = '') => typeof record[key] === 'string' ? record[key] as string : fallback
  return { identity_fields: Array.isArray(value.identity_fields) ? value.identity_fields.join('\n') : 'external_id',
    title_field: string(value, 'title_field', 'title'), content_field: string(value, 'content_field', 'content'),
    url_field: value.url_field === null ? '' : string(value, 'url_field', 'source_url'), origin_field: string(value, 'origin_field'),
    origin_values: Array.isArray(value.origin_values) ? value.origin_values.join('\n') : '', metadata_mode: value.metadata_mode === 'record' ? 'record' : 'fixed',
    product: string(metadata, 'product'), version_scope: metadata.version_scope === 'all' || metadata.version_scope === 'range' ? metadata.version_scope : 'unknown',
    min_version: string(metadata, 'min_version'), max_version: string(metadata, 'max_version'), source_commit: string(metadata, 'source_commit'),
    fetched_at: string(metadata, 'fetched_at'), valid_until: string(metadata, 'valid_until') }
}
/** 原始 Blob 直接上传，不解压、解析或把全包正文读入 JavaScript 字符串。 */
export function checkUpload(file: Pick<File, 'name' | 'size'> | null) {
  if (!file || file.size === 0) throw new Error('请选择非空 JSONL / NDJSON 文件')
  if (file.size > 64 * 1024 * 1024) throw new Error('单文件最多 64 MiB，请先拆分文件')
  if (!/\.(jsonl|ndjson)$/i.test(file.name)) throw new Error('只接受未压缩的 .jsonl / .ndjson 文件，不接受 ZIP')
}
/** 批次、当前来源版本与明确范围一起提交；字段映射不接受可执行表达式。 */
export function mappingPayload(form: MappingForm, batch: ImportBatch, source: Source) {
  const identity = lines(form.identity_fields)
  if (!identity.length || identity.length > 4) throw new Error('文档身份须指定 1–4 个顶层字段')
  return { expected_version: batch.version, expected_source_version: source.version, identity_fields: identity,
    title_field: form.title_field, content_field: form.content_field, url_field: form.url_field || null,
    origin_field: form.origin_field || null, origin_values: lines(form.origin_values), metadata_mode: form.metadata_mode,
    metadata: { product: form.product, version_scope: form.version_scope,
      min_version: form.version_scope === 'range' ? form.min_version || null : null,
      max_version: form.version_scope === 'range' ? form.max_version || null : null,
      source_commit: form.source_commit, fetched_at: form.fetched_at || null, valid_until: form.valid_until || null } }
}
/** 计划变化即使摘要相同也清空旧决定，不把其他任务的审阅套到新计划上。 */
export function planKey(batch: ImportBatch) { return `${batch.id}:${batch.plan_hash}:${batch.source_version}:${batch.current_job_id}` }
/** 跨页决定必须覆盖所有阻止 / 人工冲突，且分别满足服务端的 1000 条限额。 */
export function approval(batch: ImportBatch, decisions: Decisions, acknowledged: boolean) {
  if (batch.status !== 'ready' || !batch.plan_hash) throw new Error('请先完成当前批次预检')
  const rows = Object.entries(decisions), skip = rows.filter(([, row]) => row.value === 'skip'), append = rows.filter(([, row]) => row.value === 'append')
  if (rows.some(([line, row]) => !Number.isInteger(Number(line)) || Number(line) < 1 || Number(line) > 50000
    || !['new', 'updated', 'unchanged', 'blocked', 'conflict'].includes(row.action)
    || !['skip', 'append'].includes(row.value) || row.value === 'append' && row.action !== 'conflict')) throw new Error('行级决定不属于有效的当前计划动作')
  if (skip.length > 1000 || append.length > 1000) throw new Error('单次跳过和追加各最多 1000 条，请调整文件或分批处理')
  const actions = batch.summary.actions || {}
  if (skip.filter(([, row]) => row.action === 'blocked').length !== (actions.blocked || 0)) throw new Error('还有阻止行未明确跳过，请逐行处理')
  if (rows.filter(([, row]) => row.action === 'conflict').length !== (actions.conflict || 0)) throw new Error('还有人工修订冲突未明确选择追加或跳过')
  if (Object.values(batch.summary.flags || {}).some(count => count > 0) && !acknowledged) throw new Error('请先核对并确认全部预检警告')
  const eligible = (actions.new || 0) + (actions.updated || 0) + (actions.unchanged || 0) + (actions.conflict || 0)
  if (eligible <= skip.filter(([, row]) => row.action !== 'blocked').length) throw new Error('没有明确选择的可导入记录')
  return { expected_version: batch.version, acknowledge_warnings: acknowledged,
    skip_lines: skip.map(([line]) => Number(line)).sort((a, b) => a - b), append_lines: append.map(([line]) => Number(line)).sort((a, b) => a - b) }
}
