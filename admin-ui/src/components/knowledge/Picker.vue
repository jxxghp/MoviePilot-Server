<script setup lang="ts">
import { onBeforeUnmount, ref, useId, watch } from 'vue'
import { api } from '@/api'
import { connectors, kinds, message, usePage, type Entity, type Kind, type Source } from '@/knowledge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import Pager from './Pager.vue'
const props = defineProps<{ kind: Kind; modelValue: string | null; disabled?: boolean; required?: boolean; connector?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()
const opened = ref(false), name = ref(''), selectedError = ref(''), id = useId()
const { rows, query, page, pages, total, loading, error, load } = usePage<Entity & Partial<Source>>(
  () => `/knowledge/${props.kind}`, () => props.connector ? { connector: props.connector } : {} as Record<string, string>, 10,
)
let sequence = 0
/** 选中项独立按 ID 回读，选项不受当前搜索或首屏页数限制。 */
async function selected() {
  const current = ++sequence; name.value = ''; selectedError.value = ''
  if (!props.modelValue) return
  try { const item = await api<Entity>(`/knowledge/${props.kind}/${props.modelValue}`); if (current === sequence) name.value = item.name }
  catch (reason) { if (current === sequence) selectedError.value = message(reason) }
}
watch(() => [props.kind, props.modelValue], selected, { immediate: true })
watch(opened, value => { if (value) { query.value = ''; void load(true) } })
watch(() => props.connector, () => load(true))
onBeforeUnmount(() => { sequence++ })
</script>
<template><div class="space-y-2"><div class="flex gap-2"><Button type="button" variant="outline" class="min-w-0 flex-1 justify-start" :disabled="disabled" :aria-label="`选择${kinds[kind]}`" @click="opened = true"><span class="truncate">{{ name || (modelValue ? selectedError ? '记录不可用' : '读取中…' : `选择${kinds[kind]}`) }}</span></Button><Button v-if="modelValue && !required" type="button" variant="ghost" :disabled="disabled" @click="emit('update:modelValue', null)">清除</Button></div><p v-if="selectedError" role="alert" class="text-xs text-destructive">{{ selectedError }}</p><Dialog v-model:open="opened"><DialogContent class="max-h-[90svh] overflow-y-auto sm:max-w-lg"><DialogHeader><DialogTitle>选择{{ kinds[kind] }}</DialogTitle><DialogDescription>按名称搜索，使用分页查看全部记录。</DialogDescription></DialogHeader><form class="flex gap-2" @submit.prevent="load(true)"><Input :id="id" v-model="query" :aria-label="`搜索${kinds[kind]}`" placeholder="搜索名称" maxlength="120" /><Button variant="outline" :disabled="loading">查询</Button></form><p v-if="error" role="alert" class="text-sm text-destructive">{{ error }}</p><div class="divide-y rounded-md border"><Button v-for="row in rows" :key="row.id" type="button" variant="ghost" class="h-auto w-full justify-between gap-3 rounded-none px-3 py-3 text-left" :aria-pressed="row.id === modelValue" @click="emit('update:modelValue', row.id); opened = false"><span class="min-w-0 truncate">{{ row.name }}</span><span class="shrink-0 text-xs text-muted-foreground">{{ row.connector ? connectors[row.connector] : row.enabled ? '' : '已停用' }}</span></Button><p v-if="!rows.length" class="p-6 text-center text-sm text-muted-foreground">{{ loading ? '正在加载…' : error ? '读取失败，请重试' : '暂无记录' }}</p></div><Pager v-if="!error" :page="page" :pages="pages" :total="total" :loading="loading" @change="value => { page = value; load() }" /></DialogContent></Dialog></div></template>
