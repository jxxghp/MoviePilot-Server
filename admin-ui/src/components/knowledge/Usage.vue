<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RefreshCw } from '@lucide/vue'
import { api, date, number } from '@/api'
import { message, type KnowledgeUsageSummary } from '@/knowledge'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from '@/components/ui/table'

const emit = defineEmits<{ document: [id: string] }>()
const days = ref(14)
const data = ref<KnowledgeUsageSummary | null>(null)
const loading = ref(false)
const error = ref('')
let sequence = 0
let controller: AbortController | undefined

const hitRate = computed(() => {
  const total = data.value?.totals.search_calls || 0
  if (!total) return '—'
  const matched = data.value?.totals.search_matched_calls || 0
  return `${Math.round((matched / total) * 1000) / 10}%`
})

const metrics = computed(() => [
  {
    label: '累计调用次数',
    value: number(data.value?.totals.total_calls),
    detail: `检索 ${number(data.value?.totals.search_calls)} · 补读 ${number(data.value?.totals.read_calls)}`,
  },
  {
    label: '今日调用次数',
    value: number(data.value?.windows.today_calls),
    detail: `近 7 天 ${number(data.value?.windows.last_7_days_calls)} · 近 30 天 ${number(data.value?.windows.last_30_days_calls)}`,
  },
  {
    label: '检索命中率',
    value: hitRate.value,
    detail: `命中 ${number(data.value?.totals.search_matched_calls)} / 检索 ${number(data.value?.totals.search_calls)} 次`,
  },
  {
    label: '累计知识命中',
    value: number(data.value?.totals.total_hits),
    detail: `已命中 ${number(data.value?.totals.hit_documents)} / ${number(data.value?.totals.published_documents)} 篇已发布知识`,
  },
])

const channelRows = computed(() => {
  if (!data.value) return []
  return [
    { key: 'agent', label: '公共 Agent 接口', item: data.value.channels.agent },
    { key: 'client', label: '独立授权客户端', item: data.value.channels.client },
  ]
})

/** 读取真实累计调用与知识命中汇总，失败时保留错误提示，不伪装为零值。 */
async function load() {
  const current = ++sequence
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ days: String(days.value), limit: '20' })
    const result = await api<KnowledgeUsageSummary>(`/knowledge/usage?${params}`, { signal: controller.signal })
    if (current === sequence) data.value = result
  } catch (reason) {
    if (current === sequence) error.value = message(reason)
  } finally {
    if (current === sequence) loading.value = false
  }
}

watch(days, () => { void load() })
onMounted(() => { void load() })
onBeforeUnmount(() => { sequence++; controller?.abort() })
</script>
<template>
  <div class="space-y-8">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <p class="text-sm text-muted-foreground">统计 Agent 与授权客户端对知识服务的真实调用次数及各篇知识命中情况，不记录原始查询词。</p>
      <div class="flex flex-wrap items-center gap-2">
        <div class="flex rounded-md border p-0.5" role="group" aria-label="统计天数范围">
          <Button v-for="option in [7, 14, 30]" :key="option" size="sm" :variant="days === option ? 'secondary' : 'ghost'" @click="days = option">近 {{ option }} 天</Button>
        </div>
        <Button variant="outline" size="sm" :disabled="loading" @click="load">
          <RefreshCw class="mr-1 size-4" :class="loading ? 'animate-spin' : ''" />刷新
        </Button>
      </div>
    </div>

    <p v-if="error" role="alert" class="rounded-md border border-destructive/30 p-4 text-sm text-destructive">{{ error }}</p>

    <div class="grid grid-cols-2 gap-y-6 xl:grid-cols-4">
      <div v-for="(item, index) in metrics" :key="item.label" class="px-4 first:pl-0 sm:px-6" :class="index ? 'border-l' : ''">
        <p class="mb-2 text-sm text-muted-foreground">{{ item.label }}</p>
        <Skeleton v-if="loading && !data" class="h-10 w-24" />
        <template v-else>
          <p class="text-3xl font-semibold tracking-tight tabular-nums">{{ item.value }}</p>
          <p class="mt-2 text-xs text-muted-foreground">{{ item.detail }}</p>
        </template>
      </div>
    </div>

    <section class="space-y-3 border-t pt-6">
      <h2 class="text-lg font-semibold tracking-tight">调用渠道分布</h2>
      <div class="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            <TableRow class="bg-muted/50">
              <TableHead>渠道</TableHead>
              <TableHead class="text-right">总调用</TableHead>
              <TableHead class="text-right">检索调用</TableHead>
              <TableHead class="text-right">检索命中</TableHead>
              <TableHead class="text-right">补读调用</TableHead>
              <TableHead class="text-right">知识命中人次</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow v-for="row in channelRows" :key="row.key">
              <TableCell class="font-medium">{{ row.label }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ number(row.item.total_calls) }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ number(row.item.search_calls) }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ number(row.item.search_matched_calls) }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ number(row.item.read_calls) }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ number(row.item.search_hits + row.item.read_hits) }}</TableCell>
            </TableRow>
            <TableRow v-if="!channelRows.length">
              <TableCell :colspan="6" class="h-24 text-center text-muted-foreground">{{ loading ? '正在加载…' : error ? '读取失败，请重试' : '暂无调用数据' }}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </section>

    <section class="space-y-3 border-t pt-6">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-lg font-semibold tracking-tight">热门命中知识（Top 20）</h2>
        <span class="text-xs text-muted-foreground">按累计命中次数排序，点击可查看对应知识</span>
      </div>
      <div class="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            <TableRow class="bg-muted/50">
              <TableHead class="w-14">#</TableHead>
              <TableHead>知识标题</TableHead>
              <TableHead>集合 / 来源</TableHead>
              <TableHead class="text-right">累计命中</TableHead>
              <TableHead class="text-right">检索 / 补读</TableHead>
              <TableHead>最近命中</TableHead>
              <TableHead class="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow v-for="(row, index) in data?.top_documents || []" :key="row.document_id">
              <TableCell class="tabular-nums text-muted-foreground">{{ index + 1 }}</TableCell>
              <TableCell class="min-w-56 max-w-80 whitespace-normal">
                <p class="font-medium leading-snug break-words [overflow-wrap:anywhere] line-clamp-2" :title="row.title">{{ row.title }}</p>
                <p class="mt-1 text-xs text-muted-foreground break-all line-clamp-1" :title="row.external_id">{{ row.external_id }}</p>
              </TableCell>
              <TableCell class="min-w-40 max-w-60 whitespace-normal">
                <div class="flex items-center gap-1.5">
                  <Badge variant="outline">{{ row.domain.toUpperCase() }}</Badge>
                  <span class="truncate text-sm" :title="row.collection_name">{{ row.collection_name }}</span>
                </div>
                <p class="mt-1 truncate text-xs text-muted-foreground" :title="row.source_name">{{ row.source_name }}</p>
              </TableCell>
              <TableCell class="text-right font-semibold tabular-nums">{{ number(row.total_hits) }}</TableCell>
              <TableCell class="text-right text-xs text-muted-foreground tabular-nums">{{ number(row.search_hits) }} / {{ number(row.read_hits) }}</TableCell>
              <TableCell class="whitespace-nowrap text-muted-foreground">{{ date(row.last_hit_at) }}</TableCell>
              <TableCell class="text-right">
                <Button size="sm" variant="ghost" @click="emit('document', row.document_id)">查看与审核</Button>
              </TableCell>
            </TableRow>
            <TableRow v-if="!data?.top_documents?.length">
              <TableCell :colspan="7" class="h-28 text-center text-muted-foreground">{{ loading ? '正在加载…' : error ? '读取失败，请重试' : '暂无知识命中记录' }}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </section>

    <section class="space-y-3 border-t pt-6">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-lg font-semibold tracking-tight">近期按日调用（近 {{ days }} 天）</h2>
        <span class="text-xs text-muted-foreground">按 UTC 日期统计，仅展示有调用记录的日期</span>
      </div>
      <div class="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            <TableRow class="bg-muted/50">
              <TableHead>日期（UTC）</TableHead>
              <TableHead class="text-right">总调用</TableHead>
              <TableHead class="text-right">检索调用</TableHead>
              <TableHead class="text-right">检索命中</TableHead>
              <TableHead class="text-right">补读调用</TableHead>
              <TableHead class="text-right">知识命中人次</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow v-for="row in data?.daily || []" :key="row.day">
              <TableCell class="font-mono text-xs">{{ row.day }}</TableCell>
              <TableCell class="text-right font-medium tabular-nums">{{ number(row.total_calls) }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ number(row.search_calls) }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ number(row.search_matched_calls) }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ number(row.read_calls) }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ number(row.search_hits + row.read_hits) }}</TableCell>
            </TableRow>
            <TableRow v-if="!data?.daily?.length">
              <TableCell :colspan="6" class="h-24 text-center text-muted-foreground">{{ loading ? '正在加载…' : error ? '读取失败，请重试' : '所选时间段内暂无调用记录' }}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </section>
  </div>
</template>
