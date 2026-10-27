<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertCircle, MessageSquareText, Mic, MicOff, ShieldCheck } from '@lucide/vue'

type Connection = 'connecting' | 'online' | 'offline'
type SpeechState = 'loading' | 'idle' | 'starting' | 'listening' | 'stopping' | 'error' | 'closed'
type SpeechError = { code: string; message: string }
type Segment = { id: number; text: string; start_seconds: number; end_seconds: number; reason: string }
type Snapshot = {
  type: 'state'; schema_version: 1; state: SpeechState; ready: boolean; session_id: string | null
  owned: boolean; partial_text: string; segments: Segment[]; audio_seconds: number; error: SpeechError | null
}

const emit = defineEmits<{ connection: [value: Connection] }>()
const titleId = useId()
const hintId = useId()
const liveTitleId = useId()
const historyTitleId = useId()
const { t } = useI18n({
  useScope: 'local', inheritLocale: true,
  messages: {
    'zh-CN': {
      title: '语音识别', description: '开启后，对着小鸭说话，文字会实时显示在这里。',
      turnOn: '开启收音', turnOff: '关闭收音', sending: '正在发送…',
      connecting: '正在连接设备', offline: '连接已断开，正在重连', occupied: '其他页面正在使用麦克风',
      states: { loading: '语音服务准备中', idle: '收音已关闭', starting: '正在开启麦克风', listening: '正在聆听', stopping: '正在关闭麦克风', error: '语音服务暂不可用', closed: '语音服务已停止' },
      live: '实时文字', draft: '识别中', liveHint: '文字会随说话更新，停顿后形成完整句子。',
      waiting: '正在听，试着说一句话…', startHint: '开启收音后，识别文字会出现在这里。',
      preparing: '正在准备收音，请稍候…', stopHint: '正在结束收音，未完成的文字不会保留。',
      history: '已识别的句子', recent: '最近 {count} 句', empty: '还没有完整句子',
      emptyHint: '说完后稍作停顿，就能在这里看到识别结果。', duration: '本次收音时长',
      privacy: '声音来自板端麦克风。关闭收音、离开此页面或断开连接后，会停止采集。',
      retention: '仅显示本次会话最近 20 句，录音和文字不长期保存。',
      errors: {
        busy: '麦克风正在被其他任务使用，请稍后重试。', not_owner: '当前收音由其他页面控制。',
        not_ready: '语音服务尚未准备好，请稍候。', device_busy: '麦克风正在被占用，请结束其他音频任务后重试。',
        device_unavailable: '未能连接板端麦克风，请检查设备。', audio_overrun: '收音未能跟上处理速度，请重新开启。',
        timeout: '语音处理超时，请重新开启收音。', command_timeout: '设备未确认操作，已断开连接并请求停止收音。',
        model_unavailable: '识别模型不可用，请检查板端模型配置。', unsupported_model: '模型文件不匹配，请检查板端配置。',
        missing_dependency: '板端语音运行环境尚未准备好。', backend_failure: '语音识别暂时失败，请稍后重试。',
        protocol: '设备返回了无法识别的状态，正在重新连接。', unknown: '语音操作未成功，请检查设备后重试。',
      },
    },
    en: {
      title: 'Speech recognition', description: 'Turn on listening and speak to your Duck. Your words appear here as you talk.',
      turnOn: 'Start listening', turnOff: 'Stop listening', sending: 'Sending…',
      connecting: 'Connecting to your Duck', offline: 'Connection lost. Reconnecting', occupied: 'The microphone is in use by another page',
      states: { loading: 'Preparing speech recognition', idle: 'Listening is off', starting: 'Starting the microphone', listening: 'Listening', stopping: 'Stopping the microphone', error: 'Speech recognition is unavailable', closed: 'Speech service stopped' },
      live: 'Live transcript', draft: 'In progress', liveHint: 'Words update while you speak. Pause briefly to finish a sentence.',
      waiting: 'Listening. Try saying something…', startHint: 'Start listening to see your words here.',
      preparing: 'Getting the microphone ready…', stopHint: 'Stopping. Unfinished words will be discarded.',
      history: 'Completed sentences', recent: 'Latest {count}', empty: 'No completed sentences yet',
      emptyHint: 'Pause briefly after speaking to see the result here.', duration: 'Listening time',
      privacy: 'Audio comes from your Duck’s microphone. Capture stops when you switch it off, leave this page or disconnect.',
      retention: 'Only the latest 20 sentences in this session are shown. Audio and transcripts are not kept long term.',
      errors: {
        busy: 'The microphone is in use. Please try again shortly.', not_owner: 'Another page controls this listening session.',
        not_ready: 'Speech recognition is still preparing. Please wait.', device_busy: 'The microphone is busy. Finish other audio tasks and try again.',
        device_unavailable: 'The microphone is unavailable. Check your Duck.', audio_overrun: 'Audio capture fell behind. Please start listening again.',
        timeout: 'Speech processing timed out. Please start listening again.', command_timeout: 'Your Duck did not confirm the action. Disconnected to stop capture.',
        model_unavailable: 'The recognition model is unavailable. Check the device configuration.', unsupported_model: 'The model files do not match. Check the device configuration.',
        missing_dependency: 'The device’s speech runtime is not ready.', backend_failure: 'Speech recognition failed. Please try again shortly.',
        protocol: 'The device returned an unsupported response. Reconnecting.', unknown: 'The action failed. Check your Duck and try again.',
      },
    },
  },
})

const states = new Set<SpeechState>(['loading', 'idle', 'starting', 'listening', 'stopping', 'error', 'closed'])
const errorCodes = new Set(['busy', 'not_owner', 'not_ready', 'device_busy', 'device_unavailable', 'audio_overrun', 'timeout', 'command_timeout', 'model_unavailable', 'unsupported_model', 'missing_dependency', 'backend_failure', 'protocol'])
const connection = ref<Connection>('connecting')
const snapshot = ref<Snapshot | null>(null)
const pending = ref<'start' | 'stop' | null>(null)
const localError = ref<string | null>(null)
const historyElement = ref<HTMLElement | null>(null)
let socket: WebSocket | null = null
let retryTimer: ReturnType<typeof setTimeout> | undefined
let commandTimer: ReturnType<typeof setTimeout> | undefined
let heartbeatTimer: ReturnType<typeof setTimeout> | undefined
let retries = 0
let disposed = false
let suspended = false

const active = computed(() => !!snapshot.value && ['starting', 'listening', 'stopping'].includes(snapshot.value.state))
const occupied = computed(() => active.value && !snapshot.value?.owned)
const transition = computed(() => snapshot.value?.state === 'stopping' || pending.value !== null)
const canToggle = computed(() => connection.value === 'online' && !!snapshot.value && !transition.value && !occupied.value
  && (active.value || (snapshot.value.ready && ['idle', 'error'].includes(snapshot.value.state))))
const statusText = computed(() => connection.value !== 'online' ? t(connection.value)
  : occupied.value ? t('occupied') : t(`states.${snapshot.value?.state ?? 'loading'}`))
const errorText = computed(() => {
  const code = localError.value ?? snapshot.value?.error?.code
  return code ? t(`errors.${errorCodes.has(code) ? code : 'unknown'}`) : ''
})
const placeholder = computed(() => {
  if (connection.value !== 'online') return statusText.value
  if (snapshot.value?.state === 'listening') return t('waiting')
  if (snapshot.value?.state === 'starting') return t('preparing')
  if (snapshot.value?.state === 'stopping') return t('stopHint')
  return t('startHint')
})
const segments = computed(() => snapshot.value?.segments ?? [])
watch(() => `${snapshot.value?.session_id ?? ''}:${segments.value.at(-1)?.id ?? ''}`, async () => {
  const node = historyElement.value
  const follow = !node || node.scrollHeight - node.scrollTop - node.clientHeight < 48
  await nextTick()
  if (follow && historyElement.value) historyElement.value.scrollTop = historyElement.value.scrollHeight
})

function formatTime(seconds: number): string {
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60).toString().padStart(2, '0')}:${(whole % 60).toString().padStart(2, '0')}`
}

function isError(value: unknown): value is SpeechError {
  if (!value || typeof value !== 'object') return false
  const error = value as SpeechError
  return typeof error.code === 'string' && error.code.length <= 128
    && typeof error.message === 'string' && error.message.length <= 4096
}

function isSnapshot(value: unknown): value is Snapshot {
  if (!value || typeof value !== 'object') return false
  const item = value as Snapshot
  return item.type === 'state' && item.schema_version === 1 && states.has(item.state)
    && typeof item.ready === 'boolean' && typeof item.owned === 'boolean'
    && (item.session_id === null || (typeof item.session_id === 'string' && item.session_id.length <= 128))
    && typeof item.partial_text === 'string' && item.partial_text.length <= 8192
    && Number.isFinite(item.audio_seconds) && item.audio_seconds >= 0
    && (item.error === null || isError(item.error))
    && Array.isArray(item.segments) && item.segments.length <= 20 && item.segments.every(segment => segment
      && Number.isSafeInteger(segment.id) && segment.id >= 0 && typeof segment.text === 'string' && segment.text.length <= 8192
      && Number.isFinite(segment.start_seconds) && segment.start_seconds >= 0
      && Number.isFinite(segment.end_seconds) && segment.end_seconds >= segment.start_seconds
      && typeof segment.reason === 'string' && segment.reason.length <= 128)
}

function setConnection(value: Connection): void {
  connection.value = value
  emit('connection', value)
}

function clearPending(): void {
  clearTimeout(commandTimer)
  pending.value = null
}

function disconnect(): void {
  const current = socket
  socket = null
  clearTimeout(heartbeatTimer)
  clearPending()
  current?.close()
}

function scheduleReconnect(): void {
  if (disposed || suspended || retryTimer !== undefined) return
  const delay = Math.min(16_000, 1000 * 2 ** Math.min(retries++, 4))
  retryTimer = setTimeout(() => { retryTimer = undefined; connect() }, delay)
}

function failConnection(code?: string): void {
  if (code) localError.value = code
  disconnect()
  snapshot.value = null
  setConnection('offline')
  scheduleReconnect()
}

function connect(): void {
  if (disposed || suspended || socket) return
  setConnection('connecting')
  const url = new URL('/api/speech/live', window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  let current: WebSocket
  try { current = new WebSocket(url) } catch { failConnection(); return }
  socket = current
  const refreshHeartbeat = () => {
    clearTimeout(heartbeatTimer)
    heartbeatTimer = setTimeout(() => {
      if (socket === current && !disposed) failConnection()
    }, 30_000)
  }
  refreshHeartbeat()
  current.onmessage = event => {
    if (disposed || socket !== current) return
    if (typeof event.data !== 'string' || event.data.length > 200_000) { failConnection('protocol'); return }
    let message: unknown
    try { message = JSON.parse(event.data) } catch { failConnection('protocol'); return }
    if (isSnapshot(message)) {
      snapshot.value = message
      retries = 0
      refreshHeartbeat()
      setConnection('online')
      if ((pending.value === 'start' && ['starting', 'listening', 'error', 'closed'].includes(message.state))
        || (pending.value === 'stop' && ['stopping', 'idle', 'error', 'closed'].includes(message.state))) clearPending()
      if (message.state === 'listening' && message.owned) localError.value = null
    } else if (message && typeof message === 'object' && (message as { type?: unknown }).type === 'error'
      && isError((message as { error?: unknown }).error)) {
      localError.value = (message as { error: SpeechError }).error.code
      clearPending()
      refreshHeartbeat()
    } else { failConnection('protocol') }
  }
  current.onclose = () => { if (!disposed && socket === current) failConnection() }
  current.onerror = () => { if (!disposed && socket === current) failConnection() }
}

function toggle(): void {
  if (!canToggle.value || !socket || socket.readyState !== WebSocket.OPEN) return
  const command = active.value ? 'stop' : 'start'
  pending.value = command
  localError.value = null
  try { socket.send(JSON.stringify({ type: command })) } catch { failConnection(); return }
  commandTimer = setTimeout(() => failConnection('command_timeout'), 12_000)
}

function suspend(): void {
  suspended = true
  clearTimeout(retryTimer)
  retryTimer = undefined
  disconnect()
  snapshot.value = null
}

function resume(): void {
  if (!suspended || disposed) return
  suspended = false
  connect()
}

onMounted(() => {
  window.addEventListener('pagehide', suspend)
  window.addEventListener('pageshow', resume)
  connect()
})
onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('pagehide', suspend)
  window.removeEventListener('pageshow', resume)
  suspend()
})
</script>

<template>
  <div class="speech-recognition">
    <section class="listening-card" :aria-labelledby="titleId">
      <div class="listening-heading">
        <span class="mic-symbol" aria-hidden="true"><Mic :size="26" :stroke-width="1.6" /></span>
        <div class="listening-copy"><h2 :id="titleId">{{ t('title') }}</h2><p>{{ t('description') }}</p></div>
      </div>
      <div class="listening-controls">
        <p class="speech-status" :data-active="snapshot?.state === 'listening' && connection === 'online'" role="status" aria-atomic="true">
          <span aria-hidden="true"></span>{{ statusText }}
        </p>
        <button type="button" class="listening-toggle" role="switch" :aria-checked="active"
          :aria-labelledby="titleId" :aria-describedby="hintId" :disabled="!canToggle" :aria-busy="pending !== null" @click="toggle">
          <span class="toggle-track" aria-hidden="true"><span></span></span>
          <span>{{ pending ? t('sending') : active ? t('turnOff') : t('turnOn') }}</span>
        </button>
      </div>
      <p :id="hintId" class="listening-hint"><ShieldCheck :size="17" aria-hidden="true" />{{ t('privacy') }}</p>
    </section>

    <p v-if="errorText" class="speech-error" role="alert"><AlertCircle :size="20" aria-hidden="true" /><span>{{ errorText }}</span></p>

    <section class="transcript-card" :aria-labelledby="liveTitleId">
      <header class="transcript-heading">
        <div><h2 :id="liveTitleId">{{ t('live') }}</h2><p>{{ t('liveHint') }}</p></div>
        <span class="duration" :aria-label="t('duration')">{{ formatTime(snapshot?.audio_seconds ?? 0) }}</span>
      </header>
      <div class="live-transcript" :class="{ 'has-text': snapshot?.partial_text }">
        <span v-if="snapshot?.partial_text" class="draft-label">{{ t('draft') }}</span>
        <p v-if="snapshot?.partial_text" class="partial-text" aria-live="off">{{ snapshot.partial_text }}</p>
        <p v-else class="transcript-placeholder"><MicOff v-if="!active" :size="22" aria-hidden="true" />{{ placeholder }}</p>
      </div>
      <div class="history-heading"><h3 :id="historyTitleId">{{ t('history') }}</h3><span>{{ t('recent', { count: segments.length }) }}</span></div>
      <div ref="historyElement" class="transcript-history" role="log" :tabindex="segments.length ? 0 : undefined"
        :aria-labelledby="historyTitleId" aria-live="polite" aria-relevant="additions" aria-atomic="false">
        <ol v-if="segments.length">
          <li v-for="segment in segments" :key="`${snapshot?.session_id}:${segment.id}`">
            <span class="sentence-time" aria-hidden="true">{{ formatTime(segment.start_seconds) }}</span><p>{{ segment.text }}</p>
          </li>
        </ol>
        <div v-else class="empty-history"><MessageSquareText :size="28" :stroke-width="1.5" aria-hidden="true" /><p>{{ t('empty') }}</p><span>{{ t('emptyHint') }}</span></div>
      </div>
      <p class="retention-note">{{ t('retention') }}</p>
    </section>
  </div>
</template>

<style scoped>
.speech-recognition { display: grid; gap: 24px; min-width: 0; }
.listening-card, .transcript-card { min-width: 0; padding: 28px 32px; border: 1px solid var(--border); border-radius: var(--radius-panel, 24px); background: var(--surface); box-shadow: var(--shadow-soft); }
.listening-heading { display: flex; gap: 16px; align-items: flex-start; }
.mic-symbol { display: grid; place-items: center; flex: 0 0 52px; height: 52px; border-radius: 18px; background: var(--accent-soft); color: var(--accent); }
.listening-copy { min-width: 0; }
h2 { margin: 0; color: var(--text); font-size: 20px; font-weight: 600; line-height: 1.4; letter-spacing: -.025em; }
.listening-copy p, .transcript-heading p { margin: 7px 0 0; color: var(--muted); font-size: 14px; line-height: 1.75; text-wrap: pretty; }
.listening-controls { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px 24px; margin-top: 24px; }
.speech-status { display: flex; gap: 9px; align-items: center; margin: 0; color: var(--muted); font-size: 14px; }
.speech-status > span { flex: 0 0 7px; height: 7px; border-radius: 50%; background: currentColor; }
.speech-status[data-active="true"] { color: var(--online, var(--accent)); }
.listening-toggle { display: inline-flex; justify-content: center; align-items: center; gap: 12px; min-height: 48px; min-width: 150px; padding: 8px 12px; border: 1px solid transparent; border-radius: var(--radius-control, 16px); background: transparent; color: var(--text); font: inherit; font-weight: 550; cursor: pointer; touch-action: manipulation; }
.listening-toggle:disabled { opacity: .6; cursor: not-allowed; }
.listening-toggle:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
.toggle-track { flex: 0 0 52px; display: flex; align-items: center; height: 30px; padding: 3px; border: 1px solid var(--muted); border-radius: 99px; background: var(--surface); }
.toggle-track > span { width: 22px; height: 22px; border-radius: 50%; background: var(--muted); }
.listening-toggle[aria-checked="true"] .toggle-track { border-color: var(--accent); background: var(--accent); }
.listening-toggle[aria-checked="true"] .toggle-track > span { transform: translateX(22px); background: var(--surface); }
.listening-hint { display: flex; align-items: flex-start; gap: 8px; margin: 20px 0 0; padding-top: 18px; border-top: 1px solid var(--border); color: var(--muted); font-size: 12px; line-height: 1.8; }
.listening-hint svg, .speech-error svg { flex-shrink: 0; margin-top: 2px; }
.speech-error { display: flex; gap: 10px; align-items: flex-start; margin: 0; padding: 16px 20px; border: 1px solid color-mix(in srgb, var(--offline) 35%, var(--border)); border-radius: var(--radius-control, 16px); background: var(--surface); color: var(--offline); font-size: 14px; line-height: 1.7; }
.transcript-heading, .history-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.duration { flex-shrink: 0; padding: 5px 10px; border: 1px solid var(--border); border-radius: 10px; color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.live-transcript { display: flex; flex-direction: column; justify-content: center; min-height: 144px; margin-top: 24px; padding: 24px; border: 1px solid var(--border); border-radius: 18px; background: color-mix(in srgb, var(--accent-soft) 40%, var(--surface)); overflow-wrap: anywhere; }
.draft-label { color: var(--accent); font-size: 12px; font-weight: 600; }
.partial-text { margin: 10px 0 0; font-size: clamp(18px, 2vw, 24px); line-height: 1.7; color: var(--text); }
.transcript-placeholder { display: flex; align-items: center; justify-content: center; gap: 12px; margin: 0; color: var(--muted); font-size: 15px; line-height: 1.8; text-align: center; }
.transcript-placeholder svg { flex-shrink: 0; }
.history-heading { align-items: center; margin-top: 30px; }
.history-heading h3 { margin: 0; font-size: 15px; font-weight: 600; color: var(--text); }
.history-heading > span { color: var(--muted); font-size: 12px; }
.transcript-history { min-height: 168px; max-height: 440px; overflow-y: auto; scrollbar-gutter: stable; }
.transcript-history:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
.transcript-history ol { list-style: none; margin: 12px 0 0; padding: 0; }
.transcript-history li { display: flex; align-items: baseline; gap: 16px; padding: 16px 0; border-bottom: 1px solid var(--border); }
.transcript-history li:last-child { border-bottom: 0; }
.sentence-time { flex: 0 0 42px; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.transcript-history li p { min-width: 0; margin: 0; color: var(--text); font-size: 16px; line-height: 1.8; overflow-wrap: anywhere; }
.empty-history { display: flex; min-height: 168px; flex-direction: column; align-items: center; justify-content: center; padding: 20px; color: var(--muted); text-align: center; }
.empty-history > p { margin: 12px 0 4px; color: var(--text); font-size: 14px; }
.empty-history > span, .retention-note { font-size: 12px; line-height: 1.8; }
.retention-note { margin: 20px 0 0; color: var(--muted); }
@media (max-width: 539px) {
  .speech-recognition { gap: 18px; }
  .listening-card, .transcript-card { padding: 22px 20px; }
  .listening-heading { gap: 12px; }
  .mic-symbol { flex-basis: 44px; height: 44px; border-radius: 15px; }
  h2 { font-size: 18px; }
  .listening-controls { align-items: flex-start; flex-direction: column; gap: 12px; }
  .listening-toggle { align-self: flex-end; }
  .live-transcript { padding: 18px; }
  .transcript-placeholder { flex-direction: column; }
  .transcript-history li { gap: 10px; }
}
@media (prefers-reduced-motion: no-preference) {
  .listening-toggle, .toggle-track, .toggle-track > span { transition: transform var(--motion-fast, 180ms) var(--ease-out, ease), box-shadow var(--motion-fast, 180ms) ease; }
}
@media (hover: hover) and (pointer: fine) {
  .listening-toggle:not(:disabled):hover { background: var(--accent-soft); box-shadow: var(--shadow-soft); }
}
@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .listening-toggle:not(:disabled):hover { transform: translateY(-2px); }
}
</style>
