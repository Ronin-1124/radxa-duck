<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { AudioLines, RefreshCw, Volume2, VolumeX } from '@lucide/vue'
import { managementApi, ManagementApiError } from './management-api'
import type { AudioStatus } from './management-types'
import UiAlert from './UiAlert.vue'
import UiEmptyState from './UiEmptyState.vue'

const id = useId()
const { t, locale } = useI18n({ useScope: 'local', inheritLocale: true, messages: {
  'zh-CN': {
    title: '音频设备', description: '管理板端收音设备与扬声器输出。', refresh: '刷新音频状态',
    inputs: '输入设备', outputs: '输出设备', noInputs: '未发现输入设备', noOutputs: '未发现输出设备',
    inputFormat: '{rate} Hz · {channels} 声道', left: '有效麦克风：左声道',
    volume: '扬声器音量', volumeSlider: '调整扬声器音量', mute: '静音扬声器', muted: '静音', unmuted: '未静音', unknown: '无法读取',
    draft: '有未应用的修改', apply: '应用音频设置', applying: '正在应用', reset: '放弃修改',
    loading: '正在读取音频设备', checked: '最近读取 {time}', stale: '显示上次读取的设备信息。',
    noControl: '当前输出设备不支持音量或静音控制。', editHint: '调整后点击应用；实际音量以设备确认值为准。',
    missingVolume: '当前音量无法读取，可输入目标音量后应用。', range: '请输入 0–100 之间的整数音量。', muteHint: '静音会将音量归零；解除静音后可重新调整音量。',
    warning: '部分音频信息无法读取，已显示可确认的状态。', saved: '音频设置已由设备确认。',
    errors: {
      device_busy: '音频正在被使用，请先结束收音或播放，再应用设置。',
      device_unavailable: '音频设备或输出控制暂不可用。', permission_denied: '设备尚未授权音频控制。',
      mutation_failed: '音频设置失败，设备已恢复原设置。', rollback_failed: '设置与恢复未能完成，请刷新并检查设备音频状态。',
      timeout: '读取音频状态超时，请刷新后重试。', network: '无法读取音频状态，请检查连接后刷新。',
      writeTimeout: '设备响应超时，操作结果尚未确认，请刷新状态。', writeNetwork: '无法连接音频服务，操作结果尚未确认，请刷新状态。',
      invalid_response: '设备返回的音频状态无法识别，请刷新后重试。', unavailable: '音频服务暂不可用，请刷新后重试。',
    },
  },
  en: {
    title: 'Audio devices', description: 'Manage board audio inputs and speaker output.', refresh: 'Refresh audio status',
    inputs: 'Input devices', outputs: 'Output devices', noInputs: 'No input devices found', noOutputs: 'No output devices found',
    inputFormat: '{rate} Hz · {channels} channels', left: 'Active microphone channel: left',
    volume: 'Speaker volume', volumeSlider: 'Adjust speaker volume', mute: 'Mute speaker', muted: 'Muted', unmuted: 'Unmuted', unknown: 'Not reported',
    draft: 'Unapplied changes', apply: 'Apply audio settings', applying: 'Applying', reset: 'Discard changes',
    loading: 'Reading audio devices', checked: 'Last read {time}', stale: 'Showing previously read device information.',
    noControl: 'Volume and mute controls are unavailable for the current output.', editHint: 'Apply your changes below. The device confirms the actual output level.',
    missingVolume: 'The current level is unknown. Enter a target volume to set it.', range: 'Enter a whole-number volume between 0 and 100.', muteHint: 'Muting also sets the level to zero. Adjust the volume again after unmuting.',
    warning: 'Some audio information is unavailable. Confirmed values are shown.', saved: 'Audio settings confirmed by the device.',
    errors: {
      device_busy: 'Audio is in use. Finish listening or playback before applying settings.',
      device_unavailable: 'The audio device or output controls are unavailable.', permission_denied: 'Audio control is not authorized on this device.',
      mutation_failed: 'Audio settings failed. The device restored its previous settings.', rollback_failed: 'The change and restoration failed. Refresh and check device audio.',
      timeout: 'Reading audio status timed out. Refresh and retry.', network: 'Audio status could not be read. Check the connection and refresh.',
      writeTimeout: 'The device timed out. The operation outcome is unknown; refresh its status.', writeNetwork: 'Audio service could not be reached. The operation outcome is unknown; refresh its status.',
      invalid_response: 'The device returned an unrecognized audio state. Refresh and retry.', unavailable: 'Audio service is unavailable. Refresh and retry.',
    },
  },
} })
const state = shallowRef<AudioStatus | null>(null)
const error = ref<string | null>(null)
const errorPhase = ref<'read' | 'write'>('read')
const loading = ref(false)
const saving = ref(false)
const saved = ref(false)
const volume = ref<number | ''>('')
const muted = ref(false)
const touchedVolume = ref(false)
let disposed = false
let interval: ReturnType<typeof setInterval> | undefined
let activeRequest: AbortController | null = null
const volumeChanged = computed(() => volume.value !== (state.value?.output.volume_percent ?? ''))
const muteChanged = computed(() => state.value?.output.muted != null && muted.value !== state.value.output.muted)
const dirty = computed(() => volumeChanged.value || muteChanged.value)
const validVolume = computed(() => typeof volume.value === 'number' && Number.isInteger(volume.value) && volume.value >= 0 && volume.value <= 100)
const canVolume = computed(() => Boolean(state.value?.capabilities.output_volume))
const canMute = computed(() => Boolean(state.value?.capabilities.output_mute) && state.value?.output.muted != null)
const busy = computed(() => loading.value || saving.value)
const editable = computed(() => state.value !== null && !error.value && !busy.value)
const canApply = computed(() => editable.value && dirty.value && (!volumeChanged.value || (canVolume.value && validVolume.value))
  && (!muteChanged.value || canMute.value))
const checked = computed(() => {
  const sampled = state.value?.sampled_at ? Date.parse(state.value.sampled_at) : NaN
  return Number.isFinite(sampled) ? t('checked', { time: new Intl.DateTimeFormat(locale.value, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(sampled) }) : ''
})
const errorText = computed(() => {
  const known = ['device_busy', 'device_unavailable', 'permission_denied', 'mutation_failed', 'rollback_failed', 'timeout', 'network', 'invalid_response']
  if (!error.value) return ''
  if (errorPhase.value === 'write' && ['timeout', 'network'].includes(error.value)) return t(error.value === 'timeout' ? 'errors.writeTimeout' : 'errors.writeNetwork')
  return t(`errors.${known.includes(error.value) ? error.value : 'unavailable'}`)
})
function syncDraft(): void {
  volume.value = state.value?.output.volume_percent ?? ''
  muted.value = state.value?.output.muted ?? false
  touchedVolume.value = false
  saved.value = false
}
function edit(): void { saved.value = false }
async function refresh(): Promise<void> {
  if (disposed || busy.value) return
  loading.value = true
  const controller = new AbortController()
  activeRequest = controller
  try {
    const data = await managementApi.audio(controller.signal)
    if (disposed) return
    const keepVolume = volumeChanged.value
    const keepMute = muteChanged.value
    state.value = data
    error.value = null
    if (!keepVolume) {
      volume.value = data.output.volume_percent ?? ''
      touchedVolume.value = false
    }
    if (!keepMute) muted.value = data.output.muted ?? false
    if (!keepVolume && !keepMute) saved.value = false
  } catch (cause) {
    if (!disposed) {
      errorPhase.value = 'read'
      error.value = cause instanceof ManagementApiError ? (cause.code === 'network_error' ? 'network' : cause.code) : 'network'
    }
  } finally {
    if (!disposed) loading.value = false
    if (activeRequest === controller) activeRequest = null
  }
}
async function apply(): Promise<void> {
  touchedVolume.value = true
  if (!canApply.value || disposed) return
  const patch: { volume_percent?: number; muted?: boolean } = {}
  if (volumeChanged.value && typeof volume.value === 'number') patch.volume_percent = volume.value
  if (muteChanged.value) patch.muted = muted.value
  saving.value = true
  saved.value = false
  const controller = new AbortController()
  activeRequest = controller
  try {
    const data = await managementApi.setAudio(patch, controller.signal)
    if (disposed) return
    state.value = data
    error.value = null
    syncDraft()
    saved.value = true
  } catch (cause) {
    if (!disposed) {
      errorPhase.value = 'write'
      error.value = cause instanceof ManagementApiError ? (cause.code === 'network_error' ? 'network' : cause.code) : 'network'
    }
  } finally {
    if (!disposed) saving.value = false
    if (activeRequest === controller) activeRequest = null
  }
}
function refreshVisible(): void { if (!document.hidden) void refresh() }
onMounted(() => {
  refreshVisible()
  interval = setInterval(refreshVisible, 15_000)
  document.addEventListener('visibilitychange', refreshVisible)
})
onBeforeUnmount(() => {
  disposed = true
  clearInterval(interval)
  document.removeEventListener('visibilitychange', refreshVisible)
  activeRequest?.abort()
})
</script>

<template>
  <section class="audio-panel" :aria-labelledby="`${id}-audio-title`">
    <header class="card-heading">
      <span class="section-icon ui-section-icon"><AudioLines :size="22" :stroke-width="1.6" aria-hidden="true" /></span>
      <div><h2 :id="`${id}-audio-title`" class="ui-section-title">{{ t('title') }}</h2><p class="ui-section-description">{{ t('description') }}</p></div>
      <button class="refresh-button ui-icon-button" type="button" :aria-label="t('refresh')" :disabled="busy" :aria-busy="loading" @click="refresh"><RefreshCw :size="17" aria-hidden="true" /></button>
    </header>
    <p v-if="loading && !state" class="notice" role="status">{{ t('loading') }}</p>
    <UiAlert v-if="error" class="audio-error" role="status">{{ errorText }}<span v-if="state"> {{ t('stale') }}</span></UiAlert>
    <template v-if="state">
      <div class="device-lists">
        <div><h3>{{ t('inputs') }}</h3><ul v-if="state.inputs.length"><li v-for="input in state.inputs" :key="input.id"><strong>{{ input.name }}</strong><span v-if="input.rate != null && input.channels != null">{{ t('inputFormat', { rate: input.rate, channels: input.channels }) }}</span><span v-if="input.active_channel === 'left'">{{ t('left') }}</span></li></ul><UiEmptyState v-else :icon="AudioLines" :title="t('noInputs')" compact /></div>
        <div><h3>{{ t('outputs') }}</h3><ul v-if="state.outputs.length"><li v-for="output in state.outputs" :key="output.id"><strong>{{ output.name }}</strong></li></ul><UiEmptyState v-else :icon="Volume2" :title="t('noOutputs')" compact /></div>
      </div>
      <div class="output-controls">
        <div class="volume-heading"><label :for="`${id}-volume`">{{ t('volume') }}</label><div class="number-input"><input :id="`${id}-volume`" v-model.number="volume" type="number" inputmode="numeric" min="0" max="100" step="1" placeholder="—" :disabled="!editable || !canVolume" :aria-invalid="touchedVolume && volumeChanged && !validVolume" :aria-describedby="`${id}-volume-hint${touchedVolume && volumeChanged && !validVolume ? ` ${id}-volume-error` : ''}`" @input="edit" @blur="touchedVolume = true" /><span aria-hidden="true">%</span></div></div>
        <input v-model.number="volume" class="volume-range" type="range" min="0" max="100" step="1" :aria-label="t('volumeSlider')" :disabled="!editable || !canVolume || volume === ''" @input="edit" />
        <p :id="`${id}-volume-hint`" class="hint">{{ canVolume && state.output.volume_percent === null ? t('missingVolume') : t('editHint') }}</p>
        <p v-if="touchedVolume && volumeChanged && !validVolume" :id="`${id}-volume-error`" class="notice error">{{ t('range') }}</p>
        <div class="mute-row"><span><component :is="muted ? VolumeX : Volume2" :size="17" aria-hidden="true" />{{ t('mute') }}</span><button class="mute-toggle" type="button" role="switch" :aria-label="t('mute')" :aria-checked="muted" :disabled="!editable || !canMute" @click="muted = !muted; edit()"><span class="switch-track" aria-hidden="true"><span></span></span><span>{{ state.output.muted === null ? t('unknown') : t(muted ? 'muted' : 'unmuted') }}</span></button></div>
        <p v-if="canMute" class="hint">{{ t('muteHint') }}</p>
        <p v-if="!canVolume && !canMute" class="hint">{{ t('noControl') }}</p>
        <p v-if="dirty" class="draft-label">{{ t('draft') }}</p>
        <div class="actions"><button class="button primary ui-button ui-button--primary" type="button" :disabled="!canApply" :aria-busy="saving" @click="apply">{{ t(saving ? 'applying' : 'apply') }}</button><button v-if="dirty" class="button ui-button" type="button" :disabled="busy" @click="syncDraft">{{ t('reset') }}</button></div>
        <p class="saved" role="status" aria-atomic="true">{{ saved ? t('saved') : '' }}</p>
      </div>
      <p v-if="state.warnings.length" class="hint warning">{{ t('warning') }}</p>
      <p class="checked">{{ checked }}</p>
    </template>
  </section>
</template>

<style scoped>
.audio-panel { min-width: 0; padding: 26px; border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); box-shadow: var(--shadow-soft); }
h2, h3, p { margin: 0; }
.card-heading { display: flex; align-items: flex-start; gap: 14px; }
.card-heading > div { min-width: 0; flex: 1; }
.ui-section-description { margin-top: 5px; }
.refresh-button { margin-left: auto; }
.device-lists { display: grid; gap: 18px; margin-top: 24px; }
h3, label { color: var(--text); font-size: 13px; font-weight: 600; line-height: 1.7; }
ul { display: grid; gap: 10px; padding: 0; margin: 9px 0 0; list-style: none; }
li { display: grid; gap: 3px; min-width: 0; overflow-wrap: anywhere; }
li strong { color: var(--text); font-size: 12px; font-weight: 500; line-height: 1.7; }
li span { color: var(--muted); font-size: 12px; line-height: 1.7; }
.output-controls { margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--border); }
.volume-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.number-input { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 12px; }
.number-input input { width: 76px; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 12px; color: var(--text); background: var(--background); font: inherit; font-size: 14px; text-align: center; }
.number-input input[aria-invalid="true"] { border-color: var(--offline); }
.volume-range { width: 100%; min-height: 44px; margin: 8px 0 0; accent-color: var(--accent); cursor: pointer; }
.volume-range:disabled { cursor: default; }
.hint, .notice, .checked { color: var(--muted); font-size: 12px; line-height: 1.8; text-wrap: pretty; }
.notice { margin-top: 18px; }
.audio-error { margin-top: 18px; }
.error { color: var(--offline); }
.mute-row, .mute-row > span, .mute-toggle { display: flex; align-items: center; gap: 9px; }
.mute-row { justify-content: space-between; flex-wrap: wrap; margin-top: 18px; gap: 12px; color: var(--text); font-size: 13px; }
.mute-row > span > svg { color: var(--muted); }
.mute-toggle { min-height: 44px; padding: 6px 0 6px 8px; border: 0; color: var(--muted); background: transparent; font: inherit; font-size: 12px; cursor: pointer; }
.mute-toggle:disabled { cursor: default; }
.switch-track { display: flex; align-items: center; width: 34px; height: 20px; padding: 3px; border: 1px solid var(--muted); border-radius: 999px; }
.switch-track > span { width: 12px; height: 12px; border-radius: 50%; background: var(--muted); }
.mute-toggle[aria-checked="true"] .switch-track { background: var(--accent); border-color: var(--accent); justify-content: flex-end; }
.mute-toggle[aria-checked="true"] .switch-track > span { background: var(--surface); }
.mute-toggle:disabled .switch-track { opacity: .5; }
.actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
.draft-label { margin-top: 14px; color: var(--accent); font-size: 12px; line-height: 1.7; }
.saved { margin-top: 10px; color: var(--accent); font-size: 12px; line-height: 1.7; }
.saved:empty { display: none; }
.warning { margin-top: 16px; }
.checked { margin-top: 16px; font-size: 11px; }
.mute-toggle:focus-visible, input:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
@media (max-width: 539px) {
  .audio-panel { padding: 20px 16px; }
  .card-heading { gap: 12px; }
  .actions .button { flex: 1 1 100%; }
  .number-input input { font-size: 16px; }
}
@media (hover: hover) and (pointer: fine) { .mute-toggle:not(:disabled):hover { color: var(--accent); } }
</style>
