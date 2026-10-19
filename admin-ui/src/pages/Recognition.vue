<script setup lang="ts">
import { ref } from 'vue'
import { Search, Eye, Trash2 } from '@lucide/vue'
import { api, notify, write } from '@/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import ConfirmAction from '@/components/ConfirmAction.vue'
const rows = ref<any[]>([]), query = ref(''), activeQuery = ref(''), cursor = ref(0), loaded = ref(false), loading = ref(false), error = ref(''), selected = ref<any>(null), opened = ref(false), confirm = ref(false), busy = ref(false)
/** 使用有界游标扫描，继续扫描时追加并去重当前页面结果。 */
async function scan(reset = false) {
  loading.value = true; error.value = ''
  if (reset) { cursor.value = 0; rows.value = []; activeQuery.value = query.value }
  try { const result = await api(`/recognition?${new URLSearchParams({q: activeQuery.value, cursor: String(cursor.value)})}`); rows.value = [...new Map([...rows.value, ...result.items].map(row => [row.key, row])).values()]; cursor.value = result.next_cursor; loaded.value = true }
  catch (reason) { error.value = (reason as Error).message }
  finally { loading.value = false }
}
/** 只删除选中的精确业务键，不提供全库清空入口。 */
async function remove() {
  busy.value = true
  try { const result = await write('/recognition', 'DELETE', {key: selected.value.key, confirm: '删除'}); rows.value = rows.value.filter(row => row.key !== selected.value.key); opened.value = false; confirm.value = false; notify(result.message) }
  catch (reason) { notify((reason as Error).message, true) }
  finally { busy.value = false }
}
void scan()
</script>
<template>
  <section><h1 class="text-3xl font-semibold tracking-tight">识别缓存</h1><p class="mt-3 mb-8 text-sm text-muted-foreground">查找共享媒体识别记录，并定向移除错误缓存</p>
    <form class="mb-6 flex max-w-xl gap-3" @submit.prevent="scan(true)"><Input v-model="query" aria-label="搜索识别关键词" placeholder="搜索关键词、类型或缓存身份" maxlength="200" /><Button :disabled="loading"><Search class="mr-2 size-4" />查询</Button></form>
    <p v-if="error" role="alert" class="mb-5 text-sm text-destructive">{{ error }}</p>
    <div class="rounded-md border"><Table><TableHeader><TableRow class="bg-muted/50"><TableHead>识别关键词</TableHead><TableHead>媒体名称</TableHead><TableHead>类型</TableHead><TableHead>年份</TableHead><TableHead>来源</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.key"><TableCell class="max-w-56 truncate">{{ row.data?.keyword || row.key.split(':item:')[1] }}</TableCell><TableCell>{{ row.data?.name || row.data?.title || '—' }}</TableCell><TableCell>{{ row.data?.type || '—' }}</TableCell><TableCell>{{ row.data?.year || '—' }}</TableCell><TableCell>{{ row.data?.media_source || '—' }}</TableCell><TableCell class="text-right"><Button variant="ghost" size="sm" @click="selected = row; opened = true"><Eye class="mr-1 size-4" />详情</Button></TableCell></TableRow><TableRow v-if="!rows.length"><TableCell :colspan="6" class="h-36 text-center text-muted-foreground">{{ loading ? '正在扫描…' : '本次扫描未找到记录' }}</TableCell></TableRow></TableBody></Table></div>
    <div class="mt-5 flex flex-wrap items-center justify-between gap-4"><p class="text-xs leading-6 text-muted-foreground">已展示 {{ rows.length }} 条。缓存采用分批扫描，扫描中没有结果时仍可继续。</p><Button v-if="cursor" variant="outline" :disabled="loading" @click="scan()">{{ loading ? '扫描中…' : '继续扫描' }}</Button><Button v-else-if="loaded" variant="outline" :disabled="loading" @click="scan(true)">重新扫描</Button></div>
    <Sheet v-model:open="opened"><SheetContent class="w-full overflow-y-auto sm:max-w-xl"><SheetHeader><SheetTitle>识别记录详情</SheetTitle><SheetDescription>删除仅影响当前共享识别记录。</SheetDescription></SheetHeader><div class="space-y-5 p-6"><p class="break-all text-xs text-muted-foreground">{{ selected?.key }}</p><pre class="whitespace-pre-wrap break-all rounded-md bg-muted p-4 text-xs leading-6">{{ JSON.stringify(selected?.data, null, 2) }}</pre><p class="text-xs leading-6 text-muted-foreground">删除后，公共查询的代理缓存可能仍保留最多 10 分钟。</p><Button variant="destructive" @click="confirm = true"><Trash2 class="mr-2 size-4" />删除缓存</Button></div></SheetContent></Sheet>
    <ConfirmAction v-model:open="confirm" title="删除这条识别缓存？" description="请先核对媒体身份。此操作不可撤销。" :busy="busy" @confirm="remove" />
  </section>
</template>
