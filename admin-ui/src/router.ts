import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from '@/stores/auth'
import Login from '@/pages/Login.vue'
import Shell from '@/components/Shell.vue'
import Overview from '@/pages/Overview.vue'
import DataPage from '@/pages/DataPage.vue'
import Recognition from '@/pages/Recognition.vue'
import Features from '@/pages/Features.vue'
import System from '@/pages/System.vue'
import Users from '@/pages/Users.vue'
import { notify } from '@/api'

export const router = createRouter({
  history: createWebHistory('/admin/'),
  routes: [
    { path: '/login', component: Login },
    { path: '/', component: Shell, children: [
      { path: '', redirect: '/overview' },
      { path: 'overview', component: Overview, meta: { title: '服务总览' } },
      { path: 'usage', component: DataPage, meta: { title: '使用统计', kind: 'usage' } },
      { path: 'subscriptions', component: DataPage, meta: { title: '订阅数据', kind: 'subscriptions' } },
      { path: 'shares', component: DataPage, meta: { title: '共享内容', kind: 'shares' } },
      { path: 'plugins', component: DataPage, meta: { title: '插件数据', kind: 'plugins' } },
      { path: 'recognition', component: Recognition, meta: { title: '识别缓存' } },
      { path: 'features', component: Features, meta: { title: '权限与功能' } },
      { path: 'system', component: System, meta: { title: '服务与维护' } },
      { path: 'users', component: Users, meta: { title: '用户管理', super: true } },
      { path: 'audit', component: DataPage, meta: { title: '操作记录', kind: 'audit' } },
    ] },
    { path: '/:pathMatch(.*)*', redirect: '/overview' },
  ],
})
router.beforeEach(async (to) => {
  const auth = useAuth()
  if (!auth.ready) {
    try { await auth.load() } catch (error) { notify((error as Error).message, true) }
  }
  if (!auth.user || auth.user.must_change_password) {
    if (to.path !== '/login') return { path: '/login', query: { next: to.fullPath } }
  } else if (to.path === '/login') return '/overview'
  if (to.meta.super && auth.user?.role !== 'super_admin') return '/overview'
  document.title = `${to.meta.title || '登录'} · MoviePilot Server`
})
