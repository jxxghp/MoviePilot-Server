<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, ApiError, date, notify, write } from '@/api'
import { message, text, usePage, type ImportBatch, type ImportRow, type Page, type Source } from '@/knowledge'
import { approval, checkUpload, mappingFor, mappingPayload, planKey, type Decisions } from '@/importing'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import ConfirmAction from '@/components/ConfirmAction.vue'
import Pager from './Pager.vue'
import Picker from './Picker.vue'
const props = defineProps<{ batchId?: string; enabled?: boolean }>()
const emit = defineEmits<{ changed: []; job: [id: string]; document: [id: string] }>()
const { rows, page, pages, total, loading, error, load } = usePage<ImportBatch>(() => '/knowledge/imports')
const uploadOpen = ref(false), uploadSource = ref<string | null>(null), selectedFile = ref<File | null>(null), fileInput = ref<HTMLInputElement | null>(null)
const uploadError = ref(''), uncertain = ref(false), busy = ref(false), opened = ref(false), selected = ref(''), batch = ref<ImportBatch | null>(null)
const source = ref<Source | null>(null), detailLoading = ref(false), detailError = ref(''), stale = ref(false), mapping = ref(mappingFor()), mappingOpen = ref(false)
const entries = ref<ImportRow[]>([]), rowPage = ref(1), rowTotal = ref(0), decisions = ref<Decisions>({}), acknowledge = ref(false)
const lineOpen = ref(false), line = ref<ImportRow | null>(null), lineLoading = ref(false), lineError = ref(''), confirmation = ref<'apply' | 'discard' | null>(null)
let sequence = 0, lineSequence = 0, alive = true, uploadController: AbortController | undefined
/** 批次列表没有内嵌 job 时仍能识别排队状态。 */
const activity = (item: ImportBatch) => ['uploading', 'preflight_queued', 'apply_queued'].includes(item.status) || !!item.job && ['queued', 'running'].includes(item.job.status)
const hasRows = computed(() => !!batch.value?.plan_hash && ['ready', 'blocked', 'imported'].includes(batch.value.status))
const canPreflight = computed(() => !!batch.value && ['uploaded', 'ready', 'blocked', 'cancelled', 'failed'].includes(batch.value.status) && !activity(batch.value))
const canDiscard = computed(() => !!batch.value && !['uploading', 'imported', 'discarded', 'expired'].includes(batch.value.status) && !activity(batch.value))
const readiness = computed(() => {
  if (!batch.value || !source.value) return '请先读取批次与来源'
  if (source.value.version !== batch.value.source_version) return '来源已更新，请先重新预检'
  try { approval(batch.value, decisions.value, acknowledge.value); return '' } catch (reason) { return message(reason) }
})
/** 打开上传窗口时不复用旧 File 对象或上次未确认的结果。 */
function openUpload() { uploadSource.value = null; selectedFile.value = null; uploadError.value = ''; uncertain.value = false; uploadOpen.value = true }
/** 文件只作为浏览器 Blob，绝不调用 text / ArrayBuffer 解析整个知识包。 */
function chooseFile(event: Event) { selectedFile.value = (event.target as HTMLInputElement).files?.[0] || null; uploadError.value = '' }
/** 流上传仅执行一次；失去响应时先核对真实批次列表，不自动重新上传。 */
async function upload() {
  if (busy.value || uncertain.value) return
  uploadError.value = ''
  try { checkUpload(selectedFile.value); if (!uploadSource.value) throw new Error('请选择 JSONL 来源') }
  catch (reason) { uploadError.value = message(reason); return }
  busy.value = true; uploadController = new AbortController()
  try {
    const origin = await api<Source>(`/knowledge/sources/${uploadSource.value}`)
    if (!alive) return
    if (origin.connector !== 'jsonl') throw new Error('请选择当前 JSONL 来源')
    const file = selectedFile.value!; selectedFile.value = null; if (fileInput.value) fileInput.value.value = ''
    const value = await api<ImportBatch>(`/knowledge/imports/upload?${new URLSearchParams({ source_id: origin.id, source_version: String(origin.version) })}`, {
      method: 'POST', headers: { 'Content-Type': 'application/x-ndjson' }, body: file, signal: uploadController.signal,
    })
    if (!alive) return
    uploadOpen.value = false; notify('已上传到私有暂存，尚未预检或导入'); await load(true); await detail(value.id, true)
  } catch (reason) {
    if (alive) { uploadError.value = message(reason); uncertain.value = reason instanceof ApiError && (reason.status === 0 || reason.status === 408 || reason.status >= 500)
      if (uncertain.value) uploadError.value = '上传结果未确认，请先返回列表核对，勿重复上传' }
  } finally { busy.value = false }
}
/** 当前计划 / 任务变化就作废旧决定和正文预览，跨页决定只属于同一不可变计划。 */
function clearReview() { decisions.value = {}; acknowledge.value = false; lineSequence++; line.value = null; lineOpen.value = false }
/** 按 ID 回读真实批次；只有明确进入逐行区域才查询有界元数据页。 */
async function detail(id: string, reset = false) {
  const current = ++sequence, requestedPage = reset ? 1 : rowPage.value
  if (reset || selected.value !== id) { batch.value = null; source.value = null; entries.value = []; rowPage.value = requestedPage; clearReview() }
  opened.value = true; selected.value = id; detailLoading.value = true; detailError.value = ''
  try {
    const value = await api<ImportBatch>(`/knowledge/imports/${id}`)
    const origin = !source.value || reset || source.value.id !== value.source_id ? await api<Source>(`/knowledge/sources/${value.source_id}`) : source.value
    const reviewable = !!value.plan_hash && ['ready', 'blocked', 'imported'].includes(value.status)
    const result = reviewable ? await api<Page<ImportRow>>(`/knowledge/imports/${id}/rows?page=${requestedPage}&size=20`) : null
    if (current !== sequence) return
    if (!batch.value || planKey(batch.value) !== planKey(value)) clearReview()
    batch.value = value; source.value = origin; entries.value = result?.items || []; rowTotal.value = result?.total || 0; stale.value = false
    if (reset) { mapping.value = mappingFor(value.configuration); mappingOpen.value = !value.plan_hash }
  } catch (reason) { if (current === sequence) { detailError.value = message(reason); stale.value = true; entries.value = [] } }
  finally { if (current === sequence) detailLoading.value = false }
}
/** 预检只保存映射和排队，不把上传成功或排队误报成已导入。 */
async function preflight() {
  if (busy.value || stale.value || !batch.value || !source.value || !canPreflight.value) return
  busy.value = true; detailError.value = ''
  try {
    const payload = mappingPayload(mapping.value, batch.value, source.value)
    const value = await write<ImportBatch>(`/knowledge/imports/${batch.value.id}/preflight`, 'POST', payload)
    if (!alive) return
    batch.value = value; clearReview(); mappingOpen.value = false; notify('预检已排队，等待完整计划'); await load(); await detail(value.id); emit('changed')
  } catch (reason) { if (alive) { detailError.value = message(reason); stale.value = reason instanceof ApiError && [0, 408, 409, 410].includes(reason.status) || reason instanceof ApiError && reason.status >= 500 } }
  finally { busy.value = false }
}
/** 只允许有效行级决定，达到服务端限额时明确拒绝，不截断或默认跳过。 */
function decide(row: ImportRow, event: Event) {
  const select = event.target as HTMLSelectElement, value = select.value
  if (!value) { delete decisions.value[row.line]; return }
  const proposed = { ...decisions.value, [row.line]: { action: row.action, value: value as 'skip' | 'append' } }
  if (Object.values(proposed).filter(item => item.value === value).length > 1000) { detailError.value = '单次跳过和追加各最多 1000 条，请调整文件或分批处理'; select.value = decisions.value[row.line]?.value || ''; return }
  decisions.value = proposed
}
/** 正文逐行明确读取，阻止行的空候选不尝试从原文件恢复。 */
async function readLine(row: ImportRow) {
  if (!batch.value) return
  const current = ++lineSequence; lineOpen.value = true; line.value = null; lineLoading.value = true; lineError.value = ''
  try { const value = await api<ImportRow>(`/knowledge/imports/${batch.value.id}/rows/${row.line}`); if (current === lineSequence) line.value = value }
  catch (reason) { if (current === lineSequence) lineError.value = message(reason) }
  finally { if (current === lineSequence) lineLoading.value = false }
}
/** 明确导入或丢弃只提交所见版本一次，所有冲突都要求重新读取并审阅。 */
async function act() {
  if (!batch.value || !confirmation.value || busy.value || stale.value) return
  const action = confirmation.value; busy.value = true; detailError.value = ''
  try {
    const payload = action === 'apply' ? approval(batch.value, decisions.value, acknowledge.value) : { expected_version: batch.value.version }
    const value = await write<ImportBatch>(`/knowledge/imports/${batch.value.id}/${action}`, 'POST', payload)
    if (!alive) return
    batch.value = value; confirmation.value = null; clearReview(); notify(action === 'apply' ? '导入已排队，完成后仍需审核发布' : '暂存已丢弃，历史知识与回执保留'); await load(); await detail(value.id); emit('changed')
  } catch (reason) { if (alive) { detailError.value = message(reason); stale.value = true; confirmation.value = null } }
  finally { busy.value = false }
}
/** 用户可见且存在活动任务时轮询；确认和逐行审阅窗口不被后台刷新覆盖。 */
function refresh() { void load(); if (opened.value && selected.value && !detailLoading.value && !busy.value && !confirmation.value && !lineOpen.value) void detail(selected.value) }
/** 显式重读作废行级决定，保留尚未提交的映射输入，之后仍需再次排队预检。 */
async function reload() { const input = mapping.value; await detail(selected.value, true); if (alive) mapping.value = input }
const timer = setInterval(() => {
  if (!props.enabled || document.visibilityState !== 'visible' || busy.value) return
  if (!loading.value && rows.value.some(activity)) void load()
  if (opened.value && batch.value && activity(batch.value) && !detailLoading.value && !confirmation.value && !lineOpen.value) void detail(selected.value)
}, 5000)
watch(() => props.batchId, value => { if (value) void detail(value, true) }, { immediate: true })
watch(uploadOpen, value => { if (!value) { selectedFile.value = null; if (fileInput.value) fileInput.value.value = '' } })
watch(opened, value => { if (!value) { sequence++; batch.value = null; source.value = null; entries.value = []; clearReview() } })
watch(lineOpen, value => { if (!value) { lineSequence++; line.value = null; lineLoading.value = false } })
onBeforeUnmount(() => { alive = false; sequence++; lineSequence++; clearInterval(timer); uploadController?.abort(); selectedFile.value = null; line.value = null; entries.value = []; mapping.value = mappingFor(); decisions.value = {} })
</script>
<template><div><div class="mb-5 flex flex-wrap justify-between gap-3"><p class="text-sm text-muted-foreground">JSONL 上传 → 预检 → 明确导入待审；不会自动发布。</p><div class="flex gap-2"><Button variant="outline" :disabled="loading" @click="refresh">刷新</Button><Button :disabled="busy" @click="openUpload">上传 JSONL</Button></div></div><p v-if="error" role="alert" class="mb-4 text-sm text-destructive">{{ error }}</p><div class="overflow-hidden rounded-md border"><Table><TableHeader><TableRow><TableHead>批次 / 创建时间</TableHead><TableHead>状态</TableHead><TableHead>大小</TableHead><TableHead>结果</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell><p class="font-mono text-xs">{{ row.id.slice(0, 12) }}</p><p class="mt-1 whitespace-nowrap text-xs text-muted-foreground">{{ date(row.created_at) }}</p></TableCell><TableCell>{{ text(row.status) }}<p v-if="row.error_code" class="mt-1 text-xs text-destructive">{{ text(row.error_code) }}</p></TableCell><TableCell>{{ (row.size_bytes / 1024).toFixed(1) }} KiB</TableCell><TableCell>{{ row.summary.rows == null ? '尚无预检' : `${row.summary.rows} 行` }}</TableCell><TableCell class="text-right"><Button variant="ghost" size="sm" @click="detail(row.id, true)">查看与处理</Button></TableCell></TableRow><TableRow v-if="!rows.length"><TableCell :colspan="5" class="h-28 text-center text-muted-foreground">{{ loading ? '正在读取…' : error ? '读取失败，请重试' : '暂无导入批次' }}</TableCell></TableRow></TableBody></Table></div><Pager v-if="!error" :page="page" :pages="pages" :total="total" :loading="loading" @change="value => { page = value; load() }" />
  <Dialog :open="uploadOpen" @update:open="value => { if (!busy) uploadOpen = value }"><DialogContent class="max-h-[90svh] overflow-y-auto sm:max-w-lg"><DialogHeader><DialogTitle>上传私有知识文件</DialogTitle><DialogDescription>仅未压缩 JSONL / NDJSON，最大 64 MiB；每行一个 JSON 对象。上传不代表导入。</DialogDescription></DialogHeader><form id="knowledge-upload-form" class="space-y-4" @submit.prevent="upload"><div class="space-y-2"><Label>JSONL 来源</Label><Picker v-model="uploadSource" kind="sources" connector="jsonl" required :disabled="busy || uncertain" /></div><div class="space-y-2"><Label for="knowledge-upload-file">知识文件</Label><input id="knowledge-upload-file" ref="fileInput" type="file" accept=".jsonl,.ndjson" class="block w-full rounded-md border p-3 text-sm" :disabled="busy || uncertain" @change="chooseFile" /></div><p v-if="selectedFile" class="break-all text-xs text-muted-foreground">{{ selectedFile.name }} · {{ (selectedFile.size / 1024).toFixed(1) }} KiB</p><p v-if="uploadError" role="alert" class="text-sm text-destructive">{{ uploadError }}</p><Button v-if="uncertain" type="button" variant="outline" @click="uploadOpen = false; load(true)">返回列表核对</Button></form><DialogFooter><Button variant="outline" :disabled="busy" @click="uploadOpen = false">取消</Button><Button type="submit" form="knowledge-upload-form" :disabled="busy || uncertain">{{ busy ? '上传中…' : '上传到私有暂存' }}</Button></DialogFooter></DialogContent></Dialog>
  <Dialog :open="opened" @update:open="value => { if (!busy) opened = value }"><DialogContent class="max-h-[92svh] overflow-y-auto sm:max-w-5xl"><DialogHeader><DialogTitle>导入预检与回执</DialogTitle><DialogDescription class="break-all">{{ selected }}</DialogDescription></DialogHeader><p v-if="detailLoading" class="text-muted-foreground">正在读取…</p><template v-if="batch"><div class="space-y-2 rounded-md border p-3 text-sm"><p>{{ text(batch.status) }} · 来源：{{ source?.name || '读取中' }} · 版本 {{ batch.source_version }}</p><p>暂存到期：{{ new Date(batch.expires_at * 1000).toLocaleString() }}</p><p v-if="batch.summary.actions">预检：<span v-for="(count, key) in batch.summary.actions" :key="key" class="mr-3">{{ text(String(key)) }} {{ count }}</span></p><p v-if="batch.summary.flags && Object.keys(batch.summary.flags).length">全部警告：<span v-for="(count, key) in batch.summary.flags" :key="key" class="mr-3">{{ text(String(key)) }} {{ count }}</span></p><p v-if="Object.keys(batch.result).length">实际结果：<span v-for="(count, key) in batch.result" :key="key" class="mr-3">{{ text(String(key)) }} {{ count }}</span></p><p v-if="batch.error_code" class="text-destructive">{{ text(batch.error_code) }}</p><Button v-if="batch.job" type="button" size="sm" variant="link" class="px-0" @click="emit('job', batch!.job!.id)">查看 {{ text(batch.job.status) }} 的执行任务</Button></div>
    <details v-if="canPreflight" :open="mappingOpen" class="rounded-md border p-4"><summary class="cursor-pointer text-sm font-medium">字段、外部来源与适用范围</summary><form id="knowledge-preflight-form" class="mt-4 space-y-4" @submit.prevent="preflight"><fieldset class="space-y-4" :disabled="busy || stale || detailLoading"><div class="grid gap-4 sm:grid-cols-2"><div class="space-y-2"><Label for="import-identity">文档身份字段</Label><Textarea id="import-identity" v-model="mapping.identity_fields" placeholder="每行一个顶层键，最多 4 个" required /></div><div class="space-y-2"><Label for="import-origin">外部来源字段</Label><Input id="import-origin" v-model="mapping.origin_field" placeholder="包中有 source_id 时必须明确映射" /><Label for="import-origin-values">允许的外部来源值</Label><Textarea id="import-origin-values" v-model="mapping.origin_values" placeholder="每行一个，填写字段时必须明确允许值" /></div></div><div class="grid gap-4 sm:grid-cols-3"><div class="space-y-2"><Label for="import-title">标题字段</Label><Input id="import-title" v-model="mapping.title_field" required /></div><div class="space-y-2"><Label for="import-content">正文字段</Label><Input id="import-content" v-model="mapping.content_field" required /></div><div class="space-y-2"><Label for="import-url">出处字段</Label><Input id="import-url" v-model="mapping.url_field" placeholder="可留空" /></div></div><div class="grid gap-4 sm:grid-cols-2"><div class="space-y-2"><Label for="import-metadata-mode">范围处理</Label><select id="import-metadata-mode" v-model="mapping.metadata_mode" class="h-9 w-full rounded-md border bg-background px-3"><option value="fixed">固定为此范围，冲突记录阻止</option><option value="record">允许记录自带范围，仍校验归属</option></select></div><div class="space-y-2"><Label for="import-version">明确适用版本</Label><select id="import-version" v-model="mapping.version_scope" class="h-9 w-full rounded-md border bg-background px-3"><option value="unknown">未知，需核对</option><option value="all">明确所有版本</option><option value="range">明确版本范围</option></select></div></div><div v-if="mapping.version_scope === 'range'" class="grid gap-4 sm:grid-cols-2"><div class="space-y-2"><Label for="import-min">最低版本</Label><Input id="import-min" v-model="mapping.min_version" /></div><div class="space-y-2"><Label for="import-max">最高版本</Label><Input id="import-max" v-model="mapping.max_version" /></div></div><details class="space-y-3 rounded-md border p-3"><summary class="cursor-pointer text-sm">产品与来源时间（未知留空）</summary><Label for="import-product">产品标识</Label><Input id="import-product" v-model="mapping.product" placeholder="留空继承集合产品" /><Label for="import-commit">来源提交</Label><Input id="import-commit" v-model="mapping.source_commit" /><Label for="import-fetched">采集时间（含时区）</Label><Input id="import-fetched" v-model="mapping.fetched_at" /><Label for="import-until">有效截止（含时区）</Label><Input id="import-until" v-model="mapping.valid_until" /></details><p class="text-xs text-muted-foreground">映射只接受顶层字段名，不执行脚本。外部来源值不授予任何中心访问权限。</p></fieldset><Button type="submit" :disabled="busy || stale || detailLoading">排队预检</Button></form></details>
    <template v-if="hasRows"><div class="overflow-hidden rounded-md border"><Table><TableHeader><TableRow><TableHead>行 / 身份</TableHead><TableHead>动作</TableHead><TableHead>提示</TableHead><TableHead>决定</TableHead><TableHead class="text-right">查看</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in entries" :key="row.line"><TableCell class="max-w-64 break-all">第 {{ row.line }} 行<p class="mt-1 text-xs text-muted-foreground">{{ row.title || row.external_id || (row.document_id ? `文档 ${row.document_id.slice(0, 8)}` : '受限记录') }}</p></TableCell><TableCell>{{ text(row.action) }}<p v-if="row.revision" class="text-xs">r{{ row.revision }}</p></TableCell><TableCell class="max-w-64 break-words text-xs">{{ row.flags.map(text).join('、') || '—' }}</TableCell><TableCell><select v-if="batch.status === 'ready' && row.action !== 'excluded'" :value="decisions[row.line]?.value || ''" :aria-label="`第 ${row.line} 行决定`" class="h-9 rounded-md border bg-background px-2 text-sm" :disabled="busy || stale || detailLoading" @change="decide(row, $event)"><option value="">{{ ['blocked', 'conflict'].includes(row.action) ? '请选择' : '按计划处理' }}</option><option v-if="row.action === 'conflict'" value="append">追加为待审</option><option value="skip">明确跳过</option></select><span v-else class="text-xs text-muted-foreground">{{ batch.status === 'imported' ? '已执行' : '只读' }}</span></TableCell><TableCell class="text-right"><div class="flex justify-end gap-1"><Button size="sm" variant="ghost" :disabled="detailLoading" @click="readLine(row)">对照</Button><Button v-if="row.document_id && batch.status === 'imported'" size="sm" variant="ghost" @click="emit('document', row.document_id)">知识</Button></div></TableCell></TableRow><TableRow v-if="!entries.length"><TableCell :colspan="5" class="h-24 text-center text-muted-foreground">{{ detailLoading ? '正在读取…' : detailError ? '读取失败，请重试' : '没有行记录' }}</TableCell></TableRow></TableBody></Table></div><Pager :page="rowPage" :pages="Math.max(1, Math.ceil(rowTotal / 20))" :total="rowTotal" :loading="detailLoading" @change="value => { rowPage = value; detail(selected) }" /><template v-if="batch.status === 'ready'"><p class="text-sm">已明确处理 {{ Object.keys(decisions).length }} 行；未跳过的可用记录将按计划导入待审。</p><label v-if="Object.values(batch.summary.flags || {}).some(count => count > 0)" class="flex gap-2 text-sm"><input v-model="acknowledge" type="checkbox" :disabled="busy || stale" />我已核对全部预检警告，正文指令不执行</label><p v-if="readiness" class="text-xs text-muted-foreground">{{ readiness }}</p></template></template>
  </template><p v-if="detailError" role="alert" class="text-sm text-destructive">{{ detailError }}</p><DialogFooter><Button variant="outline" :disabled="busy" @click="opened = false">关闭</Button><Button variant="outline" :disabled="busy || detailLoading" @click="reload">重新读取并审阅</Button><Button v-if="canDiscard" variant="outline" :disabled="busy || stale || detailLoading" @click="confirmation = 'discard'">丢弃暂存</Button><Button v-if="batch?.status === 'ready'" :disabled="busy || stale || detailLoading || !!readiness" @click="confirmation = 'apply'">确认计划并导入待审</Button></DialogFooter></DialogContent></Dialog>
  <Dialog :open="lineOpen" @update:open="value => lineOpen = value"><DialogContent class="max-h-[92svh] overflow-y-auto sm:max-w-5xl"><DialogHeader><DialogTitle>单行知识对照</DialogTitle><DialogDescription>{{ line ? `第 ${line.line} 行 · ${text(line.action)}` : '明确读取这一行，不加载整个包' }}</DialogDescription></DialogHeader><p v-if="lineLoading" class="text-muted-foreground">正在读取…</p><template v-if="line"><p class="text-sm">{{ line.flags.map(text).join('、') }}</p><div class="grid gap-4 sm:grid-cols-2"><div class="min-w-0 space-y-2"><p class="font-medium">原版本：{{ line.base?.title || '无' }}</p><pre class="max-h-[45svh] overflow-auto rounded-md border p-3 text-sm leading-6 whitespace-pre-wrap break-words">{{ line.base?.content || '没有可显示的原文' }}</pre></div><div class="min-w-0 space-y-2"><p class="font-medium">候选：{{ line.candidate?.title || '无' }}</p><pre class="max-h-[45svh] overflow-auto rounded-md border p-3 text-sm leading-6 whitespace-pre-wrap break-words">{{ line.candidate?.content || '阻止行或空候选不回显原文件正文' }}</pre></div></div></template><p v-if="lineError" role="alert" class="text-sm text-destructive">{{ lineError }}</p><DialogFooter><Button @click="lineOpen = false">关闭对照</Button></DialogFooter></DialogContent></Dialog>
  <ConfirmAction :open="!!confirmation" :title="confirmation === 'apply' ? '按所见计划导入待审？' : '丢弃此批次暂存？'" :description="confirmation === 'apply' ? '处理整个已审阅计划中未跳过的可用记录；新增内容仅进入待审，当前发布版保留。排队不代表已完成。' : '删除此批次私有暂存。已经提交的历史修订和行回执仍会保留。'" :word="confirmation === 'apply' ? '导入' : '丢弃'" :busy="busy" @update:open="value => { if (!value) confirmation = null }" @confirm="act" />
</div></template>
