<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { Plus, Search } from '@lucide/vue'
import { api, date, notify, write } from '@/api'
import { differences, isConflict, message, text, usePage, type Difference, type Document, type Form, type Job, type Revision } from '@/knowledge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import ConfirmAction from '@/components/ConfirmAction.vue'
import Changes from './Changes.vue'
import Picker from './Picker.vue'
import Pager from './Pager.vue'
interface Draft extends Form {
  title: string; content: string; product: string; version_scope: Revision['version_scope']; min_version: string; max_version: string
  source_url: string; source_commit: string; fetched_at: string; valid_until: string
}
const props = defineProps<{ sourceId?: string; documentId?: string }>()
const emit = defineEmits<{ changed: []; job: [id: string] }>()
const source = ref<string | null>(props.sourceId || null), newSource = ref<string | null>(null), externalId = ref('')
const { rows, query, page, pages, total, loading, error, load } = usePage<Document>(() => '/knowledge/documents', () => source.value ? { source_id: source.value } : {} as Record<string, string>)
const viewing = ref(false), doc = ref<Document | null>(null), viewLoading = ref(false), viewError = ref(''), revision = ref(1), stale = ref(false)
const editing = ref(false), editTarget = ref<Document | null>(null), draft = ref<Draft>(draftFor()), base = ref<Form>({})
const busy = ref(false), formError = ref(''), changes = ref<Difference[]>([]), latest = ref<Document | null>(null)
const confirmation = ref<'publish' | 'withdraw' | null>(null), indexJob = ref('')
let sequence = 0, alive = true
/** 可选元数据保留未知，不把手动维护时间伪造成来源采集时间。 */
function draftFor(value?: Revision): Draft {
  return { title: value?.title || '', content: value?.content || '', product: value?.product || '', version_scope: value?.version_scope || 'unknown',
    min_version: value?.min_version || '', max_version: value?.max_version || '', source_url: value?.source_url || '',
    source_commit: value?.source_commit || '', fetched_at: value?.fetched_at || '', valid_until: value?.valid_until || '' }
}
/** 历史修订按明确号码读取，版本框和正文始终属于同一响应。 */
async function view(id: string, number?: number) {
  const current = ++sequence; confirmation.value = null; viewing.value = true; viewLoading.value = true; viewError.value = ''; doc.value = null; stale.value = false; indexJob.value = ''
  try {
    const result = await api<Document>(`/knowledge/documents/${id}${number ? '?revision=' + number : ''}`)
    if (current !== sequence) return
    doc.value = result; revision.value = result.revision.number
  } catch (reason) { if (current === sequence) viewError.value = message(reason) }
  finally { if (current === sequence) viewLoading.value = false }
}
/** 从所见历史内容创建新草稿，旧修订和当前发布版都不原地修改。 */
function edit(value?: Document) {
  editTarget.value = value || null; draft.value = draftFor(value?.revision); base.value = { ...draft.value }
  newSource.value = null; externalId.value = ''; formError.value = ''; changes.value = []; latest.value = null; editing.value = true
}
/** 按文档版本追加；发生冲突先对照新 head，保留输入且不自动重试写入。 */
async function save() {
  if (busy.value || changes.value.length) return
  if (!editTarget.value && !newSource.value) { formError.value = '请选择手工来源'; return }
  busy.value = true; formError.value = ''
  try {
    const payload = { ...draft.value,
      min_version: draft.value.version_scope === 'range' ? draft.value.min_version || null : null,
      max_version: draft.value.version_scope === 'range' ? draft.value.max_version || null : null,
      fetched_at: draft.value.fetched_at || null, valid_until: draft.value.valid_until || null,
      ...(editTarget.value ? { expected_version: editTarget.value.version } : { source_id: newSource.value, external_id: externalId.value }),
    }
    const result = await write<Document>(`/knowledge/documents${editTarget.value ? '/' + editTarget.value.id + '/revisions' : ''}`, 'POST', payload)
    if (!alive) return
    editing.value = false; draft.value = draftFor(); notify('草稿已保存，尚未发布'); await load(); emit('changed'); await view(result.id)
  } catch (reason) {
    if (!alive) return
    formError.value = message(reason)
    if (isConflict(reason) && editTarget.value) {
      try {
        const fresh = await api<Document>(`/knowledge/documents/${editTarget.value.id}`)
        if (!alive) return
        if (fresh.version !== editTarget.value.version) {
          latest.value = fresh; changes.value = differences(base.value, draft.value, draftFor(fresh.revision))
          if (changes.value.length) formError.value = ''
          if (!changes.value.length) { editTarget.value = fresh; base.value = draftFor(fresh.revision); formError.value = '版本已更新，输入已保留，请再次核对后保存' }
        }
      } catch (refreshError) { if (alive) formError.value = `读取最新版失败：${message(refreshError)}；当前输入已保留` }
    }
  } finally { busy.value = false }
}
/** 明确合并后才采用新文档版本，草稿内容由用户再次确认保存。 */
function apply(value: Form) {
  draft.value = value as Draft; editTarget.value = latest.value; base.value = draftFor(latest.value?.revision)
  changes.value = []; latest.value = null; formError.value = '对照结果已采用，请检查后再次保存'
}
/** 发布锁定所见修订，不跟随并发 head；撤回同样校验当前文档版本。 */
async function publish() {
  if (!doc.value || !confirmation.value || busy.value || stale.value) return
  const action = confirmation.value, selected = doc.value
  busy.value = true; viewError.value = ''
  try {
    const result = await write<{ document: Document; job: Job | null; index_pending: boolean }>(`/knowledge/documents/${selected.id}/${action}`, 'POST', {
      expected_version: selected.version, ...(action === 'publish' ? { revision: selected.revision.number } : {}),
    })
    if (!alive) return
    confirmation.value = null; indexJob.value = result.job?.id || ''
    const fresh = await api<Document>(`/knowledge/documents/${selected.id}?revision=${selected.revision.number}`)
    if (!alive) return
    doc.value = fresh
    notify(action === 'withdraw' ? '知识已撤回；索引更新期间仍会拒绝旧引用' : result.index_pending ? '所选修订已批准，索引仍在排队或更新' : '所选修订已发布')
    await load(); emit('changed')
  } catch (reason) { if (alive) { viewError.value = message(reason); stale.value = true; confirmation.value = null } }
  finally { busy.value = false }
}
watch(source, () => load(true))
watch(() => props.sourceId, value => { source.value = value || null })
watch(() => props.documentId, value => { if (value) void view(value) }, { immediate: true })
watch(editing, value => { if (!value) { draft.value = draftFor(); base.value = {}; changes.value = [] } })
watch(viewing, value => { if (!value && !busy.value) { sequence++; doc.value = null; viewLoading.value = false } })
onBeforeUnmount(() => { alive = false; sequence++; draft.value = draftFor(); doc.value = null })
</script>
<template><div><div class="mb-5 flex flex-wrap items-center justify-between gap-3"><form class="flex min-w-0 max-w-md flex-1 gap-2" @submit.prevent="load(true)"><Input v-model="query" aria-label="搜索知识标题" placeholder="搜索知识标题" maxlength="120" /><Button variant="outline" :disabled="loading"><Search class="mr-1 size-4" />查询</Button></form><Button @click="edit()"><Plus class="mr-1 size-4" />新增知识</Button></div><div class="mb-5 max-w-sm space-y-2"><Label>按来源筛选</Label><Picker v-model="source" kind="sources" /></div><p v-if="error" role="alert" class="mb-4 text-sm text-destructive">{{ error }}</p>
  <div class="overflow-hidden rounded-md border"><Table><TableHeader><TableRow class="bg-muted/50"><TableHead>标题</TableHead><TableHead>最新修订</TableHead><TableHead>发布版本</TableHead><TableHead>更新时间</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell class="max-w-80 break-words"><p class="font-medium">{{ row.revision.title }}</p><p class="mt-1 break-all text-xs text-muted-foreground">{{ row.external_id }}</p></TableCell><TableCell><Badge variant="outline">r{{ row.head_revision }} · {{ text(row.revision.state) }}</Badge></TableCell><TableCell>{{ row.published_revision ? `r${row.published_revision}` : '未发布' }}</TableCell><TableCell class="whitespace-nowrap">{{ date(row.updated_at) }}</TableCell><TableCell class="text-right"><Button size="sm" variant="ghost" @click="view(row.id)">查看与审核</Button></TableCell></TableRow><TableRow v-if="!rows.length"><TableCell :colspan="5" class="h-32 text-center text-muted-foreground">{{ loading ? '正在加载…' : error ? '读取失败，请重试' : '暂无符合条件的知识' }}</TableCell></TableRow></TableBody></Table></div><Pager v-if="!error" :page="page" :pages="pages" :total="total" :loading="loading" @change="value => { page = value; load() }" />
  <Dialog :open="viewing" @update:open="value => { if (!busy) viewing = value }"><DialogContent class="max-h-[92svh] overflow-y-auto sm:max-w-4xl"><DialogHeader><DialogTitle>{{ doc?.revision.title || '知识详情' }}</DialogTitle><DialogDescription>{{ doc ? `当前 head r${doc.head_revision} · ${doc.published_revision ? '已发布 r' + doc.published_revision : '尚未发布'}` : '正在读取私有知识' }}</DialogDescription></DialogHeader><p v-if="viewLoading" class="py-8 text-muted-foreground">正在读取…</p><template v-if="doc"><form class="flex flex-wrap items-center gap-2" @submit.prevent="view(doc!.id, revision)"><Label for="knowledge-revision">历史修订</Label><Input id="knowledge-revision" v-model="revision" type="number" class="w-24" min="1" :max="doc.head_revision" required /><Button variant="outline" :disabled="busy">读取</Button><Button type="button" variant="ghost" :disabled="busy || doc.revision.number <= 1" @click="view(doc.id, doc.revision.number - 1)">上一修订</Button><Button type="button" variant="ghost" :disabled="busy || doc.revision.number >= doc.head_revision" @click="view(doc.id, doc.revision.number + 1)">下一修订</Button></form><div class="space-y-2 rounded-md border p-3 text-sm"><p>正在查看 r{{ doc.revision.number }} · {{ text(doc.revision.state) }} · {{ doc.revision.product || '通用' }}</p><p>适用版本：{{ doc.revision.version_scope === 'all' ? '所有版本' : doc.revision.version_scope === 'range' ? `${doc.revision.min_version || '不限'} 至 ${doc.revision.max_version || '不限'}` : '未知，需核对' }}</p><p>采集时间：{{ date(doc.revision.fetched_at) }} · 有效截止：{{ date(doc.revision.valid_until) }}</p><p v-if="doc.revision.source_url" class="break-all">出处：<a :href="doc.revision.source_url" target="_blank" rel="noopener noreferrer" class="underline">{{ doc.revision.source_url }}</a></p><p v-if="doc.revision.source_commit" class="break-all text-xs">来源提交：{{ doc.revision.source_commit }}</p></div><pre class="max-h-[50svh] overflow-auto rounded-md border bg-muted/20 p-4 text-sm leading-7 whitespace-pre-wrap break-words">{{ doc.revision.content }}</pre><p class="text-xs text-muted-foreground">正文作为参考资料展示，任何其中的指令均不执行。</p><Button v-if="stale" variant="outline" :disabled="busy" @click="view(doc.id)">重新读取最新版本并审阅</Button><Button v-if="indexJob" variant="link" class="justify-start px-0" @click="emit('job', indexJob)">查看索引任务</Button></template><p v-if="viewError" role="alert" class="text-sm text-destructive">{{ viewError }}</p><DialogFooter><Button variant="outline" :disabled="busy" @click="viewing = false">关闭</Button><template v-if="doc"><Button variant="outline" :disabled="busy || stale" @click="edit(doc)">编辑为新草稿</Button><Button v-if="doc.published_revision" variant="outline" :disabled="busy || stale" @click="confirmation = 'withdraw'">撤回发布</Button><Button :disabled="busy || stale || doc.published_revision === doc.revision.number" @click="confirmation = 'publish'">发布所见 r{{ doc.revision.number }}</Button></template></DialogFooter></DialogContent></Dialog>
  <Dialog :open="editing" @update:open="value => { if (!busy) editing = value }"><DialogContent class="max-h-[92svh] overflow-y-auto sm:max-w-3xl"><DialogHeader><DialogTitle>{{ editTarget ? '追加新草稿' : '新增手工知识' }}</DialogTitle><DialogDescription>正文按原样保存；保存草稿后仍需明确批准发布。</DialogDescription></DialogHeader><form id="knowledge-draft-form" class="space-y-4" @submit.prevent="save"><fieldset class="space-y-4" :disabled="busy || !!changes.length"><template v-if="!editTarget"><div class="space-y-2"><Label>手工来源</Label><Picker v-model="newSource" kind="sources" connector="manual" required /></div><div class="space-y-2"><Label for="knowledge-external">文档标识</Label><Input id="knowledge-external" v-model="externalId" maxlength="512" placeholder="来源内唯一，如 download-guide" required /></div></template><div class="space-y-2"><Label for="knowledge-title">标题</Label><Input id="knowledge-title" v-model="draft.title" maxlength="300" required /></div><div class="space-y-2"><Label for="knowledge-content">正文（Markdown / 纯文本）</Label><Textarea id="knowledge-content" v-model="draft.content" class="min-h-64 font-mono text-sm" maxlength="262144" required /></div><div class="space-y-2"><Label for="knowledge-version">适用版本</Label><select id="knowledge-version" v-model="draft.version_scope" class="h-9 w-full rounded-md border bg-background px-3"><option value="unknown">未知，保留待核对标记</option><option value="all">明确适用所有版本</option><option value="range">明确版本范围</option></select></div><div v-if="draft.version_scope === 'range'" class="grid gap-3 sm:grid-cols-2"><div class="space-y-2"><Label for="knowledge-draft-min">最低版本</Label><Input id="knowledge-draft-min" v-model="draft.min_version" placeholder="如 3.2.0" /></div><div class="space-y-2"><Label for="knowledge-draft-max">最高版本</Label><Input id="knowledge-draft-max" v-model="draft.max_version" placeholder="如 3.2.8" /></div></div><details class="space-y-4 rounded-md border p-3"><summary class="cursor-pointer text-sm">产品、出处与时间</summary><div class="space-y-2"><Label for="knowledge-product">产品标识</Label><Input id="knowledge-product" v-model="draft.product" placeholder="留空继承集合产品" /></div><div class="space-y-2"><Label for="knowledge-url">出处</Label><Input id="knowledge-url" v-model="draft.source_url" type="url" maxlength="2048" /></div><div class="space-y-2"><Label for="knowledge-commit">来源提交</Label><Input id="knowledge-commit" v-model="draft.source_commit" pattern="[a-fA-F0-9]{40}|[a-fA-F0-9]{64}" /></div><div class="space-y-2"><Label for="knowledge-fetched">采集时间（含时区）</Label><Input id="knowledge-fetched" v-model="draft.fetched_at" placeholder="未知留空；如 2026-11-18T12:00:00+08:00" /></div><div class="space-y-2"><Label for="knowledge-until">有效截止时间（含时区）</Label><Input id="knowledge-until" v-model="draft.valid_until" placeholder="未设定留空" /></div></details></fieldset><Changes v-if="changes.length" :changes="changes" :draft="draft" @apply="apply" /><p v-if="formError" role="alert" class="text-sm text-destructive">{{ formError }}</p></form><DialogFooter><Button variant="outline" :disabled="busy" @click="editing = false">取消</Button><Button form="knowledge-draft-form" type="submit" :disabled="busy || !!changes.length">{{ busy ? '保存中…' : '保存草稿' }}</Button></DialogFooter></DialogContent></Dialog>
  <ConfirmAction :open="!!confirmation" :title="confirmation === 'publish' ? `发布 r${doc?.revision.number}？` : '撤回当前发布版？'" :description="confirmation === 'publish' ? '只批准当前所见修订；索引异步更新，排队不代表已可检索。' : '立即撤回访问资格并异步更新索引，历史修订会保留。'" :word="confirmation === 'publish' ? '发布' : '撤回'" :busy="busy" @update:open="value => { if (!value) confirmation = null }" @confirm="publish" />
</div></template>
