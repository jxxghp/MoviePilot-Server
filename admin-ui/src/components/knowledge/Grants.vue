<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api'
import { message, type Collection } from '@/knowledge'
import { Button } from '@/components/ui/button'
import Picker from './Picker.vue'
const props = defineProps<{ modelValue: string[]; disabled?: boolean; reviewIds?: string[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]]; names: [value: Record<string, string>]; ready: [value: boolean] }>()
const pending = ref<string | null>(null), cache = ref<Record<string, Collection>>({}), loading = ref(false), error = ref('')
let sequence = 0
/** 仅当前编辑器按 ID 加载授权名，四个并发避免大量授权造成无界请求扇出。 */
async function hydrate(force = false) {
  const current = ++sequence; loading.value = true; error.value = ''; emit('ready', false)
  const ids = Array.from(new Set([...props.modelValue, ...(props.reviewIds || [])])).filter(id => force || !cache.value[id])
  try {
    for (let offset = 0; offset < ids.length; offset += 4) {
      const records = await Promise.all(ids.slice(offset, offset + 4).map(id => api<Collection>(`/knowledge/collections/${id}`)))
      if (current !== sequence) return
      for (const item of records) cache.value[item.id] = item
    }
    emit('names', Object.fromEntries(Object.values(cache.value).map(item => [item.id, item.name])))
  } catch (reason) { if (current === sequence) error.value = message(reason) }
  finally {
    if (current === sequence) { loading.value = false; emit('ready', props.modelValue.every(id => !!cache.value[id])) }
  }
}
/** 新增只接受当前启用集合；已有停用授权保留显示，由管理员决定是否移除。 */
async function add(id: string | null) {
  const current = sequence
  pending.value = null
  if (!id || props.disabled || props.modelValue.includes(id)) return
  if (props.modelValue.length >= 100) { error.value = '单个客户端最多授权 100 个集合'; return }
  try {
    const item = await api<Collection>(`/knowledge/collections/${id}`)
    if (current !== sequence) return
    if (!item.enabled) { error.value = '只能新增启用集合'; return }
    cache.value[id] = item; emit('update:modelValue', [...props.modelValue, id])
  } catch (reason) { error.value = message(reason) }
}
watch(() => [...props.modelValue, ...(props.reviewIds || [])].join(','), () => hydrate(), { immediate: true })
onBeforeUnmount(() => { sequence++; cache.value = {} })
</script>
<template><div class="space-y-3"><div class="space-y-2 rounded-md border p-3"><div v-for="id in modelValue" :key="id" class="flex min-w-0 items-center justify-between gap-2 text-sm"><span class="min-w-0 break-words">{{ cache[id]?.name || `读取中 / 记录不可用（${id.slice(0, 8)}）` }}<span v-if="cache[id] && !cache[id].enabled" class="ml-2 text-xs text-muted-foreground">已停用，授权暂不生效</span></span><Button type="button" variant="ghost" size="sm" :disabled="disabled" :aria-label="`移除授权 ${cache[id]?.name || id.slice(0, 8)}`" @click="emit('update:modelValue', modelValue.filter(value => value !== id))">移除</Button></div><p v-if="!modelValue.length" class="text-sm text-muted-foreground">未授权集合，没有读取权限</p></div><Picker :model-value="pending" kind="collections" enabled-only :disabled="disabled || modelValue.length >= 100" @update:model-value="add" /><div class="flex flex-wrap items-center justify-between gap-2"><p class="text-xs text-muted-foreground">{{ modelValue.length }} / 100 个集合</p><Button type="button" variant="link" size="sm" :disabled="loading || disabled" @click="hydrate(true)">刷新集合状态</Button></div><p class="text-xs text-muted-foreground">停用集合的保留授权会在重新启用后恢复；永久撤销请移除。</p><p v-if="error" role="alert" class="text-sm text-destructive">{{ error }}</p></div></template>
