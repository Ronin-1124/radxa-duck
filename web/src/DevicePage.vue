<script setup lang="ts">
import { computed, useId, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Activity, ArrowUpRight, Camera, ChevronDown, Cpu, HardDrive,
  MemoryStick, Mic, RefreshCw, ShieldCheck, Thermometer } from '@lucide/vue'
import { useDeviceStatus } from './useDeviceStatus'
import type { VisionModel } from './vision-types'
import DeviceAudio from './DeviceAudio.vue'
import DeviceNetwork from './DeviceNetwork.vue'
import DeviceMaintenance from './DeviceMaintenance.vue'
import UiAlert from './UiAlert.vue'

const emit = defineEmits<{ connection: [value: 'connecting' | 'online' | 'offline'] }>()
const id = useId()
const { device, speech, vision, refreshing, checkedAt, refresh, connection } = useDeviceStatus()
const { t, locale } = useI18n({
  useScope: 'local', inheritLocale: true,
  messages: {
    'zh-CN': {
      hardware: '当前设备', model: 'Radxa Duck', hardwareHint: '设备名称尚未读取',
      intro: '查看小鸭的运行服务，管理网络、音频与系统。',
      refresh: '刷新状态', refreshing: '正在刷新', neverChecked: '尚未完成状态检查', checked: '最近检查 {time}',
      reading: '正在读取服务状态', servicesRead: '已读取 {count} / 2 项服务状态',
      serviceHint: '这里只查询服务状态，不会开启麦克风或摄像头。',
      resources: '系统资源', pending: '等待状态', unavailable: '未提供',
      resourceHint: '按板端采样展示，缺失指标显示为 —。',
      sampled: '采样于 {time}', previousSample: '上次读取于 {time}', telemetryFailed: '整机状态读取失败，已有数据为上次结果。',
      sampleWindow: '采样间隔 {seconds} 秒', firstSample: '等待有效 CPU 采样', availableMemory: '可用 {value}',
      usage: '{used} / {total}', daysHours: '{days} 天 {hours} 小时', hoursMinutes: '{hours} 小时 {minutes} 分钟',
      metrics: { cpu: 'CPU 使用率', memory: '内存', storage: '存储空间', temperature: '温度' },
      identity: '系统信息', identityHint: '来自设备当前运行环境。',
      facts: { hostname: '主机名称', os: '操作系统', kernel: '内核版本', uptime: '运行时长', version: '应用版本' },
      services: '交互服务', servicesHint: '查看服务是否就绪，以及当前任务状态。',
      speech: { title: '语音识别', description: '在板端 CPU 上处理语音。', link: '前往语音', source: '任务状态', sourceHint: '请在语音页主动开启收音。' },
      vision: { title: '视觉交互', description: '摄像头预览与 NPU 目标检测。', link: '前往视觉', models: '可用检测模型', noModels: '暂无检测模型', size: '预览采集尺寸', sourceHint: '状态检查不代表摄像头已通过采集验证。' },
      states: { loading: '正在加载', idle: '待机', starting: '正在启动', listening: '正在收音', streaming: '正在采集', stopping: '正在停止', error: '服务异常', closed: '服务已停止', notReady: '尚未就绪' },
      readFailed: '状态读取失败', waiting: '等待状态', stale: '上次读取：{state} · {time}',
      errors: { network: '无法读取服务状态，请检查设备连接后重试。', timeout: '设备响应超时，请稍后重试。', invalid: '设备返回的状态暂时无法识别。' },
      privacy: '交互由你开启', privacyHint: '设备页仅查看状态；录音、图片与会话不长期保存。',
    },
    en: {
      hardware: 'Current device', model: 'Radxa Duck', hardwareHint: 'Device name not reported',
      intro: 'Check your Duck’s services and manage its network, audio and system.',
      refresh: 'Refresh status', refreshing: 'Refreshing', neverChecked: 'No status check completed yet', checked: 'Last checked {time}',
      reading: 'Reading service status', servicesRead: '{count} of 2 service states received',
      serviceHint: 'Status checks do not start the microphone or camera.',
      resources: 'System resources', pending: 'Waiting for status', unavailable: 'Not reported',
      resourceHint: 'Sampled on the board. Missing measurements appear as —.',
      sampled: 'Sampled at {time}', previousSample: 'Previously read at {time}', telemetryFailed: 'Device status could not be read. Existing values are from the previous check.',
      sampleWindow: '{seconds}s sampling interval', firstSample: 'Waiting for a valid CPU sample', availableMemory: '{value} available',
      usage: '{used} / {total}', daysHours: '{days}d {hours}h', hoursMinutes: '{hours}h {minutes}m',
      metrics: { cpu: 'CPU usage', memory: 'Memory', storage: 'Storage', temperature: 'Temperature' },
      identity: 'System information', identityHint: 'Reported by the running device.',
      facts: { hostname: 'Hostname', os: 'Operating system', kernel: 'Kernel version', uptime: 'Uptime', version: 'App version' },
      services: 'Interaction services', servicesHint: 'Check service readiness and current task status.',
      speech: { title: 'Speech recognition', description: 'Speech processing on the board’s CPU.', link: 'Open Voice', source: 'Task state', sourceHint: 'Start listening from the Voice page.' },
      vision: { title: 'Vision interaction', description: 'Camera preview and NPU object detection.', link: 'Open Vision', models: 'Available detection models', noModels: 'No detection models available', size: 'Preview capture size', sourceHint: 'A status check does not verify camera capture.' },
      states: { loading: 'Loading', idle: 'Idle', starting: 'Starting', listening: 'Listening', streaming: 'Capturing', stopping: 'Stopping', error: 'Service error', closed: 'Service stopped', notReady: 'Not ready' },
      readFailed: 'Status check failed', waiting: 'Waiting for status', stale: 'Previously: {state} · {time}',
      errors: { network: 'Unable to read service status. Check the device connection and retry.', timeout: 'The device took too long to respond. Please retry.', invalid: 'The device returned an unrecognized status.' },
      privacy: 'You start every interaction', privacyHint: 'This page only checks status. Recordings, images and conversations are temporary.',
    },
  },
})

const telemetry = computed(() => device.value.data)
const metrics = computed(() => {
  const data = telemetry.value
  return [
    { name: 'cpu', icon: Activity, value: data?.cpu.usage_percent == null ? '—' : `${number(data.cpu.usage_percent)}%`,
      hint: data?.cpu.sample_seconds == null ? t('firstSample') : t('sampleWindow', { seconds: number(data.cpu.sample_seconds) }) },
    { name: 'memory', icon: MemoryStick, value: formatBytes(data?.memory.used_bytes),
      hint: t('usage', { used: formatBytes(data?.memory.used_bytes), total: formatBytes(data?.memory.total_bytes) }),
      extra: t('availableMemory', { value: formatBytes(data?.memory.available_bytes) }) },
    { name: 'storage', icon: HardDrive, value: formatBytes(data?.storage.used_bytes),
      hint: t('usage', { used: formatBytes(data?.storage.used_bytes), total: formatBytes(data?.storage.total_bytes) }),
      extra: t('availableMemory', { value: formatBytes(data?.storage.available_bytes) }) },
    { name: 'temperature', icon: Thermometer, value: data?.temperature.celsius == null ? '—' : `${number(data.temperature.celsius)} °C`,
      hint: data?.temperature.source ?? t('unavailable') },
  ]
})
const facts = computed(() => [
  { name: 'hostname', value: telemetry.value?.hostname },
  { name: 'os', value: telemetry.value?.os },
  { name: 'kernel', value: telemetry.value?.kernel },
  { name: 'uptime', value: formatUptime(telemetry.value?.uptime_seconds) },
  { name: 'version', value: telemetry.value?.app_version },
])
const sampleTime = computed(() => {
  if (!device.value.checkedAt) return t('pending')
  const sampled = telemetry.value?.sampled_at ? Date.parse(telemetry.value.sampled_at) : NaN
  return t(device.value.error ? 'previousSample' : 'sampled', { time: formatTime(Number.isFinite(sampled) ? sampled : device.value.checkedAt) })
})
function number(value: number): string { return new Intl.NumberFormat(locale.value, { maximumFractionDigits: 1 }).format(value) }
function formatBytes(value: number | null | undefined): string {
  if (value == null) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  const index = value === 0 ? 0 : Math.min(4, Math.floor(Math.log(value) / Math.log(1024)))
  return `${number(value / 1024 ** index)} ${units[index]}`
}
function formatUptime(value: number | null | undefined): string | null {
  if (value == null) return null
  const minutes = Math.floor(value / 60), hours = Math.floor(minutes / 60), days = Math.floor(hours / 24)
  return days > 0 ? t('daysHours', { days, hours: hours % 24 }) : t('hoursMinutes', { hours, minutes: minutes % 60 })
}
const modelLabels: Record<VisionModel, string> = { yolov8n: 'YOLOv8n', yolov5n: 'YOLOv5n', yolo26n: 'YOLO26n', yolo26s: 'YOLO26s' }
const receivedCount = computed(() => [speech.value, vision.value].filter(resource => resource.data && !resource.error).length)
const lastCheck = computed(() => checkedAt.value ? t('checked', { time: formatTime(checkedAt.value) }) : t('neverChecked'))
const models = computed(() => vision.value.data?.capabilities.available_models.map(model => modelLabels[model]).join(' · ') ?? '')
const services = computed(() => [
  { key: 'speech', route: 'voice', icon: Mic, processor: 'CPU', resource: speech.value },
  { key: 'vision', route: 'vision', icon: Camera, processor: 'NPU', resource: vision.value },
] as const)

function formatTime(value: number): string {
  return new Intl.DateTimeFormat(locale.value, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(value)
}
function statusText(resource: { data: { state: string; ready: boolean } | null; error: string | null }): string {
  if (resource.error) return t('readFailed')
  if (!resource.data) return t('waiting')
  if (!resource.data.ready && resource.data.state === 'idle') return t('states.notReady')
  return t(`states.${resource.data.state}`)
}
function statusTone(resource: { data: { state: string; ready: boolean } | null; error: string | null }): string {
  if (resource.error || resource.data?.state === 'error') return 'error'
  if (resource.data?.ready && resource.data.state !== 'closed') return 'available'
  return 'neutral'
}

watch(connection, value => emit('connection', value), { immediate: true })
</script>

<template>
  <div class="device-page ui-page">
    <section class="device-summary panel" :aria-labelledby="`${id}-device`">
      <div class="summary-copy">
        <span class="hardware-icon"><Cpu :size="28" :stroke-width="1.5" aria-hidden="true" /></span>
        <div><p class="eyebrow">{{ t('hardware') }}</p><h2 :id="`${id}-device`">{{ telemetry?.model || t('model') }}</h2><p class="hardware-meta">{{ telemetry?.hostname || t('hardwareHint') }}</p></div>
      </div>
      <div class="summary-actions">
        <button class="button primary ui-button ui-button--primary" type="button" :disabled="refreshing" :aria-busy="refreshing" @click="refresh()">
          <RefreshCw :size="17" :stroke-width="1.8" aria-hidden="true" /><span>{{ t(refreshing ? 'refreshing' : 'refresh') }}</span>
        </button>
        <p class="last-check">{{ lastCheck }}</p>
      </div>
      <div class="summary-status">
        <p role="status" aria-atomic="true"><span class="status-dot" aria-hidden="true"></span>{{ refreshing && !checkedAt ? t('reading') : t('servicesRead', { count: receivedCount }) }}</p>
        <p>{{ t('serviceHint') }}</p>
      </div>
    </section>

    <section class="resource-panel panel" :aria-labelledby="`${id}-resources`">
      <header class="section-heading"><div><h2 :id="`${id}-resources`" class="ui-section-title">{{ t('resources') }}</h2><p class="ui-section-description">{{ t('resourceHint') }}</p></div><span class="sample-time" :data-stale="Boolean(device.error)">{{ sampleTime }}</span></header>
      <UiAlert v-if="device.error" class="telemetry-error" role="status">{{ telemetry ? t('telemetryFailed') : t(`errors.${device.error}`) }}</UiAlert>
      <div class="metric-grid">
        <div v-for="metric in metrics" :key="metric.name" class="metric">
          <component :is="metric.icon" :size="19" :stroke-width="1.6" aria-hidden="true" />
          <p>{{ t(`metrics.${metric.name}`) }}</p><strong>{{ metric.value }}</strong><span class="metric-hint">{{ metric.hint }}</span><span v-if="metric.extra" class="metric-hint">{{ metric.extra }}</span>
        </div>
      </div>
      <details class="system-details">
        <summary><span>{{ t('identity') }}</span><ChevronDown :size="17" aria-hidden="true" /></summary>
        <p class="details-hint">{{ t('identityHint') }}</p>
        <dl class="fact-list"><div v-for="fact in facts" :key="fact.name"><dt>{{ t(`facts.${fact.name}`) }}</dt><dd>{{ fact.value ?? t('unavailable') }}</dd></div></dl>
      </details>
    </section>

    <section :aria-labelledby="`${id}-services`">
      <header class="section-heading outside"><div><h2 :id="`${id}-services`" class="ui-section-title">{{ t('services') }}</h2><p class="ui-section-description">{{ t('servicesHint') }}</p></div></header>
      <div class="two-column">
        <article v-for="service in services" :key="service.key" class="service-card panel" :aria-labelledby="`${id}-${service.key}`">
          <header class="card-heading"><span class="section-icon ui-section-icon"><component :is="service.icon" :size="22" :stroke-width="1.6" aria-hidden="true" /></span><div><h3 :id="`${id}-${service.key}`" class="ui-section-title">{{ t(`${service.key}.title`) }}</h3><p class="ui-section-description">{{ t(`${service.key}.description`) }}</p></div><span class="engine-badge">{{ service.processor }}</span></header>
          <div class="service-status" :data-tone="statusTone(service.resource)"><span class="status-dot" aria-hidden="true"></span><p role="status">{{ statusText(service.resource) }}</p></div>
          <div v-if="service.resource.error" class="service-body service-error">
            <UiAlert><p>{{ t(`errors.${service.resource.error}`) }}</p><p v-if="service.resource.data && service.resource.checkedAt" class="stale-state">{{ t('stale', { state: t(`states.${service.resource.data.state}`), time: formatTime(service.resource.checkedAt) }) }}</p></UiAlert>
          </div>
          <div v-else class="service-body">
            <template v-if="service.key === 'vision' && vision.data">
              <dl class="fact-list compact"><div><dt>{{ t('vision.models') }}</dt><dd>{{ models || t('vision.noModels') }}</dd></div><div><dt>{{ t('vision.size') }}</dt><dd>{{ vision.data.capabilities.capture_size.join(' × ') }}</dd></div></dl>
              <p class="service-note">{{ t('vision.sourceHint') }}</p>
            </template>
            <template v-else-if="service.key === 'speech' && speech.data">
              <dl class="fact-list compact"><div><dt>{{ t('speech.source') }}</dt><dd>{{ t(`states.${speech.data.state}`) }}</dd></div></dl>
              <p class="service-note">{{ t('speech.sourceHint') }}</p>
            </template>
            <p v-else class="service-note">{{ t('waiting') }}</p>
          </div>
          <RouterLink class="service-link ui-button" :to="{ name: service.route }"><span>{{ t(`${service.key}.link`) }}</span><ArrowUpRight :size="18" aria-hidden="true" /></RouterLink>
        </article>
      </div>
    </section>

    <div class="two-column management-grid"><DeviceNetwork /><DeviceAudio /></div>
    <DeviceMaintenance />

    <div class="privacy-note"><ShieldCheck :size="20" :stroke-width="1.6" aria-hidden="true" /><div><p>{{ t('privacy') }}</p><p>{{ t('privacyHint') }}</p></div></div>
  </div>
</template>

<style scoped>
.device-page { min-width: 0; }
.panel { min-width: 0; padding: 26px; border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); box-shadow: var(--shadow-soft); }
h2, h3, p { margin: 0; }
.device-summary { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 24px; background: linear-gradient(115deg, var(--surface) 45%, color-mix(in srgb, var(--accent-soft) 65%, var(--surface))); }
.summary-copy { display: flex; gap: 18px; align-items: center; min-width: 0; }
.summary-copy > div { min-width: 0; }
.hardware-icon { display: grid; place-items: center; width: 68px; height: 68px; flex-shrink: 0; border: 1px solid color-mix(in srgb, var(--accent) 15%, var(--border)); border-radius: 22px; background: var(--surface); color: var(--accent); box-shadow: var(--shadow-soft); }
.eyebrow { margin-bottom: 5px; color: var(--muted); font-size: 11px; font-weight: 600; letter-spacing: .08em; }
.summary-copy h2 { overflow-wrap: anywhere; color: var(--text); font-size: clamp(22px, 2.2vw, 27px); font-weight: 600; letter-spacing: -.025em; line-height: 1.35; }
.hardware-meta { overflow-wrap: anywhere; margin-top: 6px; color: var(--muted); font-size: 12px; letter-spacing: .025em; }
.summary-actions { display: flex; flex-direction: column; align-items: flex-end; justify-content: center; gap: 8px; }
.last-check { color: var(--muted); font-size: 11px; line-height: 1.6; }
.summary-status { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 20px; padding-top: 20px; border-top: 1px solid var(--border); color: var(--muted); font-size: 12px; line-height: 1.7; }
.summary-status p:first-child { display: flex; align-items: center; gap: 8px; font-weight: 500; }
.summary-status p:last-child { margin-left: auto; font-size: 11px; }
.status-dot { width: 6px; height: 6px; flex-shrink: 0; border-radius: 50%; background: currentColor; }
.section-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 22px; }
.ui-section-description { margin-top: 5px; }
.engine-badge { display: inline-flex; align-items: center; flex-shrink: 0; min-height: 28px; padding: 4px 9px; border: 1px solid var(--border); border-radius: 9px; background: var(--background); color: var(--muted); font-size: 10px; font-weight: 500; line-height: 1.5; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.metric { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 9px; min-width: 0; padding: 18px; border: 1px solid var(--border); border-radius: 17px; background: var(--background); }
.metric > svg { grid-column: 2; color: var(--muted); }
.metric p { grid-row: 1; grid-column: 1; color: var(--muted); font-size: 12px; line-height: 1.6; }
.metric strong { grid-column: 1 / -1; color: var(--text); font-size: 24px; font-weight: 500; line-height: 1.3; overflow-wrap: anywhere; }
.metric-hint { grid-column: 1 / -1; color: var(--muted); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.sample-time { color: var(--muted); font-size: 11px; line-height: 1.7; text-align: right; }
.sample-time[data-stale="true"] { color: var(--offline); }
.telemetry-error { margin-bottom: 18px; }
.system-details { margin-top: 20px; border-top: 1px solid var(--border); }
.system-details summary { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 52px; padding-top: 4px; color: var(--text); font-size: 12px; font-weight: 500; cursor: pointer; list-style: none; }
.system-details summary::-webkit-details-marker { display: none; }
.system-details[open] summary > svg { transform: rotate(180deg); }
.details-hint { color: var(--muted); font-size: 11px; line-height: 1.7; }
.fact-list { display: grid; gap: 13px; margin: 22px 0 0; }
.fact-list > div { display: flex; align-items: baseline; justify-content: space-between; gap: 20px; min-width: 0; }
.fact-list dt { color: var(--muted); font-size: 12px; line-height: 1.7; }
.fact-list dd { min-width: 0; max-width: 65%; margin: 0; color: var(--text); font-size: 12px; line-height: 1.7; text-align: right; overflow-wrap: anywhere; }
.outside { margin: 0 4px 16px; }
.two-column { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
.card-heading { display: flex; align-items: flex-start; gap: 14px; }
.card-heading > div { min-width: 0; }
.engine-badge { margin-left: auto; }
.service-card { display: flex; flex-direction: column; }
.service-status { display: flex; align-items: center; gap: 8px; margin-top: 24px; color: var(--muted); font-size: 12px; font-weight: 600; line-height: 1.7; }
.service-status[data-tone="available"] { color: var(--online); }
.service-status[data-tone="error"] { color: var(--offline); }
.service-body { flex: 1; margin-bottom: 16px; }
.compact { margin-top: 16px; }
.service-note { margin-top: 14px; color: var(--muted); font-size: 11px; line-height: 1.75; text-wrap: pretty; }
.service-error { margin-top: 10px; }
.stale-state { margin-top: 8px; font-size: 11px; }
.service-link { justify-content: space-between; margin-top: auto; color: var(--accent); }
.privacy-note { display: flex; align-items: flex-start; gap: 14px; padding: 2px 6px; color: var(--accent); }
.privacy-note > svg { flex-shrink: 0; margin-top: 2px; }
.privacy-note p { color: var(--text); font-size: 12px; line-height: 1.7; }
.privacy-note p + p { margin-top: 3px; color: var(--muted); font-size: 11px; }
summary:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
@media (max-width: 1100px) and (min-width: 960px), (max-width: 760px) {
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 680px) {
  .two-column { grid-template-columns: minmax(0, 1fr); }
  .device-summary { grid-template-columns: minmax(0, 1fr); gap: 20px; }
  .summary-actions { align-items: flex-start; }
  .summary-status { gap: 6px; }
  .summary-status p { flex-basis: 100%; }
  .summary-status p:last-child { margin-left: 0; }
}
@media (max-width: 539px) {
  .panel { padding: 20px 16px; }
  .two-column { gap: 18px; }
  .summary-copy { gap: 14px; }
  .hardware-icon { width: 56px; height: 56px; border-radius: 18px; }
  .summary-copy h2 { overflow-wrap: anywhere; font-size: 21px; }
  .summary-actions, .summary-actions .button { width: 100%; }
  .summary-actions .last-check { align-self: center; }
  .section-heading { gap: 12px; margin-bottom: 18px; }
  .metric-grid { gap: 10px; }
  .metric { padding: 14px; gap: 8px; }
  .metric p { font-size: 11px; }
  .metric > svg { width: 16px; }
  .metric strong { font-size: 24px; }
  .card-heading { gap: 12px; }
  .engine-badge { padding: 4px 6px; font-size: 9px; }
  .fact-list > div { gap: 12px; }
  .fact-list dd { max-width: 60%; }
  .privacy-note { padding-inline: 2px; }
}
@media (hover: hover) and (pointer: fine) {
  summary:hover { color: var(--accent); }
}
@media (prefers-reduced-motion: no-preference) {
  summary > svg { transition: transform var(--motion-fast) var(--ease-out); }
}
</style>
