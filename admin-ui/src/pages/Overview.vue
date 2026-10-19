<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ChevronRight, RefreshCw } from '@lucide/vue'
import { api, date, number } from '@/api'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'
import Versions from '@/components/Versions.vue'
const data = ref<any>(null), error = ref(''), loading = ref(true)
const metrics = computed(() => [
  { label: '累计安装实例', value: data.value?.usage.total_users }, { label: '今日活跃', value: data.value?.usage.active_users.today },
  { label: '近 7 天活跃', value: data.value?.usage.active_users.last_7_days }, { label: '近 30 天活跃', value: data.value?.usage.active_users.last_30_days },
])
/** 总览展示真实聚合数据，刷新失败时保留重试入口。 */
async function load() { loading.value = true; error.value = ''; try { data.value = await api('/overview') } catch (reason) { error.value = (reason as Error).message } finally { loading.value = false } }
onMounted(load)
</script>
<template>
  <div>
    <div class="flex flex-wrap items-start justify-between gap-4"><div><h1 class="text-3xl font-semibold tracking-tight sm:text-4xl">服务总览</h1><p class="mt-3 text-sm text-muted-foreground">查看安装与活跃情况</p></div><div class="flex items-center gap-3"><span v-if="data" class="hidden text-xs text-muted-foreground xl:inline">更新于 {{ date(data.usage.updated_at) }}</span><Button variant="ghost" size="icon" aria-label="刷新统计" :disabled="loading" @click="load"><RefreshCw class="size-4" :class="loading ? 'animate-spin' : ''" /></Button><Button as-child><RouterLink to="/usage">查看使用明细</RouterLink></Button></div></div>
    <div v-if="error" role="alert" class="my-8 flex items-center justify-between rounded-md border border-destructive/30 p-4 text-sm text-destructive">{{ error }}<Button variant="outline" @click="load">重试</Button></div>
    <div class="my-10 grid grid-cols-2 gap-y-7 sm:my-12 xl:grid-cols-4"><div v-for="(item, i) in metrics" :key="item.label" class="px-4 first:pl-0 sm:px-7" :class="i ? 'border-l' : ''"><p class="mb-3 text-sm text-muted-foreground">{{ item.label }}</p><Skeleton v-if="loading && !data" class="h-11 w-28" /><p v-else class="text-3xl font-semibold tracking-tight tabular-nums sm:text-[2.6rem]">{{ number(item.value) }}</p></div></div>
    <div class="border-t py-8"><Versions v-if="data" :backend="data.usage.backend_versions" :frontend="data.usage.frontend_versions" /><Skeleton v-else class="h-52 w-full" /></div>
    <section class="border-t pt-8"><div class="mb-6 flex flex-wrap items-center justify-between gap-3"><h2 class="text-xl font-semibold tracking-tight">最近共享</h2><div class="flex gap-2"><Button as-child variant="ghost" size="sm"><RouterLink to="/shares?tab=subscribe-shares">订阅分享</RouterLink></Button><Button as-child variant="ghost" size="sm"><RouterLink to="/shares?tab=workflow-shares">工作流分享</RouterLink></Button></div></div>
      <Table><TableHeader><TableRow class="bg-muted/50"><TableHead>名称</TableHead><TableHead>类型</TableHead><TableHead>作者</TableHead><TableHead>创建时间</TableHead><TableHead class="text-right">复用次数</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="item in data?.recent_shares || []" :key="`${item.resource}/${item.id}`"><TableCell class="max-w-64 truncate font-medium">{{ item.share_title }}</TableCell><TableCell><Badge variant="secondary">{{ item.resource === 'subscribe-shares' ? '订阅' : '工作流' }}</Badge></TableCell><TableCell>{{ item.share_user || '—' }}</TableCell><TableCell class="whitespace-nowrap text-muted-foreground">{{ date(item.date) }}</TableCell><TableCell class="text-right tabular-nums">{{ number(item.count) }}</TableCell><TableCell class="text-right"><Button as-child variant="ghost" size="sm"><RouterLink :to="{path: '/shares', query: {tab: item.resource, detail: item.id}}">查看详情<ChevronRight class="ml-1 size-4" /></RouterLink></Button></TableCell></TableRow><TableRow v-if="!data?.recent_shares?.length"><TableCell :colspan="6" class="h-28 text-center text-muted-foreground">{{ loading ? '正在加载…' : '暂无共享内容' }}</TableCell></TableRow></TableBody></Table>
    </section>
    <p v-if="data" class="mt-6 text-xs leading-5 text-muted-foreground">活跃按最近上报时间统计；聚合报表最长缓存 30 分钟。时间以服务端统计口径为准。</p>
  </div>
</template>
