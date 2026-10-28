import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { createRequestId, isCloudTestResult, managementApi, ManagementApiError } from './management-api'
import type { CloudConfig, CloudConfigPatch } from './management-types'
import { isActiveTask, useManagementTasks } from './useManagementTasks'

export type CloudField = 'endpoint' | 'model' | 'timeout' | 'apiKey'
export type KeyMode = 'keep' | 'replace' | 'clear'

/** Device configuration and browser-only input have separate lifetimes. */
export function useCloudSettings() {
  const saved = shallowRef<CloudConfig | null>(null)
  const draft = reactive({ endpoint: '', model: '', timeout: '8', apiKey: '' })
  const touched = reactive({ endpoint: false, model: false, timeout: false, apiKey: false })
  const keyMode = ref<KeyMode>('keep')
  const showKey = ref(false)
  const loading = ref(false)
  const saving = ref(false)
  const testing = ref(false)
  const loadError = ref<string | null>(null)
  const actionError = ref<string | null>(null)
  const conflict = ref(false)
  const saveUnknown = ref(false)
  const saveSuccess = ref(false)
  const resetMessage = ref(false)
  const pendingRequest = shallowRef<{ id: string; revision: number } | null>(null)
  const testUnknown = ref(false)
  const { tasks, error: taskError, refreshing: taskRefreshing, ready: taskReady, refresh: refreshTasks, track } = useManagementTasks(['cloud.test'])
  const activeTest = computed(() => tasks.value.find(isActiveTask) ?? null)
  const latestTest = computed(() => activeTest.value ?? tasks.value[0] ?? null)
  const testResult = computed(() => isCloudTestResult(latestTest.value?.result) ? latestTest.value.result : null)
  const changed = computed(() => !!saved.value && (draft.endpoint !== (saved.value.base_url ?? '')
    || draft.model !== (saved.value.model ?? '') || draft.timeout !== String(saved.value.timeout_seconds)
    || keyMode.value === 'replace' || (keyMode.value === 'clear' && saved.value.has_api_key)))
  const errors = computed(() => {
    let validEndpoint = false
    try {
      const url = new URL(draft.endpoint)
      validEndpoint = draft.endpoint.length <= 2048 && url.protocol === 'https:' && Boolean(url.hostname)
        && !url.username && !url.password && !/[\s\u0000-\u001f\u007f?#\\]/u.test(draft.endpoint)
        && (url.port === '' || Number(url.port) > 0)
    } catch { /* Field errors never echo entered URLs or keys. */ }
    return {
      endpoint: validEndpoint ? '' : 'endpointError',
      model: draft.model.length > 0 && draft.model.length <= 128 && !/[\s\u0000-\u001f\u007f]/u.test(draft.model) ? '' : 'modelError',
      timeout: /^\d+$/.test(draft.timeout) && Number(draft.timeout) >= 1 && Number(draft.timeout) <= 120 ? '' : 'timeoutError',
      apiKey: keyMode.value !== 'replace' || (draft.apiKey.length >= 1 && draft.apiKey.length <= 4096 && /^[\x21-\x7e]+$/.test(draft.apiKey)) ? '' : 'keyError',
    }
  })
  const busy = computed(() => loading.value || saving.value || testing.value)
  const canSave = computed(() => !!saved.value && changed.value && !busy.value && !conflict.value && !saveUnknown.value && !loadError.value && !activeTest.value && !testUnknown.value)
  const canTest = computed(() => !!saved.value?.configured && !changed.value && !busy.value && !activeTest.value
    && !testUnknown.value && !conflict.value && !saveUnknown.value && !loadError.value && taskReady.value && !taskError.value && !taskRefreshing.value)
  const controllers = new Set<AbortController>()
  let disposed = false
  const code = (failure: unknown): string => failure instanceof ManagementApiError ? failure.code : 'unavailable'
  const uncertain = (value: string): boolean => ['network_error', 'timeout', 'cancelled', 'invalid_response'].includes(value)

  watch(keyMode, () => { draft.apiKey = ''; showKey.value = false; touched.apiKey = false }, { flush: 'sync' })
  watch(() => [draft.endpoint, draft.model, draft.timeout, draft.apiKey, keyMode.value], () => {
    saveSuccess.value = false; resetMessage.value = false
  }, { flush: 'sync' })
  watch(tasks, () => {
    if (testUnknown.value && pendingRequest.value && tasks.value.some(task => task.request_id === pendingRequest.value?.id)) {
      pendingRequest.value = null; testUnknown.value = false; actionError.value = null
    }
  })
  function apply(config: CloudConfig): void {
    saved.value = config
    draft.endpoint = config.base_url ?? ''
    draft.model = config.model ?? ''
    draft.timeout = String(config.timeout_seconds)
    keyMode.value = 'keep'
    draft.apiKey = ''
    showKey.value = false
    Object.assign(touched, { endpoint: false, model: false, timeout: false, apiKey: false })
  }
  async function load(): Promise<void> {
    if (disposed || busy.value) return
    loading.value = true
    const controller = new AbortController(); controllers.add(controller)
    try {
      const config = await managementApi.cloud(controller.signal)
      if (disposed) return
      apply(config)
      loadError.value = actionError.value = null
      conflict.value = saveUnknown.value = saveSuccess.value = resetMessage.value = false
    } catch (failure) { if (!disposed) loadError.value = code(failure) }
    finally { controllers.delete(controller); if (!disposed) loading.value = false }
  }
  function resetDraft(): void {
    if (!saved.value || busy.value) return
    apply(saved.value)
    actionError.value = null
    resetMessage.value = true
  }
  function validate(): boolean {
    Object.assign(touched, { endpoint: true, model: true, timeout: true, apiKey: true })
    return Object.values(errors.value).every(value => !value)
  }
  async function save(): Promise<boolean> {
    if (!canSave.value || !saved.value || !validate()) return false
    saving.value = true; actionError.value = null; saveSuccess.value = resetMessage.value = false
    const body: CloudConfigPatch = { expected_revision: saved.value.revision, provider: 'openai-compatible',
      base_url: draft.endpoint, model: draft.model, timeout_seconds: Number(draft.timeout) }
    if (keyMode.value === 'replace') body.api_key = draft.apiKey
    if (keyMode.value === 'clear') body.api_key = null
    const controller = new AbortController(); controllers.add(controller)
    try {
      const config = await managementApi.saveCloud(body, controller.signal)
      if (disposed) return false
      apply(config)
      saveSuccess.value = true
      return true
    } catch (failure) {
      if (!disposed) {
        actionError.value = code(failure)
        conflict.value = actionError.value === 'conflict'
        saveUnknown.value = uncertain(actionError.value)
      }
      return false
    } finally {
      body.api_key = undefined
      controllers.delete(controller)
      if (!disposed) saving.value = false
    }
  }
  async function recoverTest(): Promise<void> {
    await refreshTasks()
    if (disposed || !pendingRequest.value) return
    const found = tasks.value.find(task => task.request_id === pendingRequest.value?.id)
    if (found) { track(found); pendingRequest.value = null; testUnknown.value = false; actionError.value = null }
  }
  async function test(retry = false): Promise<void> {
    if (disposed || !saved.value || busy.value || (retry ? !testUnknown.value || !pendingRequest.value : !canTest.value)) return
    const request = retry ? pendingRequest.value! : { id: createRequestId(), revision: saved.value.revision }
    pendingRequest.value = request
    testing.value = true; actionError.value = null
    const controller = new AbortController(); controllers.add(controller)
    try {
      const task = await managementApi.testCloud(request.id, request.revision, controller.signal)
      if (task.kind !== 'cloud.test' || task.request_id !== request.id) throw new ManagementApiError('invalid_response')
      if (disposed) return
      track(task)
      pendingRequest.value = null; testUnknown.value = false
    } catch (failure) {
      if (!disposed) {
        actionError.value = code(failure)
        testUnknown.value = uncertain(actionError.value)
        if (!testUnknown.value) pendingRequest.value = null
        if (actionError.value === 'conflict') conflict.value = true
      }
    } finally { controllers.delete(controller); if (!disposed) testing.value = false }
    if (testUnknown.value && !disposed) await recoverTest()
  }
  function clearTransientKey(): void { draft.apiKey = ''; showKey.value = false }
  onMounted(() => {
    void load()
    window.addEventListener('pagehide', clearTransientKey)
  })
  onBeforeUnmount(() => {
    disposed = true
    window.removeEventListener('pagehide', clearTransientKey)
    controllers.forEach(controller => controller.abort()); controllers.clear()
    clearTransientKey(); pendingRequest.value = null
  })
  return { saved, draft, touched, keyMode, showKey, loading, saving, testing, busy, errors, changed,
    loadError, actionError, conflict, saveUnknown, saveSuccess, resetMessage, testUnknown, canSave, canTest,
    tasks, taskError, taskRefreshing, activeTest, latestTest, testResult, load, resetDraft, validate, save, test, recoverTest, refreshTasks }
}
