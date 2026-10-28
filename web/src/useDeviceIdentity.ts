import { inject, onBeforeUnmount, onMounted, provide, readonly, ref, shallowRef, type InjectionKey } from 'vue'
import { managementApi } from './management-api'
import type { DeviceStatus } from './management-types'

interface DeviceIdentity {
  model: string | null
  hostname: string | null
}

function createDeviceIdentity() {
  const identity = shallowRef<DeviceIdentity | null>(null)
  const stale = ref(false)
  let generation = 0
  function update(status: DeviceStatus): void {
    generation++
    identity.value = { model: status.model?.trim() || null, hostname: status.hostname?.trim() || null }
    stale.value = false
  }
  return {
    identity: readonly(identity), stale: readonly(stale), update,
    generation: () => generation,
    markStale: () => { generation++; stale.value = true },
  }
}
const identityKey: InjectionKey<ReturnType<typeof createDeviceIdentity>> = Symbol('device-identity')

/** Device identity stays in memory and is shared across routes; capture services are untouched. */
export function provideDeviceIdentity(shouldRead: () => boolean) {
  const store = createDeviceIdentity()
  provide(identityKey, store)
  let controller: AbortController | null = null
  let interval: ReturnType<typeof setInterval> | undefined
  let disposed = false
  async function refresh(): Promise<void> {
    if (disposed || document.hidden || !shouldRead() || controller) return
    const request = new AbortController()
    const generation = store.generation()
    controller = request
    try {
      const status = await managementApi.device(request.signal)
      if (!disposed && generation === store.generation()) store.update(status)
    } catch {
      if (!disposed && generation === store.generation()) store.markStale()
    } finally {
      if (controller === request) controller = null
    }
  }
  function refreshVisible(): void { void refresh() }
  onMounted(() => {
    refreshVisible()
    interval = setInterval(refreshVisible, 15_000)
    document.addEventListener('visibilitychange', refreshVisible)
  })
  onBeforeUnmount(() => {
    disposed = true
    clearInterval(interval)
    controller?.abort()
    document.removeEventListener('visibilitychange', refreshVisible)
  })
  return store
}

export function useDeviceIdentity() { return inject(identityKey, null) }
