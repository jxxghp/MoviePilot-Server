<script setup lang="ts">
import { ref } from 'vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { write } from '@/api'
const emit = defineEmits<{ changed: [] }>()
const current = ref(''), password = ref(''), repeated = ref(''), error = ref(''), busy = ref(false)
/** 验证两次新密码一致，提交成功后要求重新登录。 */
async function submit() {
  error.value = ''
  if (password.value !== repeated.value) { error.value = '两次输入的新密码不一致'; return }
  busy.value = true
  try { await write('/auth/password', 'POST', { current_password: current.value, password: password.value }); emit('changed') }
  catch (reason) { error.value = (reason as Error).message }
  finally { busy.value = false }
}
</script>
<template>
  <form class="space-y-5" @submit.prevent="submit">
    <div class="space-y-2"><Label for="old-password">当前密码</Label><Input id="old-password" v-model="current" type="password" autocomplete="current-password" maxlength="128" required /></div>
    <div class="space-y-2"><Label for="new-password">新密码</Label><Input id="new-password" v-model="password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required /><p class="text-xs text-muted-foreground">使用 12 至 128 位密码，修改后所有设备都需要重新登录。</p></div>
    <div class="space-y-2"><Label for="repeat-password">再次输入新密码</Label><Input id="repeat-password" v-model="repeated" type="password" autocomplete="new-password" minlength="12" maxlength="128" required /></div>
    <p v-if="error" role="alert" class="text-sm text-destructive">{{ error }}</p>
    <Button class="w-full" type="submit" :disabled="busy">{{ busy ? '保存中…' : '修改密码' }}</Button>
  </form>
</template>
