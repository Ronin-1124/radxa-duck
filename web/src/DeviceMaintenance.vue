<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Download, Power, RefreshCw, RotateCcw, Wrench } from '@lucide/vue'
import { createRequestId, managementApi, ManagementApiError } from './management-api'
import type { PowerAction, PowerStatus, UpdateStatus } from './management-types'
import { isActiveTask, useManagementTasks } from './useManagementTasks'
import ManagementTask from './ManagementTask.vue'
import UiAlert from './UiAlert.vue'

defineProps<{labelledby?:string}>()
const id = useId()
const power = shallowRef<PowerStatus | null>(null)
const update = shallowRef<UpdateStatus | null>(null)
const powerError = ref<string | null>(null)
const updateError = ref<string | null>(null)
const submitting = ref(false)
const refreshing = ref(false)
const actionError = ref<string | null>(null)
const uncertain = shallowRef<{ requestId: string; action: PowerAction } | null>(null)
const action = ref<PowerAction>('reboot')
const dialog = ref<HTMLDialogElement>()
const dialogRetry = ref(false)
const { tasks, error: taskError, refreshing: checkingTasks, ready: tasksReady, refresh: refreshTasks, track } = useManagementTasks(['power.reboot', 'power.shutdown', 'update.check'])
let disposed = false
let controller: AbortController | undefined
let submitController: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let opener: HTMLElement | null = null
const { t, locale } = useI18n({ useScope: 'local', inheritLocale: true, messages: {
  'zh-CN': {
    title: '系统维护', description: '更新应用，或管理整机电源。', refresh: '刷新维护状态',
    update: '应用更新', installed: '当前安装版本', latest: '可用更新版本', unknown: '尚未读取', noPackage: '未安装正式应用包', noRelease: '应用发布源尚未配置', updateUnavailable: '应用升级暂不可用', checkUpdate: '检查更新',
    updateHint: '正式应用包与发布源接通后，才可在线安装升级和回退。',
    power: '整机电源', powerHint: '重启和关机会停止当前交互，并中断网页连接。', restart: '重启设备', shutdown: '关闭设备',
    scheduled: '设备已安排{action}', scheduledUnknown: '设备已安排电源操作', scheduledAt: '计划时间：{time}', scheduledHint: '计划已被系统接收；网页断开不代表操作已经完成。',
    rebootName: '重启', shutdownName: '关机', waiting: '等待设备接收', taskHistory: '最近维护任务', tasksRefresh: '查询任务状态',
    unknownResult: '请求结果尚未确认，请查询任务状态。网页不会自动重复重启或关机。',
    retry: '重试原操作', retryHint: '如果查询仍未找到任务，可以再次确认并重试原操作；设备会合并已收到的重复请求。',
    dialogTitle: '确认{action}？', dialogHint: '设备会先停止收音和摄像头采集，再安排{action}。当前交互和网页连接会中断。', cancel: '取消', confirmReboot: '确认重启设备', confirmShutdown: '确认关闭设备',
    stalePower: '电源状态读取失败，上次状态仅供参考。', staleUpdate: '升级状态读取失败，上次状态仅供参考。',
    reasons: { permission_denied: '设备尚未授予无交互电源权限，此操作暂不可用。', already_scheduled: '系统已有电源计划，暂时无法发起其他电源操作。', unsupported: '设备暂不支持此操作。', unavailable: '电源管理服务暂时不可用。' },
    errors: { permission_denied: '设备尚未授予此操作的系统权限。', network_error: '设备连接已断开，操作结果尚未确认。', timeout: '设备响应超时，请查询任务状态。', invalid_response: '设备返回的状态暂时无法识别。', busy: '已有维护操作正在进行。', conflict: '系统已有电源计划，请重新读取状态。', unavailable: '设备管理服务暂时不可用，请稍后重试。', tasks_unavailable: '任务记录暂时不可用，请稍后重新查询。' },
  },
  en: {
    title: 'System maintenance', description: 'Update the app or manage device power.', refresh: 'Refresh maintenance status',
    update: 'App updates', installed: 'Installed version', latest: 'Available version', unknown: 'Not reported', noPackage: 'Packaged app is not installed', noRelease: 'Release source is not configured', updateUnavailable: 'App updates are unavailable', checkUpdate: 'Check for updates',
    updateHint: 'Online installation and rollback require a packaged app and release source.',
    power: 'Device power', powerHint: 'Restarting or shutting down stops active interactions and interrupts this connection.', restart: 'Restart device', shutdown: 'Shut down',
    scheduled: '{action} scheduled by device', scheduledUnknown: 'A power action is scheduled', scheduledAt: 'Scheduled time: {time}', scheduledHint: 'The system accepted the schedule. Losing this connection does not confirm completion.',
    rebootName: 'Restart', shutdownName: 'Shutdown', waiting: 'Waiting for device', taskHistory: 'Latest maintenance task', tasksRefresh: 'Check task status',
    unknownResult: 'The request outcome is unconfirmed. Check task status. This page will not automatically repeat a restart or shutdown.',
    retry: 'Retry original operation', retryHint: 'If no task is found, confirm and retry the original action. The device merges requests it has already received.',
    dialogTitle: 'Confirm {action}?', dialogHint: 'The device will stop audio and camera capture, then schedule {action}. This interrupts active interactions and the web connection.', cancel: 'Cancel', confirmReboot: 'Confirm device restart', confirmShutdown: 'Confirm shutdown',
    stalePower: 'Power status could not be updated. The previous status is shown for reference.', staleUpdate: 'Update status could not be refreshed. The previous status is shown for reference.',
    reasons: { permission_denied: 'Non-interactive power permission has not been granted on this device.', already_scheduled: 'A power action is already scheduled. Further power actions are unavailable.', unsupported: 'This device does not support this operation.', unavailable: 'Power management is unavailable.' },
    errors: { permission_denied: 'This operation needs system permission on the device.', network_error: 'Device connection lost. The operation outcome is unconfirmed.', timeout: 'The device timed out. Check task status.', invalid_response: 'The device returned an unrecognized status.', busy: 'Another maintenance operation is in progress.', conflict: 'A power action is already scheduled. Refresh status.', unavailable: 'Device management is unavailable. Try again later.', tasks_unavailable: 'Task history is unavailable. Check again later.' },
  },
} })

const latestTask = computed(() => tasks.value[0])
const activeTask = computed(() => tasks.value.some(isActiveTask))
const powerActions = ['reboot', 'shutdown'] as const
const actionName = (value: PowerAction) => t(value === 'reboot' ? 'rebootName' : 'shutdownName')
const errorLabel = (code: string) => t(`errors.${code}`, t('errors.unavailable'))
const reasonLabel = (code: string | null) => t(`reasons.${code ?? 'unavailable'}`, t('reasons.unavailable'))
const mayPower = (value: PowerAction): boolean => Boolean(power.value?.capabilities[value].available)
  && tasksReady.value && !taskError.value && !checkingTasks.value
  && !powerError.value && !submitting.value && !uncertain.value && !activeTask.value && !power.value?.scheduled
const mayRetry = computed(() => uncertain.value !== null && Boolean(power.value?.capabilities[uncertain.value.action].available)
  && tasksReady.value && !taskError.value && !checkingTasks.value && !powerError.value && !submitting.value
  && !activeTask.value && !power.value?.scheduled)
const updateReason = computed(() => !update.value ? t('unknown') : t(update.value.reason === 'package_not_installed' ? 'noPackage'
  : update.value.reason === 'release_source_unconfigured' ? 'noRelease' : 'updateUnavailable'))
function formatTime(value: string): string {
  const time = new Date(value)
  return Number.isFinite(time.getTime()) ? new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'medium' }).format(time) : t('unknown')
}
async function load(): Promise<void> {
  if (disposed || refreshing.value) return
  refreshing.value = true
  controller = new AbortController()
  const signal = controller.signal
  await Promise.all([
    managementApi.power(signal).then(value => { if (!disposed) { power.value = value; powerError.value = null } })
      .catch(error => { if (!disposed) powerError.value = error instanceof ManagementApiError ? error.code : 'unavailable' }),
    managementApi.update(signal).then(value => { if (!disposed) { update.value = value; updateError.value = null } })
      .catch(error => { if (!disposed) updateError.value = error instanceof ManagementApiError ? error.code : 'unavailable' }),
  ])
  if (!disposed) { refreshing.value = false; schedule() }
}
function schedule(): void {
  clearTimeout(timer)
  if (!disposed && !document.hidden) timer = setTimeout(() => { void load() }, activeTask.value ? 2000 : 15_000)
}
function visibility(): void { clearTimeout(timer); if (!document.hidden) void load() }
async function openAction(value: PowerAction, event: MouseEvent): Promise<void> {
  if (!mayPower(value)) return
  action.value = value
  dialogRetry.value = false
  opener = event.currentTarget as HTMLElement
  dialog.value?.showModal()
  await nextTick()
  dialog.value?.querySelector<HTMLButtonElement>('button')?.focus()
}
function closeDialog(): void { dialog.value?.close(); opener?.focus({ preventScroll: true }) }
async function openRetry(event: MouseEvent): Promise<void> {
  if (!mayRetry.value || !uncertain.value) return
  action.value = uncertain.value.action
  dialogRetry.value = true
  opener = event.currentTarget as HTMLElement
  dialog.value?.showModal()
  await nextTick()
  dialog.value?.querySelector<HTMLButtonElement>('button')?.focus()
}
function backdrop(event: MouseEvent): void { if (event.target === event.currentTarget) closeDialog() }
function keepFocus(event: KeyboardEvent): void {
  if (event.key !== 'Tab') return
  const controls = dialog.value?.querySelectorAll<HTMLButtonElement>('button:not([disabled])')
  if (!controls?.length) return
  const first = controls[0]!, last = controls[controls.length - 1]!
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
async function submit(): Promise<void> {
  closeDialog()
  const prior = uncertain.value
  if (dialogRetry.value ? !mayRetry.value || !prior : !mayPower(action.value)) return
  const requestId = dialogRetry.value ? prior!.requestId : createRequestId()
  const value = dialogRetry.value ? prior!.action : action.value
  submitting.value = true
  actionError.value = null
  submitController = new AbortController()
  try {
    const task = await managementApi.powerAction(value, requestId, submitController.signal)
    if (!disposed) { track(task); uncertain.value = null }
  } catch (error) {
    if (!disposed) {
      const code = error instanceof ManagementApiError ? error.code : 'unavailable'
      actionError.value = code
      if (['network_error', 'timeout', 'invalid_response', 'cancelled'].includes(code)) uncertain.value = { requestId, action: value }
    }
  } finally { if (!disposed) { submitting.value = false; void refreshTasks(); void load() } }
}
watch(tasks, values => {
  const request = uncertain.value
  if (request && values.some(task => task.request_id === request.requestId && task.kind === `power.${request.action}`)) {
    uncertain.value = null
    actionError.value = null
  }
})
watch(() => latestTask.value?.updated_utc, () => { if (!document.hidden) void load() })
onMounted(() => { if (!document.hidden) void load(); document.addEventListener('visibilitychange', visibility) })
onBeforeUnmount(() => {
  disposed = true
  clearTimeout(timer)
  controller?.abort()
  submitController?.abort()
  dialog.value?.close()
  document.removeEventListener('visibilitychange', visibility)
})
defineExpose({refresh:load,refreshing,refreshDisabled:refreshing})
</script>

<template>
  <section class="maintenance-card" :class="{'is-embedded':labelledby}" :aria-labelledby="labelledby || `${id}-title`">
    <header v-if="!labelledby" class="section-heading"><div class="section-intro"><span class="ui-section-icon"><Wrench :size="22" :stroke-width="1.6" aria-hidden="true" /></span><div><h2 :id="`${id}-title`" class="ui-section-title">{{ t('title') }}</h2><p class="ui-section-description">{{ t('description') }}</p></div></div><button class="icon-button ui-icon-button" type="button" :aria-label="t('refresh')" :disabled="refreshing" :aria-busy="refreshing" @click="load"><RefreshCw :size="17" aria-hidden="true" /></button></header>
    <div class="maintenance-row update-row">
      <div class="row-intro"><Download :size="21" :stroke-width="1.6" aria-hidden="true" /><div><h3>{{ t('update') }}</h3><p>{{ updateReason }}</p></div></div>
      <dl v-if="update"><div><dt>{{ t('installed') }}</dt><dd>{{ update.installed_version ?? t('noPackage') }}</dd></div><div><dt>{{ t('latest') }}</dt><dd>{{ update.latest_version ?? t('unknown') }}</dd></div></dl>
      <UiAlert v-if="updateError" class="maintenance-error" role="status">{{ update ? t('staleUpdate') : errorLabel(updateError) }}</UiAlert>
      <div class="update-actions"><p class="note">{{ t('updateHint') }}</p><button class="button ui-button" type="button" disabled :aria-describedby="`${id}-update-hint`">{{ t('checkUpdate') }}</button></div><span :id="`${id}-update-hint`" class="sr-only">{{ updateReason }}</span>
    </div>
    <div class="maintenance-row">
      <div class="row-intro"><Power :size="21" :stroke-width="1.6" aria-hidden="true" /><div><h3>{{ t('power') }}</h3><p>{{ t('powerHint') }}</p></div></div>
      <UiAlert v-if="powerError" class="maintenance-error" role="status">{{ power ? t('stalePower') : errorLabel(powerError) }}</UiAlert>
      <div v-if="power?.scheduled" class="scheduled-box"><strong>{{ power.scheduled.action ? t('scheduled', { action: actionName(power.scheduled.action) }) : t('scheduledUnknown') }}</strong><p v-if="power.scheduled.scheduled_utc">{{ t('scheduledAt', { time: formatTime(power.scheduled.scheduled_utc) }) }}</p><p>{{ t('scheduledHint') }}</p></div>
      <div class="power-actions"><button v-for="value in powerActions" :key="value" class="button ui-button" type="button" :disabled="!mayPower(value)" :aria-describedby="`${id}-power-${value}-hint`" @click="openAction(value, $event)"><component :is="value === 'reboot' ? RotateCcw : Power" :size="17" aria-hidden="true" />{{ t(value === 'reboot' ? 'restart' : 'shutdown') }}</button></div>
      <template v-if="power"><p v-for="value in powerActions" v-show="!power.capabilities[value].available && (value === 'reboot' || power.capabilities.shutdown.reason !== power.capabilities.reboot.reason)" :id="`${id}-power-${value}-hint`" :key="value" class="note">{{ reasonLabel(power.capabilities[value].reason) }}</p></template>
      <UiAlert v-if="actionError && !uncertain" class="maintenance-error" role="status">{{ errorLabel(actionError) }}</UiAlert>
      <UiAlert v-if="uncertain" class="maintenance-error" tone="warning" role="status">{{ t('unknownResult') }}</UiAlert>
      <div v-if="uncertain" class="retry-box"><p class="note">{{ t('retryHint') }}</p><button class="button ui-button" type="button" :disabled="!mayRetry" :aria-busy="submitting" @click="openRetry">{{ t('retry') }}</button></div>
      <p v-if="submitting" class="note" role="status">{{ t('waiting') }}</p>
    </div>
    <div v-if="latestTask" class="task-section"><h3>{{ t('taskHistory') }}</h3><ManagementTask :task="latestTask" :stale="Boolean(taskError)" /></div>
    <UiAlert v-if="taskError" class="maintenance-error" role="status">{{ errorLabel(taskError) }}</UiAlert>
    <button v-if="latestTask || uncertain || taskError" class="button task-refresh ui-button" type="button" :disabled="checkingTasks" @click="refreshTasks">{{ t('tasksRefresh') }}</button>
    <dialog ref="dialog" class="ui-dialog" :aria-labelledby="`${id}-dialog-title`" :aria-describedby="`${id}-dialog-hint`" @cancel="closeDialog" @click="backdrop" @keydown="keepFocus"><h2 :id="`${id}-dialog-title`" class="ui-section-title">{{ t('dialogTitle', { action: actionName(action) }) }}</h2><p :id="`${id}-dialog-hint`">{{ t('dialogHint', { action: actionName(action) }) }}</p><div class="dialog-actions ui-dialog-actions"><button class="button ui-button" type="button" @click="closeDialog">{{ t('cancel') }}</button><button class="button ui-button ui-button--danger" type="button" @click="submit">{{ t(action === 'reboot' ? 'confirmReboot' : 'confirmShutdown') }}</button></div></dialog>
  </section>
</template>

<style scoped>
.maintenance-card { min-width: 0; padding: 26px; border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); box-shadow: var(--shadow-soft); }
.maintenance-card.is-embedded { padding:0; border:0; border-radius:0; box-shadow:none; }
.maintenance-card.is-embedded > .maintenance-row:first-of-type { padding-top:0; border-top:0; }
h2, h3, p { margin: 0; }
h3 { color: var(--text); font-size: 13px; font-weight: 600; line-height: 1.7; }
.section-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 22px; }
.section-intro { display: flex; align-items: flex-start; gap: 14px; min-width: 0; }
.section-intro > div { min-width: 0; }
.ui-section-description { margin-top: 5px; }
.row-intro p, .note, .scheduled-box p { margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.8; text-wrap: pretty; overflow-wrap: anywhere; }
.maintenance-row { padding: 22px 0; border-top: 1px solid var(--border); }
.row-intro { display: flex; align-items: flex-start; gap: 14px; }
.row-intro > svg { flex-shrink: 0; margin-top: 2px; color: var(--muted); }
.row-intro > div { min-width: 0; }
dl { display: grid; gap: 10px; margin: 18px 0 0 35px; }
dl > div { display: flex; justify-content: space-between; align-items: baseline; gap: 20px; min-width: 0; }
dt, dd { font-size: 12px; line-height: 1.7; }
dt { color: var(--muted); }
dd { min-width: 0; margin: 0; color: var(--text); overflow-wrap: anywhere; text-align: right; }
.update-actions, .power-actions { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin: 18px 0 0 35px; }
.update-actions .note { max-width: 60ch; flex: 1; }
.power-actions { justify-content: flex-start; }
.maintenance-row > .note { margin-left: 35px; }
.retry-box { margin: 12px 0 0 35px; }
.retry-box .button { margin-top: 12px; }
.scheduled-box { padding: 14px 16px; margin: 18px 0 0 35px; border: 1px solid var(--border); border-radius: 16px; background: var(--background); }
.scheduled-box strong { color: var(--accent); font-size: 13px; font-weight: 600; line-height: 1.7; }
.maintenance-error { margin-top: 18px; }
.task-section { padding-top: 18px; border-top: 1px solid var(--border); }
.task-section > h3 { margin-bottom: 10px; }
.task-refresh { margin-top: 14px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
@media (max-width: 539px) { .maintenance-card { padding: 20px 16px; } .section-intro { gap: 12px; } .update-actions { align-items: stretch; flex-direction: column; } .power-actions { margin-left: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; } .power-actions .button { padding-inline: 10px; } .task-refresh { width: 100%; } }
</style>
