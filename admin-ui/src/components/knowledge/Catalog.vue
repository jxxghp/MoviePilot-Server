<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Plus, Pencil, Search } from '@lucide/vue'
import { api, date, notify, write } from '@/api'
import { connectors, differences, formFor, isConflict, kinds, lines, message, usePage, type CatalogItem, type Difference, type Form, type Kind, type Source } from '@/knowledge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import Picker from './Picker.vue'
import Pager from './Pager.vue'
import Changes from './Changes.vue'
import SourceSettings from './SourceSettings.vue'
type Detail = CatalogItem
interface CatalogForm extends Form {
  name: string; slug: string; description: string; domain: string; product: string; aliases: string; enabled: boolean
  collection_id: string; site_id: string | null; connector: Source['connector']; url: string; repository: string
  branch: string; paths: string; selector: string; refresh_minutes: number
}
const props = defineProps<{ kind: Kind }>()
const emit = defineEmits<{ job: [id: string]; documents: [id: string] }>()
const collection = ref<string | null>(null), site = ref<string | null>(null)
const { rows, query, page, pages, total, loading, error, load } = usePage<Detail>(() => `/knowledge/${props.kind}`, () => ({
  ...(collection.value ? { collection_id: collection.value } : {}), ...(site.value ? { site_id: site.value } : {}),
}))
const opened = ref(false), opening = ref(false), busy = ref(false), formError = ref(''), target = ref<Detail | null>(null)
const form = ref(formFor(props.kind) as CatalogForm), base = ref<Form>({}), changes = ref<Difference[]>([]), latest = ref<Detail | null>(null)
const settings = ref<{ id: string; mode: 'credential' | 'collection' } | null>(null)
const network = computed(() => ['github', 'http', 'nexusphp'].includes(form.value.connector))
let sequence = 0
watch([collection, site], () => load(true))
/** 依据服务器实际依赖冻结身份字段，提交时服务端仍会重新核验。 */
function locked(field: string) {
  if (!target.value) return false
  if (props.kind === 'collections') return field === 'slug' || !!target.value.has_sources && ['domain', 'product'].includes(field)
  if (props.kind === 'sites') return !!target.value.has_sources && field === 'domain'
  return !!target.value.has_documents && ['collection_id', 'site_id', 'connector', 'url', 'repository'].includes(field)
    || !!target.value.has_credential && ['collection_id', 'site_id', 'connector', 'url', 'repository', 'branch', 'paths', 'selector'].includes(field)
}
/** 编辑从当前详情开始，不把列表里的旧版本作为保存基线。 */
async function open(item?: Detail) {
  const current = ++sequence; opening.value = true; error.value = ''
  try {
    const fresh = item ? await api<Detail>(`/knowledge/${props.kind}/${item.id}`) : null
    if (current !== sequence) return
    target.value = fresh; form.value = formFor(props.kind, fresh || undefined) as CatalogForm
    base.value = { ...form.value }; changes.value = []; latest.value = null; formError.value = ''; opened.value = true
  } catch (reason) { if (current === sequence) error.value = message(reason) }
  finally { if (current === sequence) opening.value = false }
}
/** 类型切换只清理当前不再适用的参数，不在读取现有表单时触发。 */
function connectorChanged() {
  Object.assign(form.value, { url: '', repository: '', branch: 'main', paths: '', selector: '', refresh_minutes: 0 })
}
/** 保存后关闭；409 仅加载差异供审阅，绝不直接使用新版本重试。 */
async function save() {
  if (busy.value || changes.value.length) return
  formError.value = ''
  if (props.kind === 'sources' && (!form.value.collection_id || form.value.connector === 'nexusphp' && !form.value.site_id)) {
    formError.value = '请选择所属集合，NexusPHP 来源还需选择站点'; return
  }
  busy.value = true
  try {
    const payload: Form = { ...form.value }
    if (props.kind === 'sites') payload.aliases = lines(payload.aliases)
    if (props.kind === 'sources') { payload.paths = lines(payload.paths); payload.refresh_minutes = Number(payload.refresh_minutes) }
    if (target.value) payload.expected_version = target.value.version
    await write(`/knowledge/${props.kind}${target.value ? '/' + target.value.id : ''}`, target.value ? 'PUT' : 'POST', payload)
    opened.value = false; notify(`${kinds[props.kind]}已保存`); await load()
  } catch (reason) {
    formError.value = message(reason)
    if (isConflict(reason) && target.value) {
      try {
        const fresh = await api<Detail>(`/knowledge/${props.kind}/${target.value.id}`)
        if (fresh.version !== target.value.version) {
          latest.value = fresh; changes.value = differences(base.value, form.value, formFor(props.kind, fresh))
          if (changes.value.length) formError.value = ''
          if (!changes.value.length) { target.value = fresh; base.value = formFor(props.kind, fresh); formError.value = '记录版本已更新，输入已保留，请核对后再次保存' }
        }
      } catch (refreshError) { formError.value = `${message(reason)}；读取最新版失败：${message(refreshError)}` }
    }
  } finally { busy.value = false }
}
/** 明确采用逐字段选择后更新基线，下一次保存才会发生写入。 */
function apply(value: Form) {
  form.value = value as CatalogForm; target.value = latest.value; base.value = formFor(props.kind, latest.value || undefined)
  changes.value = []; latest.value = null; formError.value = '对照结果已采用，请检查后再次保存'
}
onBeforeUnmount(() => { sequence++; form.value = formFor(props.kind) as CatalogForm })
</script>
<template><div>
  <div class="mb-5 flex flex-wrap items-center justify-between gap-3"><form class="flex min-w-0 max-w-md flex-1 gap-2" @submit.prevent="load(true)"><Input v-model="query" :aria-label="`搜索${kinds[kind]}名称`" :placeholder="`搜索${kinds[kind]}名称`" maxlength="120" /><Button variant="outline" :disabled="loading"><Search class="mr-1 size-4" />查询</Button></form><Button :disabled="opening" @click="open()"><Plus class="mr-1 size-4" />新增{{ kinds[kind] }}</Button></div>
  <div v-if="kind === 'sources'" class="mb-5 grid max-w-2xl gap-3 sm:grid-cols-2"><div class="space-y-2"><Label>按集合筛选</Label><Picker v-model="collection" kind="collections" /></div><div class="space-y-2"><Label>按站点筛选</Label><Picker v-model="site" kind="sites" /></div></div>
  <p v-if="error" role="alert" class="mb-4 text-sm text-destructive">{{ error }}</p>
  <div class="overflow-hidden rounded-md border"><Table><TableHeader><TableRow class="bg-muted/50"><TableHead>名称</TableHead><TableHead>{{ kind === 'collections' ? '领域 / 产品' : kind === 'sites' ? '域名 / 别名' : '类型 / 定位' }}</TableHead><TableHead>状态</TableHead><TableHead>更新时间</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell class="max-w-64 whitespace-normal"><p class="break-words [overflow-wrap:anywhere] font-medium">{{ row.name }}</p><p v-if="kind === 'collections'" class="mt-1 break-all text-xs text-muted-foreground">{{ row.slug }}</p></TableCell><TableCell class="max-w-80 whitespace-normal break-all"><template v-if="kind === 'collections'">{{ row.domain?.toUpperCase() }}<p class="mt-1 text-xs text-muted-foreground">{{ row.product || '通用' }}</p></template><template v-else-if="kind === 'sites'">{{ row.domain }}<p class="mt-1 text-xs text-muted-foreground">{{ row.aliases?.join('、') || '无别名' }}</p></template><template v-else>{{ connectors[row.connector!] }}<p class="mt-1 text-xs text-muted-foreground">{{ row.url || row.repository || '中心维护' }}</p></template></TableCell><TableCell><Badge :variant="row.enabled ? 'secondary' : 'outline'">{{ row.enabled ? '已启用' : '已停用' }}</Badge></TableCell><TableCell class="whitespace-nowrap">{{ date(row.updated_at) }}</TableCell><TableCell class="text-right"><div class="flex justify-end gap-1"><Button variant="ghost" size="sm" :disabled="opening" @click="open(row)"><Pencil class="mr-1 size-4" />编辑</Button><template v-if="kind === 'sources'"><Button variant="ghost" size="sm" @click="emit('documents', row.id)">知识</Button><template v-if="['github', 'http', 'nexusphp'].includes(row.connector || '')"><Button variant="ghost" size="sm" @click="settings = { id: row.id, mode: 'credential' }">凭据</Button><Button variant="ghost" size="sm" @click="settings = { id: row.id, mode: 'collection' }">采集</Button></template></template></div></TableCell></TableRow><TableRow v-if="!rows.length"><TableCell :colspan="5" class="h-32 text-center text-muted-foreground">{{ loading ? '正在加载…' : error ? '读取失败，请重试' : '暂无符合条件的记录' }}</TableCell></TableRow></TableBody></Table></div>
  <Pager v-if="!error" :page="page" :pages="pages" :total="total" :loading="loading" @change="value => { page = value; load() }" />
  <Dialog :open="opened" @update:open="value => { if (!busy) opened = value }"><DialogContent class="max-h-[90svh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{{ target ? '编辑' : '新增' }}{{ kinds[kind] }}</DialogTitle><DialogDescription>保存配置不会立即采集，也不会自动发布知识。</DialogDescription></DialogHeader>
    <form id="knowledge-catalog-form" class="space-y-4" @submit.prevent="save"><fieldset class="space-y-4" :disabled="busy || !!changes.length">
      <div class="space-y-2"><Label for="catalog-name">名称</Label><Input id="catalog-name" v-model="form.name" maxlength="120" required /></div>
      <template v-if="kind === 'collections'"><div class="grid gap-4 sm:grid-cols-2"><div class="space-y-2"><Label for="catalog-slug">稳定标识</Label><Input id="catalog-slug" v-model="form.slug" pattern="[a-z][a-z0-9-]{1,63}" :disabled="locked('slug')" required /></div><div class="space-y-2"><Label for="catalog-domain">领域</Label><select id="catalog-domain" v-model="form.domain" class="h-9 w-full rounded-md border bg-background px-3" :disabled="locked('domain')"><option value="mp">MP</option><option value="pt">PT</option></select></div></div><div class="space-y-2"><Label for="catalog-product">产品标识</Label><Input id="catalog-product" v-model="form.product" pattern="[a-z][a-z0-9-]{0,63}" placeholder="moviepilot；通用知识可留空" :disabled="locked('product')" /></div><div class="space-y-2"><Label for="catalog-description">说明</Label><Textarea id="catalog-description" v-model="form.description" maxlength="2000" /></div></template>
      <template v-else-if="kind === 'sites'"><div class="space-y-2"><Label for="catalog-domain">主域名</Label><Input id="catalog-domain" v-model="form.domain" placeholder="site.example.com" maxlength="253" :disabled="locked('domain')" required /></div><div class="space-y-2"><Label for="catalog-aliases">别名</Label><Textarea id="catalog-aliases" v-model="form.aliases" placeholder="每行一个别名，最多 32 个" /></div></template>
      <template v-else><div class="grid gap-4 sm:grid-cols-2"><div class="space-y-2"><Label>所属集合</Label><Picker v-model="form.collection_id" kind="collections" required :disabled="locked('collection_id')" /></div><div class="space-y-2"><Label>所属站点</Label><Picker v-model="form.site_id" kind="sites" :required="form.connector === 'nexusphp'" :disabled="locked('site_id')" /></div></div><div class="space-y-2"><Label for="catalog-connector">来源类型</Label><select id="catalog-connector" v-model="form.connector" class="h-9 w-full rounded-md border bg-background px-3" :disabled="locked('connector')" @change="connectorChanged"><option v-for="(label, key) in connectors" :key="key" :value="key">{{ label }}</option></select></div>
      <template v-if="network"><div v-if="form.connector === 'github'" class="grid gap-4 sm:grid-cols-2"><div class="space-y-2"><Label for="catalog-repository">GitHub 仓库</Label><Input id="catalog-repository" v-model="form.repository" placeholder="owner/repository" :disabled="locked('repository')" required /></div><div class="space-y-2"><Label for="catalog-branch">分支</Label><Input id="catalog-branch" v-model="form.branch" :disabled="locked('branch')" required /></div></div><div v-else class="space-y-2"><Label for="catalog-url">HTTPS 页面地址</Label><Input id="catalog-url" v-model="form.url" type="url" maxlength="2048" :disabled="locked('url')" required /><p class="text-xs text-muted-foreground">只填规则或文档页地址，认证值在独立凭据窗口维护。</p></div><div class="space-y-2"><Label for="catalog-paths">{{ form.connector === 'github' ? '允许的仓库文件 / 目录' : '额外允许的相对页面' }}</Label><Textarea id="catalog-paths" v-model="form.paths" :disabled="locked('paths')" :required="form.connector === 'github'" placeholder="每行一个相对路径，最多 100 个" /></div><div v-if="form.connector !== 'github'" class="space-y-2"><Label for="catalog-selector">正文 CSS 选择器</Label><Input id="catalog-selector" v-model="form.selector" maxlength="200" :disabled="locked('selector')" placeholder="留空使用正文模板" /></div><div class="space-y-2"><Label for="catalog-interval">刷新间隔（分钟）</Label><Input id="catalog-interval" v-model="form.refresh_minutes" type="number" min="0" max="525600" required /><p class="text-xs text-muted-foreground">0 表示仅手动；周期任务由独立 worker 执行。</p></div></template></template>
      <div class="flex items-center justify-between rounded-md border p-3"><Label for="catalog-enabled">启用{{ kinds[kind] }}</Label><Switch id="catalog-enabled" v-model="form.enabled" /></div>
    </fieldset><p v-if="target?.has_credential" class="text-xs text-muted-foreground">来源已绑定凭据，目标范围已锁定；更改前需由超级管理员清除凭据。</p><p v-else-if="target?.has_sources || target?.has_documents" class="text-xs text-muted-foreground">已被使用的身份字段保持锁定，请为不同归属创建新记录。</p><Changes v-if="changes.length" :changes="changes" :draft="form" @apply="apply" /><p v-if="formError" role="alert" class="text-sm text-destructive">{{ formError }}</p></form>
    <DialogFooter><Button variant="outline" :disabled="busy" @click="opened = false">取消</Button><Button form="knowledge-catalog-form" type="submit" :disabled="busy || !!changes.length">{{ busy ? '保存中…' : '保存' }}</Button></DialogFooter>
  </DialogContent></Dialog>
  <SourceSettings v-if="settings" :key="settings.id + settings.mode" :source-id="settings.id" :mode="settings.mode" @close="settings = null" @changed="load()" @job="id => { settings = null; emit('job', id) }" />
</div></template>
