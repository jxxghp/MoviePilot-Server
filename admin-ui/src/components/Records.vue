<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Eye, RefreshCw, Search, Trash2, ChevronLeft, ChevronRight } from '@lucide/vue'
import { useRoute } from 'vue-router'
import { api, date, number, notify, write } from '@/api'
import { columns, labels } from '@/resources'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import ConfirmAction from '@/components/ConfirmAction.vue'
const props = defineProps<{ resource: string }>()
const route = useRoute()
const rows = ref<Record<string, any>[]>([]), total = ref(0), page = ref(1), query = ref(''), mediaType = ref('all'), source = ref('all'), sort = ref('id')
const loading = ref(false), error = ref(''), detail = ref<Record<string, any> | null>(null), detailOpen = ref(false), deleting = ref(false), confirmOpen = ref(false)
const sourceOptions = ref<string[]>([]), typeOptions = ref<string[]>([])
const size = 20
let sequence = 0
const fields = computed(() => columns[props.resource] || [])
const pages = computed(() => Math.max(Math.ceil(total.value / size), 1))
const deletable = computed(() => props.resource !== 'audit')
const sortable = computed(() => fields.value.filter(field => !['average_rating', 'rating_count'].includes(field.key)))
const mediaFilter = computed(() => ['subscriptions', 'subscribe-shares'].includes(props.resource))
/** 只接收最后一次分页请求的结果，避免切换标签后旧响应覆盖新列表。 */
async function load(reset = false) {
  if (reset) page.value = 1
  const current = ++sequence; loading.value = true; error.value = ''
  const params = new URLSearchParams({ q: query.value, page: String(page.value), size: String(size), sort: sort.value, media_type: mediaType.value === 'all' ? '' : mediaType.value, source: source.value === 'all' ? '' : source.value })
  try {
    const result = await api(`/data/${props.resource}?${params}`)
    if (current === sequence) { rows.value = result.items; total.value = result.total; sourceOptions.value = result.filters?.sources || []; typeOptions.value = result.filters?.types || [] }
  } catch (reason) { if (current === sequence) error.value = (reason as Error).message }
  finally { if (current === sequence) loading.value = false }
}
/** 读取独立详情接口，避免误调用公共复用接口而增加次数。 */
async function show(id: number) {
  try { detail.value = await api(`/data/${props.resource}/${id}`); detailOpen.value = true }
  catch (reason) { notify((reason as Error).message, true) }
}
/** 删除后校正最后一页页码，再刷新服务端列表和计数。 */
async function remove() {
  if (!detail.value) return
  deleting.value = true
  try {
    await write(`/data/${props.resource}/${detail.value.id}`, 'DELETE', { confirm: '删除' })
    confirmOpen.value = false; detailOpen.value = false; notify('记录已删除')
    if (rows.value.length === 1 && page.value > 1) page.value -= 1
    await load()
  } catch (reason) { notify((reason as Error).message, true) }
  finally { deleting.value = false }
}
/** 将复杂 JSON 作为纯文本展示，避免将分享内容当成 HTML 执行。 */
function formatted(key: string, value: unknown) {
  if (value == null || value === '') return '—'
  if (['detail', 'actions', 'flows'].includes(key)) { try { return JSON.stringify(typeof value === 'string' ? JSON.parse(value) : value, null, 2) } catch { return String(value) } }
  if (key.endsWith('_at') || key === 'date') return date(value)
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}
/** 切换资源时清除筛选、详情和页码。 */
function reset() {
  query.value = ''; page.value = 1; mediaType.value = 'all'; source.value = 'all'; sort.value = 'id'; detailOpen.value = false
  void load()
}
watch(() => props.resource, reset, { immediate: true })
watch([mediaType, source, sort], () => { void load(true) })
if (route.query.detail && Number.isInteger(Number(route.query.detail))) void show(Number(route.query.detail))
</script>
<template>
  <div>
    <form class="mb-6 flex flex-wrap items-end gap-3" @submit.prevent="load(true)">
      <div class="min-w-48 flex-1 space-y-2"><Label for="record-search">搜索</Label><div class="relative"><Search class="absolute top-2.5 left-3 size-4 text-muted-foreground" /><Input id="record-search" v-model="query" class="pl-9" placeholder="搜索名称、分享者或标识" maxlength="200" /></div></div>
      <div v-if="mediaFilter" class="space-y-2"><Label for="record-type">类型</Label><Select v-model="mediaType"><SelectTrigger id="record-type" class="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部类型</SelectItem><SelectItem v-for="item in typeOptions" :key="item" :value="item">{{ item }}</SelectItem></SelectContent></Select></div>
      <div v-if="mediaFilter" class="space-y-2"><Label for="record-source">来源</Label><Select v-model="source"><SelectTrigger id="record-source" class="w-36"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部来源</SelectItem><SelectItem v-for="item in sourceOptions" :key="item" :value="item">{{ item }}</SelectItem></SelectContent></Select></div>
      <div class="space-y-2"><Label for="record-sort">降序排列</Label><Select v-model="sort"><SelectTrigger id="record-sort" class="w-36"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="id">最近记录</SelectItem><SelectItem v-for="field in sortable" :key="field.key" :value="field.key">{{ field.label }}</SelectItem></SelectContent></Select></div>
      <Button type="submit" :disabled="loading">查询</Button><Button variant="outline" type="button" size="icon" aria-label="刷新列表" :disabled="loading" @click="load()"><RefreshCw class="size-4" :class="loading ? 'animate-spin' : ''" /></Button>
    </form>
    <div v-if="error" role="alert" class="mb-5 flex items-center justify-between rounded-md border p-4 text-sm text-destructive">{{ error }}<Button variant="outline" @click="load()">重试</Button></div>
    <div class="overflow-hidden rounded-md border" :aria-busy="loading"><Table><TableHeader><TableRow class="bg-muted/50"><TableHead v-for="field in fields" :key="field.key" :class="field.numeric ? 'text-right' : ''">{{ field.label }}</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell v-for="field in fields" :key="field.key" class="max-w-64 truncate" :class="field.numeric ? 'text-right tabular-nums' : ''" :title="String(row[field.key] ?? '')">{{ field.time ? date(row[field.key]) : field.numeric ? number(row[field.key]) : row[field.key] || '—' }}</TableCell><TableCell class="text-right"><Button variant="ghost" size="sm" @click="show(row.id)"><Eye class="mr-1 size-4" />详情</Button></TableCell></TableRow><TableRow v-if="!rows.length"><TableCell :colspan="fields.length + 1" class="h-40 text-center text-muted-foreground">{{ loading ? '正在加载…' : error ? '数据加载失败' : '没有找到符合条件的记录' }}</TableCell></TableRow></TableBody></Table></div>
    <div class="mt-5 flex flex-wrap items-center justify-between gap-4 text-sm"><span class="text-muted-foreground">共 {{ number(total) }} 条 · 每页 {{ size }} 条</span><div class="flex items-center gap-3"><Button variant="outline" size="sm" :disabled="page <= 1 || loading" @click="page -= 1; load()"><ChevronLeft class="mr-1 size-4" />上一页</Button><span class="tabular-nums">{{ page }} / {{ pages }}</span><Button variant="outline" size="sm" :disabled="page >= pages || loading" @click="page += 1; load()">下一页<ChevronRight class="ml-1 size-4" /></Button></div></div>
    <Sheet v-model:open="detailOpen"><SheetContent class="w-full overflow-y-auto sm:max-w-xl"><SheetHeader><SheetTitle>记录详情</SheetTitle><SheetDescription>查看原始字段；实例标识已脱敏。</SheetDescription></SheetHeader><dl class="space-y-5 p-6"><div v-for="(value, key) in detail" :key="key"><dt class="mb-1.5 text-xs text-muted-foreground">{{ labels[key] || key }}</dt><dd class="whitespace-pre-wrap break-all text-sm" :class="['actions','flows','detail'].includes(key) ? 'rounded-md bg-muted p-3 font-mono text-xs' : ''">{{ formatted(key, value) }}</dd></div></dl><div v-if="deletable" class="border-t p-6"><p class="mb-4 text-xs leading-5 text-muted-foreground">{{ resource === 'plugins' ? '删除仅移除安装统计，不删除插件源码或评分记录。' : resource === 'ratings' ? '删除后会重新计算该插件的平均分与评分人数。' : '删除后无法恢复，请先核对记录。' }}</p><Button variant="destructive" @click="confirmOpen = true"><Trash2 class="mr-2 size-4" />删除记录</Button></div></SheetContent></Sheet>
    <ConfirmAction v-model:open="confirmOpen" title="删除这条记录？" description="此操作不可撤销，相关统计和列表将同步更新。" :busy="deleting" @confirm="remove" />
  </div>
</template>
