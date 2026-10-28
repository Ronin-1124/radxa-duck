import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { isVisionSnapshot, type VisionSnapshot } from './vision-protocol'
import { managementApi, ManagementApiError } from './management-api'
import type { DeviceStatus } from './management-types'
import { useDeviceIdentity } from './useDeviceIdentity'
import { isSpeechHealth, type SpeechHealth } from './service-status'
export type { SpeechHealth } from './service-status'

export interface StatusResource<T> {
  data: T | null
  error: 'network' | 'timeout' | 'invalid' | null
  /** Time of the last successful read; preserved when a later refresh fails. */
  checkedAt: number | null
}
const empty = <T>(): StatusResource<T> => ({ data: null, error: null, checkedAt: null })

/** Read-only service checks. Never opens a capture socket or infers system telemetry. */
export function useDeviceStatus() {
  const identity = useDeviceIdentity()
  const device = shallowRef(empty<DeviceStatus>())
  const speech = shallowRef(empty<SpeechHealth>())
  const vision = shallowRef(empty<VisionSnapshot>())
  const refreshing = ref(false)
  const checkedAt = ref<number | null>(null)
  const controllers = new Set<AbortController>()
  let disposed = false
  let interval: ReturnType<typeof setInterval> | undefined
  const connection = computed(() => {
    if ([device.value, speech.value, vision.value].some(resource => resource.data && !resource.error)) return 'online'
    return checkedAt.value === null ? 'connecting' : 'offline'
  })

  async function read<T>(path: string, validate: (data: unknown) => data is T, previous: StatusResource<T>): Promise<StatusResource<T>> {
    const controller = new AbortController()
    controllers.add(controller)
    let timedOut = false
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, 5000)
    try {
      const response = await fetch(path, { signal: controller.signal, cache: 'no-store', redirect: 'error', headers: { Accept: 'application/json' } })
      if (!response.ok) return { ...previous, error: 'network' }
      if (!response.headers.get('content-type')?.includes('application/json')) return { ...previous, error: 'invalid' }
      const reader = response.body?.getReader()
      if (!reader) return { ...previous, error: 'invalid' }
      const chunks: Uint8Array[] = []
      let size = 0
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          size += value.byteLength
          if (size > 64 * 1024) {
            await reader.cancel()
            return { ...previous, error: 'invalid' }
          }
          chunks.push(value)
        }
      } finally { reader.releaseLock() }
      const bytes = new Uint8Array(size)
      let offset = 0
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
      let data: unknown
      try { data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) } catch { return { ...previous, error: 'invalid' } }
      return validate(data) ? { data, error: null, checkedAt: Date.now() } : { ...previous, error: 'invalid' }
    } catch {
      return { ...previous, error: timedOut ? 'timeout' : 'network' }
    } finally {
      clearTimeout(timer)
      controllers.delete(controller)
    }
  }
  async function readDevice(): Promise<void> {
    const controller = new AbortController()
    controllers.add(controller)
    try {
      const data = await managementApi.device(controller.signal)
      if (!disposed) {
        device.value = { data, error: null, checkedAt: Date.now() }
        identity?.update(data)
      }
    } catch (error) {
      if (!disposed) {
        identity?.markStale()
        const code = error instanceof ManagementApiError ? error.code : 'network'
        device.value = { ...device.value, error: code === 'timeout' ? 'timeout'
          : ['invalid', 'invalid_response'].includes(code) ? 'invalid' : 'network' }
      }
    } finally {
      controllers.delete(controller)
    }
  }
  async function refresh(): Promise<void> {
    if (disposed || refreshing.value) return
    refreshing.value = true
    await Promise.all([
      readDevice(),
      read('/api/health', isSpeechHealth, speech.value).then(value => { if (!disposed) speech.value = value }),
      read('/api/vision/status', isVisionSnapshot, vision.value).then(value => { if (!disposed) vision.value = value }),
    ])
    if (!disposed) { checkedAt.value = Date.now(); refreshing.value = false }
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
    controllers.forEach(controller => controller.abort())
    controllers.clear()
  })
  return { device, speech, vision, refreshing, checkedAt, refresh, connection }
}
