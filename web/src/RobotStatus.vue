<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Activity, Camera, Check, CircleHelp, Cloud, Cpu, MemoryStick, Mic, MicOff, Pause, Play, RefreshCw, Square, Thermometer, Zap } from '@lucide/vue'
import { displayDeviceModel } from './device-model'
import { cloudAnswerState, sensorState } from './capability-state'
import type { BrainStatus } from './console-protocol'
import { useRobotConsole } from './useRobotConsole'
import UiAlert from './UiAlert.vue'
type BrainSensor = BrainStatus['camera'] | undefined
const { device, brain, connection, deviceStale, brainStale, brainCheckedAt, fresh, unavailable, pending, controlError, outcomeUnknown, refreshing, refresh, control, reconcile } = useRobotConsole()
const { t, locale } = useI18n({useScope:'local',messages:{
 'zh-CN':{name:'小鸭',robot:'RADXA DUCK',unknown:'状态未知',waiting:'等待大脑接入',starting:'正在启动',paused:'交互已暂停',stopped:'大脑未运行',error:'需要留意',idle:'自在待命',listening:'正在听你说',thinking:'正在思考',speaking:'正在回答',acting:'正在执行',brain:'大脑',running:'运行中',notConnected:'尚未接入',previous:'上次同步 {time}',read:'刷新状态',pause:'暂停交互',resume:'恢复交互',mute:'关闭麦克风',unmute:'开启麦克风',cancel:'停止当前任务',pending:'等待确认',mic:'麦克风',camera:'摄像头',cloud:'云端模型',motion:'运动控制',activeMic:'正在收音',activeCamera:'正在采集',enabled:'已启用 · 空闲',disabled:'未启用',unavailable:'能力不可用',ready:'就绪',busy:'执行中',unconfigured:'未配置',notReported:'未报告',cpu:'CPU',memory:'内存',temperature:'温度',uptime:'已运行',day:'天',hour:'小时',minute:'分钟',current:'当前任务',latest:'最近任务',succeeded:'已完成',failed:'未完成',cancelled:'已取消',taskRunning:'执行中',cancelling:'正在停止',stale:'状态暂未更新',disconnected:'网页已断连，机器人运行状态未知',controlUnknown:'操作结果未确认，请重新读取状态。',controlFailure:'操作未完成，请刷新状态后重试。',query:'重新读取',deviceStale:'设备指标为上次读数',brainPending:'大脑状态待接入',warning:'异常',verified:'回答已验证',verificationPending:'已配置 · 待验证',verificationFailed:'模型调用失败'},
 en:{name:'Duck',robot:'RADXA DUCK',unknown:'State unknown',waiting:'Awaiting brain',starting:'Starting up',paused:'Interaction paused',stopped:'Brain stopped',error:'Needs attention',idle:'At ease',listening:'Listening to you',thinking:'Thinking',speaking:'Responding',acting:'In motion',brain:'Brain',running:'Running',notConnected:'Not connected',previous:'Last synced {time}',read:'Refresh status',pause:'Pause interaction',resume:'Resume interaction',mute:'Disable microphone',unmute:'Enable microphone',cancel:'Stop current task',pending:'Awaiting confirmation',mic:'Microphone',camera:'Camera',cloud:'Cloud model',motion:'Motion',activeMic:'Capturing audio',activeCamera:'Capturing frames',enabled:'Enabled · idle',disabled:'Not enabled',unavailable:'Capability unavailable',ready:'Ready',busy:'Executing',unconfigured:'Not configured',notReported:'Not reported',cpu:'CPU',memory:'Memory',temperature:'Temperature',uptime:'Uptime',day:'d',hour:'h',minute:'m',current:'Current task',latest:'Last task',succeeded:'Completed',failed:'Failed',cancelled:'Cancelled',taskRunning:'In progress',cancelling:'Stopping',stale:'Status is out of date',disconnected:'Web disconnected. Robot state is unknown.',controlUnknown:'Outcome unconfirmed. Read the current state.',controlFailure:'The operation could not be completed. Refresh and retry.',query:'Read state',deviceStale:'Device metrics are from the last reading',brainPending:'Brain status pending',warning:'Error',verified:'Answer verified',verificationPending:'Configured · unverified',verificationFailed:'Model call failed'}
}})
const phase = computed(() => !fresh.value ? unavailable.value && !brain.value ? 'waiting' : 'unknown' : brain.value!.state === 'running' ? brain.value!.activity : brain.value!.state)
const name = computed(() => brain.value?.identity.name || t('name'))
const interactionAction = computed(() => brain.value?.controls.resume ? 'resume' : brain.value?.controls.pause ? 'pause' : null)
const capabilities = computed(() => {
 const b = fresh.value ? brain.value : null
 const sensorText = (sensor: BrainSensor, active: string) => {
  const state = sensorState(sensor)
  return t(state === 'active' ? active : state)
 }
 return [
  {key:'mic',icon:Mic,value:sensorText(b?.microphone,'activeMic'),active:b?.microphone.active},
  {key:'camera',icon:Camera,value:sensorText(b?.camera,'activeCamera'),active:b?.camera.active},
  {key:'cloud',icon:Cloud,value:t(cloudAnswerState(b?.cloud)),detail:b?.cloud.configured?b.cloud.model:null,active:b?.cloud.health === 'ready'},
  {key:'motion',icon:Zap,value:!b ? t('unknown') : !b.motion.available ? t('unavailable') : t(b.motion.state === 'error' ? 'warning' : b.motion.state),active:b?.motion.state === 'busy'},
 ]
})
const n = (v:number) => new Intl.NumberFormat(locale.value,{maximumFractionDigits:1}).format(v)
const metrics = computed(() => {
 const d = device.value
 return [
  {key:'cpu',icon:Cpu,value:d?.cpu.usage_percent == null ? '—' : `${n(d.cpu.usage_percent)}%`,percent:d?.cpu.usage_percent},
  {key:'memory',icon:MemoryStick,value:d?.memory.used_bytes == null ? '—' : `${n(d.memory.used_bytes / 1048576)} MB`,percent:d?.memory.used_bytes != null && d.memory.total_bytes ? d.memory.used_bytes / d.memory.total_bytes * 100 : null},
  {key:'temperature',icon:Thermometer,value:d?.temperature.celsius == null ? '—' : `${n(d.temperature.celsius)}°C`,percent:null},
 ]
})
const uptime = computed(() => { const s=device.value?.uptime_seconds; if(s==null)return '—'; const m=Math.floor(s/60),h=Math.floor(m/60),d=Math.floor(h/24); return d ? `${d} ${t('day')} ${h%24} ${t('hour')}` : h ? `${h} ${t('hour')} ${m%60} ${t('minute')}` : `${m} ${t('minute')}` })
const synced = computed(() => brainCheckedAt.value ? t('previous',{time:new Intl.DateTimeFormat(locale.value,{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(brainCheckedAt.value)}) : '')
</script>
<template>
 <div class="console-page status-page">
  <section class="presence-panel console-panel" :data-phase="phase" aria-labelledby="robot-name">
   <div class="presence-top"><span class="console-label">{{ t('robot') }}</span><span class="brain-badge" :class="{ healthy:fresh && brain?.state === 'running' }"><span aria-hidden="true"></span>{{ t('brain') }}<span class="badge-separator">·</span>{{ !fresh ? t(unavailable ? 'notConnected' : 'unknown') : t(brain?.state === 'running' ? 'running' : brain!.state) }}</span></div>
   <div class="presence-content">
    <div class="presence-copy"><h1 id="robot-name">{{ name }}</h1><p class="robot-phase" role="status">{{ t(phase) }}</p><p class="sync-time">{{ synced || (displayDeviceModel(device?.model) || device?.hostname || '—') }}</p>
     <div v-if="fresh" class="brain-controls">
      <button v-if="interactionAction" class="ui-button" type="button" :disabled="!!pending || outcomeUnknown" @click="control(interactionAction)"><component :is="interactionAction === 'resume' ? Play : Pause" :size="16" aria-hidden="true" />{{ t(pending === 'pause' || pending === 'resume' ? 'pending' : interactionAction) }}</button>
      <button v-if="brain?.microphone.enabled ? brain.controls.disable_microphone : brain?.controls.enable_microphone" class="ui-icon-button" type="button" :disabled="!!pending || outcomeUnknown" :aria-label="t(brain?.microphone.enabled ? 'mute' : 'unmute')" :title="t(brain?.microphone.enabled ? 'mute' : 'unmute')" @click="control(brain!.microphone.enabled ? 'disable_microphone' : 'enable_microphone')"><component :is="brain?.microphone.enabled ? MicOff : Mic" :size="18" aria-hidden="true" /></button>
     </div>
     <button v-else type="button" class="status-refresh ui-button" :disabled="refreshing" @click="refresh(true)"><RefreshCw :size="15" :class="{spinning:refreshing}" aria-hidden="true" />{{ t('read') }}</button>
    </div>
    <div class="presence-art" aria-hidden="true"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="orbit-trace"></div><div class="presence-shadow"></div><div class="presence-orb"><div class="orb-highlight"></div><div class="orb-face"><span></span><span></span></div><div class="orb-mouth"></div></div><span class="orbit-dot"></span></div>
   </div>
   <div class="capability-strip"><div v-for="item in capabilities" :key="item.key" class="capability-item" :title="item.detail ? `${item.value} · ${item.detail}` : item.value"><span class="capability-icon" :class="{active:item.active}"><component :is="item.icon" :size="19" :stroke-width="1.6" aria-hidden="true" /></span><div><span class="console-label">{{ t(item.key) }}</span><strong>{{ item.value }}</strong><span v-if="item.detail" class="capability-detail">{{ item.detail }}</span></div></div></div>
  </section>
  <UiAlert v-if="connection === 'offline' || brainStale && brain" tone="info" role="status" class="console-state-notice">{{ connection === 'offline' ? t('disconnected') : t('stale') }}</UiAlert>
  <UiAlert v-if="controlError" role="alert"><span>{{ t(outcomeUnknown ? 'controlUnknown' : 'controlFailure') }}</span><button type="button" class="ui-button" :disabled="refreshing" @click="reconcile">{{ t('query') }}</button></UiAlert>
  <section v-if="brain?.task && fresh" class="task-ribbon console-panel" :aria-label="t(['running','cancelling'].includes(brain.task.state) ? 'current' : 'latest')"><span class="task-icon"><component :is="brain.task.state === 'running' ? Activity : brain.task.state === 'succeeded' ? Check : CircleHelp" :size="19" aria-hidden="true" /></span><div><span class="console-label">{{ t(['running','cancelling'].includes(brain.task.state) ? 'current' : 'latest') }}</span><p>{{ brain.task.summary || '—' }}</p></div><span class="task-state">{{ t(brain.task.state === 'running' ? 'taskRunning' : brain.task.state) }}</span><button v-if="brain.task.state === 'running' && brain.controls.cancel_task" type="button" class="ui-icon-button" :aria-label="t('cancel')" :title="t('cancel')" :disabled="!!pending || outcomeUnknown" @click="control('cancel_task')"><Square :size="16" aria-hidden="true" /></button></section>
  <section class="device-metrics" :aria-label="displayDeviceModel(device?.model) || device?.hostname || 'Radxa Duck'" :data-stale="deviceStale">
   <div class="device-identity"><Cpu :size="20" :stroke-width="1.5" aria-hidden="true" /><div><strong>{{ displayDeviceModel(device?.model) || device?.hostname || '—' }}</strong><span>{{ deviceStale ? t('deviceStale') : device?.hostname || '—' }}</span></div></div>
   <div v-for="metric in metrics" :key="metric.key" class="device-metric"><span class="console-label"><component :is="metric.icon" :size="13" aria-hidden="true" />{{ t(metric.key) }}</span><strong>{{ metric.value }}</strong><span v-if="metric.percent != null" class="metric-track" aria-hidden="true"><i :style="{transform:`scaleX(${Math.min(1,metric.percent/100)})`}"></i></span></div>
   <div class="device-metric"><span class="console-label">{{ t('uptime') }}</span><strong>{{ uptime }}</strong></div>
  </section>
 </div>
</template>
<style scoped>
.status-page { display:grid; gap:20px; }
.presence-panel { overflow:hidden; padding:28px 36px 0; border-radius:32px; }
.presence-top { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.presence-top > .console-label { font-size:11px; letter-spacing:.18em; }
.brain-badge { display:inline-flex; align-items:center; gap:7px; padding:7px 11px; border-radius:999px; border:1px solid var(--border); color:var(--muted); font-size:12px; }
.brain-badge > span:first-child { width:5px; height:5px; border-radius:50%; background:var(--status); }
.brain-badge.healthy > span:first-child { background:var(--online); }
.badge-separator { opacity:.5; }
.presence-content { display:grid; grid-template-columns:1fr 1fr; align-items:center; min-height:352px; padding:22px 28px 32px; gap:24px; }
h1 { margin:0; font-size:clamp(32px,4vw,48px); line-height:1.2; font-weight:550; letter-spacing:-1.5px; overflow-wrap:anywhere; }
.robot-phase { font-size:22px; line-height:1.5; margin:17px 0 0; font-weight:450; letter-spacing:-.6px; }
.sync-time { margin:12px 0 0; color:var(--muted); font-size:12px; min-height:18px; }
.brain-controls { display:flex; flex-wrap:wrap; gap:10px; margin-top:30px; }
.status-refresh { margin-top:28px; background:transparent; }
.presence-art { position:relative; width:300px; height:300px; justify-self:center; }
.presence-orb { position:absolute; width:164px; height:164px; left:68px; top:61px; border-radius:50%; background:radial-gradient(circle at 35% 25%,var(--orb-light),var(--orb-mid) 55%,var(--orb-deep)); box-shadow:inset 2px 3px 8px rgb(255 255 255 / .6),inset -5px -10px 20px color-mix(in srgb,var(--accent) 12%,transparent),0 18px 38px -24px color-mix(in srgb,var(--accent) 60%,transparent); }
.orb-highlight { position:absolute; inset:8px; border-radius:inherit; border-top:1px solid rgb(255 255 255 / .5); transform:rotate(-25deg); }
.orb-face { display:flex; justify-content:center; gap:29px; position:absolute; top:70px; width:100%; }
.orb-face span { height:15px; width:8px; border-radius:10px; background:var(--orb-eye); box-shadow:0 1px 1px rgb(255 255 255 / .3); }
.orb-mouth { position:absolute; left:75px; top:101px; width:15px; height:5px; border-radius:0 0 12px 12px; background:color-mix(in srgb,var(--orb-eye) 60%,transparent); }
.presence-shadow { position:absolute; width:110px; height:14px; border-radius:50%; background:color-mix(in srgb,var(--accent) 12%,transparent); filter:blur(10px); left:95px; bottom:27px; }
.orbit { position:absolute; border:1px solid color-mix(in srgb,var(--accent) 8%,transparent); border-radius:50%; inset:18px; }
.orbit-two { inset:-14px; border-style:dashed; border-color:color-mix(in srgb,var(--accent) 7%,transparent); }
.orbit-trace { position:absolute; inset:45px; border:1px solid color-mix(in srgb,var(--accent) 10%,transparent); border-radius:50%; transform:rotateX(65deg) rotate(-25deg); }
.orbit-dot { position:absolute; left:30px; top:65px; width:7px; height:7px; border-radius:50%; background:var(--accent); box-shadow:0 0 0 6px var(--accent-soft); opacity:.6; }
[data-phase="unknown"] .presence-art,[data-phase="waiting"] .presence-art,[data-phase="stopped"] .presence-art { filter:grayscale(.82); opacity:.65; }
[data-phase="paused"] .orb-face span { height:4px; margin-top:6px; }
[data-phase="thinking"] .orb-face { transform:translate(6px,-6px); }
[data-phase="speaking"] .orb-mouth { height:11px; border-radius:50%; }
.capability-strip { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border-top:1px solid var(--border); gap:24px; padding:25px 0; }
.capability-item { display:flex; gap:11px; align-items:center; min-width:0; }
.capability-icon { display:grid; place-items:center; width:38px; height:38px; border-radius:13px; flex-shrink:0; color:var(--muted); background:var(--background); }
.capability-icon.active { color:var(--accent); background:var(--accent-soft); }
.capability-item > div { min-width:0; }
.capability-item strong { display:block; font-size:13px; font-weight:500; line-height:1.5; margin-top:6px; overflow-wrap:anywhere; }
.capability-detail { display:block; margin-top:5px; color:var(--muted); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.device-metrics { display:flex; align-items:center; justify-content:space-between; gap:28px; padding:14px 26px; }
.device-identity { display:flex; gap:12px; align-items:center; color:var(--muted); max-width:30%; }
.device-identity strong { font-size:12px; font-weight:500; display:block; color:var(--text); line-height:1.6; overflow-wrap:anywhere; }
.device-identity span { font-size:11px; margin-top:4px; display:block; }
.device-metric { min-width:60px; position:relative; }
.device-metric .console-label { display:flex; align-items:center; gap:5px; font-size:11px; }
.device-metric strong { display:block; font-size:14px; font-weight:500; margin-top:9px; white-space:nowrap; }
.metric-track { display:block; width:65px; height:2px; background:var(--border); margin-top:8px; border-radius:2px; overflow:hidden; }
.metric-track i { display:block; width:100%; height:100%; background:var(--accent); transform-origin:left; }
[data-stale="true"] .device-metric { opacity:.6; }
.task-ribbon { display:flex; align-items:center; gap:16px; padding:20px 26px; }
.task-icon { color:var(--accent); }
.task-ribbon > div { flex:1; min-width:0; }
.task-ribbon p { margin:6px 0 0; font-size:13px; line-height:1.6; white-space:pre-wrap; overflow-wrap:anywhere; }
.task-state { color:var(--muted); font-size:12px; }
@media (min-width:1440px) { .presence-content { min-height:410px; } }
@media (max-width:799px) { .presence-panel { padding:24px 24px 0; } .presence-content { padding-inline:8px; } .presence-art { transform:scale(.85); } .capability-strip { gap:16px; grid-template-columns:repeat(2,minmax(0,1fr)); } .device-metrics { flex-wrap:wrap; padding-inline:12px; gap:24px; } .device-identity { max-width:100%; width:100%; } }
@media (max-width:539px) { .presence-panel { padding:20px 20px 0; border-radius:28px; } .presence-content { display:flex; flex-direction:column-reverse; gap:0; padding:0 0 28px; } .presence-art { width:300px; height:210px; transform:scale(.72); margin-block:-24px -30px; flex-shrink:0; } .presence-copy { text-align:center; } h1 { font-size:32px; } .robot-phase { font-size:20px; margin-top:12px; } .brain-controls { justify-content:center; margin-top:22px; } .status-refresh { margin-top:20px; } .presence-top > .console-label { font-size:9px; } .brain-badge { font-size:11px; padding:6px 9px; } .capability-strip { padding-block:20px; gap:22px 10px; } .capability-icon { width:32px; height:32px; border-radius:11px; } .capability-item { gap:9px; } .capability-item strong { font-size:12px; } .console-label { font-size:11px; } .device-metrics { display:grid; grid-template-columns:repeat(2,1fr); padding:14px 12px; } .device-identity { grid-column:1/-1; } .task-ribbon { padding:18px; gap:12px; } .task-state { display:none; } }
@media (prefers-reduced-motion:no-preference) { .presence-orb { animation:orb-float 5s ease-in-out infinite; } .presence-shadow { animation:shadow-breathe 5s ease-in-out infinite; } .orbit-dot { animation:orbit-glow 4s ease-in-out infinite; } [data-phase="unknown"] .presence-orb,[data-phase="waiting"] .presence-orb,[data-phase="stopped"] .presence-orb,[data-phase="paused"] .presence-orb { animation:none; } [data-phase="unknown"] .presence-shadow,[data-phase="waiting"] .presence-shadow,[data-phase="stopped"] .presence-shadow,[data-phase="paused"] .presence-shadow { animation:none; } [data-phase="listening"] .orbit-one,[data-phase="thinking"] .orbit-one { animation:ring-breathe 2s ease-in-out infinite; } .spinning { animation:spin 1.5s linear infinite; } .metric-track i { transition:transform .8s var(--ease-out); } @keyframes orb-float { 50% { transform:translateY(-12px) rotate(3deg); } } @keyframes shadow-breathe { 50% { transform:scale(.8); opacity:.55; } } @keyframes ring-breathe { 50% { transform:scale(1.035); border-color:color-mix(in srgb,var(--accent) 22%,transparent); } } @keyframes orbit-glow { 50% { opacity:.3; } } @keyframes spin { to { transform:rotate(360deg); } } }
</style>
