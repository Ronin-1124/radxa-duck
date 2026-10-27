<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowRight, Blocks } from '@lucide/vue'

const { t } = useI18n({
  useScope: 'local', inheritLocale: true,
  messages: {
    'zh-CN': { title: '这个工作台正在准备中', description: '先到语音页面，开启收音并查看实时识别文字。', action: '前往语音' },
    en: { title: 'This workspace is being prepared', description: 'Visit Voice to start listening and view live speech recognition.', action: 'Go to Voice' },
  },
})
</script>

<template>
  <section class="pending-workspace">
    <span class="pending-icon"><Blocks :size="28" :stroke-width="1.5" aria-hidden="true" /></span>
    <h2>{{ t('title') }}</h2>
    <p>{{ t('description') }}</p>
    <RouterLink :to="{ name: 'voice' }">{{ t('action') }}<ArrowRight :size="18" aria-hidden="true" /></RouterLink>
  </section>
</template>

<style scoped>
.pending-workspace { padding: 36px; border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); box-shadow: var(--shadow-soft); }
.pending-icon { display: grid; place-items: center; width: 56px; height: 56px; margin-bottom: 24px; border-radius: 18px; color: var(--accent); background: var(--accent-soft); }
h2 { margin: 0 0 12px; color: var(--text); font-size: 22px; line-height: 1.5; }
p { margin: 0; color: var(--muted); font-size: 14px; line-height: 1.8; }
a { display: inline-flex; align-items: center; gap: 16px; min-height: 46px; margin-top: 24px; padding: 10px 18px; border: 1px solid var(--border); border-radius: var(--radius-control); color: var(--accent); background: var(--accent-soft); text-decoration: none; }
a:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
@media (hover: hover) and (pointer: fine) { a:hover { box-shadow: var(--shadow-soft); border-color: var(--accent); } }
@media (prefers-reduced-motion: no-preference) { a { transition: transform var(--motion-fast) var(--ease-out), box-shadow var(--motion-fast) ease; } }
@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) { a:hover { transform: translateY(-2px); } a:active { transform: none; } }
@media (max-width: 539px) { .pending-workspace { padding: 26px 22px; } }
</style>
