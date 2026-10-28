<script setup lang="ts">
import type { Component } from 'vue'

withDefaults(defineProps<{
  icon: Component
  title: string
  description?: string
  compact?: boolean
}>(), { description: '', compact: false })
</script>

<template>
  <div class="ui-empty-state" :class="{ 'is-compact': compact }">
    <span class="ui-empty-icon"><component :is="icon" :size="26" :stroke-width="1.5" aria-hidden="true" /></span>
    <p class="ui-empty-title">{{ title }}</p>
    <p v-if="description" class="ui-empty-description">{{ description }}</p>
    <slot />
  </div>
</template>

<style scoped>
.ui-empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; min-width: 0; min-height: 168px; padding: 24px 20px; text-align: center; }
.ui-empty-icon { display: grid; place-items: center; flex-shrink: 0; width: 58px; height: 58px; margin-bottom: 16px; border: 1px solid var(--border); border-radius: 20px; color: var(--muted); background: var(--surface); }
.ui-empty-title { margin: 0; color: var(--text); font-size: 13px; font-weight: 600; line-height: 1.7; text-wrap: balance; }
.ui-empty-description { max-width: 36ch; margin: 8px 0 0; color: var(--muted); font-size: 12px; line-height: 1.8; text-wrap: pretty; }
.ui-empty-state.is-compact { min-height: 0; padding: 20px 12px; }
.is-compact .ui-empty-icon { width: 44px; height: 44px; border-radius: 15px; margin-bottom: 12px; }
</style>
