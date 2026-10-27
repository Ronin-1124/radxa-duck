import { createRouter, createWebHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import ConnectionOverview from './ConnectionOverview.vue'
import SpeechRecognition from './SpeechRecognition.vue'
import PendingWorkspace from './PendingWorkspace.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: { name: 'overview' } },
    { path: '/overview', name: 'overview', component: ConnectionOverview },
    { path: '/voice', name: 'voice', component: SpeechRecognition },
    ...['vision', 'apps', 'device', 'settings'].map(name => ({
      path: `/${name}`, name, component: PendingWorkspace,
    })),
    { path: '/:pathMatch(.*)*', redirect: { name: 'overview' } },
  ],
  scrollBehavior: (_to, _from, savedPosition) => savedPosition ?? { top: 0 },
})

export const i18n = createI18n({
  legacy: false,
  locale: 'zh-CN',
  fallbackLocale: 'en',
  messages: {},
})
