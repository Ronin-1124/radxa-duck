<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch, type Component } from 'vue'
import { Check, ChevronDown } from '@lucide/vue'
import { captureMotionStart, setMotionAnchor } from './anchored-motion'

type Option = { value: string; label: string; description?: string; icon?: Component; lang?: string }

const props = withDefaults(defineProps<{
  label: string
  modelValue: string
  options: readonly Option[]
  icon?: Component
  align?: 'start' | 'end'
  placement?: 'top' | 'bottom'
  compact?: boolean
}>(), { align: 'end', placement: 'bottom', compact: false })
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const id = useId()
const root = ref<HTMLElement>()
const trigger = ref<HTMLButtonElement>()
const listbox = ref<HTMLElement>()
const panel = ref<HTMLElement>()
const expanded = ref(false)
const panelOffset = ref(0)
function positionCompactPanel(): void {
  if (!props.compact || !root.value) return
  const width = Math.min(248, innerWidth - 40)
  panelOffset.value = Math.min(0, innerWidth - 16 - root.value.getBoundingClientRect().left - width)
}
const pointerFocus = ref(false)
const activeIndex = ref(0)
const selectedIndex = computed(() => props.options.findIndex(option => option.value === props.modelValue))
const selected = computed(() => props.options[selectedIndex.value])
const activeId = computed(() => expanded.value ? `${id}-option-${activeIndex.value}` : undefined)
let search = ''
let lastTypedAt = 0

function close(): void {
  if (expanded.value && panel.value) {
    captureMotionStart(panel.value)
    setMotionAnchor(panel.value, trigger.value)
  }
  expanded.value = false
  search = ''
}

function enterPanel(element: Element): void {
  const surface = element as HTMLElement
  surface.inert = false
  surface.removeAttribute('aria-hidden')
  surface.style.transition = 'none'
  surface.style.transform = 'none'
  setMotionAnchor(surface, trigger.value)
  surface.style.removeProperty('transform')
  // Commit the collapsed frame before Vue starts the expansion transition.
  void surface.offsetWidth
  surface.style.removeProperty('transition')
}

function leavePanel(element: Element): void {
  const surface = element as HTMLElement
  surface.inert = true
  surface.setAttribute('aria-hidden', 'true')
}

function open(index = Math.max(0, selectedIndex.value)): void {
  if (!props.options.length) return
  positionCompactPanel()
  activeIndex.value = index
  expanded.value = true
}

function choose(index: number): void {
  const option = props.options[index]
  if (option) emit('update:modelValue', option.value)
  close()
}

function selectWithPointer(index: number): void {
  choose(index)
  pointerFocus.value = true
  trigger.value?.focus({ preventScroll: true })
}

function dismissOutside(event: Event): void {
  if (event.target instanceof Node && !root.value?.contains(event.target)) close()
}

function onKeydown(event: KeyboardEvent): void {
  pointerFocus.value = false
  if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey || !props.options.length) return
  const last = props.options.length - 1
  switch (event.key) {
    case 'ArrowDown':
    case 'ArrowUp':
      event.preventDefault()
      if (!expanded.value) open()
      else activeIndex.value = Math.max(0, Math.min(last, activeIndex.value + (event.key === 'ArrowDown' ? 1 : -1)))
      break
    case 'Home':
    case 'End':
      event.preventDefault()
      open(event.key === 'Home' ? 0 : last)
      break
    case 'Enter':
    case ' ':
      event.preventDefault()
      if (event.repeat) return
      if (expanded.value) choose(activeIndex.value)
      else open()
      break
    case 'Escape':
      if (expanded.value) { event.preventDefault(); event.stopPropagation(); close() }
      break
    case 'Tab':
      if (expanded.value) choose(activeIndex.value)
      break
    default: {
      if (event.key.length !== 1) return
      event.preventDefault()
      if (!expanded.value) open()
      const now = Date.now()
      search = now - lastTypedAt > 700 ? event.key : search + event.key
      lastTypedAt = now
      const query = search.toLocaleLowerCase()
      const repeated = [...query].every(character => character === query[0])
      const prefix = repeated ? query[0]! : query
      const start = activeIndex.value + (repeated ? 1 : 0)
      for (let step = 0; step < props.options.length; step++) {
        const index = (start + step) % props.options.length
        if (props.options[index]?.label.toLocaleLowerCase().startsWith(prefix)) {
          activeIndex.value = index
          break
        }
      }
    }
  }
}

// Keep the active option visible without scrolling the surrounding page.
watch([expanded, activeIndex], async () => {
  if (!expanded.value) return
  await nextTick()
  const panel = listbox.value
  const option = panel?.children[activeIndex.value] as HTMLElement | undefined
  if (!panel || !option) return
  const top = option.offsetTop
  if (top < panel.scrollTop) panel.scrollTop = top
  else if (top + option.offsetHeight > panel.scrollTop + panel.clientHeight) {
    panel.scrollTop = top + option.offsetHeight - panel.clientHeight
  }
})
watch(() => props.modelValue, () => { activeIndex.value = Math.max(0, selectedIndex.value) })

onMounted(() => {
  document.addEventListener('pointerdown', dismissOutside)
  document.addEventListener('focusin', dismissOutside)
  window.addEventListener('blur', close)
  window.addEventListener('resize', positionCompactPanel)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', dismissOutside)
  document.removeEventListener('focusin', dismissOutside)
  window.removeEventListener('blur', close)
  window.removeEventListener('resize', positionCompactPanel)
})
</script>

<template>
  <div ref="root" class="preference-select" :data-align="align" :data-placement="placement" :class="{ compact }">
    <button ref="trigger" type="button" class="preference-trigger" role="combobox"
      :aria-label="`${label}: ${selected?.label ?? ''}`" aria-haspopup="listbox"
      :aria-expanded="expanded" :aria-controls="`${id}-listbox`" :aria-activedescendant="activeId"
      :disabled="!options.length" :data-pointer-focus="pointerFocus"
      @pointerdown="pointerFocus = true" @blur="pointerFocus = false"
      @click="expanded ? close() : open()" @keydown="onKeydown">
      <component :is="selected?.icon ?? icon" v-if="selected?.icon ?? icon" :size="18" :stroke-width="1.7" class="trigger-icon" aria-hidden="true" />
      <span class="trigger-label" :lang="selected?.lang">{{ selected?.label }}</span>
      <ChevronDown :size="14" class="trigger-chevron" aria-hidden="true" />
    </button>
    <Transition name="preference-panel" @enter="enterPanel" @before-leave="leavePanel">
      <div v-if="expanded" ref="panel" class="preference-panel" :style="compact ? { left: `${panelOffset}px`, right: 'auto' } : undefined">
        <div :id="`${id}-label`" class="panel-label">{{ label }}</div>
        <div :id="`${id}-listbox`" ref="listbox" class="preference-options" role="listbox" :aria-labelledby="`${id}-label`">
          <div v-for="(option, index) in options" :id="`${id}-option-${index}`" :key="option.value"
            class="preference-option" :class="{ 'is-active': index === activeIndex }" role="option"
            :aria-selected="option.value === modelValue" :aria-labelledby="`${id}-text-${index}`"
            :aria-describedby="option.description ? `${id}-description-${index}` : undefined"
            @pointermove="activeIndex = index" @mousedown.prevent @click="selectWithPointer(index)">
            <component :is="option.icon" v-if="option.icon" :size="19" :stroke-width="1.7" class="option-icon" aria-hidden="true" />
            <span class="option-copy">
              <span :id="`${id}-text-${index}`" class="option-label" :lang="option.lang">{{ option.label }}</span>
              <span v-if="option.description" :id="`${id}-description-${index}`" class="option-description">{{ option.description }}</span>
            </span>
            <Check :size="17" :stroke-width="2" class="option-check" :class="{ 'is-selected': option.value === modelValue }" aria-hidden="true" />
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.compact.preference-select { min-width:46px; }
.compact .preference-trigger { width:46px; min-height:46px; padding:0; justify-content:center; background:transparent; border-color:transparent; box-shadow:none; }
.compact .trigger-label,.compact .trigger-chevron { display:none; }
[data-placement="top"] .preference-panel { top:auto; bottom:calc(100% + 12px); }
[data-placement="top"] .preference-panel::before { top:100%; height:16px; }

.preference-select { position: relative; min-width: 142px; }
.preference-select:focus-within { z-index: 41; }
.preference-trigger {
  display: flex; align-items: center; gap: 10px; width: 100%; min-height: 46px; padding: 10px 14px;
  border: 1px solid var(--border); border-radius: var(--radius-control, 16px); background: var(--surface); color: var(--text);
  font: inherit; font-size: 13px; line-height: 1.5; cursor: pointer; touch-action: manipulation;
  box-shadow: var(--shadow-soft, 0 3px 10px -5px rgb(0 0 0 / .1));
}
.preference-trigger[aria-expanded="true"] {
  background: var(--surface); border-color: color-mix(in srgb, var(--muted) 40%, var(--border));
}
.preference-trigger:focus-visible { outline: 2px solid var(--muted); outline-offset: 3px; }
.preference-trigger[data-pointer-focus="true"]:focus { outline: none; }
.preference-trigger:disabled { cursor: default; opacity: .6; }
.trigger-icon, .trigger-chevron { flex-shrink: 0; color: var(--muted); }
.trigger-label { flex: 1; text-align: start; white-space: nowrap; }
.trigger-chevron { margin-left: 4px; }
.preference-trigger[aria-expanded="true"] .trigger-chevron { transform: rotate(180deg); }
.preference-panel {
  position: absolute; z-index: 40; top: calc(100% + 8px); right: 0; width: 248px; max-width: calc(100vw - 40px);
  padding: 8px; border: 1px solid var(--border); border-radius: calc(var(--radius-panel, 24px) - 4px); color: var(--text); background: var(--surface);
  box-shadow: var(--shadow-float, 0 16px 40px -12px rgb(0 0 0 / .2), 0 4px 10px -5px rgb(0 0 0 / .12));
  transform-origin: center;
}
/* Keep a continuous pointer area between the raised trigger and its panel. */
.preference-panel::before { content: ''; position: absolute; left: 0; right: 0; top: -12px; height: 12px; }
[data-align="start"] .preference-panel { left: 0; right: auto; }
.panel-label { padding: 7px 10px 10px; margin-bottom: 4px; border-bottom: 1px solid var(--border); font-size: 12px; font-weight: 500; color: var(--muted); }
.preference-options { position: relative; max-height: min(300px, calc(100dvh - 210px)); min-height: 60px; overflow-y: auto; overscroll-behavior: contain; }
.preference-option { display: flex; align-items: center; gap: 12px; min-height: 60px; padding: 10px; border-radius: calc(var(--radius-control, 16px) - 4px); cursor: pointer; touch-action: pan-y; }
.preference-option.is-active { background: var(--background); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--muted) 35%, transparent); }
.preference-option[aria-selected="true"] { color: var(--accent); background: var(--accent-soft); }
.option-icon { flex-shrink: 0; color: var(--muted); }
[aria-selected="true"] .option-icon { color: var(--accent); }
.option-copy { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 2px; }
.option-label { font-size: 13px; font-weight: 500; }
.option-description { font-size: 12px; line-height: 1.5; color: var(--muted); }
.option-check { flex-shrink: 0; visibility: hidden; color: var(--accent); }
.option-check.is-selected { visibility: visible; }
@media (prefers-reduced-motion: no-preference) {
  .preference-trigger { transition: transform var(--motion-fast, 180ms) var(--ease-out, cubic-bezier(.22, 1, .36, 1)), box-shadow var(--motion-fast, 180ms) ease, background-color var(--motion-fast, 180ms) ease, border-color var(--motion-fast, 180ms) ease; }
  .trigger-chevron { transition: transform var(--motion-fast, 180ms) var(--ease-out, cubic-bezier(.22, 1, .36, 1)); }
  .preference-panel-enter-active { transition: opacity var(--motion-panel-open, 400ms) var(--motion-ease-open, ease-out), transform var(--motion-panel-open, 400ms) var(--motion-ease-open, ease-out); }
  .preference-panel-leave-active { transition: opacity var(--motion-panel-close, 280ms) var(--motion-ease-close, ease-in), transform var(--motion-panel-close, 280ms) var(--motion-ease-close, ease-in); pointer-events: none; }
  .preference-panel-enter-from,.preference-panel-leave-to { opacity: 0; transform: var(--anchored-collapsed, none); }
  .preference-panel-leave-from { opacity: var(--anchored-from-opacity, 1); transform: var(--anchored-from-transform, none); }
}
@media (hover: hover) and (pointer: fine) {
  .preference-select:hover .preference-trigger:not(:disabled) { background: var(--background); border-color: color-mix(in srgb, var(--muted) 40%, var(--border)); }
}
@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .preference-select:hover .preference-trigger:not(:disabled) { transform: translateY(-2px); box-shadow: var(--shadow-float, 0 8px 20px -10px rgb(0 0 0 / .18)); }
  .preference-select .preference-trigger:not(:disabled):active { transform: translateY(0); }
}
@media (forced-colors: active) {
  .preference-option.is-active { outline: 2px solid Highlight; outline-offset: -2px; }
}
</style>
