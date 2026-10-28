<script setup lang="ts">
import { computed, ref, shallowRef, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Camera, ChevronDown, Cpu, ScanLine, SlidersHorizontal, Square, View } from '@lucide/vue'
import CaptureNotice from './CaptureNotice.vue'
import PreferenceSelect from './PreferenceSelect.vue'
import VisionFrame from './VisionFrame.vue'
import InteractionControl from './InteractionControl.vue'
import UiAlert from './UiAlert.vue'
import UiEmptyState from './UiEmptyState.vue'
import { detectionLabel } from './vision-labels'
import { emptyVisionState, type CameraRotation, type DetectionResult, type VisionModel, type VisionFrame as VisionFrameData, type VisionSettings, type VisionViewState } from './vision-types'

const props = withDefaults(defineProps<{ state?: VisionViewState }>(), { state: emptyVisionState })
const emit = defineEmits<{
  start: [settings: VisionSettings]
  stop: []
  displayed: [frame: VisionFrameData]
  release: [frame: VisionFrameData]
  decodeError: [frame: VisionFrameData]
  connection: [value: 'unknown' | 'connecting' | 'online' | 'offline']
}>()
const id = useId()
const modelLabels: Record<VisionModel, string> = {
  yolov8n: 'YOLOv8n', yolov5n: 'YOLOv5n', yolo26n: 'YOLO26n', yolo26s: 'YOLO26s',
}
const model = ref<VisionModel>('yolov8n')
const rotation = ref<CameraRotation>(0)
const detectionEnabled = ref(true)
const maxFps = ref<number | string>(10)
const scoreThreshold = ref<number | string>(35)
const fpsTouched = ref(false)
const thresholdTouched = ref(false)
let defaultsLoaded = false
const showBoxes = ref(true)
const frameFresh = ref(false)
const renderedResult = shallowRef<DetectionResult | null>(null)
const localPending = ref<'start' | 'stop' | null>(null)
const pending = computed(() => props.state.pending === undefined ? localPending.value : props.state.pending)
const { t, locale } = useI18n({
  useScope: 'local', inheritLocale: true,
  messages: {
    'zh-CN': {
      title: '小鸭的视角', subtitle: '开启摄像头，看看身边有什么。',
      start: '开启视觉', stop: '停止视觉', incompleteStop: '停止未完成', requestingStart: '正在请求开启', requestingStop: '正在请求停止', requestingHint: '等待设备确认操作。',
      unavailable: '视觉服务待接入', unavailableHint: '设备尚未提供可用的视觉连接，暂时无法开启摄像头。',
      connecting: '正在连接视觉服务', connectingHint: '连接完成后，由你开启摄像头。',
      offline: '视觉连接已断开', offlineHint: '暂时无法确认摄像头状态，请等待设备连接恢复。',
      occupied: '视觉任务由其他页面控制', occupiedHint: '请在控制该任务的页面停止后，再开启新的任务。',
      states: { idle: '摄像头未开启', starting: '正在准备摄像头', streaming: '视觉交互进行中', stopping: '正在停止视觉任务', error: '视觉任务遇到问题', closed: '视觉服务已停止' },
      idleHint: '开启后，这里将显示板端摄像头画面和识别到的目标。',
      startingHint: '正在准备摄像头和检测模型，请稍候。',
      streamingHint: '正在等待板端传来画面。',
      stoppingHint: '正在结束处理，等待设备确认资源已释放。',
      errorHint: '请根据错误提示检查设备后重试。',
      closedHint: '请等待视觉服务恢复后重新连接。',
      notReady: '视觉服务暂未就绪', notReadyHint: '设备尚未准备好，请检查服务状态后再开启。',
      boxes: '目标标注', boxesHint: '只影响画面标注的显示，不会停止目标检测。',
      boardCamera: '板端摄像头', localDetection: '本地目标检测', temporary: '仅当前画面', previewOnly: '仅画面预览',
      detectionOff: '目标检测已关闭', detectionOffHint: '当前仅显示摄像头画面。下次开启前可启用目标检测。',
      results: '当前目标', confidence: '置信度', resultCount: '{count} 个目标',
      resultsEmpty: '等待检测结果', resultsEmptyHint: '开启视觉后，这里会列出当前画面中的目标。',
      resultsWaiting: '等待同帧结果', resultsWaitingHint: '检测结果与画面对应后显示，避免标注错位。',
      noObjects: '未检测到目标', noObjectsHint: '当前画面中暂未发现可识别的目标。',
      resultsPaused: '检测结果已暂停', resultsPausedHint: '收到新的画面后继续更新。',
      settings: '采集设置', settingsHint: '选择下次开启时使用的检测方式与方向。',
      detection: '目标检测', detectionHint: '使用板端 NPU 识别目标；关闭后仅预览画面。',
      noModels: '未配置检测模型', loadingModels: '等待设备配置', availableModel: '板端已配置',
      advanced: '高级采集设置', advancedHint: '帧率与检测阈值',
      maxFps: '最高帧率', maxFpsHint: '1–{max} 帧/秒；实际速度取决于设备负载。',
      fpsError: '请输入 1–{max} 之间的整数。', threshold: '最低置信度（%）',
      thresholdHint: '5–95；提高阈值可减少低置信度的目标。', thresholdError: '请输入 5–95 之间的数值。',
      settingsInvalid: '请检查采集设置中的数值或检测模型。',
      model: '检测模型', defaultModel: '默认模型', alternativeModel: '备选模型',
      rotation: '画面方向', upright: '原始方向', clockwise: '顺时针 {degrees}°',
      settingPending: '选择会在下一次开启时发送给设备。', settingsLocked: '结束当前任务后可调整下次开启设置。',
      details: '处理详情', detailsHint: '查看当前帧的检测数据',
      inference: '推理调用', processing: '检测处理', age: '检测时帧龄', frame: '帧编号', resolution: '画面尺寸', actualModel: '当前模型',
      timingHint: '推理调用包含数据传输与输出转换。检测处理为完整检测调用；检测时帧龄为采集到检测完成，不含网页传输和显示耗时。',
      privacy: '画面来自小鸭的摄像头',
      captureStop: '停止视觉、离开此页面或断开连接后，会停止采集。',
      privacyHint: '本页面仅展示当前画面与检测结果，不保存历史图片。',
      errors: {
        invalid_config: '采集或检测参数无效，请检查设置后重试。',
        cancelled: '视觉任务已取消。', invalid_state: '视觉任务状态已变化，请等待设备更新状态。',
        dependency_unavailable: '板端视觉运行环境尚未准备好。',
        device_unavailable: '未能连接板端摄像头，请检查设备。',
        device_busy: '摄像头或 NPU 正被其他任务占用，请先结束该任务。',
        capture_failed: '摄像头取帧失败，请检查设备后重试。',
        capture_ended: '摄像头画面传输已结束，请重新开启。',
        timeout: '摄像头未能及时返回画面，请检查设备后重试。',
        invalid_frame: '收到的画面不可用，请重新开启视觉任务。',
        cleanup_failed: '停止尚未完成，设备资源还未释放。请检查板端视觉服务，暂时无法重新开启。',
        unsupported_model: '检测模型不受支持，请检查板端模型配置。',
        model_unavailable: '检测模型不可用，请检查板端模型文件。',
        model_load_failed: '检测模型加载失败，请检查板端环境。',
        runtime_unavailable: '板端 NPU 暂不可用，请检查视觉运行环境。',
        invalid_output: '模型返回了无法使用的检测结果，请检查模型配置。',
        inference_failed: '目标检测失败，请稍后重试。',
        busy: '视觉任务仍在处理，请稍候再操作。',
        not_owner: '当前视觉任务由其他页面控制。', not_ready: '视觉服务尚未准备好，请检查设备状态。',
        restart_required: '请先停止当前任务，再调整采集设置。', closed: '视觉服务已关闭，请等待服务恢复。',
        invalid_request: '采集请求无效，请检查设置后重试。', encode_failed: '摄像头画面编码失败，请重新开启。',
        frame_too_large: '摄像头画面超过传输大小限制，请检查视觉服务。',
        command_timeout: '设备未及时确认操作，已断开连接并请求停止采集。',
        protocol: '设备返回了无法识别的视觉数据，连接已关闭。', decode_failed: '画面无法解码，连接已关闭以停止采集。',
        status_unavailable: '暂时无法读取视觉状态，请检查设备连接。', connection_timeout: '视觉连接超时，请检查设备和网络。',
        unknown: '视觉操作未成功，请检查设备状态后重试。',
      },
    },
    en: {
      title: 'Through your Duck’s eyes', subtitle: 'Turn on the camera and explore what is nearby.',
      start: 'Start vision', stop: 'Stop vision', incompleteStop: 'Stop incomplete', requestingStart: 'Requesting start', requestingStop: 'Requesting stop', requestingHint: 'Waiting for the device to confirm the request.',
      unavailable: 'Vision service not connected', unavailableHint: 'Your Duck does not yet provide a vision connection. Camera capture is unavailable.',
      connecting: 'Connecting to vision', connectingHint: 'Once connected, you choose when the camera starts.',
      offline: 'Vision connection lost', offlineHint: 'The camera state cannot be confirmed. Wait for the device connection to return.',
      occupied: 'Another page controls this vision task', occupiedHint: 'Stop the task from its controlling page before starting a new one.',
      states: { idle: 'Camera is off', starting: 'Preparing the camera', streaming: 'Vision is running', stopping: 'Stopping vision', error: 'Vision needs attention', closed: 'Vision service stopped' },
      idleHint: 'Start vision to see the device camera and detected objects here.',
      startingHint: 'Preparing the camera and detection model. Please wait.',
      streamingHint: 'Waiting for a frame from your Duck.',
      stoppingHint: 'Finishing processing. Waiting for the device to confirm that resources are released.',
      errorHint: 'Check the device using the error details, then try again.',
      closedHint: 'Wait for the vision service to return, then reconnect.',
      notReady: 'Vision is not ready', notReadyHint: 'The device is not ready. Check its service before starting.',
      boxes: 'Object labels', boxesHint: 'Changes the overlay only. Object detection keeps running.',
      boardCamera: 'Device camera', localDetection: 'Local object detection', temporary: 'Current frame only', previewOnly: 'Camera preview only',
      detectionOff: 'Object detection is off', detectionOffHint: 'Only the camera image is shown. Enable detection before your next start to see objects.',
      results: 'Objects in view', confidence: 'Confidence', resultCount: '{count} objects',
      resultsEmpty: 'Waiting for detections', resultsEmptyHint: 'Start vision to see the objects in the current frame.',
      resultsWaiting: 'Waiting for matching results', resultsWaitingHint: 'Detections appear with their matching frame to keep labels aligned.',
      noObjects: 'No objects detected', noObjectsHint: 'No recognizable objects were found in this frame.',
      resultsPaused: 'Detections paused', resultsPausedHint: 'Results will update when a new frame arrives.',
      settings: 'Capture settings', settingsHint: 'Choose detection and orientation for your next start.',
      detection: 'Object detection', detectionHint: 'Use the device NPU to recognize objects. Turn off for camera preview only.',
      noModels: 'No detection models configured', loadingModels: 'Waiting for device settings', availableModel: 'Configured on device',
      advanced: 'Advanced capture settings', advancedHint: 'Frame rate and detection threshold',
      maxFps: 'Maximum frame rate', maxFpsHint: '1–{max} frames/s. Actual speed depends on device load.',
      fpsError: 'Enter a whole number from 1 to {max}.', threshold: 'Minimum confidence (%)',
      thresholdHint: '5–95. Raise the threshold to exclude less certain detections.', thresholdError: 'Enter a value from 5 to 95.',
      settingsInvalid: 'Check the capture values and selected detection model.',
      model: 'Detection model', defaultModel: 'Default model', alternativeModel: 'Alternative model',
      rotation: 'Image orientation', upright: 'Original orientation', clockwise: '{degrees}° clockwise',
      settingPending: 'Your choices will be sent to the device when you next start vision.', settingsLocked: 'Stop the current task to adjust settings for your next start.',
      details: 'Processing details', detailsHint: 'Detection data for the current frame',
      inference: 'Inference call', processing: 'Detection processing', age: 'Frame age at detection', frame: 'Frame number', resolution: 'Image size', actualModel: 'Current model',
      timingHint: 'The inference call includes data transfer and output conversion. Detection processing covers the full detect call. Frame age spans capture to detection completion and excludes web transport and display.',
      privacy: 'Images come from your Duck’s camera',
      captureStop: 'Capture stops when you stop vision, leave this page or disconnect.',
      privacyHint: 'This page shows only the current frame and detections. It does not save image history.',
      errors: {
        invalid_config: 'Invalid capture or detection settings. Check your choices and try again.',
        cancelled: 'The vision task was cancelled.', invalid_state: 'The vision task state changed. Wait for the device to update it.',
        dependency_unavailable: 'The device’s vision environment is not ready.',
        device_unavailable: 'The camera is unavailable. Check your Duck.',
        device_busy: 'Another task is using the camera or NPU. Stop that task first.',
        capture_failed: 'Camera capture failed. Check the device and try again.',
        capture_ended: 'The camera stream ended. Please start vision again.',
        timeout: 'The camera did not return a frame in time. Check the device and try again.',
        invalid_frame: 'The frame could not be used. Please start vision again.',
        cleanup_failed: 'Stopping is incomplete and device resources have not been released. Check the vision service before starting again.',
        unsupported_model: 'The detection model is unsupported. Check the model configuration.',
        model_unavailable: 'The detection model is unavailable. Check the model files on your Duck.',
        model_load_failed: 'The detection model could not load. Check the device environment.',
        runtime_unavailable: 'The NPU is unavailable. Check the device’s vision environment.',
        invalid_output: 'The model returned unusable detections. Check its configuration.',
        inference_failed: 'Object detection failed. Please try again shortly.',
        busy: 'Vision is still processing. Please wait before trying again.',
        not_owner: 'Another page controls this vision task.', not_ready: 'Vision is not ready. Check the device state.',
        restart_required: 'Stop the current task before changing capture settings.', closed: 'Vision service is closed. Wait for it to return.',
        invalid_request: 'The capture request was invalid. Check the settings and try again.', encode_failed: 'The camera image could not be encoded. Please start again.',
        frame_too_large: 'The image exceeds the transfer limit. Check the vision service.',
        command_timeout: 'The device did not confirm the request. Disconnected to stop capture.',
        protocol: 'The device returned unsupported vision data. The connection was closed.', decode_failed: 'The frame could not be decoded. Disconnected to stop capture.',
        status_unavailable: 'Vision status is unavailable. Check the device connection.', connection_timeout: 'The vision connection timed out. Check the device and network.',
        unknown: 'The vision action failed. Check the device and try again.',
      },
    },
  },
})

const errorCodes = new Set(['invalid_config', 'cancelled', 'invalid_state', 'dependency_unavailable', 'device_unavailable', 'device_busy', 'capture_failed', 'capture_ended', 'timeout', 'invalid_frame', 'cleanup_failed', 'unsupported_model', 'model_unavailable', 'model_load_failed', 'runtime_unavailable', 'invalid_output', 'inference_failed', 'busy', 'not_owner', 'not_ready', 'restart_required', 'closed', 'invalid_request', 'encode_failed', 'frame_too_large', 'command_timeout', 'protocol', 'decode_failed', 'status_unavailable', 'connection_timeout'])
const active = computed(() => ['starting', 'streaming', 'stopping'].includes(props.state.status))
const occupied = computed(() => active.value && !props.state.owned)
const streaming = computed(() => props.state.connection === 'online' && props.state.status === 'streaming' && props.state.owned && pending.value !== 'stop')
const showStop = computed(() => props.state.owned && (active.value || props.state.status === 'error'))
const canEdit = computed(() => !pending.value && !props.state.owned && !active.value)
const availableModels = computed<readonly VisionModel[]>(() => props.state.capabilities?.available_models ?? [])
const rotations = computed<readonly CameraRotation[]>(() => props.state.capabilities?.rotations ?? [0, 90, 180, 270])
const fpsLimit = computed(() => props.state.capabilities?.max_fps ?? 15)
const fpsValid = computed(() => maxFps.value !== '' && Number.isInteger(Number(maxFps.value)) && Number(maxFps.value) >= 1 && Number(maxFps.value) <= fpsLimit.value)
const thresholdValid = computed(() => scoreThreshold.value !== '' && Number.isFinite(Number(scoreThreshold.value)) && Number(scoreThreshold.value) >= 5 && Number(scoreThreshold.value) <= 95)
const draft = computed<VisionSettings>(() => ({ model: model.value, rotation: rotation.value, detection_enabled: detectionEnabled.value,
  max_fps: Number(maxFps.value), score_threshold: Number(scoreThreshold.value) / 100 }))
const shownSettings = computed(() => (active.value || props.state.owned) && props.state.settings ? props.state.settings : draft.value)
const settingsValid = computed(() => fpsValid.value && thresholdValid.value && rotations.value.includes(rotation.value)
  && (!detectionEnabled.value || availableModels.value.includes(model.value)))
const canStart = computed(() => props.state.connection === 'online' && props.state.ready !== false && !pending.value && !props.state.owned
  && ['idle', 'error'].includes(props.state.status) && props.state.errorCode !== 'cleanup_failed' && settingsValid.value)
const canStop = computed(() => props.state.connection === 'online' && !pending.value && showStop.value
  && props.state.status !== 'stopping' && props.state.errorCode !== 'cleanup_failed' && props.state.status !== 'closed')
const result = computed(() => streaming.value && frameFresh.value && shownSettings.value.detection_enabled ? renderedResult.value : null)
const detections = computed(() => result.value?.detections.slice(0, 20) ?? [])
const errorText = computed(() => props.state.errorCode ? t(`errors.${errorCodes.has(props.state.errorCode) ? props.state.errorCode : 'unknown'}`) : '')
const statusText = computed(() => {
  if (props.state.connection !== 'online') return t(props.state.connection)
  if (occupied.value) return t('occupied')
  if (pending.value) return t(pending.value === 'start' ? 'requestingStart' : 'requestingStop')
  if (props.state.ready === false && props.state.status === 'idle') return t('notReady')
  return t(`states.${props.state.status}`)
})
const statusHint = computed(() => {
  if (props.state.connection !== 'online') return t(`${props.state.connection}Hint`)
  if (occupied.value) return t('occupiedHint')
  if (pending.value) return t('requestingHint')
  if (props.state.ready === false && props.state.status === 'idle') return t('notReadyHint')
  if (canEdit.value && !settingsValid.value) return t('settingsInvalid')
  return t(`${props.state.status}Hint`)
})
const resultEmptyTitle = computed(() => !shownSettings.value.detection_enabled ? t('detectionOff') : !streaming.value ? t('resultsEmpty')
  : !frameFresh.value ? t(props.state.frame ? 'resultsPaused' : 'resultsEmpty')
    : result.value ? t('noObjects') : t('resultsWaiting'))
const resultEmptyHint = computed(() => !shownSettings.value.detection_enabled ? t('detectionOffHint') : !streaming.value ? t('resultsEmptyHint')
  : !frameFresh.value ? t(props.state.frame ? 'resultsPausedHint' : 'streamingHint')
    : result.value ? t('noObjectsHint') : t('resultsWaitingHint'))
const modelOptions = computed(() => availableModels.value.map(value => ({ value, label: modelLabels[value],
  description: value === props.state.capabilities?.default_settings.model ? t('defaultModel') : t('availableModel') })))
const rotationOptions = computed(() => rotations.value.map(value => ({ value: String(value), label: value ? t('clockwise', { degrees: value }) : t('upright') })))
const rotationLabel = computed(() => shownSettings.value.rotation ? t('clockwise', { degrees: shownSettings.value.rotation }) : t('upright'))

watch(() => props.state.capabilities, capabilities => {
  if (!capabilities || defaultsLoaded) return
  defaultsLoaded = true
  const defaults = capabilities.default_settings
  model.value = defaults.model
  rotation.value = defaults.rotation
  detectionEnabled.value = defaults.detection_enabled
  maxFps.value = defaults.max_fps
  scoreThreshold.value = Math.round(defaults.score_threshold * 10000) / 100
}, { immediate: true })
watch(() => props.state.connection, value => emit('connection', value === 'unavailable' ? 'unknown' : value), { immediate: true })
// Controlled sessions own their pending state, including error replies that leave
// the capture state unchanged. Standalone fixtures can use local request locking.
watch([
  () => props.state.status, () => props.state.connection,
  () => props.state.owned, () => props.state.errorCode,
], () => { localPending.value = null })

function requestToggle(): void {
  if (showStop.value && canStop.value) {
    localPending.value = 'stop'
    emit('stop')
  } else if (!showStop.value && canStart.value) {
    localPending.value = 'start'
    emit('start', { ...draft.value })
  }
}

function selectModel(value: string): void {
  if (canEdit.value && availableModels.value.includes(value as VisionModel)) model.value = value as VisionModel
}

function selectRotation(value: string): void {
  const degrees = Number(value)
  if (canEdit.value && rotations.value.includes(degrees as CameraRotation)) rotation.value = degrees as CameraRotation
}

function toggleDetection(): void {
  if (canEdit.value && (detectionEnabled.value || availableModels.value.length)) detectionEnabled.value = !detectionEnabled.value
}

function milliseconds(value: number | null | undefined): string {
  return value != null && Number.isFinite(value) ? `${value.toLocaleString(locale.value, { maximumFractionDigits: 1 })} ms` : '—'
}

function confidence(score: number): string {
  return score.toLocaleString(locale.value, { style: 'percent', maximumFractionDigits: 0 })
}
</script>

<template>
  <div class="vision-workspace ui-page">
    <section class="vision-console" :aria-labelledby="`${id}-title`">
      <InteractionControl :title-id="`${id}-title`" :hint-id="`${id}-status-hint`" :title="t('title')" :description="t('subtitle')" :icon="Camera"
        :action-icon="showStop ? Square : Camera"
        :action-label="pending ? t(pending === 'start' ? 'requestingStart' : 'requestingStop') : showStop ? t(state.errorCode === 'cleanup_failed' ? 'incompleteStop' : 'stop') : t('start')"
        :disabled="showStop ? !canStop : !canStart" :active="showStop" :busy="!!pending || state.status === 'starting' || state.status === 'stopping'"
        :status="statusText" :hint="statusHint" :status-state="state.connection === 'online' ? state.status : state.connection"
        :tone="state.connection === 'offline' || !!errorText ? 'error' : streaming ? 'active' : 'neutral'" @action="requestToggle" />
      <UiAlert v-if="errorText" class="error-note">{{ errorText }}</UiAlert>

      <div class="vision-grid">
        <div class="preview-panel">
          <div class="preview-toolbar">
            <span class="camera-source"><Camera :size="16" aria-hidden="true" />{{ t('boardCamera') }}</span>
            <button type="button" class="overlay-toggle ui-button" :aria-pressed="showBoxes" :title="t('boxesHint')" @click="showBoxes = !showBoxes">
              <ScanLine :size="17" aria-hidden="true" /><span>{{ t('boxes') }}</span><span class="toggle-track" aria-hidden="true"><span></span></span>
            </button>
          </div>
          <VisionFrame :frame="state.frame" :result="state.result" :streaming="streaming" :show-boxes="showBoxes && shownSettings.detection_enabled"
            :placeholder-title="statusText" :placeholder-hint="statusHint" @freshness="frameFresh = $event" @detections="renderedResult = $event"
            @displayed="emit('displayed', $event)" @release="emit('release', $event)" @decode-error="emit('decodeError', $event)" />
          <div class="preview-caption"><span><Cpu :size="15" aria-hidden="true" />{{ t(shownSettings.detection_enabled ? 'localDetection' : 'previewOnly') }}</span><span>{{ t('temporary') }}</span></div>
        </div>

        <section class="targets-panel" :aria-labelledby="`${id}-objects`">
          <header class="targets-heading"><h3 :id="`${id}-objects`">{{ t('results') }}</h3><span class="object-count">{{ result ? t('resultCount', { count: detections.length }) : '—' }}</span></header>
          <template v-if="detections.length">
            <p class="confidence-heading">{{ t('confidence') }}</p>
            <ul class="object-list" tabindex="0" :aria-labelledby="`${id}-objects`">
              <li v-for="(detection, index) in detections" :key="index">
                <span class="object-number" aria-hidden="true">{{ String(index + 1).padStart(2, '0') }}</span>
                <span class="object-name">{{ detectionLabel(detection, locale) }}</span>
                <span class="confidence">{{ confidence(detection.score) }}</span>
              </li>
            </ul>
          </template>
          <UiEmptyState v-else class="targets-empty" :icon="View" :title="resultEmptyTitle" :description="resultEmptyHint" />
        </section>
      </div>
    </section>

    <section class="settings-panel" :aria-labelledby="`${id}-settings`">
      <div class="settings-intro"><span class="ui-section-icon"><SlidersHorizontal :size="24" :stroke-width="1.6" aria-hidden="true" /></span><div><h2 :id="`${id}-settings`" class="ui-section-title">{{ t('settings') }}</h2><p class="ui-section-description">{{ t('settingsHint') }}</p></div></div>
      <div class="settings-fields">
        <div class="detection-setting">
          <div><span :id="`${id}-detection-label`" class="field-label">{{ t('detection') }}</span><p :id="`${id}-detection-hint`" class="field-hint">{{ t('detectionHint') }}</p></div>
          <button type="button" class="detection-switch ui-icon-button" role="switch" :aria-checked="shownSettings.detection_enabled"
            :aria-labelledby="`${id}-detection-label`" :aria-describedby="`${id}-detection-hint`"
            :disabled="!canEdit || (!detectionEnabled && !availableModels.length)" @click="toggleDetection">
            <span class="toggle-track" aria-hidden="true"><span></span></span>
          </button>
        </div>
        <div class="setting-field"><span class="field-label">{{ t('model') }}</span><PreferenceSelect v-if="canEdit && modelOptions.length" :label="t('model')" :model-value="model" :options="modelOptions" align="start" @update:model-value="selectModel" /><span v-else class="locked-value">{{ availableModels.length ? modelLabels[shownSettings.model] : t(state.capabilities ? 'noModels' : 'loadingModels') }}</span></div>
        <div class="setting-field"><span class="field-label">{{ t('rotation') }}</span><PreferenceSelect v-if="canEdit" :label="t('rotation')" :model-value="String(rotation)" :options="rotationOptions" align="start" @update:model-value="selectRotation" /><span v-else class="locked-value">{{ rotationLabel }}</span></div>
        <details class="advanced-settings">
          <summary><span>{{ t('advanced') }}<small>{{ t('advancedHint') }}</small></span><ChevronDown :size="17" aria-hidden="true" /></summary>
          <div class="advanced-fields">
            <div class="setting-field">
              <label :for="`${id}-fps`" class="field-label">{{ t('maxFps') }}</label>
              <input v-if="canEdit" :id="`${id}-fps`" v-model.number="maxFps" type="number" inputmode="numeric" min="1" :max="fpsLimit" step="1"
                :aria-invalid="!fpsValid" :aria-describedby="`${id}-fps-hint${fpsTouched && !fpsValid ? ` ${id}-fps-error` : ''}`" @blur="fpsTouched = true" />
              <input v-else :id="`${id}-fps`" type="number" :value="shownSettings.max_fps" disabled :aria-describedby="`${id}-fps-hint`" />
              <p :id="`${id}-fps-hint`" class="field-hint">{{ t('maxFpsHint', { max: fpsLimit }) }}</p>
              <p v-if="fpsTouched && !fpsValid" :id="`${id}-fps-error`" class="field-error">{{ t('fpsError', { max: fpsLimit }) }}</p>
            </div>
            <div class="setting-field">
              <label :for="`${id}-threshold`" class="field-label">{{ t('threshold') }}</label>
              <input v-if="canEdit" :id="`${id}-threshold`" v-model.number="scoreThreshold" type="number" inputmode="decimal" min="5" max="95" step="any"
                :aria-invalid="!thresholdValid" :aria-describedby="`${id}-threshold-hint${thresholdTouched && !thresholdValid ? ` ${id}-threshold-error` : ''}`" @blur="thresholdTouched = true" />
              <input v-else :id="`${id}-threshold`" type="number" :value="Number((shownSettings.score_threshold * 100).toFixed(2))" disabled :aria-describedby="`${id}-threshold-hint`" />
              <p :id="`${id}-threshold-hint`" class="field-hint">{{ t('thresholdHint') }}</p>
              <p v-if="thresholdTouched && !thresholdValid" :id="`${id}-threshold-error`" class="field-error">{{ t('thresholdError') }}</p>
            </div>
          </div>
        </details>
        <p class="settings-footnote">{{ t(canEdit ? 'settingPending' : 'settingsLocked') }}</p>
      </div>
    </section>

    <details class="processing-details">
      <summary><span class="details-label">{{ t('details') }}<small>{{ t('detailsHint') }}</small></span><ChevronDown :size="18" aria-hidden="true" /></summary>
      <div class="details-body">
        <dl class="metrics-grid">
          <div><dt>{{ t('inference') }}</dt><dd>{{ milliseconds(result?.inference_call_ms) }}</dd></div>
          <div><dt>{{ t('processing') }}</dt><dd>{{ milliseconds(result?.total_ms) }}</dd></div>
          <div><dt>{{ t('age') }}</dt><dd>{{ milliseconds(result?.frame_age_ms) }}</dd></div>
          <div><dt>{{ t('frame') }}</dt><dd>{{ result?.frame_id ?? '—' }}</dd></div>
          <div><dt>{{ t('resolution') }}</dt><dd>{{ result ? `${result.image_size[0]} × ${result.image_size[1]}` : '—' }}</dd></div>
          <div><dt>{{ t('actualModel') }}</dt><dd>{{ result ? modelLabels[result.model_name] : '—' }}</dd></div>
        </dl>
        <p class="timing-note">{{ t('timingHint') }}</p>
      </div>
    </details>

    <CaptureNotice :source="t('privacy')" :notes="[t('captureStop'), t('privacyHint')]" />
  </div>
</template>

<style scoped>
.vision-console, .settings-panel, .processing-details { min-width: 0; border: 1px solid var(--border); border-radius: var(--radius-panel, 24px); background: var(--surface); box-shadow: var(--shadow-soft); }
.vision-console { padding: 26px; }
h2, h3, h4, p { margin: 0; }
summary:focus-visible, input:focus-visible, .object-list:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
.error-note { margin-bottom: 20px; }
.vision-grid { display: grid; grid-template-columns: minmax(0, 1.8fr) minmax(240px, 1fr); gap: 24px; }
.preview-panel { min-width: 0; }
.preview-toolbar, .preview-caption { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px 14px; }
.preview-toolbar { margin: -3px 0 10px; }
.camera-source, .preview-caption span { display: inline-flex; align-items: center; gap: 7px; }
.camera-source { color: var(--muted); font-size: 12px; }
.overlay-toggle { color: var(--muted); }
.overlay-toggle[aria-pressed="true"] { color: var(--accent); }
.toggle-track { position: relative; width: 26px; height: 16px; flex-shrink: 0; padding: 3px; border-radius: 10px; background: var(--muted); }
.toggle-track > span { display: block; width: 10px; height: 10px; border-radius: 50%; background: var(--surface); }
[aria-pressed="true"] .toggle-track { background: var(--accent); }
[aria-pressed="true"] .toggle-track > span { transform: translateX(10px); }
.preview-caption { padding: 13px 3px 0; color: var(--muted); font-size: 12px; }
.targets-panel { display: flex; flex-direction: column; min-width: 0; padding: 20px; border: 1px solid var(--border); border-radius: 20px; background: var(--background); }
.targets-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.targets-heading h3 { color: var(--text); font-size: 14px; font-weight: 600; }
.object-count { color: var(--muted); font-size: 12px; }
.confidence-heading { margin-top: 18px; text-align: end; color: var(--muted); font-size: 12px; }
.object-list { max-height: 340px; margin: 6px -4px 0; padding: 0 4px; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; list-style: none; }
.object-list li { display: flex; align-items: center; gap: 10px; min-height: 52px; border-bottom: 1px solid var(--border); }
.object-list li:last-child { border: 0; }
.object-number { display: grid; place-items: center; flex-shrink: 0; width: 28px; height: 28px; border-radius: 9px; background: var(--surface); color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.object-name { flex: 1; min-width: 0; color: var(--text); font-size: 13px; overflow-wrap: anywhere; }
.confidence { color: var(--accent); font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.targets-empty { flex: 1; min-height: 230px; }
/* Keep its menus above sibling cards, below shell navigation and preferences. */
.settings-panel { position: relative; z-index: 1; display: grid; grid-template-columns: minmax(180px, .9fr) minmax(0, 1.4fr); gap: 24px; padding: 26px; }
.settings-intro { display: flex; align-items: flex-start; gap: 14px; }
.settings-intro > div { min-width: 0; }
.settings-intro p { max-width: 28ch; margin-top: 5px; }
.settings-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 16px; }
.setting-field { min-width: 0; }
.field-label { display: block; margin-bottom: 8px; color: var(--text); font-size: 13px; font-weight: 500; line-height: 1.6; }
.setting-field :deep(.preference-select) { min-width: 0; }
.setting-field :deep(.preference-trigger) { background: var(--background); }
.setting-field :deep(.trigger-label) { white-space: normal; overflow-wrap: anywhere; }
.locked-value { display: flex; align-items: center; min-height: 46px; padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius-control, 16px); background: var(--background); color: var(--muted); font-size: 13px; }

.detection-setting { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-bottom: 8px; }
.detection-setting > div { min-width: 0; }
.detection-setting .field-label { margin-bottom: 4px; color: var(--text); font-weight: 500; }
.detection-switch { background: var(--background); }
.detection-switch:disabled { opacity: .6; }
.detection-switch[aria-checked="true"] .toggle-track { background: var(--accent); }
.detection-switch[aria-checked="true"] .toggle-track > span { transform: translateX(10px); }
.field-hint { margin-top: 7px; color: var(--muted); font-size: 12px; line-height: 1.7; }
.field-error { margin-top: 6px; color: var(--offline); font-size: 12px; line-height: 1.7; }
.setting-field input { display: block; width: 100%; min-width: 0; min-height: 46px; padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius-control, 16px); background: var(--background); color: var(--text); font: inherit; font-size: 14px; }
.setting-field input:disabled { color: var(--muted); opacity: .7; }
.setting-field input[aria-invalid="true"] { border-color: var(--offline); }
.advanced-settings { grid-column: 1 / -1; margin-top: 4px; border-top: 1px solid var(--border); }
.advanced-settings summary { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 52px; padding: 12px 2px; border-radius: 10px; cursor: pointer; list-style: none; color: var(--text); font-size: 12px; }
.advanced-settings summary::-webkit-details-marker { display: none; }
.advanced-settings summary small { display: block; margin-top: 3px; color: var(--muted); font-size: 12px; }
.advanced-settings summary svg { flex-shrink: 0; }
.advanced-settings[open] summary svg { transform: rotate(180deg); }
.advanced-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; padding-block: 4px 10px; }

.settings-footnote { grid-column: 1 / -1; color: var(--muted); font-size: 12px; line-height: 1.7; }
.processing-details summary { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 66px; padding: 18px 26px; border-radius: var(--radius-panel, 24px); cursor: pointer; list-style: none; color: var(--muted); touch-action: manipulation; }
.processing-details summary::-webkit-details-marker { display: none; }
.details-label { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 16px; color: var(--text); font-size: 13px; font-weight: 500; }
.details-label small { color: var(--muted); font-size: 12px; font-weight: 400; }
.processing-details summary > svg { flex-shrink: 0; }
.processing-details[open] summary > svg { transform: rotate(180deg); }
.details-body { padding: 0 26px 24px; }
.metrics-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 22px 24px; margin: 0; padding: 20px 0; border-top: 1px solid var(--border); }
.metrics-grid dt { color: var(--muted); font-size: 12px; }
.metrics-grid dd { margin: 7px 0 0; color: var(--text); font-size: 16px; font-weight: 500; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.timing-note { max-width: 95ch; color: var(--muted); font-size: 12px; line-height: 1.8; }
@media (max-width: 1190px) and (min-width: 960px), (max-width: 800px) {
  .vision-grid { grid-template-columns: minmax(0, 1fr); }
  .targets-empty { min-height: 180px; padding-block: 20px; }
  .settings-panel { grid-template-columns: minmax(0, 1fr); gap: 20px; }
  .settings-intro p { max-width: none; }
}
@media (max-width: 539px) {
  .vision-console, .settings-panel { padding: 20px 16px; }
  .vision-grid { gap: 20px; }
  .targets-panel { padding: 18px; }
  .targets-empty { min-height: 174px; }
  .preview-toolbar { gap: 4px; }
  .overlay-toggle > svg { display: none; }
  .preview-caption { font-size: 12px; }
  .settings-fields, .advanced-fields { grid-template-columns: minmax(0, 1fr); gap: 16px; }
  .setting-field input { font-size: 16px; }
  .processing-details summary { padding: 18px 20px; }
  .details-label { display: block; }
  .details-label small { display: block; margin-top: 4px; }
  .details-body { padding: 0 20px 20px; }
  .metrics-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px 16px; }
  .metrics-grid dd { font-size: 14px; }
}
@media (hover: hover) and (pointer: fine) {
  .advanced-settings summary:hover { background: var(--background); }
  .processing-details summary:hover { background: var(--background); }
}
@media (prefers-reduced-motion: no-preference) {
  .processing-details summary { transition: background-color 180ms ease; }
  .toggle-track, .toggle-track > span { transition: background-color 160ms ease, transform 160ms var(--ease-out, ease-out); }
  .processing-details summary > svg { transition: transform 180ms var(--ease-out, ease-out); }
}
</style>
