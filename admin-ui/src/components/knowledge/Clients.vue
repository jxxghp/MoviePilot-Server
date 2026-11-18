<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, ApiError, date, notify, write } from '@/api'
import { differences, isConflict, message, usePage, type Client, type Difference, type Form, type IssuedClient } from '@/knowledge'
import { useAuth } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import ConfirmAction from '@/components/ConfirmAction.vue'
import Changes from './Changes.vue'
import Grants from './Grants.vue'
import Pager from './Pager.vue'
interface ClientForm extends Form { name: string; enabled: boolean; expires_at: string; collection_ids: string[] }
const auth = useAuth(), allowed = computed(() => auth.user?.role === 'super_admin')
const { rows, query, page, pages, total, loading, error, load } = usePage<Client>(() => '/knowledge/clients', () => ({}), 30, () => allowed.value)
const opened = ref(false), target = ref<Client | null>(null), form = ref<ClientForm>(formFor()), base = ref<Form>({})
const latest = ref<Client | null>(null), changes = ref<Difference[]>([]), names = ref<Record<string, string>>({}), grantsReady = ref(false)
const busy = ref(false), formError = ref(''), uncertain = ref(false), rotation = ref<Client | null>(null)
const issuedToken = ref(''), tokenOpen = ref(false), reveal = ref(false), tokenName = ref('')
const editorVersion = ref(0)
let sequence = 0, alive = true
/** 白名单仅含可编辑授权字段，明文令牌从不进入此表单或列表。 */
function formFor(item?: Client): ClientForm { return { name: item?.name || '', enabled: item?.enabled ?? true, expires_at: item?.expires_at || '', collection_ids: [...(item?.collection_ids || [])] } }
/** 打开时回读当前版本，不使用列表旧状态编辑或轮换。 */
async function open(item?: Client, rotate = false) {
  if (!allowed.value || busy.value) return
  const current = ++sequence; error.value = ''
  try {
    const fresh = item ? await api<Client>(`/knowledge/clients/${item.id}`) : null
    if (current !== sequence || !allowed.value) return
    if (rotate && fresh) { rotation.value = fresh; return }
    target.value = fresh; form.value = formFor(fresh || undefined); base.value = formFor(fresh || undefined)
    latest.value = null; changes.value = []; formError.value = ''; uncertain.value = false; names.value = {}; grantsReady.value = false; editorVersion.value++; opened.value = true
  } catch (reason) { if (current === sequence) error.value = message(reason) }
}
/** 一次性响应只在独立窗口内保留，异常格式视为结果未确认而非自动重发。 */
function issued(result: IssuedClient) {
  if (!/^mpk_[A-Za-z0-9_-]{43}$/.test(result.token)) throw new Error('令牌响应未确认，请刷新列表后明确轮换')
  tokenName.value = result.name; issuedToken.value = result.token; reveal.value = false; tokenOpen.value = true
}
/** 保存范围有版本检查；并发变化需对照，未知提交结果需先返回列表核对。 */
async function save() {
  if (busy.value || !allowed.value || uncertain.value || changes.value.length || !grantsReady.value) return
  const current = sequence; busy.value = true; formError.value = ''
  try {
    const payload = { ...form.value, expires_at: form.value.expires_at || null, ...(target.value ? { expected_version: target.value.version } : {}) }
    const result = await write<Client | IssuedClient>(`/knowledge/clients${target.value ? '/' + target.value.id : ''}`, target.value ? 'PUT' : 'POST', payload)
    if (!alive || !allowed.value || current !== sequence) return
    if (!target.value) issued(result as IssuedClient)
    opened.value = false; notify(target.value ? '客户端与授权已保存' : '客户端已签发，令牌仅在当前窗口显示'); await load()
  } catch (reason) {
    if (!alive || !allowed.value || current !== sequence) return
    formError.value = message(reason)
    if (isConflict(reason) && target.value) {
      try {
        const fresh = await api<Client>(`/knowledge/clients/${target.value.id}`)
        if (!alive || !allowed.value || current !== sequence) return
        if (fresh.version !== target.value.version) {
          latest.value = fresh; changes.value = differences(base.value, form.value, formFor(fresh))
          if (changes.value.length) formError.value = ''
          else { target.value = fresh; base.value = formFor(fresh); formError.value = '版本已更新，请核对输入后再次保存' }
        }
      } catch (refreshError) { if (current === sequence) formError.value = `读取最新授权失败：${message(refreshError)}；输入已保留` }
    } else if (!(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500 || reason.status === 408) {
      uncertain.value = true; formError.value = '操作结果未确认，请先返回列表核对，勿重复签发或保存'
    }
  } finally { if (current === sequence) busy.value = false }
}
/** 授权集合对照用当前名称显示，未知记录保持可识别而不自动移除。 */
function display(key: string, value: unknown) {
  if (key === 'collection_ids' && Array.isArray(value)) return value.map(id => names.value[String(id)] || `集合 ${String(id).slice(0, 8)}`).join('、') || '无读取权限'
  return typeof value === 'boolean' ? value ? '启用' : '停用' : value ? String(value) : '（空）'
}
/** 采用审阅结果只更新基线，仍需明确再次保存。 */
function apply(value: Form) { form.value = value as ClientForm; target.value = latest.value; base.value = formFor(latest.value || undefined); latest.value = null; changes.value = []; formError.value = '已采用对照结果，请核对后再次保存' }
/** 轮换只提交一次所见版本，任何失败均不重放，也不保留旧窗口的令牌。 */
async function rotate() {
  if (!rotation.value || busy.value || !allowed.value) return
  const current = sequence; busy.value = true; error.value = ''; issuedToken.value = ''
  try {
    const result = await write<IssuedClient>(`/knowledge/clients/${rotation.value.id}/rotate`, 'POST', { expected_version: rotation.value.version })
    if (!alive || !allowed.value || current !== sequence) return
    issued(result); notify('令牌已轮换，旧令牌已失效'); await load()
  } catch (reason) { if (alive && allowed.value && current === sequence) error.value = `${message(reason)}。请刷新列表核对版本后，再明确发起轮换` }
  finally { if (current === sequence) { busy.value = false; rotation.value = null } }
}
/** 只有用户明确点击才写剪贴板，通知文本不包含令牌。 */
async function copy() { try { await navigator.clipboard.writeText(issuedToken.value); notify('令牌已复制，请保存到受保护的部署配置') } catch { notify('复制失败，可显示后手动复制', true) } }
watch(tokenOpen, value => { if (!value) { issuedToken.value = ''; reveal.value = false; tokenName.value = '' } })
/** 同角色换账号也作废旧请求；迟到签发响应不能在新身份下重新打开令牌窗口。 */
watch(() => [auth.user?.id, auth.user?.role], (value, previous) => {
  sequence++; issuedToken.value = ''; tokenOpen.value = false; opened.value = false; rotation.value = null; form.value = formFor(); busy.value = false
  if (value[0] !== previous[0] && allowed.value) void load(true)
})
onBeforeUnmount(() => { alive = false; sequence++; issuedToken.value = ''; form.value = formFor() })
</script>
<template><div><p v-if="!allowed" role="alert" class="text-sm text-destructive">只有超级管理员可管理客户端授权。</p><template v-else><div class="mb-5 flex flex-wrap justify-between gap-3"><form class="flex min-w-0 max-w-md flex-1 gap-2" @submit.prevent="load(true)"><Input v-model="query" aria-label="搜索客户端" placeholder="搜索客户端名称" maxlength="120" /><Button variant="outline" :disabled="loading">查询</Button></form><Button :disabled="busy" @click="open()">签发客户端</Button></div><p v-if="error" role="alert" class="mb-4 text-sm text-destructive">{{ error }}</p><div class="overflow-hidden rounded-md border"><Table><TableHeader><TableRow><TableHead>客户端</TableHead><TableHead>状态</TableHead><TableHead>授权</TableHead><TableHead>到期</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell class="max-w-72 break-words font-medium">{{ row.name }}</TableCell><TableCell>{{ !row.enabled ? '已停用' : row.expires_at && new Date(row.expires_at).getTime() <= Date.now() ? '已到期' : '已启用' }}</TableCell><TableCell>{{ row.collection_ids.length }} 个集合</TableCell><TableCell class="whitespace-nowrap">{{ row.expires_at ? date(row.expires_at) : '不设期限' }}</TableCell><TableCell class="text-right"><Button variant="ghost" size="sm" :disabled="busy" @click="open(row)">编辑授权</Button><Button variant="ghost" size="sm" :disabled="busy" @click="open(row, true)">轮换令牌</Button></TableCell></TableRow><TableRow v-if="!rows.length"><TableCell :colspan="5" class="h-28 text-center text-muted-foreground">{{ loading ? '正在读取…' : error ? '读取失败，请重试' : '暂无客户端' }}</TableCell></TableRow></TableBody></Table></div><Pager v-if="!error" :page="page" :pages="pages" :total="total" :loading="loading" @change="value => { page = value; load() }" /></template>
  <Dialog :open="opened && allowed" @update:open="value => { if (!busy) opened = value }"><DialogContent class="max-h-[90svh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{{ target ? '编辑客户端授权' : '签发知识客户端' }}</DialogTitle><DialogDescription>知识令牌独立于后台账号。空授权没有读取权限，令牌不能用于管理接口。</DialogDescription></DialogHeader><form id="knowledge-client-form" class="space-y-4" @submit.prevent="save"><fieldset class="space-y-4" :disabled="busy || uncertain || !!changes.length"><div class="space-y-2"><Label for="knowledge-client-name">客户端名称</Label><Input id="knowledge-client-name" v-model="form.name" maxlength="120" required /></div><div class="space-y-2"><Label for="knowledge-client-expires">到期时间（含时区）</Label><Input id="knowledge-client-expires" v-model="form.expires_at" placeholder="留空不限期；如 2026-12-31T23:59:59+08:00" /></div><div class="flex items-center justify-between rounded-md border p-3"><Label for="knowledge-client-enabled">启用客户端</Label><Switch id="knowledge-client-enabled" v-model="form.enabled" /></div></fieldset><Label>完整授权范围</Label><Grants :key="editorVersion" v-model="form.collection_ids" :review-ids="latest?.collection_ids" :disabled="busy || uncertain || !!changes.length" @names="value => names = value" @ready="value => grantsReady = value" /><Changes v-if="changes.length" :changes="changes" :draft="form" :format="display" @apply="apply" /><p v-if="formError" role="alert" class="text-sm text-destructive">{{ formError }}</p><Button v-if="uncertain" type="button" variant="outline" @click="opened = false; load(true)">返回列表核对</Button></form><DialogFooter><Button variant="outline" :disabled="busy" @click="opened = false">取消</Button><Button form="knowledge-client-form" type="submit" :disabled="busy || uncertain || !!changes.length || !grantsReady">{{ busy ? '提交中…' : target ? '保存授权' : '签发' }}</Button></DialogFooter></DialogContent></Dialog>
  <Dialog v-if="tokenOpen && allowed" :open="tokenOpen" @update:open="value => tokenOpen = value"><DialogContent class="sm:max-w-lg"><DialogHeader><DialogTitle>令牌仅显示这一次</DialogTitle><DialogDescription>{{ tokenName }} · 关闭后不能找回；遗失时需明确重新轮换。</DialogDescription></DialogHeader><Input :model-value="issuedToken" :type="reveal ? 'text' : 'password'" readonly autocomplete="off" aria-label="一次性知识令牌" /><div class="flex gap-2"><Button variant="outline" @click="reveal = !reveal">{{ reveal ? '隐藏' : '显示' }}</Button><Button variant="outline" @click="copy">复制令牌</Button></div><p class="text-xs text-muted-foreground">保存到 Agent 的受保护配置中，页面不会将其写入浏览器存储。</p><DialogFooter><Button @click="tokenOpen = false">关闭并清空</Button></DialogFooter></DialogContent></Dialog>
  <ConfirmAction :open="!!rotation && allowed" title="轮换知识令牌？" description="旧令牌将立即失效。新令牌只在成功响应中显示一次；结果未确认时先核对版本，不能自动重放。" word="轮换" :busy="busy" @update:open="value => { if (!value) rotation = null }" @confirm="rotate" />
</div></template>
