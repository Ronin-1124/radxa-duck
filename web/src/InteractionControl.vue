<script setup lang="ts">
import type { Component } from 'vue'

withDefaults(defineProps<{
  titleId: string
  hintId: string
  title: string
  description: string
  icon: Component
  actionIcon: Component
  actionLabel: string
  disabled: boolean
  active: boolean
  busy: boolean
  status: string
  hint: string
  statusState: string
  tone?: 'neutral' | 'active' | 'error'
}>(), { tone: 'neutral' })
const emit = defineEmits<{ action: [] }>()
</script>

<template>
  <div class="interaction-control">
    <header class="interaction-heading">
      <div class="interaction-copy">
        <span class="interaction-icon ui-section-icon"><component :is="icon" :size="24" :stroke-width="1.6" aria-hidden="true" /></span>
        <div><h2 :id="titleId" class="ui-section-title">{{ title }}</h2><p class="ui-section-description">{{ description }}</p></div>
      </div>
      <button class="action-button ui-button" type="button" :class="{ 'stop-button': active, 'ui-button--primary': !active }"
        :disabled="disabled" :aria-describedby="hintId" :aria-busy="busy" @click="emit('action')">
        <component :is="actionIcon" :size="18" :stroke-width="1.8" aria-hidden="true" />
        <span>{{ actionLabel }}</span>
      </button>
    </header>
    <div class="capture-status" :data-state="statusState" :data-tone="tone">
      <p class="status-label" role="status" aria-atomic="true"><span class="status-dot" aria-hidden="true"></span>{{ status }}</p>
      <p :id="hintId" class="status-hint">{{ hint }}</p>
    </div>
  </div>
</template>

<style scoped>
.interaction-control { display: grid; gap: 24px; margin-bottom: 20px; min-width: 0; }
.interaction-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 20px; }
.interaction-copy { display: flex; align-items: center; gap: 14px; min-width: 0; flex: 1 1 250px; }
.interaction-copy > div { min-width: 0; }
h2, p { margin: 0; }
.interaction-copy .ui-section-description { margin-top: 4px; }
.action-button { flex-shrink: 0; min-width: 120px; }
.stop-button { border-color: var(--muted); }
.capture-status { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px 18px; padding: 14px 16px; border: 1px solid var(--border); border-radius: 16px; background: var(--background); }
.status-label { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12px; font-weight: 600; }
.status-dot { width: 6px; height: 6px; flex-shrink: 0; border-radius: 50%; background: currentColor; }
[data-tone="active"] .status-label { color: var(--online); }
[data-tone="error"] .status-label { color: var(--offline); }
.status-hint { flex: 1 1 260px; color: var(--muted); font-size: 12px; line-height: 1.7; text-wrap: pretty; }
@media (max-width: 539px) {
  .interaction-control { gap: 20px; margin-bottom: 16px; }
  .interaction-heading { gap: 16px; }
  .interaction-copy { gap: 12px; flex-basis: 100%; }
  .action-button { width: 100%; }
  .capture-status { padding: 12px; }
}
</style>
