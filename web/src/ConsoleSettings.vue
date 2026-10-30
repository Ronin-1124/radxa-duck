<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDown, Cloud, Cpu, Mic, Settings2, UserRound, Wifi, Wrench, X } from '@lucide/vue'
import IdentitySettings from './IdentitySettings.vue'
import CloudSettings from './CloudSettings.vue'
import BrainRuntimeSettings from './BrainRuntimeSettings.vue'
import DeviceAudio from './DeviceAudio.vue'
import DeviceNetwork from './DeviceNetwork.vue'
import DeviceMaintenance from './DeviceMaintenance.vue'
import { displayDeviceModel } from './device-model'
import { useRobotConsole } from './useRobotConsole'
import { captureMotionStart, setMotionAnchor } from './anchored-motion'
const props=defineProps<{initialTab:string;opener?:HTMLButtonElement}>()
const emit=defineEmits<{close:[];opening:[];closing:[];'dock-host':[element:HTMLElement|null]}>()
const id=useId(),dialog=ref<HTMLDialogElement>(),panel=ref<HTMLElement>(),dockHost=ref<HTMLElement>(),closeButton=ref<HTMLButtonElement>()
type Tab='identity'|'brain'|'device'
const tab=ref<Tab>(['identity','brain','device'].includes(props.initialTab)?props.initialTab as Tab:'identity')
const visited=ref(new Set<Tab>([tab.value]))
const role=ref<{hasUnsavedChanges:boolean;busy:boolean}>(),cloud=ref<{hasUnsavedChanges:boolean;busy:boolean}>()
const discard=ref(false)
const closing=ref(false)
const opened=ref<string|null>(null)
const { device,deviceStale }=useRobotConsole()
const { t }=useI18n({useScope:'local',messages:{
 'zh-CN':{settings:'设置',identity:'角色身份',brain:'大脑',device:'设备',close:'关闭设置',tabs:'设置分类',discardTitle:'放弃未保存的更改？',discard:'放弃并关闭',keep:'继续编辑',saving:'正在保存，请稍候',audio:'音频设备',network:'网络',maintenance:'系统维护',os:'系统',kernel:'内核',version:'应用版本',hostname:'主机名',unknown:'未报告',stale:'上次读数'},
 en:{settings:'Settings',identity:'Identity',brain:'Brain',device:'Device',close:'Close settings',tabs:'Settings sections',discardTitle:'Discard unsaved changes?',discard:'Discard and close',keep:'Keep editing',saving:'Saving. Please wait.',audio:'Audio devices',network:'Network',maintenance:'Maintenance',os:'System',kernel:'Kernel',version:'App version',hostname:'Hostname',unknown:'Not reported',stale:'Previous reading'}
}})
const busy=computed(()=>role.value?.busy||cloud.value?.busy)
function select(value:Tab) {
 if(value===tab.value)return
 tab.value=value;visited.value.add(value);discard.value=false
}
async function close() {
 if(busy.value||closing.value)return
 if(role.value?.hasUnsavedChanges||cloud.value?.hasUnsavedChanges){discard.value=true;await nextTick();dialog.value?.querySelector<HTMLButtonElement>('.keep-editing')?.focus();return}
 await dismiss()
}
let disposed=false
async function dismiss() {
 if(busy.value||closing.value||disposed)return
 const surface=panel.value
 if(!surface||window.matchMedia('(prefers-reduced-motion: reduce)').matches){emit('close');return}
 captureMotionStart(surface)
 const backdropOpacity=dialog.value?getComputedStyle(dialog.value,'::backdrop').opacity:'1'
 // Measure the resting panel, including when Escape interrupts its entrance.
 surface.style.animation='none'
 setMotionAnchor(surface,props.opener)
 dialog.value?.style.setProperty('--settings-backdrop-opacity',backdropOpacity)
 closing.value=true
 emit('closing')
 await nextTick()
 if(disposed)return
 surface.style.removeProperty('animation')
 const animation=surface.getAnimations().find(item=>item instanceof CSSAnimation&&item.animationName.startsWith('settings-close'))
 // Cancellation (including a reduced-motion change) must still finish closing.
 await animation?.finished.catch(()=>{})
 if(!disposed)emit('close')
}
function cancel(event:Event){if(event.target!==dialog.value)return;event.preventDefault();void close()}
function backdrop(event:MouseEvent){if(event.target===dialog.value)void close()}
watch(tab,()=>{const body=dialog.value?.querySelector('.settings-body');if(body)body.scrollTop=0})
let oldOverflow=''
onMounted(async()=>{
 oldOverflow=document.body.style.overflow;document.body.style.overflow='hidden'
 const surface=panel.value
 if(surface)surface.style.animation='none'
 dialog.value?.showModal()
 emit('dock-host',dockHost.value||null)
 await nextTick()
 if(disposed)return
 // Commit the dock's unchanged position after moving it into the modal layer.
 const preferences=dockHost.value?.querySelector('.dock-preferences')
 if(preferences)void getComputedStyle(preferences).transform
 emit('opening')
 if(surface){setMotionAnchor(surface,props.opener);surface.style.removeProperty('animation')}
 closeButton.value?.focus({preventScroll:true})
})
onBeforeUnmount(()=>{disposed=true;emit('dock-host',null);dialog.value?.close();document.body.style.overflow=oldOverflow;props.opener?.focus({preventScroll:true})})
</script>
<template>
 <dialog ref="dialog" class="console-settings" :class="{'is-closing':closing}" :inert="closing" :aria-labelledby="`${id}-title`" @cancel="cancel" @click="backdrop">
  <div ref="panel" class="settings-panel">
  <header class="settings-header"><h2 :id="`${id}-title`"><Settings2 :size="20" :stroke-width="1.6" aria-hidden="true" />{{ t('settings') }}</h2><button ref="closeButton" type="button" class="ui-icon-button" :aria-label="t('close')" :disabled="busy" @click="close"><X :size="19" aria-hidden="true" /></button></header>
  <div class="settings-layout">
   <aside class="settings-rail"><nav class="settings-tabs" :aria-label="t('tabs')"><button v-for="item in [{key:'identity',icon:UserRound},{key:'brain',icon:Cloud},{key:'device',icon:Cpu}] as const" :key="item.key" type="button" :aria-pressed="tab===item.key" @click="select(item.key)"><component :is="item.icon" :size="18" :stroke-width="1.7" aria-hidden="true" />{{ t(item.key) }}</button></nav></aside>
   <div class="settings-body">
    <section v-if="discard" class="discard-changes" role="alert"><strong>{{ t('discardTitle') }}</strong><div><button type="button" class="ui-button keep-editing" @click="discard=false">{{ t('keep') }}</button><button type="button" class="ui-button ui-button--danger" @click="dismiss">{{ t('discard') }}</button></div></section>
    <section v-if="visited.has('identity')" v-show="tab==='identity'" class="settings-page" :inert="tab!=='identity'" :aria-hidden="tab!=='identity' ? true : undefined" :aria-label="t('identity')"><h3 class="settings-section-title">{{ t('identity') }}</h3><IdentitySettings ref="role" /></section>
    <section v-if="visited.has('brain')" v-show="tab==='brain'" class="settings-page" :inert="tab!=='brain'" :aria-hidden="tab!=='brain' ? true : undefined" :aria-label="t('brain')"><BrainRuntimeSettings /><CloudSettings ref="cloud" /></section>
    <section v-if="visited.has('device')" v-show="tab==='device'" :aria-label="t('device')" :inert="tab!=='device'" :aria-hidden="tab!=='device' ? true : undefined" class="settings-page device-settings">
     <div class="device-facts"><div class="device-model"><Cpu :size="20" aria-hidden="true" /><h3>{{ displayDeviceModel(device?.model)||device?.hostname||t('unknown') }}</h3><span v-if="deviceStale" class="console-label">{{ t('stale') }}</span></div><dl><div v-for="fact in [{key:'hostname',value:device?.hostname},{key:'os',value:device?.os},{key:'kernel',value:device?.kernel},{key:'version',value:device?.app_version}]" :key="fact.key"><dt>{{ t(fact.key) }}</dt><dd>{{ fact.value||'—' }}</dd></div></dl></div>
     <div v-for="item in [{key:'audio',icon:Mic},{key:'network',icon:Wifi},{key:'maintenance',icon:Wrench}]" :key="item.key" class="device-disclosure"><button type="button" :aria-expanded="opened===item.key" :aria-controls="`${id}-${item.key}`" @click="opened=opened===item.key?null:item.key"><component :is="item.icon" :size="18" :stroke-width="1.6" aria-hidden="true" /><span>{{ t(item.key) }}</span><ChevronDown :size="16" :class="{rotated:opened===item.key}" aria-hidden="true" /></button><div v-if="opened===item.key" :id="`${id}-${item.key}`" class="disclosure-content"><DeviceAudio v-if="item.key==='audio'" /><DeviceNetwork v-if="item.key==='network'" /><DeviceMaintenance v-if="item.key==='maintenance'" /></div></div>
    </section>
   </div>
  </div>
  </div>
  <div ref="dockHost" class="settings-dock-host"></div>
 </dialog>
</template>
<style scoped>
.console-settings { position:fixed; inset:0; width:100%; height:100dvh; max-width:none; max-height:none; margin:0; padding:0; overflow:visible; border:0; background:transparent; color:var(--text); }
.console-settings[open] { display:grid; place-items:center; }
.console-settings.is-closing { pointer-events:none; }
.console-settings::backdrop { background:rgb(10 18 8 / .4); backdrop-filter:blur(12px); }
.settings-panel { position:relative; display:flex; flex-direction:column; width:min(960px,calc(100vw - 64px)); height:min(800px,calc(100dvh - 80px)); overflow:hidden; border:1px solid var(--border); border-radius:30px; background:var(--surface); box-shadow:var(--shadow-float); transform-origin:center; }
.settings-dock-host { position:absolute; width:0; height:0; }
.settings-header { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:20px 24px 20px 30px; border-bottom:1px solid var(--border); flex-shrink:0; }
.settings-header h2 { display:flex; align-items:center; gap:11px; margin:0; font-size:18px; font-weight:550; letter-spacing:-.5px; }
.settings-header .ui-icon-button { border:0; background:var(--background); border-radius:50%; }
.settings-layout { display:grid; grid-template-columns:190px minmax(0,1fr); flex:1; min-height:0; }
.settings-rail { display:flex; flex-direction:column; min-height:0; background:var(--background); border-right:1px solid var(--border); }
.settings-tabs { flex:1; display:flex; flex-direction:column; padding:24px 14px; gap:8px; background:var(--background); }
.settings-tabs button { display:flex; align-items:center; gap:11px; min-height:48px; padding:12px 16px; border:1px solid transparent; border-radius:16px; background:transparent; color:var(--muted); text-align:start; font-size:13px; cursor:pointer; }
.settings-tabs button[aria-pressed="true"] { background:var(--surface); color:var(--text); border-color:var(--border); box-shadow:var(--shadow-soft); }
.settings-tabs button[aria-pressed="true"] svg { color:var(--accent); }
.settings-body { padding:28px 32px; overflow-y:auto; overscroll-behavior:contain; scrollbar-gutter:stable; }
.settings-page { width:100%; min-width:0; }
.settings-section-title { margin:0 0 12px; font-size:20px; font-weight:550; letter-spacing:-.5px; }
.discard-changes { display:grid; gap:16px; padding:18px; margin-bottom:24px; border:1px solid var(--border); border-radius:18px; background:var(--background); font-size:14px; }
.discard-changes > div { display:flex; flex-wrap:wrap; gap:10px; }
.device-model { display:flex; align-items:center; gap:10px; }
.device-model h3 { margin:0; font-size:16px; font-weight:550; overflow-wrap:anywhere; }
.device-model svg { flex-shrink:0; color:var(--muted); }
dl { display:grid; gap:16px; margin:24px 0 32px; }
dl > div { display:grid; grid-template-columns:80px 1fr; gap:16px; font-size:12px; }
dt { color:var(--muted); } dd { margin:0; text-align:right; overflow-wrap:anywhere; }
.device-disclosure { border-top:1px solid var(--border); }
.device-disclosure > button { display:flex; align-items:center; gap:12px; width:100%; min-height:65px; padding:16px 0; text-align:start; border:0; background:transparent; color:var(--text); cursor:pointer; font-size:14px; }
.device-disclosure > button span { flex:1; }
.device-disclosure > button svg { color:var(--muted); }
.rotated { transform:rotate(180deg); }
.disclosure-content { padding:8px 0 24px; }
.disclosure-content :deep(.audio-panel),.disclosure-content :deep(.network-card),.disclosure-content :deep(.maintenance-card) { padding:0; border:0; border-radius:0; box-shadow:none; }
.disclosure-content :deep(.ui-section-description),.disclosure-content :deep(.card-heading h2),.disclosure-content :deep(.section-heading h2),.disclosure-content :deep(.section-icon),.disclosure-content :deep(.ui-section-icon) { display:none; }
@media (max-width:639px) { .settings-panel { width:100%; height:100dvh; border:0; border-radius:0; } .settings-header { padding:calc(16px + env(safe-area-inset-top)) 20px 16px; } .settings-layout { display:flex; flex-direction:column; padding-bottom:76px; } .settings-rail { display:contents; } .settings-tabs { flex:0 0 auto; flex-direction:row; padding:12px 16px; gap:6px; border-right:0; border-bottom:1px solid var(--border); flex-shrink:0; } .settings-tabs button { flex:1; justify-content:center; padding:10px; gap:7px; font-size:12px; } .settings-body { flex:1; min-height:0; padding:24px 20px calc(32px + env(safe-area-inset-bottom)); } }
@media (prefers-reduced-motion:no-preference) {
 .console-settings[open] .settings-panel { animation:settings-open var(--motion-panel-open) var(--motion-ease-open) both; }
 .console-settings[open]::backdrop { animation:backdrop-open var(--motion-panel-open) var(--motion-ease-open) both; }
 .console-settings[open].is-closing .settings-panel { animation:settings-close var(--motion-panel-close) var(--motion-ease-close) both; }
 .console-settings[open].is-closing::backdrop { animation:backdrop-close var(--motion-panel-close) var(--motion-ease-close) both; }
 .rotated { transition:transform .3s var(--ease-out); }
 @keyframes settings-open { from { opacity:0; transform:var(--anchored-collapsed,none); } to { opacity:1; transform:none; } }
 @keyframes settings-close { from { opacity:var(--anchored-from-opacity,1); transform:var(--anchored-from-transform,none); } to { opacity:0; transform:var(--anchored-collapsed,none); } }
 @keyframes backdrop-open { from { opacity:0; } to { opacity:1; } }
 @keyframes backdrop-close { from { opacity:var(--settings-backdrop-opacity,1); } to { opacity:0; } }
}
</style>
