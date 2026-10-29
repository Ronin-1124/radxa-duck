<script setup lang="ts">
import { computed, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { Camera, Mic, RefreshCw } from '@lucide/vue'
import { useRobotConsole } from './useRobotConsole'
import { sensorState } from './capability-state'
import UiAlert from './UiAlert.vue'

const id = useId()
const { brain, fresh, pending, controlError, outcomeUnknown, refreshing, control, reconcile } = useRobotConsole()
const { t } = useI18n({ useScope: 'local', messages: {
  'zh-CN': { title: '运行能力', microphone: '语音交互', camera: '常驻视觉', scope: '与网页预览独立',
    unknown: '状态未知', unavailable: '能力不可用', active: '正在采集', enabled: '已启用 · 空闲', disabled: '未启用',
    waiting: '等待板端确认', unsupported: '当前服务不支持切换', failure: '操作未完成，请重新读取状态。',
    uncertain: '操作结果未确认，请重新读取状态。', read: '重新读取' },
  en: { title: 'Runtime capabilities', microphone: 'Voice interaction', camera: 'Resident vision', scope: 'Independent of web preview',
    unknown: 'State unknown', unavailable: 'Capability unavailable', active: 'Capturing', enabled: 'Enabled · idle', disabled: 'Not enabled',
    waiting: 'Awaiting device confirmation', unsupported: 'Switching is not supported by this service',
    failure: 'Operation failed. Read the current state.', uncertain: 'Outcome unconfirmed. Read the current state.', read: 'Read state' },
} })
const rows = computed(() => (['microphone', 'camera'] as const).map(key => {
  const sensor = brain.value?.[key]
  const action = sensor?.enabled ? `disable_${key}` as const : `enable_${key}` as const
  const supported = typeof brain.value?.controls[action] === 'boolean'
  return { key, icon: key === 'camera' ? Camera : Mic, sensor, action, supported,
    value: pending.value === action ? 'waiting' : !supported && fresh.value ? 'unsupported' : sensorState(fresh.value ? sensor : undefined),
    disabled: !fresh.value || !!pending.value || outcomeUnknown.value || !brain.value?.controls[action] }
}))
</script>

<template>
  <section class="runtime-settings" :aria-labelledby="`${id}-title`">
    <h3 :id="`${id}-title`">{{ t('title') }}</h3>
    <div v-for="row in rows" :key="row.key" class="runtime-row">
      <span class="runtime-icon"><component :is="row.icon" :size="19" :stroke-width="1.6" aria-hidden="true" /></span>
      <div class="runtime-copy"><span :id="`${id}-${row.key}`">{{ t(row.key) }}</span><p :id="`${id}-${row.key}-status`" role="status">{{ t(row.value) }}</p><span v-if="row.key==='camera'" class="runtime-scope">{{ t('scope') }}</span></div>
      <button class="runtime-switch" type="button" role="switch" :aria-labelledby="`${id}-${row.key}`" :aria-describedby="`${id}-${row.key}-status`" :aria-checked="!!row.sensor?.enabled" :aria-busy="pending===row.action" :disabled="row.disabled" @click="control(row.action)"><span aria-hidden="true"></span></button>
    </div>
    <UiAlert v-if="controlError" role="alert"><span>{{ t(outcomeUnknown?'uncertain':'failure') }}</span><button class="ui-button" type="button" :disabled="refreshing" @click="reconcile"><RefreshCw :size="15" aria-hidden="true" />{{ t('read') }}</button></UiAlert>
  </section>
</template>

<style scoped>
.runtime-settings { margin-bottom:32px; padding-bottom:28px; border-bottom:1px solid var(--border); }
h3 { margin:0 0 10px; font-size:15px; font-weight:550; }
.runtime-row { display:flex; align-items:center; gap:14px; padding-block:16px; }
.runtime-icon { display:grid; place-items:center; width:40px; height:40px; flex-shrink:0; border-radius:14px; background:var(--background); color:var(--accent); }
.runtime-copy { flex:1; min-width:0; font-size:14px; }
.runtime-copy p { margin:6px 0 0; color:var(--muted); font-size:12px; line-height:1.6; overflow-wrap:anywhere; }
.runtime-scope { display:block; color:var(--muted); font-size:11px; line-height:1.6; margin-top:3px; }
.runtime-switch { display:flex; align-items:center; width:56px; height:44px; flex-shrink:0; padding:7px 0; border:0; border-radius:14px; background:transparent; cursor:pointer; touch-action:manipulation; }
.runtime-switch::before { content:''; position:absolute; width:56px; height:30px; border:1px solid var(--muted); border-radius:20px; background:var(--background); }
.runtime-switch { position:relative; }
.runtime-switch > span { position:relative; width:22px; height:22px; margin-left:4px; border-radius:50%; background:var(--muted); }
.runtime-switch[aria-checked="true"]::before { border-color:var(--accent); background:var(--accent); }
.runtime-switch[aria-checked="true"] > span { transform:translateX(26px); background:var(--surface); }
.runtime-switch:disabled { opacity:.5; cursor:default; }
.runtime-switch:focus-visible { outline:2px solid var(--focus); outline-offset:4px; }
@media (prefers-reduced-motion:no-preference) { .runtime-switch { transition:transform .2s var(--ease-out); } .runtime-switch > span { transition:transform .25s var(--ease-out),background .25s; } .runtime-switch::before { transition:background .25s,border-color .25s; } }
@media (hover:hover) and (pointer:fine) and (prefers-reduced-motion:no-preference) { .runtime-switch:not(:disabled):hover { transform:translateY(-2px); } .runtime-switch:not(:disabled):active { transform:none; } }
</style>
