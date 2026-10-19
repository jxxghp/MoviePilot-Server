<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { LayoutDashboard, ChartColumnIncreasing, ListVideo, Share2, Puzzle, Database, ShieldCheck, Settings2, Users, History, Menu, LogOut, KeyRound, ChevronDown } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import PasswordForm from '@/components/PasswordForm.vue'
import { useAuth } from '@/stores/auth'
import { notify } from '@/api'
const auth = useAuth(), route = useRoute(), router = useRouter()
const mobile = ref(false), passwordOpen = ref(false)
const allNav = [
  { path: '/overview', label: '总览', icon: LayoutDashboard }, { path: '/usage', label: '使用统计', icon: ChartColumnIncreasing },
  { path: '/subscriptions', label: '订阅数据', icon: ListVideo }, { path: '/shares', label: '共享内容', icon: Share2 },
  { path: '/plugins', label: '插件数据', icon: Puzzle }, { path: '/recognition', label: '识别缓存', icon: Database },
  { path: '/features', label: '权限与功能', icon: ShieldCheck }, { path: '/system', label: '服务与维护', icon: Settings2 },
  { path: '/users', label: '用户管理', icon: Users, super: true }, { path: '/audit', label: '操作记录', icon: History },
]
const nav = computed(() => allNav.filter(item => !item.super || auth.user?.role === 'super_admin'))
/** 明确撤销服务端会话；网络失败时保留错误反馈。 */
async function logout() { try { await auth.logout(); await router.replace('/login') } catch (error) { notify((error as Error).message, true) } }
/** 修改密码会撤销所有会话，关闭弹窗后回到登录页。 */
async function changed() { passwordOpen.value = false; auth.clear(); notify('密码已修改，请重新登录'); await router.replace('/login') }
</script>
<template>
  <div class="min-h-svh bg-muted/20">
    <aside class="fixed inset-y-0 left-0 z-20 hidden w-64 flex-col border-r bg-background lg:flex">
      <RouterLink to="/overview" class="flex h-24 items-center gap-3 px-7"><img src="/logo.png" alt="MoviePilot" class="size-11" /><div><p class="text-xl font-bold tracking-tight">MoviePilot</p><p class="text-sm text-muted-foreground">Server</p></div></RouterLink>
      <nav aria-label="管理后台导航" class="flex-1 space-y-1 overflow-y-auto px-4 py-5"><RouterLink v-for="item in nav" :key="item.path" :to="item.path" :aria-current="route.path === item.path ? 'page' : undefined" class="flex items-center gap-3 rounded-md px-4 py-3 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground" :class="route.path === item.path ? 'bg-accent text-accent-foreground' : 'text-muted-foreground'"><component :is="item.icon" class="size-5" />{{ item.label }}</RouterLink></nav>
      <p class="border-t px-7 py-5 text-xs text-muted-foreground">MoviePilot Server 管理后台</p>
    </aside>
    <div class="lg:pl-64">
      <header class="flex h-20 items-center justify-between gap-4 px-5 sm:px-8 lg:justify-end lg:px-10">
        <Button variant="ghost" size="icon" class="lg:hidden" aria-label="打开导航" @click="mobile = true"><Menu class="size-5" /></Button>
        <div class="flex items-center gap-3">
          <DropdownMenu><DropdownMenuTrigger as-child><Button variant="ghost" class="gap-2"><span class="flex size-7 items-center justify-center rounded-full bg-muted text-xs">{{ auth.user?.display_name?.slice(0, 1) }}</span><span class="max-w-32 truncate">{{ auth.user?.display_name }}</span><ChevronDown class="size-4 text-muted-foreground" /></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuItem @select="passwordOpen = true"><KeyRound class="mr-2 size-4" />修改密码</DropdownMenuItem><DropdownMenuItem @select="logout"><LogOut class="mr-2 size-4" />退出登录</DropdownMenuItem></DropdownMenuContent></DropdownMenu>
          <span class="hidden h-5 border-r sm:block"></span><Button variant="ghost" class="hidden sm:inline-flex" @click="logout"><LogOut class="mr-2 size-4" />退出登录</Button>
        </div>
      </header>
      <main id="main-content" class="mx-3 mb-5 min-h-[calc(100svh-6.5rem)] rounded-xl border bg-background p-5 sm:mx-5 sm:p-8 lg:mr-6 lg:ml-0 lg:p-9"><RouterView :key="route.path" /></main>
    </div>
    <Sheet v-model:open="mobile"><SheetContent side="left" class="w-72"><SheetHeader><SheetTitle>MoviePilot Server</SheetTitle><SheetDescription>管理后台导航</SheetDescription></SheetHeader><nav class="space-y-1 p-4"><RouterLink v-for="item in nav" :key="item.path" :to="item.path" class="flex items-center gap-3 rounded-md px-4 py-3 text-sm hover:bg-accent" :class="route.path === item.path ? 'bg-accent' : ''" @click="mobile = false"><component :is="item.icon" class="size-5" />{{ item.label }}</RouterLink></nav></SheetContent></Sheet>
    <Dialog v-model:open="passwordOpen"><DialogContent class="sm:max-w-md"><DialogHeader><DialogTitle>修改登录密码</DialogTitle><DialogDescription>修改后所有设备均需重新登录。</DialogDescription></DialogHeader><PasswordForm @changed="changed" /></DialogContent></Dialog>
  </div>
</template>
