import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { matchingDetections, type VisionFrame, type VisionResult } from './vision-types'

type Candidate = { frame: VisionFrame; result: VisionResult | null }
type Decode = Candidate & { image: HTMLImageElement; timer?: ReturnType<typeof setTimeout> }
type DisplayCallbacks = {
  release?: (frame: VisionFrame) => void
  decodeError?: (frame: VisionFrame) => void
}

function sameFrame(left: VisionFrame, right: VisionFrame): boolean {
  return left.sessionId === right.sessionId && left.id === right.id && left.url === right.url
    && left.width === right.width && left.height === right.height
}

/** Keeps a decoded frame on screen while decoding at most one frame and queuing only the latest. */
export function useVisionDisplay(source: () => VisionFrame | null, results: () => VisionResult | null, callbacks: DisplayCallbacks = {}) {
  const frame = shallowRef<VisionFrame | null>(null)
  const image = shallowRef<HTMLImageElement | null>(null)
  const matched = shallowRef<VisionResult | null>(null)
  const recent = shallowRef(false)
  const failed = shallowRef(false)
  let pending: Decode | null = null
  let queued: Candidate | null = null
  let sessionId: string | null = null
  let disposed = false
  let generation = 0
  let staleTimer: ReturnType<typeof setTimeout> | undefined
  // A URL can be reused by presentation fixtures. Release only after every slot
  // has stopped using it; the real transport creates a unique Blob URL per frame.
  const leases = new Map<string, VisionFrame>()

  const fresh = computed(() => !!image.value && recent.value && !failed.value)
  const detections = computed(() => fresh.value ? matchingDetections(frame.value, matched.value) : null)

  function releaseUnused(): void {
    for (const [url, owner] of leases) {
      if (url === frame.value?.url || url === pending?.frame.url || url === queued?.frame.url) continue
      leases.delete(url)
      callbacks.release?.(owner)
    }
  }

  function cancelDecode(): void {
    if (!pending) return
    const cancelled = pending
    pending = null
    clearTimeout(cancelled.timer)
    cancelled.image.removeAttribute('src')
  }

  function reset(): void {
    generation++
    clearTimeout(staleTimer)
    cancelDecode()
    queued = null
    frame.value = null
    image.value = null
    matched.value = null
    recent.value = false
    failed.value = false
    releaseUnused()
  }

  function finish(attempt: Decode, success: boolean): void {
    if (disposed || pending !== attempt) return
    const currentGeneration = generation
    clearTimeout(attempt.timer)
    pending = null
    failed.value = !success
    if (success) {
      // Install the decoded element itself, so the display never has to load it again.
      frame.value = attempt.frame
      image.value = attempt.image
      matched.value = matchingDetections(attempt.frame, results()) ? results() : attempt.result
      clearTimeout(staleTimer)
      // Browser receive-to-now only; the board has a separate monotonic clock.
      const remaining = 2000 - Math.max(0, performance.now() - attempt.frame.receivedAt)
      recent.value = remaining > 0
      if (remaining > 0) staleTimer = setTimeout(() => { recent.value = false }, remaining)
    } else {
      attempt.image.removeAttribute('src')
      // The adapter closes the control socket on decode failure, which stops
      // capture. Never grant frame credit for an image that was not displayed.
      callbacks.decodeError?.(attempt.frame)
    }
    if (disposed || generation !== currentGeneration) { releaseUnused(); return }
    const next = queued
    queued = null
    if (next && (!frame.value || !sameFrame(next.frame, frame.value))) decode(next)
    releaseUnused()
  }

  function decode(candidate: Candidate): void {
    const nextImage = new Image()
    nextImage.decoding = 'async'
    nextImage.draggable = false
    const attempt: Decode = { ...candidate, image: nextImage }
    pending = attempt
    // A broken image source must not block every later frame indefinitely.
    attempt.timer = setTimeout(() => {
      nextImage.removeAttribute('src')
      finish(attempt, false)
    }, 2000)
    nextImage.src = candidate.frame.url
    void nextImage.decode().then(() => {
      finish(attempt, nextImage.naturalWidth === candidate.frame.width && nextImage.naturalHeight === candidate.frame.height)
    }, () => { finish(attempt, false) })
  }

  watch(source, incoming => {
    if (disposed) return
    if (!incoming) { sessionId = null; reset(); return }
    if (incoming.sessionId !== sessionId) { reset(); sessionId = incoming.sessionId }
    if (!leases.has(incoming.url)) leases.set(incoming.url, incoming)
    const candidate: Candidate = { frame: incoming, result: matchingDetections(incoming, results()) ? results() : null }
    if (frame.value && sameFrame(incoming, frame.value)) {
      // Repeating a frame does not extend its freshness or create a new decoder.
      queued = null
      cancelDecode()
      releaseUnused()
      return
    }
    if (pending) {
      if (sameFrame(pending.frame, incoming)) { pending.result = candidate.result; queued = null }
      else queued = candidate
    } else decode(candidate)
    releaseUnused()
  }, { immediate: true, flush: 'sync' })

  watch(results, incoming => {
    if (matchingDetections(frame.value, incoming)) matched.value = incoming
    if (pending && matchingDetections(pending.frame, incoming)) pending.result = incoming
    if (queued && matchingDetections(queued.frame, incoming)) queued.result = incoming
  }, { flush: 'sync' })

  onBeforeUnmount(() => { disposed = true; reset() })
  return { frame, image, fresh, failed, detections }
}
