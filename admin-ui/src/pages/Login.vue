<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import PasswordForm from '@/components/PasswordForm.vue'
import { useAuth } from '@/stores/auth'
import { notify } from '@/api'
const auth = useAuth(), router = useRouter(), route = useRoute()
const username = ref(''), password = ref(''), busy = ref(false), error = ref('')
/** 登录后恢复原目标页，临时密码用户留在强制改密表单。 */
async function submit() {
  error.value = ''; busy.value = true
  try {
    await auth.login(username.value, password.value)
    password.value = ''
    if (!auth.user?.must_change_password) {
      const next = typeof route.query.next === 'string' && /^\/(?!\/)/.test(route.query.next) ? route.query.next : '/overview'
      await router.replace(next)
    }
  } catch (reason) { error.value = (reason as Error).message }
  finally { busy.value = false }
}
/** 改密成功后清理旧会话资料并回到登录表单。 */
function changed() { auth.clear(); password.value = ''; notify('密码已修改，请使用新密码登录') }
/** 临时密码用户也可以退出，避免在改密页面无法切换账号。 */
async function returnToLogin() {
  try { await auth.logout(); password.value = ''; error.value = '' }
  catch (reason) { notify((reason as Error).message, true) }
}
</script>
<template>
  <main class="flex min-h-svh items-center justify-center bg-muted/30 px-5 py-12">
    <section class="w-full max-w-sm">
      <div class="mb-9 flex items-center justify-center gap-3"><img src="/logo.png" alt="" class="size-11" /><div><p class="text-xl font-semibold tracking-tight">MoviePilot</p><p class="text-sm text-muted-foreground">Server 管理后台</p></div></div>
      <div class="rounded-xl border bg-background p-7 shadow-sm">
        <h1 class="text-xl font-semibold">{{ auth.user?.must_change_password ? '设置新的登录密码' : '登录管理后台' }}</h1>
        <p class="mt-2 mb-7 text-sm leading-6 text-muted-foreground">{{ auth.user?.must_change_password ? '首次登录或密码重置后，需要先修改临时密码。' : '使用超级管理员为你创建的账号登录。' }}</p>
        <PasswordForm v-if="auth.user?.must_change_password" @changed="changed" />
        <Button v-if="auth.user?.must_change_password" type="button" variant="ghost" class="mt-4 w-full" @click="returnToLogin">退出并返回登录</Button>
        <form v-if="!auth.user?.must_change_password" class="space-y-5" @submit.prevent="submit">
          <div class="space-y-2"><Label for="username">用户名</Label><Input id="username" v-model="username" autocomplete="username" maxlength="64" required autofocus /></div>
          <div class="space-y-2"><Label for="password">密码</Label><Input id="password" v-model="password" type="password" autocomplete="current-password" maxlength="128" required /></div>
          <p v-if="error" role="alert" class="text-sm text-destructive">{{ error }}</p>
          <Button type="submit" class="w-full" :disabled="busy">{{ busy ? '正在登录…' : '登录' }}</Button>
        </form>
      </div>
      <p class="mt-6 text-center text-xs leading-6 text-muted-foreground">账号由超级管理员统一管理。如需开通或重置密码，请联系管理员。</p>
    </section>
  </main>
</template>
