import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'

import App from './App.vue'
import router from './router'
import { setAuthBridge } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(VueQueryPlugin)

const auth = useAuthStore()
setAuthBridge({
  getAccessToken: () => auth.accessToken,
  refresh: () => auth.refresh(),
  onAuthFailure: () => {
    auth.clear()
    void router.push({ name: 'login' })
  },
})

app.mount('#app')
