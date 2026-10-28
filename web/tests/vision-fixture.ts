import { createApp, defineComponent, h, nextTick, shallowRef } from 'vue'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import AppShell from '../src/AppShell.vue'
import VisionWorkspace from '../src/VisionWorkspace.vue'
import { emptyVisionState, type ObjectDetection, type VisionCapabilities, type VisionSettings, type VisionViewState } from '../src/vision-types'

export type VisionEvent = { type: 'start'; settings: VisionSettings } | { type: 'stop' }
export interface SampleOptions {
  sessionId?: string
  frameId?: number
  width?: number
  height?: number
  url?: string
  detections?: ObjectDetection[]
}
export interface VisionFixture {
  state(): VisionViewState
  events(): VisionEvent[]
  setState(state: VisionViewState): Promise<void>
  patchState(patch: Partial<VisionViewState>): Promise<void>
  sample(options?: SampleOptions): VisionViewState
}
declare global { interface Window { visionFixture: VisionFixture } }

const settings: VisionSettings = { model: 'yolov8n', rotation: 0, detection_enabled: true, max_fps: 10, score_threshold: 0.35 }
const capabilities: VisionCapabilities = {
  transport: 'jpeg-websocket-v1', available_models: ['yolov8n', 'yolov5n'], rotations: [0, 90, 180, 270],
  default_settings: settings, max_fps: 15, max_jpeg_bytes: 262144, jpeg_quality: 75,
  capture_size: [640, 480], ack_timeout_seconds: 3,
}

// This entry is served only by Vite's test page. It is not a production build entry.
// Samples are presentation data, not a proposed network protocol or hardware result.
function sample(options: SampleOptions = {}): VisionViewState {
  const width = options.width ?? 640
  const height = options.height ?? 480
  const frameId = options.frameId ?? 1
  const sessionId = options.sessionId ?? 'simulated-session'
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#2b4534"/><circle cx="${width / 2}" cy="${height / 2}" r="${Math.min(width, height) / 5}" fill="#b1df83"/></svg>`
  return {
    connection: 'online', status: 'streaming', owned: true, errorCode: null, ready: true, capabilities,
    settings: { ...settings, rotation: width < height ? 90 : 0 },
    frame: {
      sessionId, id: frameId, width, height, receivedAt: performance.now(),
      url: options.url ?? `data:image/svg+xml,${encodeURIComponent(svg)}`,
    },
    result: {
      sessionId,
      data: {
        schema_version: 1, type: 'detections', provider: 'npu', backend: 'rknn-lite',
        model_name: 'yolov8n', model_sha256: '0'.repeat(64), precision: 'int8',
        input_size: [640, 640], initialization_ms: 10, frame_id: frameId,
        captured_monotonic: 100, image_size: [width, height],
        detections: options.detections ?? [{ class_id: 0, label: 'person', score: 0.9,
          box_xyxy: [width / 4, height / 4, 3 * width / 4, 3 * height / 4], center_normalized: [0.5, 0.5] }],
        preprocess_ms: 2, inference_call_ms: 12, postprocess_ms: 3, total_ms: 17, frame_age_ms: 25,
      },
    },
  }
}

const state = shallowRef<VisionViewState>({ ...emptyVisionState(), ready: true, settings, capabilities })
const events: VisionEvent[] = []
const connection = shallowRef<'unknown' | 'connecting' | 'online' | 'offline'>('unknown')
const router = createRouter({
  history: createMemoryHistory(),
  routes: ['overview', 'voice', 'vision', 'apps', 'device', 'settings'].map(name => ({
    path: `/${name}`, name, component: { render: () => null },
  })),
})
const i18n = createI18n({ legacy: false, locale: 'zh-CN', fallbackLocale: 'en', messages: {} })

await router.push('/vision')
await router.isReady()
createApp(defineComponent({
  setup: () => () => h(AppShell, { connection: connection.value }, {
    default: () => h(VisionWorkspace, {
      state: state.value,
      onStart: (settings: VisionSettings) => events.push({ type: 'start', settings }),
      onStop: () => events.push({ type: 'stop' }),
      onConnection: (value: typeof connection.value) => { connection.value = value },
    }),
  }),
})).use(router).use(i18n).mount('#app')

window.visionFixture = {
  state: () => state.value,
  events: () => events.slice(),
  setState: async value => { state.value = value; await nextTick() },
  patchState: async patch => { state.value = { ...state.value, ...patch }; await nextTick() },
  sample,
}
