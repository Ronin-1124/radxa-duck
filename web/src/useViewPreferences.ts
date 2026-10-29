import { inject, provide, ref, type InjectionKey } from 'vue'

function createPreferences() {
  return { previewEnabled: ref(true), showBoxes: ref(false), showDetails: ref(false) }
}
const key: InjectionKey<ReturnType<typeof createPreferences>> = Symbol('view-preferences')

/** Keep choices across main views, without retaining images, sockets or camera leases. */
export function provideViewPreferences() { const state = createPreferences(); provide(key, state); return state }
export function useViewPreferences() {
  const state = inject(key)
  if (!state) throw new Error('View preferences provider is required')
  return state
}
