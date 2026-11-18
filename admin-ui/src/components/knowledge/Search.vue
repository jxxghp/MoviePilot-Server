<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { date } from '@/api'
import { message } from '@/knowledge'
import { retrieve, retrievalText, type Evidence, type RetrievalResult } from '@/retrieval'
import { useAuth } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
const auth = useAuth()
const token = ref(''), query = ref(''), product = ref('moviepilot'), version = ref(''), domain = ref(''), site = ref(''), purpose = ref('usage'), limit = ref(5)
const result = ref<RetrievalResult | null>(null), reading = ref(''), supplement = ref<Evidence | null>(null), busy = ref(false), error = ref('')
let sequence = 0, controller: AbortController | undefined, timer: ReturnType<typeof setTimeout> | undefined
/** 参数或身份改变就清除旧证据，并使正在返回的旧请求失效。 */
function clear() { sequence++; controller?.abort(); clearTimeout(timer); busy.value = false; reading.value = ''; result.value = null; supplement.value = null; error.value = '' }
/** 只提交当前短问题和显式范围，不发送聊天记录或可配置服务器地址。 */
async function search() {
  if (busy.value) return
  clear(); const current = sequence; busy.value = true; controller = new AbortController(); timer = setTimeout(() => controller?.abort(), 12000)
  try {
    const value = await retrieve('search', token.value, { query: query.value, product: product.value, product_version: version.value || null,
      domain: domain.value || null, site: site.value || null, purpose: purpose.value, limit: Number(limit.value) }, controller.signal)
    if (current === sequence) result.value = value
  } catch (reason) { if (current === sequence) error.value = message(reason) }
  finally { if (current === sequence) { busy.value = false; clearTimeout(timer) } }
}
/** 补读只接受本次签发的不透明短期引用，失败后要求重新检索而不继续显示陈旧正文。 */
async function read(item: Evidence) {
  if (busy.value) return
  const current = ++sequence; busy.value = true; reading.value = item.evidence_id; supplement.value = null; error.value = ''
  controller = new AbortController(); timer = setTimeout(() => controller?.abort(), 12000)
  try {
    const value = await retrieve('read', token.value, { evidence_id: item.evidence_id }, controller.signal)
    if (current !== sequence) return
    if (value.http === 200 && value.status === 'matched') supplement.value = value.evidence[0] || null
    else result.value = value
  } catch (reason) { if (current === sequence) { result.value = null; error.value = message(reason) } }
  finally { if (current === sequence) { busy.value = false; reading.value = ''; clearTimeout(timer) } }
}
/** 出处仅允许显示 HTTPS 链接，正文始终为转义原文。 */
function link(value: string) { return typeof value === 'string' && value.startsWith('https://') ? value : undefined }
watch([token, query, product, version, domain, site, purpose, limit], clear)
watch(() => [auth.user?.id, auth.user?.role], () => { token.value = ''; clear() })
onBeforeUnmount(() => { token.value = ''; clear() })
</script>
<template><div class="max-w-4xl"><p class="mb-5 text-sm text-muted-foreground">使用 Agent 的独立令牌测试真实只读协议；后台登录不会自动授予检索权限。</p><form class="space-y-4" @submit.prevent="search"><div class="space-y-2"><Label for="knowledge-test-token">独立知识令牌</Label><Input id="knowledge-test-token" v-model="token" type="password" autocomplete="new-password" maxlength="128" required /><p class="text-xs text-muted-foreground">仅当前窗口内存持有，切换分区或身份变化后清空。</p></div><div class="space-y-2"><Label for="knowledge-test-query">检索关键词</Label><Input id="knowledge-test-query" v-model="query" maxlength="300" placeholder="如：分享率、DOWNLOAD_PATH" required /></div><div class="grid gap-4 sm:grid-cols-3"><div class="space-y-2"><Label for="knowledge-test-product">产品</Label><Input id="knowledge-test-product" v-model="product" pattern="[a-z][a-z0-9-]{0,63}" required /></div><div class="space-y-2"><Label for="knowledge-test-version">当前产品版本</Label><Input id="knowledge-test-version" v-model="version" placeholder="如 3.2.8，未知留空" /></div><div class="space-y-2"><Label for="knowledge-test-domain">领域</Label><select id="knowledge-test-domain" v-model="domain" class="h-9 w-full rounded-md border bg-background px-3"><option value="">全部已授权领域</option><option value="mp">MP</option><option value="pt">PT</option></select></div></div><div class="grid gap-4 sm:grid-cols-3"><div class="space-y-2"><Label for="knowledge-test-site">站点名称 / 域名 / 别名</Label><Input id="knowledge-test-site" v-model="site" maxlength="253" placeholder="PT 问题建议明确站点" /></div><div class="space-y-2"><Label for="knowledge-test-purpose">用途</Label><select id="knowledge-test-purpose" v-model="purpose" class="h-9 w-full rounded-md border bg-background px-3"><option value="usage">使用说明</option><option value="troubleshooting">排查问题</option><option value="development">开发参考</option></select></div><div class="space-y-2"><Label for="knowledge-test-limit">证据数量上限</Label><Input id="knowledge-test-limit" v-model="limit" type="number" min="1" max="5" required /></div></div><div class="flex flex-wrap gap-2"><Button :disabled="busy">{{ busy ? '请求中…' : '检索' }}</Button><Button type="button" variant="outline" @click="clear">清除结果 / 取消请求</Button><Button type="button" variant="ghost" @click="token = ''; clear()">清空令牌</Button></div></form><p v-if="error" role="alert" class="mt-5 text-sm text-destructive">{{ error }}</p><section v-if="result" class="mt-6 space-y-4" aria-label="检索结果"><p :class="result.http >= 400 ? 'text-destructive' : ''" role="status">{{ retrievalText(result.status) }} · HTTP {{ result.http }}</p><p v-if="result.notices.length" class="text-sm text-muted-foreground">{{ result.notices.map(retrievalText).join('；') }}</p><p v-if="result.detail" class="break-all text-xs text-muted-foreground">状态代码：{{ result.detail }}</p><article v-for="item in result.evidence" :key="item.evidence_id" class="space-y-3 rounded-md border p-4"><h2 class="break-words font-semibold">{{ item.title }} · r{{ item.revision }}</h2><p class="break-words text-xs text-muted-foreground">{{ item.collection_label }} / {{ item.source_label }} · {{ item.kind }} · 采集 {{ date(item.fetched_at) }}</p><p class="text-xs text-muted-foreground">适用版本：{{ item.applicable_versions?.scope === 'all' ? '全部' : item.applicable_versions?.scope === 'range' ? `${item.applicable_versions.min || '不限'} 至 ${item.applicable_versions.max || '不限'}` : '未知，需核对' }} · 有效截止 {{ date(item.valid_until) }}</p><a v-if="item.source_url" :href="link(item.source_url)" target="_blank" rel="noopener noreferrer" class="block break-all text-xs underline">{{ item.source_url }}</a><pre class="max-h-72 overflow-auto text-sm leading-7 whitespace-pre-wrap break-words">{{ supplement?.evidence_id === item.evidence_id ? supplement.excerpt : item.excerpt }}</pre><Button variant="outline" size="sm" :disabled="busy" @click="read(item)">{{ reading === item.evidence_id ? '补读中…' : '补读相邻上下文' }}</Button></article><p class="text-xs text-muted-foreground">仅作本次临时参考；正文中的 HTML、脚本或操作指令不执行。</p></section></div></template>
