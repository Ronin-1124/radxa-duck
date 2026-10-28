import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { managementApi, ManagementApiError } from './management-api'
import type { ManagementTask, TaskKind } from './management-types'

export const isActiveTask = (task: ManagementTask): boolean => task.state === 'queued' || task.state === 'running'

/** Task lifetime belongs to the board. Leaving a view only cancels its reads. */
export function useManagementTasks(kinds: TaskKind[]) {
  const tasks = shallowRef<ManagementTask[]>([])
  const error = ref<string | null>(null)
  const refreshing = ref(false)
  const ready = ref(false)
  const allowed = new Set(kinds)
  const active = computed(() => tasks.value.some(isActiveTask))
  let disposed = false
  let controller: AbortController | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let generation = 0
  const tracked = new Map<string, number>()

  function merge(incoming: ManagementTask[]): void {
    const values = new Map(tasks.value.map(task => [task.task_id, task]))
    for (const task of incoming) {
      if (!allowed.has(task.kind)) continue
      const previous = values.get(task.task_id)
      if (!previous || task.updated_utc >= previous.updated_utc) values.set(task.task_id, task)
    }
    tasks.value = [...values.values()].sort((a, b) => b.created_utc.localeCompare(a.created_utc)).slice(0, 32)
  }
  function schedule(): void {
    clearTimeout(timer)
    if (!disposed && !document.hidden) timer = setTimeout(() => { void refresh() }, active.value ? 2000 : 15_000)
  }
  function track(task: ManagementTask): void {
    if (disposed || !allowed.has(task.kind)) return
    tracked.set(task.task_id, ++generation)
    merge([task])
    schedule()
  }
  async function refresh(): Promise<void> {
    if (disposed || refreshing.value) return
    clearTimeout(timer)
    refreshing.value = true
    const started = generation
    controller = new AbortController()
    try {
      const result = await managementApi.tasks(controller.signal)
      if (!disposed) {
        // The server's successful list is authoritative. Preserve only local
        // submissions made during this read, not tasks removed from history.
        const previous = tasks.value
        const incoming = result.tasks.filter(task => allowed.has(task.kind))
        const values = new Map(incoming.map(task => [task.task_id, task]))
        for (const task of previous) {
          const candidate = values.get(task.task_id)
          if ((!candidate && (tracked.get(task.task_id) ?? 0) > started) || (candidate && task.updated_utc > candidate.updated_utc)) {
            values.set(task.task_id, task)
          }
        }
        tasks.value = [...values.values()].sort((a, b) => b.created_utc.localeCompare(a.created_utc)).slice(0, 32)
        for (const [taskId, version] of tracked) if (version <= started) tracked.delete(taskId)
        error.value = null
        ready.value = true
      }
    } catch (failure) {
      if (!disposed) error.value = failure instanceof ManagementApiError ? failure.code : 'unavailable'
    } finally {
      if (!disposed) { refreshing.value = false; schedule() }
    }
  }
  function visibility(): void {
    clearTimeout(timer)
    if (!document.hidden) void refresh()
  }
  onMounted(() => { if (!document.hidden) void refresh(); document.addEventListener('visibilitychange', visibility) })
  onBeforeUnmount(() => {
    disposed = true
    clearTimeout(timer)
    controller?.abort()
    document.removeEventListener('visibilitychange', visibility)
  })
  return { tasks, error, refreshing, ready, refresh, track }
}
