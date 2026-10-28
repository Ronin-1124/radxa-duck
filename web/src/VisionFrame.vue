<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Camera, Maximize, Minimize, Pause, ScanLine } from '@lucide/vue'
import type { DetectionResult, VisionFrame, VisionResult } from './vision-types'
import { useVisionDisplay } from './useVisionDisplay'
import { detectionLabel } from './vision-labels'

const props = defineProps<{
  frame: VisionFrame | null
  result: VisionResult | null
  streaming: boolean
  showBoxes: boolean
  placeholderTitle: string
  placeholderHint: string
}>()
const emit = defineEmits<{
  freshness: [value: boolean]
  detections: [value: DetectionResult | null]
  displayed: [frame: VisionFrame]
  release: [frame: VisionFrame]
  decodeError: [frame: VisionFrame]
}>()
const { t, locale } = useI18n({
  useScope: 'local', inheritLocale: true,
  messages: {
    'zh-CN': {
      alt: '小鸭摄像头当前画面', live: '画面更新中', stale: '画面暂未更新',
      staleHint: '这是最后收到的画面，目标标注已隐藏。', decoding: '正在加载画面…',
      imageError: '这一帧无法显示', imageErrorHint: '等待下一帧画面。',
      fullscreen: '全屏查看画面', exitFullscreen: '退出全屏', fullscreenError: '暂时无法进入全屏。',
    },
    en: {
      alt: 'Current view from your Duck’s camera', live: 'Frames arriving', stale: 'No recent frames',
      staleHint: 'Showing the last received frame. Annotations are hidden.', decoding: 'Loading frame…',
      imageError: 'This frame could not be displayed', imageErrorHint: 'Waiting for the next frame.',
      fullscreen: 'View camera fullscreen', exitFullscreen: 'Exit fullscreen', fullscreenError: 'Fullscreen is currently unavailable.',
    },
  },
})

const viewport = ref<HTMLElement>()
const imageHost = ref<HTMLElement>()
const viewportWidth = ref(640)
const viewportHeight = ref(480)
const fullscreenSupported = ref(false)
const fullscreen = ref(false)
const fullscreenError = ref(false)
const clipId = useId()
let resizeObserver: ResizeObserver | undefined
let firstPaint: number | undefined
let secondPaint: number | undefined
let disposed = false
const acknowledgedImages = new WeakSet<HTMLImageElement>()

const incomingFrame = computed(() => {
  const item = props.frame
  return props.streaming && item && Number.isFinite(item.width) && item.width > 0
    && Number.isFinite(item.height) && item.height > 0 && Number.isFinite(item.receivedAt) ? item : null
})
const { frame, image, fresh, failed, detections: result } = useVisionDisplay(() => incomingFrame.value, () => props.result, {
  release: value => emit('release', value),
  decodeError: value => emit('decodeError', value),
})
const loaded = computed(() => !!image.value)
const fit = computed(() => {
  if (!frame.value) return { width: '100%', height: '100%' }
  const ratio = frame.value.width / frame.value.height
  const containerRatio = viewportWidth.value / viewportHeight.value
  return ratio >= containerRatio
    ? { width: '100%', height: `${100 * containerRatio / ratio}%` }
    : { width: `${100 * ratio / containerRatio}%`, height: '100%' }
})
const pixelScale = computed(() => frame.value
  ? Math.max(frame.value.width / viewportWidth.value, frame.value.height / viewportHeight.value) : 1)
const boxes = computed(() => {
  const current = frame.value
  if (!current || !props.showBoxes || !result.value) return []
  return result.value.detections.flatMap((item, index) => {
    const [x1, y1, x2, y2] = item.box_xyxy
    if (![x1, y1, x2, y2, item.score].every(Number.isFinite) || x2 <= x1 || y2 <= y1) return []
    const label = `${index + 1} · ${detectionLabel(item, locale.value)} ${Math.round(item.score * 100)}%`
    const scale = pixelScale.value
    const labelHeight = 24 * scale
    // Wider CJK glyphs need more room than Latin glyphs. Clip within the frame at every size.
    const labelWidth = Math.min(current.width, (Array.from(label).reduce((width, char) => width + (/[^\u0000-\u007f]/.test(char) ? 13 : 7), 0) + 16) * scale)
    return [{ index, x1, y1, width: x2 - x1, height: y2 - y1, label,
      labelX: Math.max(0, Math.min(x1, current.width - labelWidth)),
      labelY: Math.max(0, Math.min(y1 >= labelHeight ? y1 - labelHeight : y1, current.height - labelHeight)),
      labelWidth, labelHeight,
    }]
  })
})

watch(fresh, value => emit('freshness', value), { immediate: true, flush: 'sync' })
watch(result, value => emit('detections', value), { immediate: true, flush: 'sync' })
watch([frame, image, () => props.streaming, failed], cancelDisplayAcknowledgement, { flush: 'sync' })
watch([image, imageHost], () => {
  if (image.value) image.value.alt = t('alt')
  imageHost.value?.replaceChildren(...(image.value ? [image.value] : []))
  scheduleDisplayAcknowledgement()
}, { flush: 'post' })
watch(locale, () => { if (image.value) image.value.alt = t('alt') })

function cancelDisplayAcknowledgement(): void {
  if (firstPaint !== undefined) cancelAnimationFrame(firstPaint)
  if (secondPaint !== undefined) cancelAnimationFrame(secondPaint)
  firstPaint = secondPaint = undefined
}

function scheduleDisplayAcknowledgement(): void {
  cancelDisplayAcknowledgement()
  const displayedFrame = frame.value
  const displayedImage = image.value
  const stillDisplayed = (): boolean => !disposed && props.streaming && !failed.value
    && document.visibilityState === 'visible' && frame.value === displayedFrame && image.value === displayedImage
    && !!displayedImage?.isConnected && imageHost.value?.firstChild === displayedImage
  if (!displayedFrame || !displayedImage || acknowledgedImages.has(displayedImage) || !stillDisplayed()) return
  // Vue has patched the image and SVG. Two rendering callbacks leave a paint
  // opportunity between them before granting the server credit for another frame.
  firstPaint = requestAnimationFrame(() => {
    firstPaint = undefined
    if (!stillDisplayed()) return
    secondPaint = requestAnimationFrame(() => {
      secondPaint = undefined
      if (!stillDisplayed()) return
      acknowledgedImages.add(displayedImage)
      emit('displayed', displayedFrame)
    })
  })
}

function visibilityChanged(): void {
  if (document.visibilityState === 'visible') scheduleDisplayAcknowledgement()
  else cancelDisplayAcknowledgement()
}

function fullscreenChanged(): void { fullscreen.value = document.fullscreenElement === viewport.value }
async function toggleFullscreen(): Promise<void> {
  fullscreenError.value = false
  try {
    if (fullscreen.value) await document.exitFullscreen()
    else await viewport.value?.requestFullscreen()
  } catch { fullscreenError.value = true }
}

onMounted(() => {
  fullscreenSupported.value = document.fullscreenEnabled
  document.addEventListener('fullscreenchange', fullscreenChanged)
  document.addEventListener('visibilitychange', visibilityChanged)
  resizeObserver = new ResizeObserver(entries => {
    const rect = entries[0]?.contentRect
    if (rect && rect.width > 0 && rect.height > 0) {
      viewportWidth.value = rect.width
      viewportHeight.value = rect.height
    }
  })
  if (viewport.value) resizeObserver.observe(viewport.value)
})
onBeforeUnmount(() => {
  disposed = true
  cancelDisplayAcknowledgement()
  resizeObserver?.disconnect()
  document.removeEventListener('fullscreenchange', fullscreenChanged)
  document.removeEventListener('visibilitychange', visibilityChanged)
  if (document.fullscreenElement === viewport.value) void document.exitFullscreen().catch(() => {})
})
</script>

<template>
  <div ref="viewport" class="vision-frame" :class="{ 'has-image': loaded }">
    <div v-if="frame" class="image-plane" :style="fit">
      <div ref="imageHost" class="image-host"></div>
      <svg v-if="boxes.length" class="annotations" :viewBox="`0 0 ${frame.width} ${frame.height}`" aria-hidden="true">
        <defs><clipPath :id="clipId"><rect :width="frame.width" :height="frame.height" /></clipPath></defs>
        <g :clip-path="`url(#${clipId})`">
          <g v-for="box in boxes" :key="box.index" class="detection-box">
            <rect class="box-outline" :x="box.x1" :y="box.y1" :width="box.width" :height="box.height"
              :rx="4 * pixelScale" vector-effect="non-scaling-stroke" />
            <rect class="box-label" :x="box.labelX" :y="box.labelY" :width="box.labelWidth" :height="box.labelHeight" :rx="4 * pixelScale" />
            <text :x="box.labelX + 8 * pixelScale" :y="box.labelY + 16 * pixelScale" :font-size="12 * pixelScale">{{ box.label }}</text>
          </g>
        </g>
      </svg>
    </div>

    <div v-if="!loaded" class="frame-placeholder">
      <div class="lens-mark" aria-hidden="true"><Camera :size="34" :stroke-width="1.4" /></div>
      <p class="placeholder-title">{{ failed ? t('imageError') : incomingFrame ? t('decoding') : placeholderTitle }}</p>
      <p class="placeholder-hint">{{ failed ? t('imageErrorHint') : !incomingFrame ? placeholderHint : '' }}</p>
      <span class="viewfinder-corner corner-tl" aria-hidden="true"></span>
      <span class="viewfinder-corner corner-tr" aria-hidden="true"></span>
      <span class="viewfinder-corner corner-bl" aria-hidden="true"></span>
      <span class="viewfinder-corner corner-br" aria-hidden="true"></span>
    </div>

    <div v-if="loaded" class="frame-status" :class="{ stale: !fresh }" role="status">
      <ScanLine v-if="fresh" :size="15" aria-hidden="true" /><Pause v-else :size="15" aria-hidden="true" />
      <span>{{ t(fresh ? 'live' : 'stale') }}</span>
    </div>
    <p v-if="loaded && !fresh" class="stale-hint">{{ t(failed ? 'imageErrorHint' : 'staleHint') }}</p>
    <button v-if="fullscreenSupported" type="button" class="fullscreen-button ui-icon-button" :disabled="!loaded && !fullscreen"
      :aria-label="t(fullscreen ? 'exitFullscreen' : 'fullscreen')" :title="t(fullscreen ? 'exitFullscreen' : 'fullscreen')" @click="toggleFullscreen">
      <Minimize v-if="fullscreen" :size="18" aria-hidden="true" /><Maximize v-else :size="18" aria-hidden="true" />
    </button>
    <p v-if="fullscreenError" class="fullscreen-error" role="status">{{ t('fullscreenError') }}</p>
  </div>
</template>

<style scoped>
.vision-frame {
  --viewer-background: #151e19; --viewer-surface: #213127; --viewer-text: #e7eee5;
  --viewer-muted: #b4c2b7; --viewer-line: #54705a; --viewer-accent: #c6f1a4;
  position: relative; isolation: isolate; width: 100%; aspect-ratio: 4 / 3; overflow: hidden;
  border-radius: 18px; background: radial-gradient(ellipse at 50% 36%, var(--viewer-surface), var(--viewer-background) 85%);
  color: var(--viewer-text);
}
.vision-frame:fullscreen { width: 100%; height: 100%; aspect-ratio: auto; border-radius: 0; }
.image-plane { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); }
.image-host { width: 100%; height: 100%; }
.image-host :deep(img) { display: block; width: 100%; height: 100%; object-fit: contain; }
.annotations { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.box-outline { fill: rgb(198 241 164 / .06); stroke: var(--viewer-accent); stroke-width: 2; }
.box-label { fill: var(--viewer-background); fill-opacity: .94; }
.detection-box text { fill: var(--viewer-accent); font-family: var(--font, sans-serif); font-weight: 600; }
.frame-placeholder { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 28px; text-align: center; }
.lens-mark { display: grid; place-items: center; flex-shrink: 0; width: 72px; height: 72px; margin-bottom: 20px; border: 1px solid var(--viewer-line); border-radius: 24px; color: var(--viewer-accent); background: var(--viewer-surface); box-shadow: 0 0 0 12px rgb(198 241 164 / .025); }
.placeholder-title { margin: 0; font-size: clamp(14px, 1.5vw, 18px); line-height: 1.6; font-weight: 550; text-wrap: balance; }
.placeholder-hint { max-width: 34ch; margin: 10px 0 0; font-size: 12px; line-height: 1.7; color: var(--viewer-muted); text-wrap: pretty; }
.viewfinder-corner { position: absolute; width: 24px; height: 24px; border: 1px solid var(--viewer-line); opacity: .55; }
.corner-tl { left: 20px; top: 20px; border-right: 0; border-bottom: 0; border-radius: 10px 0 0; }
.corner-tr { right: 20px; top: 20px; border-left: 0; border-bottom: 0; border-radius: 0 10px 0 0; }
.corner-bl { left: 20px; bottom: 20px; border-right: 0; border-top: 0; border-radius: 0 0 0 10px; }
.corner-br { right: 20px; bottom: 20px; border-left: 0; border-top: 0; border-radius: 0 0 10px; }
.frame-status { position: absolute; top: 14px; left: 14px; display: flex; align-items: center; gap: 7px; padding: 7px 10px; border: 1px solid var(--viewer-line); border-radius: 12px; background: var(--viewer-background); color: var(--viewer-accent); font-size: 12px; }
.frame-status.stale { color: var(--viewer-text); }
.stale-hint, .fullscreen-error { position: absolute; left: 12px; right: 66px; bottom: 12px; margin: 0; padding: 8px 12px; border-radius: 12px; background: var(--viewer-background); color: var(--viewer-text); font-size: 12px; line-height: 1.6; }
.fullscreen-button.ui-icon-button { position: absolute; right: 12px; bottom: 12px; border-color: var(--viewer-line); background: var(--viewer-background); color: var(--viewer-text); }
.fullscreen-button.ui-icon-button:disabled { opacity: .35; cursor: default; }
.fullscreen-button.ui-icon-button:focus-visible { outline-color: var(--viewer-accent); }
.fullscreen-button.ui-icon-button:active:not(:disabled) { background: var(--viewer-surface); }
@media (hover: hover) and (pointer: fine) {
  .fullscreen-button.ui-icon-button:hover:not(:disabled) { color: var(--viewer-accent); background: var(--viewer-surface); border-color: var(--viewer-accent); }
}
@media (max-width: 420px) {
  .frame-placeholder { padding: 24px 18px; }
  .lens-mark { width: 52px; height: 52px; margin-bottom: 12px; border-radius: 18px; }
  .lens-mark svg { width: 26px; height: 26px; }
  .placeholder-hint { margin-top: 6px; }
  .viewfinder-corner { width: 16px; height: 16px; }
}
</style>
