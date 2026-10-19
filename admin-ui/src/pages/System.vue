<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RefreshCw } from '@lucide/vue'
import { api, date, notify, write } from '@/api'
import { useAuth } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import ConfirmAction from '@/components/ConfirmAction.vue'
const auth = useAuth(), data = ref<any>(null), error = ref(''), loading = ref(false), preview = ref<any>(null), busy = ref(false), confirmation = ref(false)
/** 探测实际基础服务，保留失败状态与手动重试入口。 */
async function load() { loading.value = true; error.value = ''; try { data.value = await api('/system') } catch (reason) { error.value = (reason as Error).message } finally { loading.value = false } }
/** 清理前统计符合现有规则的数据，让管理员核对范围。 */
async function showPreview() { busy.value = true; try { preview.value = await api('/maintenance/preview') } catch (reason) { notify((reason as Error).message, true) } finally { busy.value = false } }
/** 经过明确文字确认后执行清理，保留成功、跳过及失败的区分。 */
async function cleanup() { busy.value = true; try { const result = await write('/maintenance/cleanup', 'POST', {confirm: '清理'}); confirmation.value = false; preview.value = null; notify(`清理完成：插件 ${result.plugins} 条、订阅分享 ${result.shares} 条、订阅统计 ${result.subscriptions} 条`); await load() } catch (reason) { notify((reason as Error).message, true) } finally { busy.value = false } }
onMounted(load)
</script>
<template>
  <section class="max-w-4xl"><div class="mb-8 flex items-start justify-between gap-4"><div><h1 class="text-3xl font-semibold tracking-tight">服务与维护</h1><p class="mt-3 text-sm text-muted-foreground">查看依赖状态、授权配置与现有数据清理规则</p></div><Button variant="outline" :disabled="loading" @click="load"><RefreshCw class="mr-2 size-4" :class="loading ? 'animate-spin' : ''" />刷新</Button></div>
    <p v-if="error" role="alert" class="text-sm text-destructive">{{ error }}</p>
    <p v-if="loading && !data" class="py-12 text-muted-foreground">正在检查服务状态…</p>
    <template v-if="data"><h2 class="mb-5 text-xl font-semibold">基础服务</h2><dl class="divide-y rounded-md border px-5"><div v-for="(value, key) in {数据库: data.database, Redis: data.redis}" :key="key" class="flex items-center justify-between py-5"><dt class="text-sm">{{ key }}</dt><dd><Badge :variant="value === 'ok' ? 'secondary' : 'destructive'">{{ value === 'ok' ? '连接正常' : '连接失败' }}</Badge></dd></div><div class="flex items-center justify-between py-5 text-sm"><dt>数据库类型</dt><dd>{{ data.database_type }}</dd></div><div class="flex items-center justify-between py-5 text-sm"><dt>服务版本 / Worker</dt><dd>{{ data.version }} / {{ data.workers }}</dd></div><div class="flex items-center justify-between py-5 text-sm"><dt>登录会话有效期</dt><dd>{{ data.session_hours }} 小时</dd></div><div class="flex items-center justify-between py-5 text-sm"><dt>登录 Cookie 安全传输</dt><dd>{{ data.cookie_secure ? '仅 HTTPS' : '允许 HTTP（本地调试）' }}</dd></div></dl>
      <section class="mt-10"><h2 class="mb-5 text-xl font-semibold">115 授权服务</h2><dl class="divide-y rounded-md border px-5"><div v-for="(value, key) in data.u115" :key="key" class="flex items-center justify-between py-5 text-sm"><dt>{{ key === 'client_id_configured' ? 'Client ID' : key === 'client_secret_configured' ? 'Client Secret' : '回调地址' }}</dt><dd><Badge :variant="value ? 'secondary' : 'outline'">{{ value ? '已配置' : '未配置' }}</Badge></dd></div></dl><p class="mt-3 text-xs text-muted-foreground">密钥由部署环境管理，后台仅展示配置完整性。</p></section>
      <section class="mt-10 border-t pt-8"><h2 class="mb-4 text-xl font-semibold">数据清理</h2><ul class="list-inside list-disc space-y-2 text-sm leading-6 text-muted-foreground"><li v-for="rule in data.cleanup.rules" :key="rule">{{ rule }}</li></ul><p class="mt-5 text-sm">下次计划时间：{{ date(data.cleanup.next_run) }}</p><p class="mt-3 text-sm text-muted-foreground">最近执行：{{ data.cleanup.latest ? `${date(data.cleanup.latest.created_at)} · ${data.cleanup.latest.target === 'success' ? '成功' : '失败'}` : '尚无执行记录' }}</p>
        <template v-if="auth.user?.role === 'super_admin'"><Button variant="outline" class="mt-6" :disabled="busy" @click="showPreview">{{ busy ? '处理中…' : '预览清理范围' }}</Button><div v-if="preview" class="mt-5 rounded-md border bg-muted/30 p-5"><p class="text-sm leading-7">预计影响：插件统计 {{ preview.plugins }} 条、订阅分享 {{ preview.shares }} 条、订阅统计 {{ preview.subscriptions }} 条。</p><p class="mt-2 text-xs text-muted-foreground">执行时重新按规则筛选，期间新增数据可能使数量发生变化。</p><Button variant="destructive" class="mt-4" :disabled="busy" @click="confirmation = true">确认范围并清理</Button></div></template>
      </section>
    </template>
    <ConfirmAction v-model:open="confirmation" title="执行数据清理？" word="清理" description="将按上述规则删除数据，操作不可撤销。建议先确认已有数据库备份。" :busy="busy" @confirm="cleanup" />
  </section>
</template>
