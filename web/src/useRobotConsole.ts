import { computed, inject, onBeforeUnmount, onMounted, provide, ref, shallowRef, type InjectionKey } from 'vue'
import { consoleApi } from './console-api'
import { createRequestId, managementApi, ManagementApiError } from './management-api'
import { isBrainStatus, type BrainAction, type BrainStatus } from './console-protocol'
import type { DeviceStatus } from './management-types'

function createConsole() {
  const device = shallowRef<DeviceStatus | null>(null)
  const brain = shallowRef<BrainStatus | null>(null)
  const connection = ref<'connecting' | 'online' | 'offline'>('connecting')
  const brainError = ref<string | null>(null)
  const deviceStale = ref(false)
  const brainStale = ref(true)
  const checkedAt = ref<number | null>(null)
  const brainCheckedAt = ref<number | null>(null)
  const pending = ref<BrainAction | null>(null)
  const controlError = ref<string | null>(null)
  const outcomeUnknown = ref(false)
  const refreshing = ref(false)
  const controllers = new Set<AbortController>()
  const retiredInstances = new Set<string>()
  let disposed = false
  let timer: ReturnType<typeof setInterval> | undefined
  let socket: WebSocket | undefined
  let socketInstance: string | null = null
  let pollCount = 0
  const unavailable = computed(() => brainError.value === 'not_supported')
  const fresh = computed(() => !!brain.value && !brainStale.value && connection.value === 'online')
  const code = (e: unknown) => e instanceof ManagementApiError ? e.status === 404 ? 'not_supported' : e.code : 'unavailable'
  function accept(status: BrainStatus): void {
    if (retiredInstances.has(status.instance_id)) return
    if (brain.value?.instance_id === status.instance_id && status.revision < brain.value.revision) return
    if (brain.value && brain.value.instance_id !== status.instance_id) {
      retiredInstances.add(brain.value.instance_id)
      if (retiredInstances.size > 16) retiredInstances.delete(retiredInstances.values().next().value!)
    }
    brain.value = status; brainStale.value = false; brainError.value = null
    brainCheckedAt.value = Date.now(); connection.value = 'online'
  }
  function closeEvents(): void {
    if (socket) { const current = socket; socket = undefined; current.onclose = current.onmessage = current.onerror = null; current.close() }
    socketInstance = null
  }
  function observe(status: BrainStatus): void {
    if (!status.events_available || document.hidden) { closeEvents(); return }
    if (socket && socketInstance === status.instance_id) return
    closeEvents()
    const current = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/brain/events`)
    socket = current; socketInstance = status.instance_id
    current.onmessage = event => {
      if (disposed || socket !== current || typeof event.data !== 'string' || event.data.length > 65536) return
      try { const value: unknown = JSON.parse(event.data); if (isBrainStatus(value) && value.instance_id === socketInstance) accept(value) } catch { /* Polling remains authoritative. */ }
    }
    current.onclose = () => { if (socket === current) { socket = undefined; socketInstance = null } }
    current.onerror = () => current.close()
  }
  async function refresh(forceDevice = false): Promise<void> {
    if (disposed || refreshing.value || document.hidden) return
    refreshing.value = true
    const controller = new AbortController(); controllers.add(controller)
    const readDevice = forceDevice || !device.value || pollCount++ % 5 === 0
    let brainReachable = false
    let deviceReachable = false
    // Telemetry may be slow. It must not delay interaction state or live events.
    const readBrain = async () => {
      try {
        const value = await consoleApi.brain(controller.signal)
        if (disposed) return
        brainReachable = true
        accept(value)
        if (brain.value?.instance_id === value.instance_id) observe(value)
      } catch (failure) {
        if (disposed) return
        brainReachable = failure instanceof ManagementApiError && failure.status > 0
        brainError.value = code(failure)
        brainStale.value = true
        closeEvents()
      }
    }
    const readTelemetry = async () => {
      if (!readDevice) return
      try {
        const value = await managementApi.device(controller.signal)
        if (disposed) return
        device.value = value
        deviceStale.value = false
        checkedAt.value = Date.now()
        deviceReachable = true
        connection.value = 'online'
      } catch { if (!disposed) deviceStale.value = true }
    }
    await Promise.allSettled([readBrain(), readTelemetry()])
    if (!disposed) {
      connection.value = brainReachable || deviceReachable || !readDevice && !deviceStale.value ? 'online' : 'offline'
      refreshing.value = false
    }
    controllers.delete(controller)
  }
  async function control(action: BrainAction): Promise<void> {
    const status = brain.value
    if (!status || !fresh.value || pending.value || outcomeUnknown.value || !status.controls[action]) return
    if (action === 'cancel_task' && status.task?.state !== 'running') return
    pending.value = action; controlError.value = null
    const controller = new AbortController(); controllers.add(controller)
    try {
      const result = await consoleApi.control(status, action, createRequestId(), controller.signal)
      if (!disposed) accept(result.status)
    } catch (error) {
      if (!disposed) {
        controlError.value = code(error)
        outcomeUnknown.value = ['network_error', 'timeout', 'invalid_response', 'cancelled'].includes(controlError.value)
      }
    } finally { controllers.delete(controller); if (!disposed) { pending.value = null; await refresh() } }
  }
  async function reconcile(): Promise<void> { await refresh(true); if (fresh.value) { outcomeUnknown.value = false; controlError.value = null } }
  function visibility(): void { if (document.hidden) { closeEvents(); brainStale.value = true } else void refresh(true) }
  function pagehide(): void { closeEvents(); brainStale.value = true }
  onMounted(() => {
    void refresh(true)
    timer = setInterval(() => { void refresh(); if (brainCheckedAt.value && Date.now() - brainCheckedAt.value > 12000) brainStale.value = true }, 2000)
    document.addEventListener('visibilitychange', visibility)
    window.addEventListener('pagehide', pagehide)
    window.addEventListener('pageshow', visibility)
  })
  onBeforeUnmount(() => {
    disposed = true; clearInterval(timer); closeEvents()
    controllers.forEach(c => c.abort()); controllers.clear()
    document.removeEventListener('visibilitychange', visibility)
    window.removeEventListener('pagehide', pagehide)
    window.removeEventListener('pageshow', visibility)
  })
  return { device, brain, connection, brainError, deviceStale, brainStale, checkedAt, brainCheckedAt, fresh, unavailable,
    pending, controlError, outcomeUnknown, refreshing, refresh, control, reconcile }
}
const key: InjectionKey<ReturnType<typeof createConsole>> = Symbol('robot-console')
export function provideRobotConsole() { const state = createConsole(); provide(key, state); return state }
export function useRobotConsole() { const state = inject(key); if (!state) throw new Error('Robot console provider is required'); return state }
