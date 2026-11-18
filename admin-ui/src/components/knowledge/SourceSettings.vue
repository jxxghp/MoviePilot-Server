<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, notify, write } from '@/api'
import { message, text, type CollectionState, type Credential, type Job, type Policy, type Source } from '@/knowledge'
import { useAuth } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import ConfirmAction from '@/components/ConfirmAction.vue'
const props = defineProps<{ sourceId: string; mode: 'credential' | 'collection' }>()
const emit = defineEmits<{ close: []; changed: []; job: [id: string] }>()
const auth = useAuth(), superAdmin = computed(() => auth.user?.role === 'super_admin')
const source = ref<Source | null>(null), credential = ref<Credential | null>(null), state = ref<CollectionState | null>(null)
const policy = ref<Policy>({ version_scope: 'unknown' }), secret = ref(''), kind = ref<'cookie' | 'bearer'>('cookie')
const loading = ref(false), busy = ref(false), error = ref(''), clearOpen = ref(false), stale = ref(false)
let sequence = 0
const policyDirty = computed(() => JSON.stringify([policy.value.version_scope, policy.value.min_version || null, policy.value.max_version || null])
  !== JSON.stringify([state.value?.policy.version_scope, state.value?.policy.min_version || null, state.value?.policy.max_version || null]))
/** 同批读取来源和对应版本；版本不一致时不给出可提交表单。 */
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''; secret.value = ''; stale.value = false
  source.value = null; credential.value = null; state.value = null
  try {
    const [origin, detail] = await Promise.all([
      api<Source>(`/knowledge/sources/${props.sourceId}`),
      api<Credential | CollectionState>(`/knowledge/sources/${props.sourceId}/${props.mode === 'credential' ? 'credential' : 'collection'}`),
    ])
    if (current !== sequence) return
    if (origin.version !== detail.source_version) throw new Error('来源刚刚更新，请重新读取')
    source.value = origin
    if (props.mode === 'credential') {
      credential.value = detail as Credential; kind.value = origin.connector === 'github' ? 'bearer' : credential.value.kind || 'cookie'
    } else { state.value = detail as CollectionState; policy.value = { ...state.value.policy } }
  } catch (reason) { if (current === sequence) error.value = message(reason) }
  finally { if (current === sequence) loading.value = false }
}
/** 写入始终使用所见来源 / 凭据版本，秘密提交后立刻从输入框清空。 */
async function credentialAction(action: 'replace' | 'clear' | 'rewrap') {
  if (busy.value || !superAdmin.value || !credential.value || !source.value || stale.value) return
  const current = sequence
  const payload = { expected_source_version: source.value.version, expected_credential_version: credential.value.version,
    ...(action === 'replace' ? { kind: kind.value, secret: secret.value } : {}) }
  secret.value = ''; busy.value = true; error.value = ''
  try {
    const result = await write<Credential>(`/knowledge/sources/${props.sourceId}/credential${action === 'replace' ? '' : '/' + action}`, action === 'replace' ? 'PUT' : 'POST', payload)
    if (current !== sequence) return
    credential.value = result; source.value = { ...source.value, version: result.source_version }
    clearOpen.value = false; notify(action === 'clear' ? '凭据已清除' : action === 'rewrap' ? '已使用当前密钥重新加密' : '凭据已替换'); emit('changed')
  } catch (reason) {
    if (current === sequence) { error.value = message(reason); stale.value = true; clearOpen.value = false }
  } finally { busy.value = false }
}
/** 保存范围仅改采集策略，手动触发独立请求并显示真实任务身份。 */
async function collectionAction(action: 'save' | 'collect') {
  if (busy.value || !source.value || stale.value || action === 'collect' && policyDirty.value) return
  const current = sequence; busy.value = true; error.value = ''
  try {
    if (action === 'save') {
      const result = await write<CollectionState>(`/knowledge/sources/${props.sourceId}/collection`, 'PUT', {
        expected_version: source.value.version, version_scope: policy.value.version_scope,
        min_version: policy.value.version_scope === 'range' ? policy.value.min_version || null : null,
        max_version: policy.value.version_scope === 'range' ? policy.value.max_version || null : null,
      })
      if (current !== sequence) return
      state.value = result; source.value = { ...source.value, version: result.source_version }; policy.value = { ...result.policy }
      notify('采集范围已保存'); emit('changed')
    } else {
      const job = await write<Job>(`/knowledge/sources/${props.sourceId}/collect`, 'POST', { expected_version: source.value.version })
      if (current !== sequence) return
      notify('采集已排队，由独立 worker 执行'); emit('job', job.id)
    }
  } catch (reason) { if (current === sequence) { error.value = message(reason); stale.value = true } }
  finally { busy.value = false }
}
watch(() => auth.user?.role, () => { secret.value = ''; clearOpen.value = false; if (!auth.user) emit('close') })
onBeforeUnmount(() => { sequence++; secret.value = '' })
void load()
</script>
<template><Dialog :open="true" @update:open="value => { if (!value && !busy) emit('close') }"><DialogContent class="max-h-[90svh] overflow-y-auto sm:max-w-lg"><DialogHeader><DialogTitle>{{ mode === 'credential' ? '采集凭据' : '采集设置' }}</DialogTitle><DialogDescription>{{ source?.name || '读取来源配置' }}</DialogDescription></DialogHeader>
  <p v-if="loading" class="py-6 text-sm text-muted-foreground">正在读取…</p>
  <template v-if="source && mode === 'credential' && credential"><div class="rounded-md border p-3 text-sm"><p>{{ credential.configured ? credential.state === 'configured' ? '已配置（不代表上游认证仍有效）' : '已配置，但当前不可用' : '未配置' }}</p><p class="mt-2 break-all text-xs text-muted-foreground">目标：{{ source.connector === 'github' ? source.repository + ' @ ' + source.branch : source.url }}</p><details v-if="source.paths.length" class="mt-3"><summary class="cursor-pointer text-xs">额外批准路径（{{ source.paths.length }}）</summary><pre class="mt-2 max-h-28 overflow-auto text-xs whitespace-pre-wrap break-all">{{ source.paths.join('\n') }}</pre></details></div>
    <form v-if="superAdmin" id="knowledge-secret-form" class="space-y-4" @submit.prevent="credentialAction('replace')"><fieldset class="space-y-4" :disabled="busy || stale"><div class="space-y-2"><Label for="knowledge-secret-kind">认证类型</Label><select id="knowledge-secret-kind" v-model="kind" class="h-9 w-full rounded-md border bg-background px-3" :disabled="source.connector === 'github'"><option v-if="source.connector !== 'github'" value="cookie">Cookie</option><option value="bearer">Bearer</option></select></div><div class="space-y-2"><Label for="knowledge-secret">新的认证值</Label><Input id="knowledge-secret" v-model="secret" type="password" autocomplete="new-password" minlength="3" maxlength="8192" required /><p class="text-xs text-muted-foreground">只输入值，不带请求头名称；不回读旧值，提交后清空输入。</p></div></fieldset></form><p v-else class="text-sm text-muted-foreground">只有超级管理员可以替换、清除或重新加密凭据。</p>
    <div v-if="superAdmin && credential.configured" class="flex flex-wrap gap-2"><Button variant="outline" :disabled="busy || stale" @click="credentialAction('rewrap')">使用当前密钥重加密</Button><Button variant="outline" :disabled="busy || stale" @click="clearOpen = true">清除凭据</Button></div>
  </template>
  <form v-if="source && mode === 'collection' && state" id="knowledge-policy-form" class="space-y-4" @submit.prevent="collectionAction('save')"><fieldset class="space-y-4" :disabled="busy || stale"><div class="space-y-2"><Label for="knowledge-policy">适用版本</Label><select id="knowledge-policy" v-model="policy.version_scope" class="h-9 w-full rounded-md border bg-background px-3"><option value="unknown">未知，保留待核对标记</option><option value="all">明确适用所有版本</option><option value="range">明确版本范围</option></select></div><div v-if="policy.version_scope === 'range'" class="grid gap-3 sm:grid-cols-2"><div class="space-y-2"><Label for="knowledge-min">最低版本</Label><Input id="knowledge-min" :model-value="policy.min_version || ''" @update:model-value="value => policy.min_version = String(value)" placeholder="如 3.2.0" /></div><div class="space-y-2"><Label for="knowledge-max">最高版本</Label><Input id="knowledge-max" :model-value="policy.max_version || ''" @update:model-value="value => policy.max_version = String(value)" placeholder="如 3.2.8" /></div></div></fieldset><div class="rounded-md border p-3 text-sm"><p>{{ source.refresh_minutes ? `每 ${source.refresh_minutes} 分钟更新，首次观察后等待完整间隔` : '仅手动采集' }}</p><p v-if="state.next_at" class="mt-2 text-xs text-muted-foreground">下次计划：{{ new Date(state.next_at * 1000).toLocaleString() }}</p><p v-if="state.job" class="mt-2">最近任务：{{ text(state.job.status) }} · {{ text(state.job.outcome) }}</p><Button v-if="state.job" type="button" size="sm" variant="link" class="px-0" @click="emit('job', state!.job!.id)">查看任务与逐页回执</Button></div><p class="text-xs text-muted-foreground">{{ policyDirty ? '请先保存版本范围，再触发采集。' : '采集只追加待审修订，发布需要另行确认。' }}</p></form>
  <p v-if="error" role="alert" class="text-sm text-destructive">{{ error }}</p><Button v-if="!loading && (!source || stale)" variant="outline" :disabled="busy" @click="load">重新读取当前版本</Button>
  <DialogFooter><Button variant="outline" :disabled="busy" @click="emit('close')">关闭</Button><template v-if="mode === 'credential' && superAdmin && source"><Button type="submit" form="knowledge-secret-form" :disabled="busy || stale || !secret">{{ busy ? '处理中…' : '替换凭据' }}</Button></template><template v-if="mode === 'collection' && source"><Button form="knowledge-policy-form" type="submit" variant="outline" :disabled="busy || stale">保存范围</Button><Button :disabled="busy || stale || !source.enabled || policyDirty" @click="collectionAction('collect')">立即采集</Button></template></DialogFooter>
</DialogContent></Dialog><ConfirmAction :open="clearOpen" title="清除采集凭据" description="删除当前来源的认证密文，旧版本任务将失效。之后可以重新配置目标范围。" word="清除" :busy="busy" @update:open="value => clearOpen = value" @confirm="credentialAction('clear')" /></template>
