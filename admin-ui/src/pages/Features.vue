<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, notify, write } from '@/api'
import { useAuth } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
const auth = useAuth(), loading = ref(true), busy = ref(false), error = ref(''), enabled = ref(false), preview = ref(''), admins = ref(''), initial = ref('')
const editable = computed(() => auth.user?.role === 'super_admin')
/** 将逗号或换行分隔名单转换为去重的 GitHub 用户名列表。 */
function names(value: string) { return [...new Set(value.split(/[,\n，]/).map(item => item.trim().toLowerCase()).filter(Boolean))].sort() }
const payload = computed(() => ({community_medals_enabled: enabled.value, community_medals_preview_users: names(preview.value), github_admin_users: names(admins.value)}))
const changed = computed(() => JSON.stringify(payload.value) !== initial.value)
/** 读取跨 worker 生效的有效配置。 */
async function load() {
  loading.value = true; error.value = ''
  try { const result = await api('/features'); enabled.value = result.community_medals_enabled; preview.value = result.community_medals_preview_users.join('\n'); admins.value = result.github_admin_users.join('\n'); initial.value = JSON.stringify(payload.value) }
  catch (reason) { error.value = (reason as Error).message }
  finally { loading.value = false }
}
/** 整组保存配置和审计，失败时保留尚未保存的输入。 */
async function save() { busy.value = true; try { await write('/features', 'PUT', payload.value); initial.value = JSON.stringify(payload.value); notify('配置已保存，全局功能开关将在公共缓存刷新后生效（通常 60 秒内）') } catch (reason) { notify((reason as Error).message, true) } finally { busy.value = false } }
onMounted(load)
</script>
<template>
  <section class="max-w-4xl"><h1 class="text-3xl font-semibold tracking-tight">权限与功能</h1><p class="mt-3 text-sm text-muted-foreground">管理功能开放范围与 MoviePilot 实例的 GitHub 权限名单</p>
    <p v-if="!editable" class="mt-6 rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">当前为只读查看；修改权限配置需要超级管理员。</p>
    <div v-if="error" class="mt-8 flex items-center justify-between" role="alert"><p class="text-destructive">{{ error }}</p><Button variant="outline" @click="load">重试</Button></div>
    <p v-else-if="loading" class="py-12 text-muted-foreground">正在加载配置…</p>
    <form v-else class="mt-10" @submit.prevent="save">
      <h2 class="mb-3 text-xl font-semibold">社区勋章</h2><p class="mb-6 text-sm text-muted-foreground">控制 MoviePilot 实例中的社区勋章入口。</p>
      <div class="flex items-center justify-between gap-6 border-y py-6"><div><Label for="medals-enabled">全局开放</Label><p class="mt-2 text-sm text-muted-foreground">关闭时，仅预览名单中的用户可见。</p></div><Switch id="medals-enabled" v-model="enabled" :disabled="!editable || busy" /></div>
      <div class="grid gap-4 border-b py-7 sm:grid-cols-[10rem_1fr]"><Label for="preview-users">预览名单</Label><div class="space-y-3"><Textarea id="preview-users" v-model="preview" :disabled="!editable || busy" rows="4" placeholder="每行一个 GitHub 用户名，也可用逗号分隔" /><p class="text-xs leading-6 text-muted-foreground">用户名不区分大小写。名单为空时无人预览；管理员也需要加入名单。</p><p class="text-sm">当前范围：{{ enabled ? '对所有实例开放' : `仅 ${names(preview).length} 位预览用户可见` }}</p></div></div>
      <div class="pt-8"><h2 class="mb-3 text-xl font-semibold">实例管理权限</h2><p class="mb-6 text-sm leading-6 text-muted-foreground">此名单控制 MoviePilot 客户端中的分享管理入口，与本后台的登录账号分别管理。</p><div class="grid gap-4 sm:grid-cols-[10rem_1fr]"><Label for="github-admins">GitHub 管理员名单</Label><Textarea id="github-admins" v-model="admins" rows="4" :disabled="!editable || busy" placeholder="每行一个 GitHub 用户名" /></div></div>
      <div v-if="editable" class="mt-8 flex flex-wrap items-center justify-end gap-3 border-t pt-6"><span v-if="changed" class="mr-auto text-xs text-muted-foreground">修改尚未保存</span><Button variant="outline" type="button" :disabled="busy || !changed" @click="load">放弃更改</Button><Button type="submit" :disabled="busy || !changed">{{ busy ? '保存中…' : '保存更改' }}</Button></div>
    </form>
  </section>
</template>
