<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import Records from '@/components/Records.vue'
import Versions from '@/components/Versions.vue'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, notify } from '@/api'
const route = useRoute()
const kind = String(route.meta.kind)
const tab = ref(kind === 'shares' && route.query.tab === 'workflow-shares' ? 'workflow-shares' : kind === 'shares' ? 'subscribe-shares' : kind === 'plugins' ? 'plugins' : kind === 'usage' ? 'installations' : kind)
const usage = ref<any>(null)
const resource = computed(() => tab.value)
const subtitles: Record<string, string> = {
  usage: '查看版本分布与安装实例的最近上报情况', subscriptions: '按媒体、来源和类型查看订阅热度',
  shares: '查看订阅与工作流分享，处理不合适的共享内容', plugins: '查看插件安装统计、评分汇总与评分明细',
  audit: '追溯登录、账号变更、配置修改与数据管理操作',
}
if (kind === 'usage') void api('/overview').then(result => { usage.value = result.usage }).catch(error => notify(error.message, true))
</script>
<template>
  <section><div class="mb-8"><h1 class="text-3xl font-semibold tracking-tight">{{ route.meta.title }}</h1><p class="mt-3 text-sm text-muted-foreground">{{ subtitles[kind] }}</p></div>
    <div v-if="kind === 'usage' && usage" class="mb-8 border-b pb-8"><Versions :backend="usage.backend_versions" :frontend="usage.frontend_versions" /></div>
    <Tabs v-if="kind === 'shares' || kind === 'plugins'" v-model="tab" class="mb-6"><TabsList><template v-if="kind === 'shares'"><TabsTrigger value="subscribe-shares">订阅分享</TabsTrigger><TabsTrigger value="workflow-shares">工作流分享</TabsTrigger></template><template v-else><TabsTrigger value="plugins">安装与评分汇总</TabsTrigger><TabsTrigger value="ratings">评分明细</TabsTrigger></template></TabsList></Tabs>
    <Records :key="resource" :resource="resource" />
  </section>
</template>
