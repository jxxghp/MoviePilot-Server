<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Search } from '@lucide/vue'
import { api, date, notify, write } from '@/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import ConfirmAction from '@/components/ConfirmAction.vue'

/** 运营列表只包含来源的脱敏值、实例标识及设备计数。 */
interface PushInstance { id: string; user_uid: string; enabled: boolean; devices: number; created_at: string; updated_at: string }
const rows = ref<PushInstance[]>([]), query = ref(''), page = ref(1), total = ref(0)
const loading = ref(false), error = ref(''), target = ref<PushInstance | null>(null), opened = ref(false), busy = ref(false)
const pages = computed(() => Math.max(1, Math.ceil(total.value / 30)))
const action = computed(() => target.value?.enabled ? '停用' : '启用')

/** 在服务器分页搜索来源 UID 或实例标识，错误时保留当前列表。 */
async function load(reset = false) {
  if (reset) page.value = 1
  loading.value = true; error.value = ''
  try {
    const result = await api<{items: PushInstance[]; total: number}>(`/push/instances?${new URLSearchParams({q: query.value, page: String(page.value), size: '30'})}`)
    rows.value = result.items; total.value = result.total
  } catch (reason) { error.value = (reason as Error).message }
  finally { loading.value = false }
}

/** 二次确认后变更实例权限，服务端执行鉴权、CSRF校验和审计。 */
async function change() {
  if (!target.value) return
  busy.value = true
  try {
    await write(`/push/instances/${target.value.id}`, 'PATCH', {enabled: !target.value.enabled, confirm: action.value})
    opened.value = false; notify(`推送实例已${action.value}`); await load()
  } catch (reason) { notify((reason as Error).message, true) }
  finally { busy.value = false }
}
onMounted(() => load())
</script>
<template>
  <section>
    <h1 class="text-3xl font-semibold tracking-tight">推送实例</h1>
    <p class="mt-3 text-sm text-muted-foreground">查看自动接入的 MoviePilot 容器，管理通知服务权限。</p>
    <form class="my-7 flex max-w-md gap-3" @submit.prevent="load(true)">
      <Input v-model="query" aria-label="搜索用户唯一 ID 或实例标识" placeholder="用户唯一 ID 或实例标识" maxlength="128" />
      <Button variant="outline" :disabled="loading"><Search class="mr-2 size-4" />查询</Button>
    </form>
    <p v-if="error" role="alert" class="mb-5 text-sm text-destructive">{{ error }}</p>
    <div class="hidden overflow-x-auto rounded-md border md:block">
      <Table><TableHeader><TableRow class="bg-muted/50"><TableHead>实例</TableHead><TableHead>状态</TableHead><TableHead>设备</TableHead><TableHead>接入时间</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader>
        <TableBody><TableRow v-for="row in rows" :key="row.id">
          <TableCell><p class="font-medium">{{ row.user_uid }}</p><p class="mt-1 font-mono text-xs text-muted-foreground">{{ row.id }}</p></TableCell>
          <TableCell><Badge :variant="row.enabled ? 'secondary' : 'outline'">{{ row.enabled ? '已启用' : '已停用' }}</Badge></TableCell>
          <TableCell>{{ row.devices }}</TableCell><TableCell class="whitespace-nowrap">{{ date(row.created_at) }}</TableCell>
          <TableCell class="text-right"><Button variant="outline" size="sm" @click="target = row; opened = true">{{ row.enabled ? '停用' : '启用' }}</Button></TableCell>
        </TableRow><TableRow v-if="!rows.length"><TableCell :colspan="5" class="h-32 text-center text-muted-foreground">{{ loading ? '正在加载…' : '暂无符合条件的推送实例' }}</TableCell></TableRow></TableBody>
      </Table>
    </div>
    <div class="space-y-3 md:hidden">
      <article v-for="row in rows" :key="row.id" class="rounded-md border p-4">
        <div class="flex items-center justify-between gap-3"><p class="font-medium">{{ row.user_uid }}</p><Badge :variant="row.enabled ? 'secondary' : 'outline'">{{ row.enabled ? '已启用' : '已停用' }}</Badge></div>
        <p class="mt-2 break-all font-mono text-xs leading-5 text-muted-foreground">{{ row.id }}</p>
        <dl class="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm"><dt class="text-muted-foreground">设备</dt><dd>{{ row.devices }}</dd><dt class="text-muted-foreground">接入时间</dt><dd>{{ date(row.created_at) }}</dd></dl>
        <Button variant="outline" size="sm" class="mt-4 w-full" @click="target = row; opened = true">{{ row.enabled ? '停用' : '启用' }}</Button>
      </article>
      <p v-if="!rows.length" class="rounded-md border px-4 py-10 text-center text-sm text-muted-foreground">{{ loading ? '正在加载…' : '暂无符合条件的推送实例' }}</p>
    </div>
    <div class="mt-5 flex items-center justify-between gap-3 text-sm"><span class="text-muted-foreground">共 {{ total }} 个实例</span><div class="flex items-center gap-3"><Button variant="outline" size="sm" :disabled="page <= 1 || loading" @click="page--; load()">上一页</Button><span>{{ page }} / {{ pages }}</span><Button variant="outline" size="sm" :disabled="page >= pages || loading" @click="page++; load()">下一页</Button></div></div>
    <p class="mt-7 text-xs leading-6 text-muted-foreground">列表不会显示密钥或设备推送地址。停用后拒绝该实例的新请求及网页测试通知；已提交给系统推送服务的通知无法撤回。</p>
    <ConfirmAction v-model:open="opened" :title="`${action}推送实例`" :description="target?.enabled ? '该容器将无法发送通知，用户不能通过重新登记旧凭据恢复。设备绑定会保留。' : '恢复该实例已有凭据的权限和保留的设备绑定。'" :word="action" :busy="busy" @confirm="change" />
  </section>
</template>
