<script setup lang="ts">
import { computed, ref } from 'vue'
import { number } from '@/api'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
interface Version { version: string; count: number }
const props = defineProps<{ backend: Version[]; frontend: Version[]; ios?: Version[] }>()
const selected = ref('backend')
const items = computed(() => selected.value === 'backend' ? props.backend : selected.value === 'ios' ? (props.ios ?? []) : props.frontend)
const total = computed(() => items.value.reduce((sum, row) => sum + row.count, 0))
</script>
<template>
  <section>
    <h2 class="mb-6 text-xl font-semibold tracking-tight">版本分布</h2>
    <Tabs v-model="selected"><TabsList><TabsTrigger value="backend">后端版本</TabsTrigger><TabsTrigger value="frontend">前端版本</TabsTrigger><TabsTrigger value="ios">iOS App 版本</TabsTrigger></TabsList></Tabs>
    <div v-if="items.length" class="mt-7 max-h-96 space-y-6 overflow-y-auto pr-1">
      <div v-for="item in items" :key="item.version" class="grid grid-cols-[5rem_1fr_4.5rem] items-center gap-4 text-sm sm:grid-cols-[6rem_1fr_6rem]">
        <span class="truncate" :title="item.version">{{ item.version }}</span><div class="h-2.5 overflow-hidden rounded-full bg-muted" role="meter" :aria-label="item.version" :aria-valuenow="item.count" :aria-valuemin="0" :aria-valuemax="total"><div class="h-full rounded-full bg-primary" :style="{ width: `${total ? item.count / total * 100 : 0}%` }"></div></div><span class="text-right tabular-nums">{{ number(item.count) }}</span>
      </div>
    </div>
    <p v-else class="py-12 text-center text-sm text-muted-foreground">暂无版本上报数据</p>
  </section>
</template>
