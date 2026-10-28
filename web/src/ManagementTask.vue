<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ManagementTask } from './management-types'
import UiAlert from './UiAlert.vue'

const props = defineProps<{ task: ManagementTask; stale?: boolean }>()
const { t, locale } = useI18n({ useScope: 'local', inheritLocale: true, messages: {
  'zh-CN': {
    states: { queued: '等待处理', running: '处理中', succeeded: '已完成', failed: '操作失败', cancelled: '已取消', interrupted: '任务已中断' },
    scheduled: '已安排{action}', scheduledAt: '计划时间：{time}', scheduledHint: '系统已接收计划；网页断开不代表操作已完成。', reboot: '重启', shutdown: '关机', progress: '任务进度', stale: '状态未更新，请重新查询。',
    phases: { queued: '等待设备处理', running: '设备正在处理', scanning: '正在搜索 Wi-Fi', creating_checkpoint: '准备网络恢复点', connecting: '正在连接新网络', awaiting_confirmation: '等待确认新网络连接', saving: '正在保存网络', rolling_back: '正在恢复原网络', rolled_back: '已恢复原网络', rollback_failed: '原网络恢复失败', testing: '正在测试云端连接', stopping_capture: '正在停止交互采集', scheduling: '正在安排电源操作', checking: '正在检查更新' },
    errors: { permission_denied: '设备尚未授予此操作的系统权限。', unavailable: '设备暂时不可用，请重新查询。', timeout: '设备响应超时，请查询任务状态。', network: '连接已中断，任务结果尚未确认。', busy: '设备正在处理其他操作。', conflict: '设备状态已改变，请重新读取。', interrupted: '服务重启导致任务中断，请检查设备状态。', cancelled: '任务已取消。', confirmation_timeout: '未及时确认新网络，设备已尝试恢复原网络。', confirmation_unavailable: '确认窗口已结束，请检查当前网络。', rollback_failed: '恢复原网络失败，请检查设备网络连接。', activation_failed: '无法连接所选网络，请检查密码与信号。', activation_timeout: '连接新网络超时，设备已尝试恢复原网络。', network_not_found: '所选 Wi-Fi 已不在扫描范围内。', radio_disabled: '设备 Wi-Fi 无线开关已关闭。', unsupported_security: '暂不支持此网络的认证方式。', stop_failed: '未能停止当前交互，未安排电源操作。', schedule_failed: '系统未确认电源计划，请检查设备状态。', tasks_unavailable: '任务记录暂时不可用。', update_unavailable: '应用安装包或发布源尚未配置。', operation_failed: '操作未完成，请检查设备状态后重试。' },
  },
  en: {
    states: { queued: 'Queued', running: 'In progress', succeeded: 'Completed', failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Interrupted' },
    scheduled: '{action} scheduled', scheduledAt: 'Scheduled time: {time}', scheduledHint: 'The system accepted the schedule. A lost connection does not confirm completion.', reboot: 'Restart', shutdown: 'Shutdown', progress: 'Task progress', stale: 'Status is not current. Check again.',
    phases: { queued: 'Waiting for the device', running: 'The device is processing this task', scanning: 'Searching for Wi-Fi', creating_checkpoint: 'Preparing network recovery', connecting: 'Connecting to the new network', awaiting_confirmation: 'Confirm the new connection', saving: 'Saving the network', rolling_back: 'Restoring the previous network', rolled_back: 'Previous network restored', rollback_failed: 'Network recovery failed', testing: 'Testing the cloud connection', stopping_capture: 'Stopping active interactions', scheduling: 'Scheduling the power action', checking: 'Checking for updates' },
    errors: { permission_denied: 'This operation needs system permission on the device.', unavailable: 'The device is unavailable. Check again.', timeout: 'The device timed out. Check task status.', network: 'Connection lost. The task outcome is unconfirmed.', busy: 'Another device operation is in progress.', conflict: 'Device state changed. Read it again.', interrupted: 'A service restart interrupted this task. Check device status.', cancelled: 'The task was cancelled.', confirmation_timeout: 'Confirmation expired. The device attempted to restore the previous network.', confirmation_unavailable: 'Confirmation has ended. Check the current network.', rollback_failed: 'Network recovery failed. Check the device connection.', activation_failed: 'Could not connect. Check the password and signal.', activation_timeout: 'Connection timed out. The device attempted network recovery.', network_not_found: 'The selected network is no longer in range.', radio_disabled: 'Wi-Fi is switched off on the device.', unsupported_security: 'This network authentication method is not supported.', stop_failed: 'Active interactions could not stop. No power action was scheduled.', schedule_failed: 'The system did not confirm power scheduling. Check device status.', tasks_unavailable: 'Task history is unavailable.', update_unavailable: 'The app package or release source is not configured.', operation_failed: 'The operation did not complete. Check device status before retrying.' },
  },
} })
const powerAction = computed(() => props.task.state === 'succeeded' && props.task.result?.state === 'scheduled'
  && (props.task.result.action === 'reboot' || props.task.result.action === 'shutdown')
  && props.task.kind === `power.${props.task.result.action}` ? props.task.result.action : null)
const stateLabel = computed(() => powerAction.value ? t('scheduled', { action: t(powerAction.value) }) : t(`states.${props.task.state}`))
const phaseLabel = computed(() => ['queued', 'running'].includes(props.task.state)
  ? t(`phases.${props.task.phase}`, t('phases.running')) : '')
const timeLabel = computed(() => {
  const time = new Date(props.task.updated_utc)
  return Number.isFinite(time.getTime()) ? new Intl.DateTimeFormat(locale.value, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(time) : ''
})
const failureLabel = computed(() => props.task.error ? t(`errors.${props.task.error.code}`, t('errors.operation_failed')) : '')
const scheduledTime = computed(() => {
  if (!powerAction.value || typeof props.task.result?.scheduled_utc !== 'string') return ''
  const time = new Date(props.task.result.scheduled_utc)
  return Number.isFinite(time.getTime()) ? new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'medium' }).format(time) : ''
})
</script>

<template>
  <div class="management-task" :data-state="task.state" :data-task-id="task.task_id">
    <div class="task-heading"><strong role="status">{{ stateLabel }}</strong><time :datetime="task.updated_utc">{{ timeLabel }}</time></div>
    <p v-if="phaseLabel">{{ phaseLabel }}</p>
    <progress v-if="task.state === 'queued' || task.state === 'running'" :value="task.progress ?? undefined" max="100" :aria-label="t('progress')"></progress>
    <UiAlert v-if="failureLabel" class="task-error" role="status">{{ failureLabel }}</UiAlert>
    <p v-if="scheduledTime">{{ t('scheduledAt', { time: scheduledTime }) }}</p>
    <p v-if="powerAction">{{ t('scheduledHint') }}</p>
    <UiAlert v-if="stale" class="task-error" tone="warning" role="status">{{ t('stale') }}</UiAlert>
  </div>
</template>

<style scoped>
.management-task { padding: 14px 16px; border: 1px solid var(--border); border-radius: 16px; background: var(--background); }
.task-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
strong { color: var(--text); font-size: 13px; font-weight: 600; line-height: 1.7; }
time, p { color: var(--muted); font-size: 12px; line-height: 1.75; }
time { flex-shrink: 0; font-variant-numeric: tabular-nums; }
p { margin: 6px 0 0; overflow-wrap: anywhere; }
.task-error { margin-top: 12px; }
[data-state="failed"] strong, [data-state="interrupted"] strong { color: var(--offline); }
[data-state="succeeded"] strong { color: var(--accent); }
progress { display: block; width: 100%; height: 5px; margin-top: 12px; accent-color: var(--accent); }
</style>
