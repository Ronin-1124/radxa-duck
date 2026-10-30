<script setup lang="ts">
import { ref, type Component } from 'vue'
import { ChevronDown, RefreshCw } from '@lucide/vue'

defineProps<{ id:string; label:string; refreshLabel:string; icon:Component; content:Component; open:boolean }>()
const emit=defineEmits<{toggle:[]}>()
const panel=ref<{refresh:()=>Promise<void>;refreshing:boolean;refreshDisabled:boolean}>()
function hideLeavingContent(element:Element) {
 const content=element as HTMLElement
 content.inert=true
 content.setAttribute('aria-hidden','true')
}
</script>

<template>
 <div class="device-disclosure" :class="{'is-open':open}">
  <div class="disclosure-heading">
   <button :id="`${id}-toggle`" class="disclosure-toggle" type="button" :aria-expanded="open" :aria-controls="id" @click="emit('toggle')">
    <component :is="icon" :size="18" :stroke-width="1.6" aria-hidden="true" />
    <span>{{ label }}</span>
    <ChevronDown class="disclosure-chevron" :size="16" aria-hidden="true" />
   </button>
   <span class="disclosure-action" :inert="!open" :aria-hidden="!open ? true : undefined"><button class="disclosure-refresh ui-icon-button" type="button" :aria-label="refreshLabel" :disabled="!panel||panel.refreshDisabled" :aria-busy="panel?.refreshing" @click="panel?.refresh()"><RefreshCw :size="17" aria-hidden="true" /></button></span>
  </div>
  <Transition name="device-disclosure" @before-leave="hideLeavingContent">
   <div v-if="open" :id="id" class="disclosure-reveal">
    <div class="disclosure-clip"><div class="disclosure-content"><component :is="content" ref="panel" :labelledby="`${id}-toggle`" /></div></div>
   </div>
  </Transition>
 </div>
</template>

<style scoped>
.device-disclosure { border-top:1px solid var(--border); --disclosure-duration:180ms; --disclosure-easing:var(--motion-ease-close); }
.device-disclosure.is-open { --disclosure-duration:240ms; --disclosure-easing:var(--motion-ease-open); }
.disclosure-heading { display:grid; grid-template-columns:minmax(0,1fr) 46px 16px; column-gap:12px; align-items:center; }
.disclosure-toggle { grid-area:1 / 1 / 2 / -1; display:flex; align-items:center; gap:12px; width:100%; min-width:0; min-height:65px; padding:16px 0; text-align:start; border:0; background:transparent; color:var(--text); cursor:pointer; font-size:14px; }
.disclosure-toggle span { flex:1; min-width:0; }
.disclosure-toggle svg { flex-shrink:0; color:var(--muted); }
.disclosure-chevron { margin-inline-start:58px; }
.is-open .disclosure-chevron { transform:rotate(180deg); }
.disclosure-action { position:relative; grid-area:1 / 2; z-index:1; display:flex; align-items:center; opacity:0; transform:translateX(12px); visibility:hidden; pointer-events:none; }
.is-open .disclosure-action { opacity:1; transform:none; visibility:visible; pointer-events:auto; }
.disclosure-refresh { border-color:transparent; background:transparent; box-shadow:none; }
.disclosure-reveal { display:grid; grid-template-rows:1fr; }
.disclosure-clip { min-height:0; }
.device-disclosure-enter-active .disclosure-clip,.device-disclosure-leave-active .disclosure-clip { overflow:hidden; }
.disclosure-content { padding:8px 0 24px; }
@media (prefers-reduced-motion:no-preference) {
 .disclosure-chevron { transition:transform var(--disclosure-duration) var(--disclosure-easing); }
 .disclosure-action { transition:transform var(--disclosure-duration) var(--disclosure-easing),opacity var(--disclosure-duration) var(--disclosure-easing),visibility 0s linear var(--disclosure-duration); }
 .is-open .disclosure-action { transition-delay:0s; }
 .device-disclosure-enter-active { transition:grid-template-rows 240ms var(--motion-ease-open),opacity 240ms var(--motion-ease-open); }
 .device-disclosure-leave-active { transition:grid-template-rows 180ms var(--motion-ease-close),opacity 180ms var(--motion-ease-close); pointer-events:none; }
 .device-disclosure-enter-from,.device-disclosure-leave-to { grid-template-rows:0fr; opacity:0; }
}
</style>
