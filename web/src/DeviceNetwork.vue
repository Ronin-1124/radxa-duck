<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { LockKeyhole, RefreshCw, Search, Wifi } from '@lucide/vue'
import { createRequestId, isNetworkScanResult, managementApi, ManagementApiError } from './management-api'
import type { NetworkEntry, NetworkStatus } from './management-types'
import { isActiveTask, useManagementTasks } from './useManagementTasks'
import ManagementTask from './ManagementTask.vue'
import UiAlert from './UiAlert.vue'
import UiEmptyState from './UiEmptyState.vue'

const id = useId()
const status = shallowRef<NetworkStatus | null>(null)
const reading = ref(false)
const readError = ref<string | null>(null)
const actionError = ref<string | null>(null)
const submitting = ref(false)
const confirming = ref(false)
const confirmed = ref(false)
const password = ref('')
const selected = shallowRef<NetworkEntry | null>(null)
const uncertain = shallowRef<{ requestId: string; kind: 'network.scan' | 'network.connect'; network: NetworkEntry | null } | null>(null)
const dialog = ref<HTMLDialogElement>()
const dialogRetry = ref(false)
let opener: HTMLElement | null = null
let disposed = false
let readingController: AbortController | undefined
let actionController: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
const { tasks, error: taskError, refreshing: checkingTasks, ready: tasksReady, refresh: refreshTasks, track } = useManagementTasks(['network.scan', 'network.connect'])
const { t } = useI18n({ useScope: 'local', inheritLocale: true, messages: {
  'zh-CN': {
    title: '网络连接', description: '管理机器人连接的局域网。', refresh: '刷新网络状态', reading: '正在读取',
    ssid: 'Wi-Fi 网络', address: '局域网地址', signal: '信号强度', interface: '网络接口', unknown: '尚未读取', unnamed: '未连接 Wi-Fi',
    states: { connected: '已连接', connecting: '正在连接', disconnecting: '正在断开', disconnected: '未连接', unavailable: '不可用', failed: '连接失败' },
    scan: '搜索 Wi-Fi', scanning: '正在搜索', networks: '可用 Wi-Fi', noNetworks: '本次扫描未发现可连接的 Wi-Fi。',
    truncated: '当前显示部分扫描结果，可重新搜索。', selectHint: '选择网络后，确认连接。', security: { open: '开放网络', 'wpa-psk': 'WPA 个人网络', unsupported: '暂不支持的认证方式' },
    password: 'Wi-Fi 密码', passwordHint: '密码仅用于本次连接，不保存在浏览器中。', passwordError: '请输入 8–63 字节密码，或 64 位十六进制密钥。',
    connect: '连接此网络', waiting: '等待设备接收', pending: '网络任务', taskHistory: '最近网络任务', tasksRefresh: '查询任务状态',
    unknownResult: '请求结果尚未确认，请先查询任务状态。不会自动重复配网或扫描。',
    retry: '重试原操作', retryHint: '如果查询仍未找到任务，可以手动重试原操作；设备会合并已收到的重复请求。', retryPassword: '请为原网络重新输入密码，再确认重试。',
    confirmTitle: '确认新网络连接', confirmHint: '请确认当前网页可通过新网络访问，再确认保存。未确认时，设备会尝试恢复原网络。', confirmTime: '设备报告的确认剩余时间：{seconds} 秒', confirm: '确认当前连接', confirming: '正在提交确认', confirmed: '确认已提交，正在等待设备保存网络。',
    dialogTitle: '连接到新的 Wi-Fi？', dialogHint: '切换到“{ssid}”会中断当前网页连接。连接恢复后，请回到设备页确认新网络，否则设备会尝试恢复原网络。', cancel: '取消', confirmConnect: '确认切换网络',
    stale: '网络状态读取失败，下面是上次读取的状态。',
    reasons: { permission_denied: '设备尚未授予无交互配网权限，此操作暂不可用。', radio_disabled: '设备 Wi-Fi 无线开关已关闭。', unavailable: '网络管理服务暂时不可用。', unsupported: '设备暂不支持此操作。' },
    errors: { permission_denied: '设备尚未授予此操作的系统权限。', network_error: '设备连接已断开，请重新查询状态。', timeout: '设备响应超时，请查询任务状态。', invalid_response: '设备返回的状态暂时无法识别。', busy: '已有网络操作正在进行。', confirmation_unavailable: '确认窗口已结束，请刷新当前网络状态。', invalid_request: '请检查网络名称和密码。', unavailable: '网络服务暂时不可用，请稍后重试。', tasks_unavailable: '任务记录暂时不可用，请稍后重新查询。' },
  },
  en: {
    title: 'Network', description: 'Manage your robot’s local network connection.', refresh: 'Refresh network status', reading: 'Reading',
    ssid: 'Wi-Fi network', address: 'Local IP address', signal: 'Signal strength', interface: 'Interface', unknown: 'Not reported', unnamed: 'No Wi-Fi connection',
    states: { connected: 'Connected', connecting: 'Connecting', disconnecting: 'Disconnecting', disconnected: 'Disconnected', unavailable: 'Unavailable', failed: 'Connection failed' },
    scan: 'Search Wi-Fi', scanning: 'Searching', networks: 'Available Wi-Fi', noNetworks: 'No connectable Wi-Fi networks were found in this scan.',
    truncated: 'Some scan results are shown. Search again to refresh.', selectHint: 'Choose a network, then confirm the connection.', security: { open: 'Open network', 'wpa-psk': 'WPA personal', unsupported: 'Unsupported authentication' },
    password: 'Wi-Fi password', passwordHint: 'Used for this connection only. Never saved in the browser.', passwordError: 'Enter an 8–63 byte password or a 64-digit hexadecimal key.',
    connect: 'Connect to network', waiting: 'Waiting for device', pending: 'Network task', taskHistory: 'Latest network task', tasksRefresh: 'Check task status',
    unknownResult: 'The request outcome is unconfirmed. Check task status before continuing. This page will not automatically repeat the operation.',
    retry: 'Retry original operation', retryHint: 'If no task is found, you can retry the original operation. The device merges requests it has already received.', retryPassword: 'Re-enter the password for the original network before confirming a retry.',
    confirmTitle: 'Confirm the new connection', confirmHint: 'Confirm that this page is reachable on the new network before saving. Without confirmation, the device will attempt to restore the previous network.', confirmTime: 'Confirmation time reported by device: {seconds} seconds', confirm: 'Confirm this connection', confirming: 'Submitting confirmation', confirmed: 'Confirmation submitted. Waiting for the device to save this network.',
    dialogTitle: 'Connect to a new Wi-Fi network?', dialogHint: 'Switching to “{ssid}” will interrupt this connection. Return to Device after reconnecting and confirm the new network, or the device will attempt network recovery.', cancel: 'Cancel', confirmConnect: 'Confirm network switch',
    stale: 'Network status could not be updated. The last received status is shown.',
    reasons: { permission_denied: 'Non-interactive network permission has not been granted on this device.', radio_disabled: 'Wi-Fi is switched off on the device.', unavailable: 'Network management is unavailable.', unsupported: 'This device does not support this operation.' },
    errors: { permission_denied: 'This operation needs system permission on the device.', network_error: 'Device connection lost. Check status again.', timeout: 'The device timed out. Check task status.', invalid_response: 'The device returned an unrecognized status.', busy: 'Another network operation is in progress.', confirmation_unavailable: 'The confirmation window ended. Refresh network status.', invalid_request: 'Check the network name and password.', unavailable: 'Network management is unavailable. Try again later.', tasks_unavailable: 'Task history is unavailable. Check again later.' },
  },
} })

const scanTask = computed(() => tasks.value.find(task => task.kind === 'network.scan'))
const connectionTask = computed(() => tasks.value.find(task => task.kind === 'network.connect'))
const latestTask = computed(() => tasks.value[0])
const activeTask = computed(() => tasks.value.some(isActiveTask))
const scanResult = computed(() => scanTask.value?.state === 'succeeded' && isNetworkScanResult(scanTask.value.result) ? scanTask.value.result : null)
const tasksKnown = computed(() => tasksReady.value && !taskError.value && !checkingTasks.value)
const mayScan = computed(() => tasksKnown.value && Boolean(status.value?.capabilities.scan.available) && !readError.value && !submitting.value && !activeTask.value && !uncertain.value)
const mayConnect = computed(() => tasksKnown.value && Boolean(status.value?.capabilities.connect.available) && !readError.value && !submitting.value && !activeTask.value && !uncertain.value)
const mayConfirm = computed(() => status.value?.pending_task?.awaiting_confirmation === true
  && (status.value.pending_task.confirmation_remaining_seconds ?? 0) > 0 && !confirming.value && !confirmed.value && !readError.value)
const validPassword = computed(() => selected.value?.security !== 'wpa-psk'
  || (!/[\u0000-\u001f\u007f]/u.test(password.value) && ((new TextEncoder().encode(password.value).length >= 8 && new TextEncoder().encode(password.value).length <= 63) || /^[0-9a-fA-F]{64}$/.test(password.value))))
const mayRetry = computed(() => {
  const request = uncertain.value
  if (!request || !tasksKnown.value || readError.value || submitting.value || activeTask.value) return false
  if (request.kind === 'network.scan') return Boolean(status.value?.capabilities.scan.available)
  return Boolean(status.value?.capabilities.connect.available) && Boolean(request.network)
    && selected.value?.ssid === request.network?.ssid && selected.value?.security === request.network?.security && validPassword.value
})
const errorLabel = (code: string) => t(`errors.${code}`, t('errors.unavailable'))
const reasonLabel = (code: string | null | undefined) => t(`reasons.${code ?? 'unavailable'}`, t('reasons.unavailable'))

async function load(): Promise<void> {
  if (disposed || reading.value) return
  reading.value = true
  readingController = new AbortController()
  try {
    const value = await managementApi.network(readingController.signal)
    if (!disposed) { status.value = value; readError.value = null; if (!value.pending_task) confirmed.value = false }
  } catch (error) {
    if (!disposed) readError.value = error instanceof ManagementApiError ? error.code : 'unavailable'
  } finally {
    if (!disposed) { reading.value = false; schedule() }
  }
}
function schedule(): void {
  clearTimeout(timer)
  if (!disposed && !document.hidden) timer = setTimeout(() => { void load() }, activeTask.value || status.value?.pending_task ? 2000 : 15_000)
}
function visibility(): void { clearTimeout(timer); if (!document.hidden) void load() }
function choose(network: NetworkEntry): void { if (uncertain.value || !network.connectable || network.security === 'unsupported') return; selected.value = network; password.value = ''; actionError.value = null }
async function submit(kind: 'network.scan' | 'network.connect', retry = false): Promise<void> {
  const prior = uncertain.value
  if (retry ? !mayRetry.value || prior?.kind !== kind
    : kind === 'network.scan' ? !mayScan.value : !mayConnect.value || !selected.value || !validPassword.value) return
  const entry = selected.value
  const requestId = retry ? prior!.requestId : createRequestId()
  submitting.value = true
  actionError.value = null
  actionController = new AbortController()
  try {
    const task = kind === 'network.scan'
      ? await managementApi.scan(requestId, actionController.signal)
      : await managementApi.connect({ request_id: requestId, ssid: entry!.ssid,
        security: entry!.security === 'open' ? 'open' : 'wpa-psk', ...(entry!.security === 'open' ? {} : { password: password.value }) }, actionController.signal)
    if (!disposed) { track(task); selected.value = null; uncertain.value = null }
  } catch (error) {
    if (!disposed) {
      const code = error instanceof ManagementApiError ? error.code : 'unavailable'
      actionError.value = code
      if (['network_error', 'timeout', 'invalid_response', 'cancelled'].includes(code)) {
        uncertain.value = { requestId, kind, network: kind === 'network.connect' && entry ? { ...entry } : null }
      }
    }
  } finally {
    password.value = ''
    if (!disposed) { submitting.value = false; void refreshTasks(); void load() }
  }
}
async function confirmConnection(): Promise<void> {
  const pending = status.value?.pending_task
  if (!pending || !mayConfirm.value) return
  confirming.value = true
  actionError.value = null
  actionController = new AbortController()
  try {
    const value = await managementApi.confirm(pending.task_id, actionController.signal)
    if (!disposed) confirmed.value = value.confirmed
  } catch (error) { if (!disposed) actionError.value = error instanceof ManagementApiError ? error.code : 'unavailable' }
  finally { if (!disposed) { confirming.value = false; void refreshTasks(); void load() } }
}
async function openConnect(event: MouseEvent, retry = false): Promise<void> {
  if (retry ? !mayRetry.value || uncertain.value?.kind !== 'network.connect'
    : !mayConnect.value || !selected.value || !validPassword.value) return
  dialogRetry.value = retry
  opener = event.currentTarget as HTMLElement
  dialog.value?.showModal()
  await nextTick()
  dialog.value?.querySelector<HTMLButtonElement>('button')?.focus()
}
function closeDialog(): void { dialog.value?.close(); opener?.focus({ preventScroll: true }) }
function acceptDialog(): void { closeDialog(); void submit('network.connect', dialogRetry.value) }
function retryAction(event: MouseEvent): void {
  if (uncertain.value?.kind === 'network.scan') void submit('network.scan', true)
  else void openConnect(event, true)
}
function clearPassword(): void { password.value = '' }
function backdrop(event: MouseEvent): void { if (event.target === event.currentTarget) closeDialog() }
function keepFocus(event: KeyboardEvent): void {
  if (event.key !== 'Tab') return
  const buttons = dialog.value?.querySelectorAll<HTMLButtonElement>('button:not([disabled])')
  if (!buttons?.length) return
  const first = buttons[0]!, last = buttons[buttons.length - 1]!
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
watch(tasks, values => {
  const request = uncertain.value
  if (request && values.some(task => task.request_id === request.requestId && task.kind === request.kind)) {
    uncertain.value = null
    actionError.value = null
  }
})
watch(() => connectionTask.value?.updated_utc, () => { if (!document.hidden) void load() })
onMounted(() => {
  if (!document.hidden) void load()
  document.addEventListener('visibilitychange', visibility)
  window.addEventListener('pagehide', clearPassword)
})
onBeforeUnmount(() => {
  disposed = true
  clearTimeout(timer)
  readingController?.abort()
  actionController?.abort()
  password.value = ''
  selected.value = null
  dialog.value?.close()
  document.removeEventListener('visibilitychange', visibility)
  window.removeEventListener('pagehide', clearPassword)
})
</script>

<template>
  <section class="network-card" :aria-labelledby="`${id}-title`">
    <header class="card-heading"><span class="section-icon ui-section-icon"><Wifi :size="22" :stroke-width="1.6" aria-hidden="true" /></span><div><h2 :id="`${id}-title`" class="ui-section-title">{{ t('title') }}</h2><p class="ui-section-description">{{ t('description') }}</p></div><button class="icon-button ui-icon-button" type="button" :aria-label="t('refresh')" :disabled="reading" :aria-busy="reading" @click="load"><RefreshCw :size="17" aria-hidden="true" /></button></header>
    <UiAlert v-if="readError" class="network-error" role="status">{{ status ? t('stale') : errorLabel(readError) }}</UiAlert>
    <p v-else-if="!status" class="note" role="status">{{ t('reading') }}</p>
    <template v-if="status">
      <p class="network-state" :data-state="status.state">{{ t(`states.${status.state}`) }}</p>
      <dl><div><dt>{{ t('ssid') }}</dt><dd>{{ status.ssid ?? t('unnamed') }}</dd></div><div><dt>{{ t('address') }}</dt><dd>{{ status.ipv4.join(' · ') || t('unknown') }}</dd></div><div v-if="status.strength !== null"><dt>{{ t('signal') }}</dt><dd>{{ status.strength }}%</dd></div><div v-if="status.interface"><dt>{{ t('interface') }}</dt><dd>{{ status.interface }}</dd></div></dl>
      <div v-if="status.pending_task?.awaiting_confirmation" class="confirmation-box">
        <h3>{{ t('confirmTitle') }}</h3><p>{{ t('confirmHint') }}</p><p v-if="status.pending_task.confirmation_remaining_seconds !== null">{{ t('confirmTime', { seconds: status.pending_task.confirmation_remaining_seconds }) }}</p>
        <button class="button primary ui-button ui-button--primary" type="button" :disabled="!mayConfirm" @click="confirmConnection">{{ t(confirming ? 'confirming' : 'confirm') }}</button>
        <p v-if="confirmed" role="status">{{ t('confirmed') }}</p>
      </div>
      <div class="network-actions"><button class="button ui-button" type="button" :disabled="!mayScan" :aria-busy="submitting || Boolean(scanTask && isActiveTask(scanTask))" @click="submit('network.scan')"><Search :size="17" aria-hidden="true" />{{ t(scanTask && isActiveTask(scanTask) ? 'scanning' : 'scan') }}</button></div>
      <p v-if="!status.capabilities.scan.available" class="note">{{ reasonLabel(status.capabilities.scan.reason) }}</p>
      <p v-if="!status.capabilities.connect.available && status.capabilities.connect.reason !== status.capabilities.scan.reason" class="note">{{ reasonLabel(status.capabilities.connect.reason) }}</p>
      <div v-if="scanResult" class="scan-results">
        <h3 :id="`${id}-networks`">{{ t('networks') }}</h3><p class="note">{{ t('selectHint') }}</p>
        <div v-if="scanResult.networks.length" class="network-list" role="group" :aria-labelledby="`${id}-networks`" tabindex="0">
          <button v-for="network in scanResult.networks" :key="`${network.ssid}-${network.security}`" class="network-option" type="button" :disabled="!network.connectable || !mayConnect" :aria-pressed="selected?.ssid === network.ssid && selected?.security === network.security" @click="choose(network)"><span><strong>{{ network.ssid }}</strong><small>{{ t(`security.${network.security}`) }}</small></span><span class="network-strength"><LockKeyhole v-if="network.security !== 'open'" :size="15" aria-hidden="true" />{{ network.strength }}%</span></button>
        </div><UiEmptyState v-else :icon="Wifi" :title="t('noNetworks')" compact /><p v-if="scanResult.truncated" class="note">{{ t('truncated') }}</p>
      </div>
      <form v-if="selected" class="connect-form" @submit.prevent>
        <h3>{{ selected.ssid }}</h3>
        <template v-if="selected.security === 'wpa-psk'"><label :for="`${id}-password`">{{ t('password') }}</label><input :id="`${id}-password`" v-model="password" type="password" autocomplete="new-password" spellcheck="false" maxlength="64" :aria-describedby="`${id}-password-hint${password && !validPassword ? ` ${id}-password-error` : ''}`" :aria-invalid="Boolean(password) && !validPassword" :disabled="submitting" /><p :id="`${id}-password-hint`" class="note">{{ t('passwordHint') }}</p><p v-if="password && !validPassword" :id="`${id}-password-error`" class="error">{{ t('passwordError') }}</p></template>
        <button class="button primary ui-button ui-button--primary" type="button" :disabled="!mayConnect || !validPassword" @click="openConnect">{{ t('connect') }}</button>
      </form>
    </template>
    <UiAlert v-if="actionError && !uncertain" class="network-error" role="status">{{ errorLabel(actionError) }}</UiAlert>
    <div v-if="uncertain" class="uncertain-box"><UiAlert tone="warning" role="status">{{ t('unknownResult') }}</UiAlert><p class="note">{{ t('retryHint') }}</p><p v-if="uncertain.kind === 'network.connect' && uncertain.network?.security === 'wpa-psk'" class="note">{{ t('retryPassword') }}</p><button class="button ui-button" type="button" :disabled="!mayRetry" :aria-busy="submitting" @click="retryAction">{{ t('retry') }}</button></div>
    <div v-if="latestTask" class="task-section"><h3>{{ t('taskHistory') }}</h3><ManagementTask :task="latestTask" :stale="Boolean(taskError)" /></div>
    <UiAlert v-if="taskError" class="network-error" role="status">{{ errorLabel(taskError) }}</UiAlert>
    <button v-if="latestTask || uncertain || taskError" class="button task-refresh ui-button" type="button" :disabled="checkingTasks" @click="refreshTasks">{{ t('tasksRefresh') }}</button>
    <dialog ref="dialog" class="ui-dialog" :aria-labelledby="`${id}-dialog-title`" :aria-describedby="`${id}-dialog-hint`" @cancel="closeDialog" @click="backdrop" @keydown="keepFocus"><h2 :id="`${id}-dialog-title`" class="ui-section-title">{{ t('dialogTitle') }}</h2><p :id="`${id}-dialog-hint`">{{ t('dialogHint', { ssid: selected?.ssid ?? '' }) }}</p><div class="dialog-actions ui-dialog-actions"><button class="button ui-button" type="button" @click="closeDialog">{{ t('cancel') }}</button><button class="button primary ui-button ui-button--primary" type="button" @click="acceptDialog">{{ t('confirmConnect') }}</button></div></dialog>
  </section>
</template>

<style scoped>
.network-card { min-width: 0; padding: 26px; border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); box-shadow: var(--shadow-soft); }
.card-heading { display: flex; align-items: flex-start; gap: 14px; }
.card-heading > div { min-width: 0; flex: 1; }
h2, h3, p { margin: 0; }
h3 { color: var(--text); font-size: 13px; font-weight: 600; line-height: 1.7; }
.ui-section-description { margin-top: 5px; }
.note, .confirmation-box p { margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.8; text-wrap: pretty; overflow-wrap: anywhere; }
.network-state { margin-top: 22px; color: var(--muted); font-size: 12px; font-weight: 600; }
.network-state[data-state="connected"] { color: var(--accent); }
dl { display: grid; gap: 12px; margin: 18px 0 0; }
dl > div { display: flex; justify-content: space-between; align-items: baseline; gap: 20px; min-width: 0; }
dt, dd { font-size: 12px; line-height: 1.7; }
dt { color: var(--muted); }
dd { min-width: 0; max-width: 65%; margin: 0; color: var(--text); text-align: right; overflow-wrap: anywhere; }
.network-actions, .connect-form, .task-section { margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--border); }
.network-actions + .note { margin-top: 12px; }
.confirmation-box, .uncertain-box { margin-top: 20px; padding: 14px; border: 1px solid var(--border); border-radius: 16px; background: var(--background); }
.confirmation-box .button { margin-top: 12px; }
.scan-results { margin-top: 20px; }
.network-list { max-height: 250px; overflow-y: auto; margin-top: 10px; padding: 3px; border: 1px solid var(--border); border-radius: 16px; overscroll-behavior: contain; }
.network-option { display: flex; justify-content: space-between; align-items: center; gap: 16px; width: 100%; min-height: 64px; padding: 10px 12px; border: 0; border-radius: 13px; background: var(--surface); color: var(--text); text-align: left; font: inherit; cursor: pointer; }
.network-option > span:first-child { min-width: 0; }
.network-option strong { display: block; font-size: 13px; line-height: 1.7; font-weight: 500; overflow-wrap: anywhere; }
.network-option small { color: var(--muted); font-size: 11px; line-height: 1.6; }
.network-option[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); }
.network-option:disabled { color: var(--muted); background: var(--background); cursor: not-allowed; }
.network-strength { display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0; color: var(--muted); font-size: 11px; }
label { display: block; margin: 12px 0 7px; color: var(--text); font-size: 12px; }
input { box-sizing: border-box; width: 100%; min-width: 0; min-height: 46px; padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius-control); background: var(--background); color: var(--text); font: inherit; font-size: 14px; }
.connect-form > .button, .task-refresh { margin-top: 14px; }
.task-section > h3 { margin-bottom: 10px; }
.error { margin-top: 12px; color: var(--offline); font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.network-error { margin-top: 18px; }
.uncertain-box .button { margin-top: 14px; }
.network-option:focus-visible, input:focus-visible, .network-list:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
@media (max-width: 539px) { .network-card { padding: 20px 16px; } .card-heading { gap: 12px; } input { font-size: 16px; } .network-actions .button, .connect-form > .button, .task-refresh { width: 100%; } }
@media (hover: hover) and (pointer: fine) { .network-option:not(:disabled):hover { background: var(--accent-soft); } }
</style>
