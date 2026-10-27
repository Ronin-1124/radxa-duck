import { createApp } from 'vue'
import App from './App.vue'
import { i18n, router } from './app-context'

const app = createApp(App).use(router).use(i18n)

await router.isReady()
app.mount('#app')
