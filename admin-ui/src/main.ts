import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import { useAuth } from './stores/auth'
import { notify } from './api'
import './style.css'

const app = createApp(App)
app.use(createPinia()).use(router).mount('#app')
window.addEventListener('admin-session-expired', () => {
  const auth = useAuth()
  if (auth.user) notify('登录已失效，请重新登录', true)
  auth.clear()
  if (router.currentRoute.value.path !== '/login') void router.replace('/login')
})
