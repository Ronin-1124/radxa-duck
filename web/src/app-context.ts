import { createRouter, createWebHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import RobotStatus from './RobotStatus.vue'
import RobotView from './RobotView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: { name: 'overview' } },
    { path: '/overview', name: 'overview', component: RobotStatus },
    { path: '/vision', name: 'vision', component: RobotView },
    { path: '/settings', redirect: { name: 'overview', query: { settings: '1' } } },
    { path: '/device', redirect: { name: 'overview', query: { settings: '1', tab: 'device' } } },
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
