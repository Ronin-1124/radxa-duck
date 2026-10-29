<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDown, Cloud, Cpu, Mic, Settings2, UserRound, Wifi, Wrench, X } from '@lucide/vue'
import ConsolePreferences from './ConsolePreferences.vue'
import IdentitySettings from './IdentitySettings.vue'
import CloudSettings from './CloudSettings.vue'
import DeviceAudio from './DeviceAudio.vue'
import DeviceNetwork from './DeviceNetwork.vue'
import DeviceMaintenance from './DeviceMaintenance.vue'
import { displayDeviceModel } from './device-model'
import { useRobotConsole } from './useRobotConsole'
const props=defineProps<{initialTab:string;opener?:HTMLButtonElement}>()
const emit=defineEmits<{close:[]}>()
const id=useId(),dialog=ref<HTMLDialogElement>(),closeButton=ref<HTMLButtonElement>()
type Tab='identity'|'brain'|'device'
const tab=ref<Tab>(['identity','brain','device'].includes(props.initialTab)?props.initialTab as Tab:'identity')
const visited=ref(new Set<Tab>([tab.value]))
const role=ref<{hasUnsavedChanges:boolean;busy:boolean}>(),cloud=ref<{hasUnsavedChanges:boolean;busy:boolean}>()
const discard=ref(false)
const opened=ref<string|null>(null)
const { device,deviceStale }=useRobotConsole()
const { t }=useI18n({useScope:'local',messages:{
 'zh-CN':{settings:'设置',identity:'角色身份',brain:'大脑',device:'设备',close:'关闭设置',tabs:'设置分类',discardTitle:'放弃未保存的更改？',discard:'放弃并关闭',keep:'继续编辑',saving:'正在保存，请稍候',audio:'音频设备',network:'网络',maintenance:'系统维护',os:'系统',kernel:'内核',version:'应用版本',hostname:'主机名',unknown:'未报告',stale:'上次读数'},
 en:{settings:'Settings',identity:'Identity',brain:'Brain',device:'Device',close:'Close settings',tabs:'Settings sections',discardTitle:'Discard unsaved changes?',discard:'Discard and close',keep:'Keep editing',saving:'Saving. Please wait.',audio:'Audio devices',network:'Network',maintenance:'Maintenance',os:'System',kernel:'Kernel',version:'App version',hostname:'Hostname',unknown:'Not reported',stale:'Previous reading'}
}})
const busy=computed(()=>role.value?.busy||cloud.value?.busy)
function select(value:Tab) {tab.value=value;visited.value.add(value);discard.value=false}
async function close() {
 if(busy.value)return
 if(role.value?.hasUnsavedChanges||cloud.value?.hasUnsavedChanges){discard.value=true;await nextTick();dialog.value?.querySelector<HTMLButtonElement>('.keep-editing')?.focus();return}
 emit('close')
}
function cancel(event:Event){if(event.target!==dialog.value)return;event.preventDefault();void close()}
function backdrop(event:MouseEvent){if(event.target!==dialog.value)return;const r=dialog.value!.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)void close()}
watch(tab,()=>{const body=dialog.value?.querySelector('.settings-body');if(body)body.scrollTop=0})
let oldOverflow=''
onMounted(async()=>{oldOverflow=document.body.style.overflow;document.body.style.overflow='hidden';dialog.value?.showModal();await nextTick();closeButton.value?.focus()})
onBeforeUnmount(()=>{dialog.value?.close();document.body.style.overflow=oldOverflow;props.opener?.focus({preventScroll:true})})
</script>
<template>
 <dialog ref="dialog" class="console-settings" :aria-labelledby="`${id}-title`" @cancel="cancel" @click="backdrop">
  <header class="settings-header"><h2 :id="`${id}-title`"><Settings2 :size="20" :stroke-width="1.6" aria-hidden="true" />{{ t('settings') }}</h2><button ref="closeButton" type="button" class="ui-icon-button" :aria-label="t('close')" :disabled="busy" @click="close"><X :size="19" aria-hidden="true" /></button></header>
  <div class="settings-layout">
   <aside class="settings-rail"><nav class="settings-tabs" :aria-label="t('tabs')"><button v-for="item in [{key:'identity',icon:UserRound},{key:'brain',icon:Cloud},{key:'device',icon:Cpu}] as const" :key="item.key" type="button" :aria-pressed="tab===item.key" @click="select(item.key)"><component :is="item.icon" :size="18" :stroke-width="1.7" aria-hidden="true" />{{ t(item.key) }}</button></nav><div class="settings-preferences"><ConsolePreferences /></div></aside>
   <div class="settings-body">
    <section v-if="discard" class="discard-changes" role="alert"><strong>{{ t('discardTitle') }}</strong><div><button type="button" class="ui-button keep-editing" @click="discard=false">{{ t('keep') }}</button><button type="button" class="ui-button ui-button--danger" @click="emit('close')">{{ t('discard') }}</button></div></section>
    <section v-if="visited.has('identity')" v-show="tab==='identity'" :aria-label="t('identity')"><h3 class="settings-section-title">{{ t('identity') }}</h3><IdentitySettings ref="role" /></section>
    <section v-if="visited.has('brain')" v-show="tab==='brain'" :aria-label="t('brain')"><CloudSettings ref="cloud" /></section>
    <section v-if="visited.has('device')" v-show="tab==='device'" :aria-label="t('device')" class="device-settings">
     <div class="device-facts"><div class="device-model"><Cpu :size="20" aria-hidden="true" /><h3>{{ displayDeviceModel(device?.model)||device?.hostname||t('unknown') }}</h3><span v-if="deviceStale" class="console-label">{{ t('stale') }}</span></div><dl><div v-for="fact in [{key:'hostname',value:device?.hostname},{key:'os',value:device?.os},{key:'kernel',value:device?.kernel},{key:'version',value:device?.app_version}]" :key="fact.key"><dt>{{ t(fact.key) }}</dt><dd>{{ fact.value||'—' }}</dd></div></dl></div>
     <div v-for="item in [{key:'audio',icon:Mic},{key:'network',icon:Wifi},{key:'maintenance',icon:Wrench}]" :key="item.key" class="device-disclosure"><button type="button" :aria-expanded="opened===item.key" :aria-controls="`${id}-${item.key}`" @click="opened=opened===item.key?null:item.key"><component :is="item.icon" :size="18" :stroke-width="1.6" aria-hidden="true" /><span>{{ t(item.key) }}</span><ChevronDown :size="16" :class="{rotated:opened===item.key}" aria-hidden="true" /></button><div v-if="opened===item.key" :id="`${id}-${item.key}`" class="disclosure-content"><DeviceAudio v-if="item.key==='audio'" /><DeviceNetwork v-if="item.key==='network'" /><DeviceMaintenance v-if="item.key==='maintenance'" /></div></div>
    </section>
   </div>
  </div>
 </dialog>
</template>
<style scoped>
.console-settings { width:min(960px,calc(100vw - 64px)); height:min(800px,calc(100dvh - 80px)); max-width:none; max-height:none; margin:auto; padding:0; overflow:hidden; border:1px solid var(--border); border-radius:30px; background:var(--surface); color:var(--text); box-shadow:var(--shadow-float); }
.console-settings[open] { display:flex; flex-direction:column; }
.console-settings::backdrop { background:rgb(10 18 8 / .4); backdrop-filter:blur(12px); }
.settings-header { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:20px 24px 20px 30px; border-bottom:1px solid var(--border); flex-shrink:0; }
.settings-header h2 { display:flex; align-items:center; gap:11px; margin:0; font-size:18px; font-weight:550; letter-spacing:-.5px; }
.settings-header .ui-icon-button { border:0; background:var(--background); border-radius:50%; }
.settings-layout { display:grid; grid-template-columns:190px minmax(0,1fr); flex:1; min-height:0; }
.settings-rail { display:flex; flex-direction:column; min-height:0; background:var(--background); border-right:1px solid var(--border); }
.settings-preferences { margin:0 14px 20px; width:fit-content; padding:5px; border-radius:18px; border:1px solid var(--border); background:var(--surface); }
.settings-tabs { flex:1; display:flex; flex-direction:column; padding:24px 14px; gap:8px; background:var(--background); }
.settings-tabs button { display:flex; align-items:center; gap:11px; min-height:48px; padding:12px 16px; border:1px solid transparent; border-radius:16px; background:transparent; color:var(--muted); text-align:start; font-size:13px; cursor:pointer; }
.settings-tabs button[aria-pressed="true"] { background:var(--surface); color:var(--text); border-color:var(--border); box-shadow:var(--shadow-soft); }
.settings-tabs button[aria-pressed="true"] svg { color:var(--accent); }
.settings-body { padding:28px 32px; overflow-y:auto; overscroll-behavior:contain; scrollbar-gutter:stable; }
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
@media (max-width:639px) { .console-settings { width:100%; height:100dvh; border:0; border-radius:0; } .settings-header { padding:calc(16px + env(safe-area-inset-top)) 20px 16px; } .settings-layout { display:flex; flex-direction:column; padding-bottom:76px; } .settings-rail { display:contents; } .settings-preferences { position:absolute; left:20px; bottom:max(16px,env(safe-area-inset-bottom)); margin:0; z-index:2; } .settings-tabs { flex:0 0 auto; flex-direction:row; padding:12px 16px; gap:6px; border-right:0; border-bottom:1px solid var(--border); flex-shrink:0; } .settings-tabs button { flex:1; justify-content:center; padding:10px; gap:7px; font-size:12px; } .settings-body { flex:1; min-height:0; padding:24px 20px calc(32px + env(safe-area-inset-bottom)); } }
@media (prefers-reduced-motion:no-preference) { .console-settings[open] { animation:settings-open .5s var(--ease-out); } .console-settings[open]::backdrop { animation:backdrop-open .3s ease; } .rotated { transition:transform .3s var(--ease-out); } @keyframes settings-open { from { opacity:0; transform:translateY(20px) scale(.97); } to { opacity:1; transform:none; } } @keyframes backdrop-open { from { opacity:0; } to { opacity:1; } } }
</style>
