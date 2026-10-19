<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Plus, Search, KeyRound, Pencil } from '@lucide/vue'
import { api, date, notify, write } from '@/api'
import type { User } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
const rows = ref<User[]>([]), query = ref(''), page = ref(1), total = ref(0), loading = ref(false), error = ref('')
const opened = ref(false), mode = ref<'create' | 'edit' | 'password'>('create'), target = ref<User | null>(null), busy = ref(false), formError = ref('')
const username = ref(''), displayName = ref(''), password = ref(''), role = ref('admin'), enabled = ref(true)
const pages = computed(() => Math.max(1, Math.ceil(total.value / 30)))
const titles = { create: '新增用户', edit: '编辑用户', password: '重置用户密码' }
/** 按用户名进行服务端分页搜索。 */
async function load(reset = false) {
  if (reset) page.value = 1
  loading.value = true; error.value = ''
  try { const result = await api(`/users?${new URLSearchParams({q: query.value, page: String(page.value), size: '30'})}`); rows.value = result.items; total.value = result.total }
  catch (reason) { error.value = (reason as Error).message }
  finally { loading.value = false }
}
/** 打开表单时清空临时密码，不在列表或浏览器存储中保存凭证。 */
function open(action: typeof mode.value, user: User | null = null) {
  mode.value = action; target.value = user; username.value = user?.username || ''; displayName.value = user?.display_name || ''
  role.value = user?.role || 'admin'; enabled.value = user?.enabled ?? true; password.value = ''; formError.value = ''; opened.value = true
}
/** 用户变更由独立 API 校验角色、最后一个超级管理员和会话失效。 */
async function save() {
  busy.value = true; formError.value = ''
  try {
    if (mode.value === 'create') await write('/users', 'POST', {username: username.value, display_name: displayName.value, password: password.value, role: role.value})
    else if (mode.value === 'edit') await write(`/users/${target.value?.id}`, 'PATCH', {display_name: displayName.value, role: role.value, enabled: enabled.value})
    else await write(`/users/${target.value?.id}/password`, 'POST', {password: password.value})
    opened.value = false; password.value = ''; notify(mode.value === 'password' ? '密码已重置，用户需重新登录并修改临时密码' : '用户信息已保存'); await load()
  } catch (reason) { formError.value = (reason as Error).message }
  finally { busy.value = false }
}
onMounted(() => load())
</script>
<template>
  <section><div class="mb-8 flex flex-wrap items-start justify-between gap-4"><div><h1 class="text-3xl font-semibold tracking-tight">用户管理</h1><p class="mt-3 text-sm text-muted-foreground">创建后台账号、分配角色与管理登录权限</p></div><Button @click="open('create')"><Plus class="mr-2 size-4" />新增用户</Button></div>
    <form class="mb-6 flex max-w-md gap-3" @submit.prevent="load(true)"><Input v-model="query" aria-label="搜索用户名" placeholder="搜索用户名" maxlength="100" /><Button variant="outline" :disabled="loading"><Search class="mr-2 size-4" />查询</Button></form>
    <p v-if="error" role="alert" class="mb-5 text-sm text-destructive">{{ error }}</p>
    <div class="overflow-hidden rounded-md border"><Table><TableHeader><TableRow class="bg-muted/50"><TableHead>用户</TableHead><TableHead>角色</TableHead><TableHead>状态</TableHead><TableHead>最近登录</TableHead><TableHead>创建时间</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell><p class="font-medium">{{ row.display_name }}</p><p class="mt-1 text-xs text-muted-foreground">{{ row.username }}</p></TableCell><TableCell>{{ row.role === 'super_admin' ? '超级管理员' : '管理员' }}</TableCell><TableCell><Badge :variant="row.enabled ? 'secondary' : 'outline'">{{ row.enabled ? '已启用' : '已禁用' }}</Badge><p v-if="row.must_change_password" class="mt-1 text-xs text-muted-foreground">需修改临时密码</p></TableCell><TableCell class="whitespace-nowrap">{{ date(row.last_login_at) }}</TableCell><TableCell class="whitespace-nowrap">{{ date(row.created_at) }}</TableCell><TableCell class="text-right"><div class="flex justify-end gap-1"><Button variant="ghost" size="sm" @click="open('edit', row)"><Pencil class="mr-1 size-4" />编辑</Button><Button variant="ghost" size="sm" @click="open('password', row)"><KeyRound class="mr-1 size-4" />重置密码</Button></div></TableCell></TableRow><TableRow v-if="!rows.length"><TableCell :colspan="6" class="h-32 text-center text-muted-foreground">{{ loading ? '正在加载…' : '暂无符合条件的用户' }}</TableCell></TableRow></TableBody></Table></div>
    <div class="mt-5 flex items-center justify-between gap-3 text-sm"><span class="text-muted-foreground">共 {{ total }} 位用户</span><div class="flex items-center gap-3"><Button variant="outline" size="sm" :disabled="page <= 1 || loading" @click="page--; load()">上一页</Button><span>{{ page }} / {{ pages }}</span><Button variant="outline" size="sm" :disabled="page >= pages || loading" @click="page++; load()">下一页</Button></div></div>
    <p class="mt-7 text-xs leading-6 text-muted-foreground">账号不开放注册。新增和重置密码后，用户首次登录必须修改临时密码；禁用账号或调整角色会立即使旧会话失效。</p>
    <Dialog :open="opened" @update:open="value => { if (!busy) { opened = value; password = '' } }"><DialogContent class="sm:max-w-md"><DialogHeader><DialogTitle>{{ titles[mode] }}</DialogTitle><DialogDescription>{{ mode === 'password' ? `为 ${target?.username} 设置临时密码，所有旧会话将失效。` : '超级管理员可以管理账号和权限；管理员可以管理业务数据。' }}</DialogDescription></DialogHeader>
      <form id="user-form" class="space-y-5" @submit.prevent="save">
        <div v-if="mode === 'create'" class="space-y-2"><Label for="user-name">用户名</Label><Input id="user-name" v-model="username" pattern="[a-zA-Z0-9][a-zA-Z0-9_.-]{2,63}" autocomplete="off" required /><p class="text-xs text-muted-foreground">3–64 位，使用字母、数字、点、下划线或短横线。</p></div>
        <template v-if="mode !== 'password'"><div class="space-y-2"><Label for="display-name">显示名称</Label><Input id="display-name" v-model="displayName" maxlength="80" required /></div><div class="space-y-2"><Label for="user-role">角色</Label><Select v-model="role"><SelectTrigger id="user-role" class="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="admin">管理员</SelectItem><SelectItem value="super_admin">超级管理员</SelectItem></SelectContent></Select></div></template>
        <div v-if="mode !== 'edit'" class="space-y-2"><Label for="temporary-password">临时密码</Label><Input id="temporary-password" v-model="password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required /><p class="text-xs text-muted-foreground">12–128 位，请通过安全方式交给用户。</p></div>
        <div v-if="mode === 'edit'" class="flex items-center justify-between rounded-md border p-4"><Label for="user-enabled">启用账号</Label><Switch id="user-enabled" v-model="enabled" /></div>
        <p v-if="formError" role="alert" class="text-sm text-destructive">{{ formError }}</p>
      </form><DialogFooter><Button variant="outline" :disabled="busy" @click="opened = false; password = ''">取消</Button><Button form="user-form" type="submit" :disabled="busy">{{ busy ? '保存中…' : '保存' }}</Button></DialogFooter>
    </DialogContent></Dialog>
  </section>
</template>
