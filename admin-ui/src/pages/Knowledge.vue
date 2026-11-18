<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import { message, type Kind } from '@/knowledge'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import Catalog from '@/components/knowledge/Catalog.vue'
import Documents from '@/components/knowledge/Documents.vue'
import Jobs from '@/components/knowledge/Jobs.vue'
const route = useRoute(), router = useRouter()
const tabs = { sources: '来源', sites: '站点', collections: '集合', documents: '知识', jobs: '任务' }
type View = keyof typeof tabs
const view = computed<View>(() => Object.hasOwn(tabs, String(route.query.view)) ? route.query.view as View : 'sources')
const status = ref<{ enabled: boolean; index: { generation: number; active_generation: number; has_snapshot: boolean } } | null>(null)
const error = ref(''), loading = ref(false)
let sequence = 0
/** 状态只读且可手动刷新，服务关闭时维护入口仍可用。 */
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''
  try { const result = await api<NonNullable<typeof status.value>>('/knowledge/status'); if (current === sequence) status.value = result }
  catch (reason) { if (current === sequence) error.value = message(reason) }
  finally { if (current === sequence) loading.value = false }
}
/** 深链只包含中心签发的记录 ID，不把查询词、正文或认证值写入 URL。 */
function go(next: View, key?: string, id?: string) { void router.push({ path: '/knowledge', query: { view: next, ...(key && id ? { [key]: id } : {}) } }) }
/** 深链参数只接受中心记录 ID，不能被拼成其他后台路径。 */
function record(key: string) { const value = route.query[key]; return typeof value === 'string' && /^[a-f0-9]{32}$/.test(value) ? value : undefined }
onMounted(load); onBeforeUnmount(() => { sequence++ })
</script>
<template><section><div class="mb-7 flex flex-wrap items-start justify-between gap-4"><div><h1 class="text-3xl font-semibold tracking-tight">知识中心</h1><p class="mt-3 text-sm text-muted-foreground">维护 MP / PT 私有知识、来源与采集任务</p></div><Button variant="outline" :disabled="loading" @click="load">刷新状态</Button></div><p v-if="error" role="alert" class="mb-4 text-sm text-destructive">{{ error }}</p><div v-if="status" class="mb-6 flex flex-wrap items-center gap-3 text-sm"><Badge :variant="status.enabled ? 'secondary' : 'outline'">{{ status.enabled ? '在线知识服务已启用' : '在线知识服务未启用' }}</Badge><span class="text-muted-foreground">{{ !status.enabled ? '可维护草稿；worker 和对外检索暂停' : !status.index.has_snapshot ? '索引尚未就绪' : status.index.generation !== status.index.active_generation ? '索引更新待完成' : '索引已就绪' }}</span></div><nav aria-label="知识中心分区" class="mb-6 flex gap-2 overflow-x-auto border-b pb-3"><Button v-for="(label, key) in tabs" :key="key" :variant="view === key ? 'secondary' : 'ghost'" class="shrink-0" :aria-current="view === key ? 'page' : undefined" @click="go(key)">{{ label }}</Button></nav><Catalog v-if="['sources', 'sites', 'collections'].includes(view)" :key="view" :kind="view as Kind" @job="id => go('jobs', 'job', id)" @documents="id => go('documents', 'source', id)" /><Documents v-else-if="view === 'documents'" :source-id="record('source')" :document-id="record('document')" @changed="load" @job="id => go('jobs', 'job', id)" /><Jobs v-else :job-id="record('job')" :enabled="status?.enabled" @changed="load" @document="id => go('documents', 'document', id)" /></section></template>
