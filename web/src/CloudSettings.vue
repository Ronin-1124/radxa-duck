<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Eye, EyeOff, KeyRound, RefreshCw, RotateCcw, Save, TestTube2 } from '@lucide/vue'
import UiAlert from './UiAlert.vue'
import UiEmptyState from './UiEmptyState.vue'
import { useCloudSettings, type CloudField } from './useCloudSettings'

const { t, locale } = useI18n({ useScope: 'local', inheritLocale: true, messages: {
  'zh-CN': {
    cloudTitle: '云端模型', cloudDescription: '配置机器人使用的云端模型连接。', protocol: 'OpenAI 兼容服务',
    draftModified: '有未保存更改', loading: '正在读取配置', notLoaded: '尚未读取设备配置', notConfigured: '尚未配置', configurationLoaded: '已读取设备配置',
    configurationHint: '每次保存都会更新设备上的同一份配置。密钥不回显，页面中的新密钥离开本页即清除。',
    endpoint: '服务地址', endpointHint: 'HTTPS API 基础地址，不包含账号、密码、查询参数或片段。',
    model: '模型名称', modelHint: '填写服务提供的模型标识。', timeout: '请求超时', timeoutHint: '1–120 秒，按单次网络操作计时。', seconds: '秒',
    keyTitle: 'API 密钥', keyConfigured: '设备已配置密钥', keyNotConfigured: '设备未配置密钥', keyKeep: '保留现有密钥', keyReplace: '替换密钥', keyClear: '清除密钥',
    apiKey: 'API 密钥', apiKeyPlaceholder: '输入新的 API 密钥', keyHint: '新密钥仅留在页面内存中；保存成功、重置或离页后清除。',
    showKey: '显示 API 密钥', hideKey: '隐藏 API 密钥',
    endpointError: '请输入有效的 HTTPS 地址，不含空白、账号、密码、查询参数或片段（最长 2048 字符）。',
    modelError: '请输入 1–128 字符的模型名称，不含空白或控制字符。', timeoutError: '请输入 1–120 之间的整数秒数。', keyError: '请输入 1–4096 字符的新密钥，仅使用可见 ASCII 字符且不含空白。',
    validationTitle: '请检查以下字段', save: '保存到设备', saving: '正在保存', test: '测试连接', testing: '正在提交测试', reset: '重置草稿',
    saveDone: '已保存，下一轮交互使用新配置。', resetDone: '已恢复上次读取的配置，页面中的新密钥已清除。',
    reload: '重新读取', reloadTitle: '重新读取会替换当前草稿，并清除页面中的新密钥。', reloadConfirm: '读取并替换草稿', keepEditing: '继续编辑',
    clearTitle: '确认从设备清除 API 密钥？', clearHint: '当前表单的其他更改也会一并保存，需要认证的云端服务可能无法继续使用。', clearConfirm: '确认清除并保存', cancel: '取消',
    conflict: '设备配置已被其他页面修改。当前草稿已保留，请重新读取后再决定保存。',
    saveUnknown: '保存结果尚未确认。草稿已保留，请重新读取设备配置核对，网页不会自动重复保存。',
    testUnknown: '设备是否已接收测试请求尚未确认。可以查询任务，或使用同一请求重新提交。',
    testNeedsSave: '先保存当前更改，再测试设备上已保存的配置。', testNeedsConfig: '保存服务地址和模型后，即可测试连接。', testInProgress: '设备正在测试已保存配置，完成后可保存新的更改或再次测试。',
    testHint: '连接测试检查服务与模型列表；实际对话能力需在应用中验证。',
    queryTask: '查询任务', retrySame: '重试同一请求', testTitle: '连接测试', testEmpty: '尚无连接测试记录',
    testCurrentConfig: '当前保存的配置', testEarlierConfig: '此前保存的配置', elapsed: '用时 {time} ms', previousResult: '此结果对应此前保存的配置，当前配置尚未通过本次测试验证。',
    taskStale: '任务状态未更新，请重新查询；下方为上次读取的结果。', resultInvalid: '暂时无法确认测试结果，请重新查询任务。',
    testStates: { queued: '等待测试', running: '正在测试已保存配置', succeeded: '连接成功，模型可用', failed: '连接测试失败', cancelled: '测试已取消', interrupted: '测试已中断' },
    errors: { network_error: '无法连接设备，请检查局域网连接后重试。', timeout: '设备响应超时，请重新查询状态。', forbidden: '当前网页未获准执行此操作，请检查访问地址。', invalid_response: '设备返回的数据暂时无法识别。', unavailable: '服务暂时不可用，请稍后重试。', management_unavailable: '设备管理服务尚未就绪。', cloud_unavailable: '云端服务暂时不可用。', config_unavailable: '设备云端配置暂时无法读取或保存。', tasks_unavailable: '设备任务记录暂时不可用。', invalid_request: '配置格式未被设备接受，请检查服务地址、模型和密钥。', conflict: '设备配置或任务状态已改变，请重新读取。', not_configured: '请先保存云端配置。', authentication_failed: '云端服务未接受当前密钥，请检查密钥。', permission_denied: '云端服务拒绝访问，请检查服务权限。', rate_limited: '云端服务请求受限，请稍后再试。', endpoint_unavailable: '云端模型列表接口不可用，请检查服务地址。', tls_failed: '无法建立云端加密连接，请检查地址与证书。', model_unavailable: '云端服务未列出配置的模型，请检查模型名称。', response_too_large: '云端模型列表过大，测试未完成。', interrupted: '服务重启导致测试中断，请重新查询。', cancelled: '操作已取消。', operation_failed: '操作未完成，请检查设备与云端配置后重试。' },
  },
  en: {
    cloudTitle: 'Cloud model', cloudDescription: 'Configure the cloud model connection your robot uses.', protocol: 'OpenAI-compatible service',
    draftModified: 'Unsaved changes', loading: 'Reading configuration', notLoaded: 'Device configuration not loaded', notConfigured: 'Not configured', configurationLoaded: 'Device configuration loaded',
    configurationHint: 'Each save updates the same configuration on the device. Saved keys are never shown; new keys are cleared when you leave this page.',
    endpoint: 'Service URL', endpointHint: 'An HTTPS API base URL without credentials, query parameters, or fragments.',
    model: 'Model name', modelHint: 'Enter the model identifier from your provider.', timeout: 'Request timeout', timeoutHint: '1–120 seconds, per network operation.', seconds: 'seconds',
    keyTitle: 'API key', keyConfigured: 'A key is configured on the device', keyNotConfigured: 'No key configured on the device', keyKeep: 'Keep existing key', keyReplace: 'Replace key', keyClear: 'Remove key',
    apiKey: 'API key', apiKeyPlaceholder: 'Enter a new API key', keyHint: 'A new key stays only in page memory and is cleared after a successful save, reset, or departure.',
    showKey: 'Show API key', hideKey: 'Hide API key',
    endpointError: 'Enter a valid HTTPS URL without whitespace, credentials, query parameters, or fragments (up to 2048 characters).',
    modelError: 'Enter a model name of 1–128 characters without whitespace or control characters.', timeoutError: 'Enter a whole number of seconds between 1 and 120.', keyError: 'Enter a new key of 1–4096 visible ASCII characters without whitespace.',
    validationTitle: 'Check these fields', save: 'Save to device', saving: 'Saving', test: 'Test connection', testing: 'Submitting test', reset: 'Reset draft',
    saveDone: 'Saved. Used from the next interaction.', resetDone: 'Restored the last loaded configuration and cleared the new key from this page.',
    reload: 'Read again', reloadTitle: 'Reading again replaces this draft and clears the new key from this page.', reloadConfirm: 'Read and replace draft', keepEditing: 'Keep editing',
    clearTitle: 'Remove the API key from the device?', clearHint: 'Other changes in this form will also be saved. Cloud services that require authentication may stop working.', clearConfirm: 'Remove key and save', cancel: 'Cancel',
    conflict: 'Another page changed the device configuration. Your draft is preserved. Read the latest configuration before deciding what to save.',
    saveUnknown: 'The save outcome is unconfirmed. Your draft is preserved. Read the device configuration to check it; this page will not save it again automatically.',
    testUnknown: 'It is unclear whether the device accepted the test. Check task status or resubmit the same request.',
    testNeedsSave: 'Save your changes before testing the configuration stored on the device.', testNeedsConfig: 'Save a service URL and model before testing the connection.', testInProgress: 'The device is testing its saved configuration. Save new changes or test again after it finishes.',
    testHint: 'This check verifies the service connection and model list. Validate conversation capabilities in an application.',
    queryTask: 'Check task', retrySame: 'Retry same request', testTitle: 'Connection test', testEmpty: 'No connection test recorded yet',
    testCurrentConfig: 'Currently saved configuration', testEarlierConfig: 'Previously saved configuration', elapsed: '{time} ms elapsed', previousResult: 'This result belongs to a previously saved configuration and does not verify the current configuration.',
    taskStale: 'Task status is not current. Check again; the last received result is shown below.', resultInvalid: 'The test result cannot be confirmed yet. Check task status again.',
    testStates: { queued: 'Queued for testing', running: 'Testing saved configuration', succeeded: 'Connected; model available', failed: 'Connection test failed', cancelled: 'Test cancelled', interrupted: 'Test interrupted' },
    errors: { network_error: 'Cannot reach the device. Check your local network and retry.', timeout: 'The device timed out. Check status again.', forbidden: 'This page is not allowed to perform the operation. Check its address.', invalid_response: 'The device returned unrecognized data.', unavailable: 'The service is unavailable. Please retry later.', management_unavailable: 'Device management is not ready.', cloud_unavailable: 'The cloud service is unavailable.', config_unavailable: 'Cloud configuration cannot be read or saved on the device.', tasks_unavailable: 'Device task history is unavailable.', invalid_request: 'The device did not accept the configuration format. Check the URL, model, and key.', conflict: 'Device configuration or task state changed. Read it again.', not_configured: 'Save cloud configuration first.', authentication_failed: 'The cloud service rejected the key. Check your credentials.', permission_denied: 'The cloud service denied access. Check its permissions.', rate_limited: 'The cloud service is rate limited. Try again later.', endpoint_unavailable: 'The models endpoint is unavailable. Check the service URL.', tls_failed: 'The encrypted cloud connection failed. Check the URL and certificate.', model_unavailable: 'The cloud service did not list this model. Check the model name.', response_too_large: 'The cloud model list exceeded the test limit.', interrupted: 'A service restart interrupted testing. Check status again.', cancelled: 'The operation was cancelled.', operation_failed: 'The operation did not complete. Check device and cloud settings before retrying.' },
  },
} })
const id = useId()
const cloud = useCloudSettings()
const { saved, draft, touched, keyMode, showKey, loading, saving, testing, busy, errors, changed, loadError, actionError,
  conflict, saveUnknown, saveSuccess, resetMessage, testUnknown, canSave, canTest, taskError, taskRefreshing, activeTest, latestTest, testResult } = cloud
const clearConfirmation = ref(false)
const reloadConfirmation = ref(false)
const validationSummary = ref<HTMLElement>()
const submittedInvalid = ref(false)
const clearDialog = ref<HTMLDialogElement>()
const cancelClearButton = ref<HTMLButtonElement>()
const saveButton = ref<HTMLButtonElement>()
let clearOpener: HTMLElement | null = null
let disposed = false
const fieldOrder: CloudField[] = ['endpoint', 'model', 'timeout', 'apiKey']
const fieldLabel = { endpoint: 'endpoint', model: 'model', timeout: 'timeout', apiKey: 'apiKey' } as const
const invalidFields = computed(() => fieldOrder.filter(field => errors.value[field]))
const taskState = computed(() => {
  const task = latestTest.value
  if (!task) return ''
  if (task.state === 'succeeded' && (!testResult.value || testResult.value.state !== 'succeeded' || testResult.value.model_available !== true)) return t('resultInvalid')
  return t(`testStates.${task.state}`)
})
const currentResult = computed(() => !!saved.value && !!testResult.value && saved.value.revision === testResult.value.revision)
const testTone = computed(() => taskError.value ? 'neutral' : latestTest.value?.state === 'succeeded' && currentResult.value
  && testResult.value?.state === 'succeeded' && testResult.value.model_available === true ? 'success'
  : latestTest.value?.state === 'failed' || latestTest.value?.state === 'interrupted' ? 'error' : 'neutral')
const taskFailure = computed(() => latestTest.value?.error?.code ?? testResult.value?.error?.code ?? null)
const canRead = computed(() => !busy.value)
function errorText(value: string): string { return t(`errors.${value}`, t('errors.operation_failed')) }
function describe(field: CloudField): string {
  return `${id}-${field}-hint${touched[field] && errors.value[field] ? ` ${id}-${field}-error` : ''}`
}
function fieldId(field: CloudField): string { return `${id}-${field}` }
function focusField(field: CloudField): void { document.getElementById(fieldId(field))?.focus() }
async function requestSave(): Promise<void> {
  if (!canSave.value) return
  submittedInvalid.value = !cloud.validate()
  if (submittedInvalid.value) { await nextTick(); validationSummary.value?.focus(); return }
  if (keyMode.value === 'clear' && saved.value?.has_api_key) {
    clearOpener = saveButton.value ?? null
    clearConfirmation.value = true
    await nextTick()
    if (disposed || !clearConfirmation.value) return
    clearDialog.value?.showModal()
    cancelClearButton.value?.focus()
    return
  }
  await cloud.save()
}
function closeClearDialog(): void {
  clearConfirmation.value = false
  clearDialog.value?.close()
}
function clearDialogClosed(): void {
  clearConfirmation.value = false
  if (!disposed && clearOpener?.isConnected) clearOpener.focus({ preventScroll: true })
  clearOpener = null
}
function clearBackdrop(event: MouseEvent): void {
  if (event.target === event.currentTarget) closeClearDialog()
}
function keepClearFocus(event: KeyboardEvent): void {
  if (event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey) return
  const controls = clearDialog.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')
  if (!controls?.length) return
  const first = controls[0], last = controls[controls.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
async function confirmClear(): Promise<void> {
  if (!clearConfirmation.value || busy.value) return
  closeClearDialog()
  await cloud.save()
}
async function readConfiguration(): Promise<void> {
  if (!canRead.value) return
  if (changed.value || saveUnknown.value) { reloadConfirmation.value = true; return }
  await cloud.load()
}
async function confirmRead(): Promise<void> { reloadConfirmation.value = false; submittedInvalid.value = false; await cloud.load() }
function resetDraft(): void { closeClearDialog(); reloadConfirmation.value = submittedInvalid.value = false; cloud.resetDraft() }
watch(keyMode, closeClearDialog)
onMounted(() => { window.addEventListener('pagehide', closeClearDialog) })
onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('pagehide', closeClearDialog)
  clearOpener = null
  clearDialog.value?.close()
})
defineExpose({ hasUnsavedChanges: changed, busy: computed(() => saving.value || testing.value) })
</script>

<template>
  <div class="cloud-settings">

    <section class="settings-card cloud-card" :aria-labelledby="`${id}-cloud-title`" :aria-busy="loading">
      <header class="section-heading">
        <div class="section-intro"><div><h2 class="ui-section-title" :id="`${id}-cloud-title`">{{ t('cloudTitle') }}</h2><p class="ui-section-description">{{ t('cloudDescription') }}</p></div></div>
        <button class="button ui-button read-button" type="button" :disabled="!canRead" @click="readConfiguration"><RefreshCw :size="17" aria-hidden="true" />{{ t('reload') }}</button>
      </header>
      <div class="draft-notice"><div class="configuration-status"><strong role="status">{{ loading ? t('loading') : saved ? (saved.configured ? t('configurationLoaded') : t('notConfigured')) : t('notLoaded') }}</strong><span v-if="changed" class="scope-label">{{ t('draftModified') }}</span></div><p>{{ t('configurationHint') }}</p></div>
      <UiAlert v-if="loadError" class="operation-error" role="alert">{{ errorText(loadError) }}</UiAlert>
      <UiAlert v-if="conflict" class="operation-error" role="alert">{{ t('conflict') }}</UiAlert>
      <UiAlert v-else-if="saveUnknown" class="operation-error" role="alert">{{ t('saveUnknown') }}</UiAlert>
      <UiAlert v-else-if="actionError && !testUnknown" class="operation-error" role="alert">{{ errorText(actionError) }}</UiAlert>
      <div v-if="reloadConfirmation" class="confirmation" role="group" :aria-label="t('reload')"><p>{{ t('reloadTitle') }}</p><div class="confirmation-actions"><button class="button ui-button ui-button--primary" type="button" :disabled="busy" @click="confirmRead">{{ t('reloadConfirm') }}</button><button class="button ui-button" type="button" @click="reloadConfirmation = false">{{ t('keepEditing') }}</button></div></div>
      <form novalidate @submit.prevent="requestSave">
        <div v-if="submittedInvalid && invalidFields.length" ref="validationSummary" class="validation-summary" role="alert" tabindex="-1" :aria-labelledby="`${id}-validation-title`"><h3 :id="`${id}-validation-title`">{{ t('validationTitle') }}</h3><ul><li v-for="field in invalidFields" :key="field"><a :href="`#${fieldId(field)}`" @click.prevent="focusField(field)">{{ t(fieldLabel[field]) }}: {{ t(errors[field]) }}</a></li></ul></div>
        <fieldset class="cloud-fieldset" :disabled="!saved || busy || clearConfirmation || reloadConfirmation">
          <legend class="sr-only">{{ t('cloudTitle') }}</legend>
          <p class="provider-label">{{ t('protocol') }}</p>
          <div class="cloud-fields">
            <div class="field field-full"><label :for="fieldId('endpoint')">{{ t('endpoint') }}</label><input :id="fieldId('endpoint')" v-model="draft.endpoint" type="url" inputmode="url" autocomplete="off" spellcheck="false" maxlength="2048" :aria-describedby="describe('endpoint')" :aria-invalid="touched.endpoint && Boolean(errors.endpoint)" @blur="touched.endpoint = true" /><p :id="`${id}-endpoint-hint`" class="field-hint">{{ t('endpointHint') }}</p><p v-if="touched.endpoint && errors.endpoint" :id="`${id}-endpoint-error`" class="field-error">{{ t(errors.endpoint) }}</p></div>
            <div class="field"><label :for="fieldId('model')">{{ t('model') }}</label><input :id="fieldId('model')" v-model="draft.model" type="text" autocomplete="off" spellcheck="false" maxlength="128" :aria-describedby="describe('model')" :aria-invalid="touched.model && Boolean(errors.model)" @blur="touched.model = true" /><p :id="`${id}-model-hint`" class="field-hint">{{ t('modelHint') }}</p><p v-if="touched.model && errors.model" :id="`${id}-model-error`" class="field-error">{{ t(errors.model) }}</p></div>
            <div class="field"><label :for="fieldId('timeout')">{{ t('timeout') }}</label><div class="timeout-input"><input :id="fieldId('timeout')" v-model="draft.timeout" type="text" inputmode="numeric" autocomplete="off" maxlength="3" :aria-describedby="describe('timeout')" :aria-invalid="touched.timeout && Boolean(errors.timeout)" @blur="touched.timeout = true" /><span aria-hidden="true">{{ t('seconds') }}</span></div><p :id="`${id}-timeout-hint`" class="field-hint">{{ t('timeoutHint') }}</p><p v-if="touched.timeout && errors.timeout" :id="`${id}-timeout-error`" class="field-error">{{ t(errors.timeout) }}</p></div>
            <div class="field field-full key-field">
              <fieldset class="key-modes"><legend class="key-label"><KeyRound :size="15" :stroke-width="1.7" aria-hidden="true" />{{ t('keyTitle') }}</legend><p class="key-status">{{ saved?.has_api_key ? t('keyConfigured') : t('keyNotConfigured') }}</p><div class="key-mode-options"><label><input v-model="keyMode" type="radio" value="keep" :name="`${id}-key-mode`" />{{ t('keyKeep') }}</label><label><input v-model="keyMode" type="radio" value="replace" :name="`${id}-key-mode`" />{{ t('keyReplace') }}</label><label><input v-model="keyMode" type="radio" value="clear" :name="`${id}-key-mode`" :disabled="!saved?.has_api_key" />{{ t('keyClear') }}</label></div></fieldset>
              <div v-if="keyMode === 'replace'" class="replacement-key"><label :for="fieldId('apiKey')">{{ t('apiKey') }}</label><div class="key-input"><input :id="fieldId('apiKey')" v-model="draft.apiKey" :type="showKey ? 'text' : 'password'" autocomplete="new-password" spellcheck="false" autocapitalize="off" maxlength="4096" :placeholder="t('apiKeyPlaceholder')" :aria-describedby="describe('apiKey')" :aria-invalid="touched.apiKey && Boolean(errors.apiKey)" @blur="touched.apiKey = true" /><button type="button" class="key-visibility" :aria-label="t(showKey ? 'hideKey' : 'showKey')" :aria-pressed="showKey" @click="showKey = !showKey"><component :is="showKey ? EyeOff : Eye" :size="18" :stroke-width="1.7" aria-hidden="true" /></button></div></div>
              <p :id="`${id}-apiKey-hint`" class="field-hint">{{ t('keyHint') }}</p><p v-if="touched.apiKey && errors.apiKey" :id="`${id}-apiKey-error`" class="field-error">{{ t(errors.apiKey) }}</p>
            </div>
          </div>
        </fieldset>
        <div class="form-actions"><div class="pending-actions"><button ref="saveButton" type="submit" class="button ui-button ui-button--primary" :disabled="!canSave || clearConfirmation || reloadConfirmation" :aria-busy="saving"><Save :size="17" aria-hidden="true" />{{ t(saving ? 'saving' : 'save') }}</button><button type="button" class="button ui-button" :disabled="!canTest || reloadConfirmation || clearConfirmation" :aria-busy="testing" :aria-describedby="`${id}-test-hint`" @click="cloud.test()"><TestTube2 :size="17" aria-hidden="true" />{{ t(testing ? 'testing' : 'test') }}</button></div><button type="button" class="button ui-button reset-button" :disabled="!saved || busy" @click="resetDraft"><RotateCcw :size="17" aria-hidden="true" />{{ t('reset') }}</button></div>
        <p :id="`${id}-test-hint`" class="section-note pending-note">{{ activeTest ? t('testInProgress') : changed ? t('testNeedsSave') : saved && !saved.configured ? t('testNeedsConfig') : t('testHint') }}</p>
        <p class="reset-message" role="status" aria-atomic="true">{{ saveSuccess ? t('saveDone') : resetMessage ? t('resetDone') : '' }}</p>
      </form>
      <div v-if="testUnknown" class="confirmation" role="status"><p>{{ t('testUnknown') }}</p><div class="confirmation-actions"><button type="button" class="button ui-button" :disabled="busy || taskRefreshing" @click="cloud.recoverTest()">{{ t('queryTask') }}</button><button type="button" class="button ui-button" :disabled="busy || taskRefreshing" @click="cloud.test(true)">{{ t('retrySame') }}</button></div></div>
      <section class="test-results" :aria-labelledby="`${id}-test-title`"><div class="test-heading"><h3 :id="`${id}-test-title`">{{ t('testTitle') }}</h3><button type="button" class="button ui-button" :disabled="taskRefreshing" @click="cloud.refreshTasks()"><RefreshCw :size="16" aria-hidden="true" />{{ t('queryTask') }}</button></div><UiAlert v-if="taskError" class="operation-error" role="status">{{ t('taskStale') }}</UiAlert><div v-if="latestTest" class="test-result" :data-state="latestTest.state" :data-tone="testTone"><p class="test-state" role="status">{{ taskState }}</p><UiAlert v-if="taskFailure" class="test-error" role="status">{{ errorText(taskFailure) }}</UiAlert><p v-if="testResult" class="test-meta">{{ t(currentResult ? 'testCurrentConfig' : 'testEarlierConfig') }} · {{ t('elapsed', { time: new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(testResult.elapsed_ms) }) }}</p><p v-if="testResult && saved && !currentResult" class="test-meta">{{ t('previousResult') }}</p><progress v-if="latestTest.state === 'queued' || latestTest.state === 'running'" :value="latestTest.progress ?? undefined" max="100" :aria-label="t('testTitle')"></progress></div><UiEmptyState v-else compact :icon="TestTube2" :title="t('testEmpty')" /></section>
    </section>

    <dialog ref="clearDialog" class="ui-dialog clear-dialog" :aria-labelledby="`${id}-clear-title`" :aria-describedby="`${id}-clear-hint`"
      @cancel.prevent="closeClearDialog" @close="clearDialogClosed" @click="clearBackdrop" @keydown="keepClearFocus">
      <h2 :id="`${id}-clear-title`" class="ui-section-title">{{ t('clearTitle') }}</h2>
      <p :id="`${id}-clear-hint`" class="dialog-hint">{{ t('clearHint') }}</p>
      <div class="ui-dialog-actions"><button ref="cancelClearButton" class="button ui-button" type="button" @click="closeClearDialog">{{ t('cancel') }}</button><button class="button ui-button ui-button--danger" type="button" :disabled="busy" @click="confirmClear">{{ t('clearConfirm') }}</button></div>
    </dialog>

  </div>
</template>

<style scoped>
.cloud-settings .cloud-card { padding:0; border:0; border-radius:0; box-shadow:none; }
.cloud-settings .section-intro { flex-basis:180px; }
.cloud-settings .section-icon,.cloud-settings .ui-section-description,.cloud-settings .draft-notice p,.cloud-settings .provider-label,.cloud-settings .field-hint,.cloud-settings .pending-note { display:none; }
.cloud-settings .draft-notice { padding:0; border:0; background:transparent; margin-bottom:24px; }
.cloud-settings .cloud-fields { gap:22px 18px; }
.cloud-settings .section-heading { margin-bottom:16px; }
.cloud-settings .test-results:has(.ui-empty-state) { display:none; }

.settings-card { min-width: 0; padding: 26px; border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); box-shadow: var(--shadow-soft); }
.section-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px; margin-bottom: 24px; }
.section-heading .ui-section-description { margin-top: 5px; }
.section-intro { display: flex; align-items: center; gap: 14px; min-width: 0; flex: 1 1 240px; }
.section-intro > div { min-width: 0; }
h2, h3, p { margin: 0; }
.scope-label { flex-shrink: 0; padding: 6px 11px; border: 1px solid var(--border); border-radius: 999px; color: var(--muted); font-size: 12px; line-height: 1.5; background: var(--background); }
h3 { color: var(--text); font-size: 14px; font-weight: 600; line-height: 1.6; }
.section-note { margin-top: 22px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--muted); font-size: 12px; line-height: 1.8; text-wrap: pretty; }
.draft-notice { padding: 14px 16px; margin-bottom: 24px; border: 1px solid var(--border); border-radius: 16px; background: var(--background); }
.draft-notice strong { color: var(--text); font-size: 12px; font-weight: 600; }
.draft-notice p { margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.8; text-wrap: pretty; }
.cloud-fields { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); gap: 22px 20px; }
.cloud-fieldset, .key-modes { min-width: 0; margin: 0; padding: 0; border: 0; }
.provider-label { margin-bottom: 20px; color: var(--muted); font-size: 12px; line-height: 1.7; }
.configuration-status { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
.configuration-status .scope-label { margin: 0; }
.operation-error { margin: 0 0 18px; }
.key-modes legend { margin-bottom: 8px; color: var(--text); font-size: 13px; font-weight: 500; }
.key-status { color: var(--muted); font-size: 12px; line-height: 1.7; }
.key-mode-options { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 8px; }
.key-mode-options label { display: flex; align-items: center; gap: 8px; min-height: 44px; margin: 0; cursor: pointer; }
.key-mode-options input { width: 18px; min-width: 18px; height: 18px; min-height: 18px; margin: 0; padding: 0; accent-color: var(--accent); }
.key-mode-options label:has(input:disabled) { color: var(--muted); cursor: not-allowed; }
.replacement-key { margin-top: 18px; }
.confirmation { margin-block: 18px; padding: 16px; border: 1px solid var(--border); border-radius: 16px; background: var(--background); }
.confirmation p { color: var(--muted); font-size: 13px; line-height: 1.8; }
.confirmation h3 + p { margin-top: 6px; }
.confirmation-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
.validation-summary { margin-bottom: 22px; padding: 16px; border: 1px solid var(--offline); border-radius: 16px; color: var(--offline); }
.validation-summary ul { margin: 10px 0 0; padding-left: 18px; }
.validation-summary a { color: var(--offline); font-size: 13px; line-height: 1.8; }
.validation-summary:focus-visible, .validation-summary a:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; }
.dialog-hint { margin-top: 14px; color: var(--muted); font-size: 12px; line-height: 1.8; text-wrap: pretty; }
.test-error { margin-top: 12px; }
.test-results > .operation-error { margin-top: 16px; }
.test-results { margin-top: 24px; padding-top: 22px; border-top: 1px solid var(--border); }
.test-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.test-result { margin-top: 16px; padding: 16px; border: 1px solid var(--border); border-radius: 16px; background: var(--background); }
.test-state { color: var(--text); font-size: 13px; font-weight: 600; line-height: 1.7; }
.test-result[data-tone="success"] .test-state { color: var(--accent); }
.test-result[data-tone="error"] .test-state { color: var(--offline); }
.test-meta { margin-top: 10px; color: var(--muted); font-size: 12px; line-height: 1.8; }
.test-result progress { display: block; width: 100%; height: 5px; margin-top: 14px; accent-color: var(--accent); }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.field { min-width: 0; }
.field-full { grid-column: 1 / -1; }
label { display: block; margin-bottom: 8px; color: var(--text); font-size: 13px; font-weight: 500; line-height: 1.6; }
.key-label { display: flex; align-items: center; gap: 7px; }
.key-label svg { color: var(--muted); }
input { width: 100%; min-width: 0; min-height: 46px; padding: 11px 14px; border: 1px solid var(--border); border-radius: var(--radius-control); color: var(--text); background: var(--background); font: inherit; font-size: 14px; line-height: 1.5; }
input::placeholder { color: var(--muted); opacity: 1; }
input[aria-invalid="true"] { border-color: var(--offline); }
input:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; }
.field-hint, .field-error { margin-top: 7px; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.field-hint { color: var(--muted); }
.field-error { color: var(--offline); }
.timeout-input, .key-input { position: relative; }
.timeout-input input { padding-right: 84px; }
.timeout-input > span { position: absolute; top: 50%; right: 14px; transform: translateY(-50%); color: var(--muted); font-size: 12px; pointer-events: none; }
.key-input input { padding-right: 52px; }
.key-visibility { position: absolute; top: 1px; right: 3px; display: grid; place-items: center; width: 44px; height: 44px; padding: 0; border: 0; border-radius: 12px; color: var(--muted); background: transparent; cursor: pointer; touch-action: manipulation; }
.form-actions, .pending-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.form-actions { justify-content: space-between; margin-top: 26px; }
input:disabled { color: var(--muted); cursor: not-allowed; }
.key-visibility:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
.pending-note { margin-top: 20px; }
.reset-message { margin-top: 8px; color: var(--accent); font-size: 12px; line-height: 1.7; }
.reset-message:empty { display: none; }

@media (max-width: 539px) {
  .settings-card { padding: 20px 16px; }
  .section-heading { align-items: flex-start; gap: 12px; margin-bottom: 20px; }
  .section-intro { gap: 12px; flex-basis: 100%; }
  .scope-label { margin-left: 56px; }
  .draft-notice { padding: 12px; }
  .cloud-fields { grid-template-columns: minmax(0, 1fr); gap: 20px; }
  input { font-size: 16px; }
  .pending-actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }
  .pending-actions .button { padding-inline: 10px; }
  .reset-button { width: 100%; }
}
@media (hover: hover) and (pointer: fine) {
  .key-visibility:hover { background: var(--accent-soft); color: var(--accent); }
}
.key-visibility:active { background: var(--accent-soft); }
@media (prefers-reduced-motion: no-preference) {
  input { transition: border-color var(--motion-fast) ease; }
}
</style>
