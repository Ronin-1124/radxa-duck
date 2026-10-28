import type { AudioPatch, AudioStatus, CloudConfig, CloudConfigPatch, CloudTestResult, DeviceStatus, ManagementTask,
  NetworkConnectRequest, NetworkScanResult, NetworkStatus, PowerAction, PowerStatus, UpdateStatus } from './management-types'

type Guard<T> = (value: unknown) => value is T
const record = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v)
const str = (v: unknown, max = 4096): v is string => typeof v === 'string' && v.length <= max
const num = (v: unknown, min = 0, max = Number.MAX_VALUE): v is number => typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max
const integer = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number => num(v, min, max) && Number.isSafeInteger(v)
const nullable = (v: unknown, guard: (value: unknown) => boolean): boolean => v === null || guard(v)
const bool = (v: unknown): v is boolean => typeof v === 'boolean'
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.length <= 256 && v.every(item => str(item))
const schema = (v: unknown, type?: string): v is Record<string, unknown> => record(v) && v.schema_version === 1 && (!type || v.type === type)
const capability = (v: unknown): boolean => record(v) && bool(v.available) && nullable(v.reason, str)
const warnings = (v: unknown): boolean => Array.isArray(v) && v.length <= 256 && v.every(w => record(w) && str(w.field, 128) && str(w.code, 128))
const timestamp = (v: unknown): v is string => str(v, 64) && Number.isFinite(Date.parse(v))
const fraction = (v: unknown): boolean => num(v, 0, 100)
const bytes = (v: unknown): boolean => integer(v)
function memory(v: unknown): boolean {
  if (!record(v) || !['total_bytes', 'available_bytes', 'used_bytes'].every(k => nullable(v[k], bytes))) return false
  const total = v.total_bytes
  return !(typeof total === 'number' && [v.available_bytes, v.used_bytes].some(n => typeof n === 'number' && n > total))
}
export const isDeviceStatus: Guard<DeviceStatus> = (v): v is DeviceStatus => schema(v, 'device_status') && timestamp(v.sampled_at)
  && ['hostname', 'model', 'os', 'kernel'].every(k => nullable(v[k], str)) && str(v.app_version, 128)
  && nullable(v.uptime_seconds, num) && record(v.cpu) && nullable(v.cpu.usage_percent, fraction)
  && nullable(v.cpu.sample_seconds, num) && nullable(v.cpu.cores, n => integer(n, 1, 4096))
  && nullable(v.cpu.load_average, a => Array.isArray(a) && a.length === 3 && a.every(n => num(n)))
  && memory(v.memory) && memory(v.storage) && record(v.storage) && str(v.storage.path)
  && record(v.temperature) && nullable(v.temperature.celsius, n => num(n, -40, 150)) && nullable(v.temperature.source, str) && warnings(v.warnings)
function audioDevice(v: unknown): boolean { return record(v) && str(v.id, 256) && str(v.card, 128) && str(v.name, 256) }
export const isAudioStatus: Guard<AudioStatus> = (v): v is AudioStatus => schema(v, 'audio_devices') && timestamp(v.sampled_at)
  && Array.isArray(v.inputs) && v.inputs.length <= 256 && v.inputs.every(i => audioDevice(i) && record(i)
    && nullable(i.rate, n => integer(n, 1, 384000)) && nullable(i.channels, n => integer(n, 1, 64)) && nullable(i.active_channel, str) && bool(i.read_only))
  && Array.isArray(v.outputs) && v.outputs.length <= 256 && v.outputs.every(audioDevice)
  && record(v.output) && nullable(v.output.device, str) && nullable(v.output.volume_percent, n => integer(n, 0, 100)) && nullable(v.output.muted, bool)
  && record(v.capabilities) && bool(v.capabilities.output_volume) && bool(v.capabilities.output_mute) && warnings(v.warnings)
export const isNetworkStatus: Guard<NetworkStatus> = (v): v is NetworkStatus => schema(v, 'network_status') && bool(v.available)
  && str(v.backend) && nullable(v.interface, str) && ['connected', 'connecting', 'disconnecting', 'disconnected', 'unavailable', 'failed'].includes(v.state as string)
  && nullable(v.ssid, str) && strings(v.ipv4) && nullable(v.strength, fraction) && record(v.capabilities)
  && capability(v.capabilities.scan) && capability(v.capabilities.connect)
  && nullable(v.pending_task, p => record(p) && str(p.task_id, 128) && bool(p.awaiting_confirmation) && nullable(p.confirmation_remaining_seconds, num))
export const isCloudConfig: Guard<CloudConfig> = (v): v is CloudConfig => schema(v) && integer(v.revision) && bool(v.configured)
  && v.provider === 'openai-compatible' && nullable(v.base_url, str) && nullable(v.model, s => str(s, 128))
  && integer(v.timeout_seconds, 1, 120) && bool(v.has_api_key) && nullable(v.updated_utc, timestamp)
  && v.configured === (v.revision > 0)
  && (v.configured ? str(v.base_url) && str(v.model, 128) && timestamp(v.updated_utc) : v.base_url === null && v.model === null && v.updated_utc === null && v.has_api_key === false)
  && !Object.hasOwn(v, 'api_key')
export const isPowerStatus: Guard<PowerStatus> = (v): v is PowerStatus => schema(v, 'power_status') && record(v.capabilities)
  && capability(v.capabilities.reboot) && capability(v.capabilities.shutdown)
  && nullable(v.scheduled, s => record(s) && (s.action === null || s.action === 'reboot' || s.action === 'shutdown') && str(s.type, 32)
    && num(s.scheduled_usec) && nullable(s.scheduled_utc, timestamp))
export const isUpdateStatus: Guard<UpdateStatus> = (v): v is UpdateStatus => schema(v, 'update_status') && str(v.package, 128)
  && bool(v.installed) && nullable(v.installed_version, str) && nullable(v.latest_version, str) && bool(v.available) && str(v.reason, 128)
  && record(v.capabilities) && bool(v.capabilities.check) && bool(v.capabilities.install) && bool(v.capabilities.rollback)
const publicError = (v: unknown): boolean => record(v) && str(v.code, 128) && str(v.message, 4096)
export const isManagementTask: Guard<ManagementTask> = (v): v is ManagementTask => schema(v)
  && typeof v.task_id === 'string' && /^[0-9a-f]{32}$/.test(v.task_id)
  && typeof v.request_id === 'string' && /^[A-Za-z0-9_-]{8,64}$/.test(v.request_id)
  && ['network.scan', 'network.connect', 'cloud.test', 'power.reboot', 'power.shutdown', 'update.check'].includes(v.kind as string)
  && ['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'].includes(v.state as string)
  && str(v.phase, 64) && nullable(v.progress, n => integer(n, 0, 100)) && timestamp(v.created_utc) && timestamp(v.updated_utc)
  && nullable(v.result, record) && nullable(v.error, publicError)
  && (['queued', 'running'].includes(v.state as string) ? v.result === null && v.error === null
    : v.state === 'succeeded' ? v.error === null : publicError(v.error))
export function isCloudTestResult(v: unknown): v is CloudTestResult {
  return schema(v) && integer(v.revision) && ['succeeded', 'failed'].includes(v.state as string) && num(v.elapsed_ms)
    && nullable(v.error, publicError) && nullable(v.model_available, bool)
    && (v.state === 'succeeded' ? v.error === null && v.model_available === true : publicError(v.error))
}
export function isNetworkScanResult(v: unknown): v is NetworkScanResult {
  return record(v) && str(v.interface) && bool(v.truncated) && Array.isArray(v.networks) && v.networks.length <= 128
    && v.networks.every(n => record(n) && str(n.ssid, 128) && fraction(n.strength) && ['open', 'wpa-psk', 'unsupported'].includes(n.security as string) && bool(n.connectable))
}

/** Only a bounded code is exposed; provider messages and request bodies never enter errors/logs. */
export class ManagementApiError extends Error {
  constructor(public readonly code: string, public readonly status = 0) { super(code); this.name = 'ManagementApiError' }
}
export function createRequestId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('')
}
async function request<T>(path: string, guard: Guard<T>, options: { method?: 'GET' | 'POST' | 'PUT' | 'PATCH'; body?: unknown; signal?: AbortSignal; maxBytes?: number } = {}): Promise<T> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) controller.abort()
  let timeout = false
  const timer = setTimeout(() => { timeout = true; controller.abort() }, 8000)
  try {
    const method = options.method ?? 'GET'
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (method !== 'GET') { headers['Content-Type'] = 'application/json'; headers['X-Radxa-Duck-Request'] = '1' }
    const response = await fetch(path, { method, headers, body: method === 'GET' ? undefined : JSON.stringify(options.body),
      cache: 'no-store', redirect: 'error', signal: controller.signal })
    if (!response.headers.get('content-type')?.toLowerCase().includes('application/json')) throw new ManagementApiError('invalid_response', response.status)
    const reader = response.body?.getReader()
    if (!reader) throw new ManagementApiError('invalid_response', response.status)
    const chunks: Uint8Array[] = []; let size = 0
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        size += value.byteLength
        if (size > (options.maxBytes ?? 1024 * 1024)) { await reader.cancel(); throw new ManagementApiError('invalid_response', response.status) }
        chunks.push(value)
      }
    } finally { reader.releaseLock() }
    const bytes = new Uint8Array(size); let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
    let value: unknown
    try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) } catch { throw new ManagementApiError('invalid_response', response.status) }
    if (!response.ok) {
      const code = record(value) && record(value.error) && typeof value.error.code === 'string' && /^[a-z][a-z0-9_.-]{0,63}$/.test(value.error.code) ? value.error.code : 'unavailable'
      throw new ManagementApiError(code, response.status)
    }
    if (!guard(value)) throw new ManagementApiError('invalid_response', response.status)
    return value
  } catch (error) {
    if (error instanceof ManagementApiError) throw error
    throw new ManagementApiError(timeout ? 'timeout' : options.signal?.aborted ? 'cancelled' : 'network_error')
  } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort) }
}
const isTaskList: Guard<{ schema_version: 1; tasks: ManagementTask[] }> = (v): v is { schema_version: 1; tasks: ManagementTask[] } =>
  schema(v) && Array.isArray(v.tasks) && v.tasks.length <= 256 && v.tasks.every(isManagementTask)
    && new Set(v.tasks.map(t => t.task_id)).size === v.tasks.length && new Set(v.tasks.map(t => t.request_id)).size === v.tasks.length
const taskFor = (kind: ManagementTask['kind'], requestId: string): Guard<ManagementTask> => (v): v is ManagementTask =>
  isManagementTask(v) && v.kind === kind && v.request_id === requestId
export const managementApi = {
  device: (signal?: AbortSignal) => request('/api/device/status', isDeviceStatus, { signal }),
  audio: (signal?: AbortSignal) => request('/api/audio', isAudioStatus, { signal }),
  setAudio: (body: AudioPatch, signal?: AbortSignal) => request('/api/audio', isAudioStatus, { method: 'PATCH', body, signal }),
  network: (signal?: AbortSignal) => request('/api/network/status', isNetworkStatus, { signal }),
  scan: (requestId: string, signal?: AbortSignal) => request('/api/network/scan', taskFor('network.scan', requestId), { method: 'POST', body: { request_id: requestId }, signal }),
  connect: (body: NetworkConnectRequest, signal?: AbortSignal) => request('/api/network/connect', taskFor('network.connect', body.request_id), { method: 'POST', body, signal }),
  confirm: (taskId: string, signal?: AbortSignal) => request('/api/network/confirm', (v): v is { task_id: string; confirmed: boolean } =>
    record(v) && v.task_id === taskId && bool(v.confirmed), { method: 'POST', body: { task_id: taskId }, signal }),
  cloud: (signal?: AbortSignal) => request('/api/cloud/config', isCloudConfig, { signal }),
  saveCloud: (body: CloudConfigPatch, signal?: AbortSignal) => request('/api/cloud/config', isCloudConfig, { method: 'PUT', body, signal }),
  testCloud: (requestId: string, expectedRevision: number, signal?: AbortSignal) => request('/api/cloud/test', taskFor('cloud.test', requestId),
    { method: 'POST', body: { request_id: requestId, expected_revision: expectedRevision }, signal }),
  power: (signal?: AbortSignal) => request('/api/power/status', isPowerStatus, { signal }),
  powerAction: (action: PowerAction, requestId: string, signal?: AbortSignal) => request('/api/power', taskFor(`power.${action}`, requestId), { method: 'POST', body: { action, request_id: requestId }, signal }),
  update: (signal?: AbortSignal) => request('/api/update/status', isUpdateStatus, { signal }),
  tasks: (signal?: AbortSignal) => request('/api/tasks', isTaskList, { signal, maxBytes: 8 * 1024 * 1024 }),
  task: (id: string, signal?: AbortSignal) => request(`/api/tasks/${encodeURIComponent(id)}`, (v): v is ManagementTask => isManagementTask(v) && v.task_id === id, { signal }),
}
