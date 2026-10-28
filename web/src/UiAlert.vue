<script setup lang="ts">
import { AlertCircle, Info, TriangleAlert } from '@lucide/vue'

withDefaults(defineProps<{
  tone?: 'error' | 'warning' | 'info'
  role?: 'alert' | 'status'
}>(), { tone: 'error', role: 'alert' })
</script>

<template>
  <div class="ui-alert" :data-tone="tone" :role="role">
    <component :is="tone === 'info' ? Info : tone === 'warning' ? TriangleAlert : AlertCircle" :size="18" :stroke-width="1.7" aria-hidden="true" />
    <div class="ui-alert-content"><slot /></div>
  </div>
</template>

<style scoped>
.ui-alert { display: flex; align-items: flex-start; gap: 10px; min-width: 0; padding: 14px 16px; border: 1px solid color-mix(in srgb, var(--offline) 35%, var(--border)); border-radius: 16px; background: color-mix(in srgb, var(--offline) 5%, var(--surface)); color: var(--offline); font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }
.ui-alert > svg { flex-shrink: 0; margin-top: 2px; }
.ui-alert-content { min-width: 0; }
.ui-alert-content :deep(p) { margin: 0; }
.ui-alert-content :deep(p + p) { margin-top: 6px; }
.ui-alert[data-tone="info"], .ui-alert[data-tone="warning"] { border-color: var(--border); background: var(--background); color: var(--text); }
.ui-alert[data-tone="info"] > svg { color: var(--accent); }
.ui-alert[data-tone="warning"] > svg { color: var(--muted); }
@media (max-width: 539px) { .ui-alert { padding: 12px; } }
</style>
