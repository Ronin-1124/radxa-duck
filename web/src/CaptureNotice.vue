<script setup lang="ts">
import { useId } from 'vue'
import { ShieldCheck } from '@lucide/vue'

defineProps<{ source: string; notes: readonly string[] }>()
const titleId = useId()
</script>

<template>
  <aside class="capture-notice" role="note" :aria-labelledby="titleId">
    <ShieldCheck :size="21" :stroke-width="1.6" aria-hidden="true" />
    <div>
      <p :id="titleId" class="notice-source">{{ source }}</p>
      <p v-for="note in notes" :key="note" class="notice-detail">{{ note }}</p>
    </div>
  </aside>
</template>

<style scoped>
.capture-notice { display: flex; align-items: flex-start; gap: 12px; min-width: 0; padding: 4px 6px; }
.capture-notice > svg { flex-shrink: 0; margin-top: 2px; color: var(--accent); }
.capture-notice > div { min-width: 0; }
.capture-notice p { margin: 0; font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; text-wrap: pretty; }
.notice-source { color: var(--text); font-weight: 500; }
.capture-notice .notice-detail { margin-top: 3px; color: var(--muted); }
@media (max-width: 539px) {
  .capture-notice { padding-inline: 2px; }
}
</style>
