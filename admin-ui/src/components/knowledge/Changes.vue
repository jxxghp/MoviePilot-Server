<script setup lang="ts">
import { ref, watch } from 'vue'
import { labels, type Difference, type Form, merged } from '@/knowledge'
import { Button } from '@/components/ui/button'
const props = defineProps<{ changes: Difference[]; draft: Form }>()
const emit = defineEmits<{ apply: [form: Form] }>()
const rows = ref<Difference[]>([])
watch(() => props.changes, value => { rows.value = value.map(row => ({ ...row })) }, { immediate: true })
/** 布尔与空值采用可读文本；长正文安全地保留原文而不渲染 HTML。 */
function display(value: unknown) { return typeof value === 'boolean' ? value ? '启用' : '停用' : value === null || value === '' ? '（空）' : String(value) }
</script>
<template><div role="alert" class="space-y-4 rounded-md border p-4"><p class="text-sm font-medium">记录已更新，请对照最新改动</p><p class="text-xs text-muted-foreground">当前输入已保留。完成对照后，再次保存才会提交。</p><div v-for="row in rows" :key="row.key" class="space-y-2 border-t pt-3"><p class="text-sm font-medium">{{ labels[row.key] || row.key }}</p><div class="grid gap-2 sm:grid-cols-2"><label v-for="option in (['latest', 'mine'] as const)" :key="option" class="min-w-0 rounded-md border p-3 text-sm"><span class="flex gap-2"><input v-model="row.choice" type="radio" :name="`change-${row.key}`" :value="option" />{{ option === 'latest' ? '最新版本' : '我的输入' }}</span><pre class="mt-2 max-h-36 overflow-auto whitespace-pre-wrap break-all text-xs">{{ display(row[option]) }}</pre></label></div></div><Button type="button" variant="outline" :disabled="rows.some(row => !row.choice)" @click="emit('apply', merged(draft, rows))">采用对照结果</Button></div></template>
