<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, date, notify, write } from '@/api'
import { message, text, usePage, type Document, type Job, type Page, type Revision } from '@/knowledge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import ConfirmAction from '@/components/ConfirmAction.vue'
import Pager from './Pager.vue'
interface Receipt { external_id: string; document_id: string | null; revision_number: number | null; outcome: string; flags: string[]; candidate_id: string | null }
interface Receipts extends Page<Receipt> { job: Job; result: { counts?: Record<string, number>; flags?: Record<string, number> } }
interface Candidate {
  candidate: { id: string; version: number; document_id: string; document_version: number; base_revision: number; flags: string[]; applied_revision: number | null; discarded: boolean }
  current: Document; proposal: Revision & { content: string }
}
const props = defineProps<{ jobId?: string; enabled?: boolean }>()
const emit = defineEmits<{ document: [id: string]; changed: [] }>()
const { rows, page, pages, total, loading, error, load } = usePage<Job>(() => '/knowledge/jobs')
const selected = ref(''), opened = ref(false), job = ref<Job | null>(null), detailLoading = ref(false), detailError = ref('')
const receipts = ref<Receipts | null>(null), receiptPage = ref(1), busy = ref(false), cancelOpen = ref(false)
const candidate = ref<Candidate | null>(null), candidateLoading = ref(false), candidateError = ref(''), candidateId = ref(''), candidateOpen = ref(false)
const acknowledge = ref(false), candidateStale = ref(false), candidateAction = ref<'append' | 'discard' | null>(null)
/** 请求取消仍属于活动任务，只有执行器终态才能视为结束。 */
const active = (item: Job | null) => !!item && ['queued', 'running'].includes(item.status)
const candidateInvalid = computed(() => candidateStale.value || !!candidate.value && candidate.value.current.version !== candidate.value.candidate.document_version)
let sequence = 0, candidateSequence = 0, alive = true
/** 明确 ID 回读当前状态，采集任务再读取所选页的无正文回执。 */
async function detail(id: string, reset = false) {
  const current = ++sequence
  if (selected.value !== id || reset) { job.value = null; receipts.value = null; cancelOpen.value = false }
  if (reset) receiptPage.value = 1
  selected.value = id; opened.value = true; detailLoading.value = true; detailError.value = ''
  try {
    const value = await api<Job>(`/knowledge/jobs/${id}`)
    const report = value.kind === 'collect' ? await api<Receipts>(`/knowledge/jobs/${id}/collection?page=${receiptPage.value}&size=20`) : null
    if (current !== sequence) return
    job.value = report?.job || value; receipts.value = report
  } catch (reason) { if (current === sequence) detailError.value = message(reason) }
  finally { if (current === sequence) detailLoading.value = false }
}
/** 索引重建只入队，执行结果从任务详情读取。 */
async function index() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const value = await write<Job>('/knowledge/jobs/index', 'POST')
    if (!alive) return
    notify('索引重建已排队'); await load(true); await detail(value.id, true); emit('changed')
  } catch (reason) { if (alive) error.value = message(reason) }
  finally { busy.value = false }
}
/** 取消使用所见版本；运行中只是取消请求，不冒充执行器已经结束。 */
async function cancel() {
  if (!job.value || busy.value) return
  busy.value = true; detailError.value = ''
  try {
    const result = await write<Job>(`/knowledge/jobs/${job.value.id}/cancel`, 'POST', { expected_version: job.value.version })
    if (!alive) return
    job.value = result; cancelOpen.value = false; notify(result.status === 'cancelled' ? '任务已取消' : '已请求取消，等待执行器确认'); await load()
  } catch (reason) { if (alive) { detailError.value = message(reason); cancelOpen.value = false } }
  finally { busy.value = false }
}
/** 候选正文仅在明确打开时读取，不由任务轮询刷新或覆盖审阅基线。 */
async function readCandidate(id: string) {
  const current = ++candidateSequence; candidateId.value = id; candidateOpen.value = true; candidateLoading.value = true
  candidate.value = null; candidateError.value = ''; acknowledge.value = false; candidateStale.value = false
  try { const result = await api<Candidate>(`/knowledge/collection/candidates/${id}`); if (current === candidateSequence) candidate.value = result }
  catch (reason) { if (current === candidateSequence) candidateError.value = message(reason) }
  finally { if (current === candidateSequence) candidateLoading.value = false }
}
/** 追加仅生成待审，不改变发布指针；忽略保留原人工版本。 */
async function resolveCandidate() {
  if (!candidate.value || !candidateAction.value || busy.value) return
  const value = candidate.value, action = candidateAction.value
  if (action === 'append' && (candidateInvalid.value || value.candidate.flags.length > 0 && !acknowledge.value)) return
  busy.value = true; candidateError.value = ''
  try {
    await write(`/knowledge/collection/candidates/${value.candidate.id}/${action}`, 'POST', {
      expected_version: value.candidate.version,
      ...(action === 'append' ? { expected_document_version: value.current.version, acknowledge_warnings: acknowledge.value } : {}),
    })
    if (!alive) return
    candidateAction.value = null; notify(action === 'append' ? '已追加待审修订，尚未发布' : '候选已忽略')
    await readCandidate(value.candidate.id); emit('changed')
  } catch (reason) { if (alive) { candidateError.value = message(reason); candidateStale.value = true; candidateAction.value = null } }
  finally { busy.value = false }
}
/** 仅当前可见且有活动任务时刷新，关闭详情后不再读取其回执。 */
function refresh() { void load(); if (opened.value && selected.value && !detailLoading.value && !busy.value && !cancelOpen.value) void detail(selected.value) }
const timer = setInterval(() => {
  if (props.enabled && document.visibilityState === 'visible' && !loading.value && !busy.value && !cancelOpen.value && (rows.value.some(active) || opened.value && active(job.value))) refresh()
}, 5000)
watch(() => props.jobId, value => { if (value) void detail(value, true) }, { immediate: true })
watch(opened, value => { if (!value) { sequence++; job.value = null; receipts.value = null; selected.value = ''; detailLoading.value = false } })
watch(candidateOpen, value => { if (!value) { candidateSequence++; candidate.value = null; candidateLoading.value = false } })
onBeforeUnmount(() => { alive = false; sequence++; candidateSequence++; clearInterval(timer); candidate.value = null; receipts.value = null })
</script>
<template><div><div class="mb-5 flex flex-wrap justify-between gap-3"><p class="text-sm text-muted-foreground">任务由独立 worker 执行，排队不代表完成。</p><div class="flex gap-2"><Button variant="outline" :disabled="loading" @click="refresh">刷新</Button><Button :disabled="busy" @click="index">重建索引</Button></div></div><p v-if="error" role="alert" class="mb-4 text-sm text-destructive">{{ error }}</p><div class="overflow-hidden rounded-md border"><Table><TableHeader><TableRow class="bg-muted/50"><TableHead>类型 / 创建时间</TableHead><TableHead>状态</TableHead><TableHead>结果</TableHead><TableHead>尝试次数</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell>{{ text(row.kind) }}<p class="mt-1 whitespace-nowrap text-xs text-muted-foreground">{{ date(row.created_at) }}</p></TableCell><TableCell>{{ text(row.status) }}<p v-if="row.cancel_requested && active(row)" class="mt-1 text-xs text-muted-foreground">等待取消确认</p></TableCell><TableCell>{{ text(row.outcome) }}</TableCell><TableCell>{{ row.attempts }} / {{ row.max_attempts }}</TableCell><TableCell class="text-right"><Button variant="ghost" size="sm" @click="detail(row.id, true)">查看</Button></TableCell></TableRow><TableRow v-if="!rows.length"><TableCell :colspan="5" class="h-32 text-center text-muted-foreground">{{ loading ? '正在加载…' : error ? '读取失败，请重试' : '暂无任务' }}</TableCell></TableRow></TableBody></Table></div><Pager v-if="!error" :page="page" :pages="pages" :total="total" :loading="loading" @change="value => { page = value; load() }" />
  <Dialog :open="opened" @update:open="value => { if (!busy) opened = value }"><DialogContent class="max-h-[92svh] overflow-y-auto sm:max-w-4xl"><DialogHeader><DialogTitle>任务详情与回执</DialogTitle><DialogDescription class="break-all">{{ selected }}</DialogDescription></DialogHeader><p v-if="detailLoading" class="text-sm text-muted-foreground">正在读取…</p><template v-if="job"><div class="space-y-2 rounded-md border p-3 text-sm"><p>{{ text(job.kind) }} · {{ text(job.status) }} · {{ text(job.outcome) }}</p><p v-if="job.kind === 'index'">已处理文档 {{ job.processed_documents }} · 索引分块 {{ job.processed_chunks }}</p><p>尝试 {{ job.attempts }} / {{ job.max_attempts }}<span v-if="job.cancel_requested && active(job)"> · 已请求取消，等待确认</span></p><p v-if="job.status === 'queued'">最早执行：{{ new Date(job.available_at * 1000).toLocaleString() }}</p><p v-if="receipts?.result.counts">结果：<span v-for="(count, key) in receipts.result.counts" :key="key" class="mr-3">{{ text(String(key)) }} {{ count }}</span></p><p v-if="receipts?.result.flags && Object.keys(receipts.result.flags).length">提示：<span v-for="(count, key) in receipts.result.flags" :key="key" class="mr-3">{{ text(String(key)) }} {{ count }}</span></p></div><template v-if="receipts"><div class="overflow-hidden rounded-md border"><Table><TableHeader><TableRow><TableHead>页面 / 文件</TableHead><TableHead>结果</TableHead><TableHead>提示</TableHead><TableHead class="text-right">查看</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in receipts.items" :key="row.external_id"><TableCell class="max-w-sm whitespace-normal break-all">{{ row.external_id }}</TableCell><TableCell>{{ text(row.outcome) }}<p v-if="row.revision_number" class="mt-1 text-xs">r{{ row.revision_number }}</p></TableCell><TableCell class="max-w-sm whitespace-normal break-words">{{ row.flags.map(text).join('、') || '—' }}</TableCell><TableCell class="text-right"><div class="flex justify-end gap-1"><Button v-if="row.document_id" size="sm" variant="ghost" @click="emit('document', row.document_id)">知识</Button><Button v-if="row.candidate_id" size="sm" variant="ghost" @click="readCandidate(row.candidate_id)">差异候选</Button></div></TableCell></TableRow><TableRow v-if="!receipts.items.length"><TableCell :colspan="4" class="h-24 text-center text-muted-foreground">{{ active(job) ? '尚无回执，等待执行器提交结果' : '没有逐页回执' }}</TableCell></TableRow></TableBody></Table></div><Pager :page="receiptPage" :pages="Math.max(1, Math.ceil(receipts.total / 20))" :total="receipts.total" :loading="detailLoading" @change="value => { receiptPage = value; detail(selected) }" /></template></template><p v-if="detailError" role="alert" class="text-sm text-destructive">{{ detailError }}</p><DialogFooter><Button variant="outline" :disabled="busy" @click="opened = false">关闭</Button><Button variant="outline" :disabled="busy || detailLoading" @click="detail(selected)">刷新详情</Button><Button v-if="active(job) && !job?.cancel_requested" variant="outline" :disabled="busy || detailLoading || !!detailError" @click="cancelOpen = true">取消任务</Button></DialogFooter></DialogContent></Dialog>
  <Dialog :open="candidateOpen" @update:open="value => { if (!busy) candidateOpen = value }"><DialogContent class="max-h-[92svh] overflow-y-auto sm:max-w-5xl"><DialogHeader><DialogTitle>审阅上游差异</DialogTitle><DialogDescription>对照当前人工知识与受保护的候选，追加后仍需单独发布。</DialogDescription></DialogHeader><p v-if="candidateLoading" class="text-muted-foreground">正在读取…</p><template v-if="candidate"><p class="text-sm">{{ candidate.candidate.applied_revision ? `已追加为待审 r${candidate.candidate.applied_revision}` : candidate.candidate.discarded ? '已忽略该候选' : `候选基于 r${candidate.candidate.base_revision}，当前 head r${candidate.current.head_revision}` }}</p><p v-if="candidateInvalid && !candidate.candidate.applied_revision" class="text-sm text-destructive">文档已发生变化或候选状态需刷新；不能直接追加旧候选，请重新读取并审阅。</p><div class="grid gap-4 sm:grid-cols-2"><div class="min-w-0 space-y-2"><p class="text-sm font-medium">当前：{{ candidate.current.revision.title }}</p><pre class="max-h-[45svh] overflow-auto rounded-md border p-3 text-sm leading-6 whitespace-pre-wrap break-words">{{ candidate.current.revision.content }}</pre></div><div class="min-w-0 space-y-2"><p class="text-sm font-medium">上游：{{ candidate.proposal.title }}</p><pre class="max-h-[45svh] overflow-auto rounded-md border p-3 text-sm leading-6 whitespace-pre-wrap break-words">{{ candidate.proposal.content }}</pre></div></div><p v-if="candidate.candidate.flags.length" class="text-sm">提示：{{ candidate.candidate.flags.map(text).join('、') }}</p><label v-if="candidate.candidate.flags.length && !candidate.candidate.applied_revision && !candidate.candidate.discarded" class="flex gap-2 text-sm"><input v-model="acknowledge" type="checkbox" />我已审阅内容警告；正文中的指令不会执行</label></template><p v-if="candidateError" role="alert" class="text-sm text-destructive">{{ candidateError }}</p><DialogFooter><Button variant="outline" :disabled="busy" @click="candidateOpen = false">关闭</Button><Button variant="outline" :disabled="busy || candidateLoading" @click="readCandidate(candidateId)">重新读取</Button><template v-if="candidate && !candidate.candidate.applied_revision && !candidate.candidate.discarded"><Button variant="outline" :disabled="busy || candidateStale" @click="candidateAction = 'discard'">忽略候选</Button><Button :disabled="busy || candidateInvalid || candidate.candidate.flags.length > 0 && !acknowledge" @click="candidateAction = 'append'">追加为待审</Button></template></DialogFooter></DialogContent></Dialog>
  <ConfirmAction :open="cancelOpen" title="取消任务？" description="排队任务会取消；运行中的任务需等待执行器确认，已经提交的结果不会回滚。" word="取消" :busy="busy" @update:open="value => cancelOpen = value" @confirm="cancel" />
  <ConfirmAction :open="!!candidateAction" :title="candidateAction === 'append' ? '追加上游候选为待审？' : '忽略上游候选？'" :description="candidateAction === 'append' ? '所审阅内容将追加为新的待审修订，当前发布版保留。' : '保留当前人工知识，将此候选标记为已忽略。'" :word="candidateAction === 'append' ? '追加' : '忽略'" :busy="busy" @update:open="value => { if (!value) candidateAction = null }" @confirm="resolveCandidate" />
</div></template>
