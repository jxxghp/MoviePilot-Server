<script setup lang="ts">
import { ref } from 'vue'
import { notify, write } from '@/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

/** 已保存关系的管理投影；原来源身份由父级只读展示。 */
interface MappingRecord { id: number; tmdb_id: number; tmdb_type: string; tmdb_season: number | null; blocked: boolean }
const props = defineProps<{ mapping: MappingRecord }>()
const emit = defineEmits<{ saved: [] }>()
const tmdbId = ref(String(props.mapping.tmdb_id)), tmdbType = ref(props.mapping.tmdb_type)
const season = ref(props.mapping.tmdb_season == null ? '' : String(props.mapping.tmdb_season))
const blocked = ref(props.mapping.blocked), busy = ref(false)

/** 保存明确选中的目标和撤销状态；服务端保留原身份并记录审计。 */
async function save() {
  busy.value = true
  try {
    await write(`/mappings/${props.mapping.id}`, 'PATCH', {
      tmdb_id: Number(tmdbId.value), tmdb_type: tmdbType.value,
      tmdb_season: tmdbType.value === 'tv' && season.value !== '' ? Number(season.value) : null,
      blocked: blocked.value, confirm: '保存',
    })
    notify('对应关系已保存，实例缓存会在一小时内更新')
    emit('saved')
  } catch (reason) { notify((reason as Error).message, true) }
  finally { busy.value = false }
}
</script>
<template>
  <form class="space-y-5 border-t p-6" @submit.prevent="save">
    <h3 class="font-medium">纠正对应关系</h3>
    <div class="space-y-2"><Label for="mapping-id">TMDB ID</Label><Input id="mapping-id" v-model="tmdbId" type="number" min="1" max="2147483647" required /></div>
    <div class="space-y-2"><Label for="mapping-type">TMDB 类型</Label><Select v-model="tmdbType"><SelectTrigger id="mapping-type"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="movie">电影</SelectItem><SelectItem value="tv">电视剧</SelectItem></SelectContent></Select></div>
    <div v-if="tmdbType === 'tv'" class="space-y-2"><Label for="mapping-season">TMDB 季号（未知时留空）</Label><Input id="mapping-season" v-model="season" type="number" min="0" max="10000" /></div>
    <label class="flex items-center gap-2 text-sm"><input v-model="blocked" type="checkbox" />撤销此关系</label>
    <p class="text-xs leading-5 text-muted-foreground">撤销后保留记录，阻止旧实例重新共享错误关系。实例缓存最多一小时后生效。</p>
    <Button :disabled="busy" type="submit">{{ busy ? '保存中…' : '保存对应关系' }}</Button>
  </form>
</template>
