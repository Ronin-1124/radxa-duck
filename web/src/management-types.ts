export interface Capability { available: boolean; reason: string | null }
export interface FieldWarning { field: string; code: string }
export interface DeviceStatus {
  schema_version: 1; type: 'device_status'; sampled_at: string
  hostname: string | null; model: string | null; os: string | null; kernel: string | null
  uptime_seconds: number | null; app_version: string
  cpu: { usage_percent: number | null; sample_seconds: number | null; cores: number | null; load_average: number[] | null }
  memory: { total_bytes: number | null; available_bytes: number | null; used_bytes: number | null }
  storage: { path: string; total_bytes: number | null; available_bytes: number | null; used_bytes: number | null }
  temperature: { celsius: number | null; source: string | null }; warnings: FieldWarning[]
}
export interface AudioDevice { id: string; card: string; name: string }
export interface AudioStatus {
  schema_version: 1; type: 'audio_devices'; sampled_at: string
  inputs: (AudioDevice & { rate: number | null; channels: number | null; active_channel: string | null; read_only: boolean })[]
  outputs: AudioDevice[]
  output: { device: string | null; volume_percent: number | null; muted: boolean | null }
  capabilities: { output_volume: boolean; output_mute: boolean }; warnings: FieldWarning[]
}
export type AudioPatch = { volume_percent?: number; muted?: boolean }
export interface NetworkStatus {
  schema_version: 1; type: 'network_status'; available: boolean; backend: string; backend_version?: string | null
  reason?: string; interface: string | null
  state: 'connected' | 'connecting' | 'disconnecting' | 'disconnected' | 'unavailable' | 'failed'
  state_code?: number; ssid: string | null; ipv4: string[]; strength: number | null; security?: string | null; radio_enabled?: boolean
  capabilities: { scan: Capability; connect: Capability }
  pending_task: { task_id: string; awaiting_confirmation: boolean; confirmation_remaining_seconds: number | null } | null
}
export interface NetworkEntry { ssid: string; strength: number; security: 'open' | 'wpa-psk' | 'unsupported'; connectable: boolean }
export interface NetworkScanResult { interface: string; networks: NetworkEntry[]; truncated: boolean }
export interface NetworkConnectRequest { request_id: string; ssid: string; password?: string; security?: 'open' | 'wpa-psk' }
export interface CloudConfig {
  schema_version: 1; revision: number; configured: boolean; provider: 'openai-compatible'
  base_url: string | null; model: string | null; timeout_seconds: number; has_api_key: boolean; updated_utc: string | null
}
export interface CloudConfigPatch {
  expected_revision: number; provider?: 'openai-compatible'; base_url?: string; model?: string
  timeout_seconds?: number; api_key?: string | null
}
export interface CloudTestResult {
  schema_version: 1; revision: number; state: 'succeeded' | 'failed'; elapsed_ms: number
  error: { code: string; message: string } | null; model_available: boolean | null
}
export type PowerAction = 'reboot' | 'shutdown'
export interface PowerStatus {
  schema_version: 1; type: 'power_status'; capabilities: { reboot: Capability; shutdown: Capability }
  scheduled: { action: PowerAction | null; type: string; scheduled_usec: number; scheduled_utc: string | null } | null
}
export interface UpdateStatus {
  schema_version: 1; type: 'update_status'; package: string; installed: boolean; installed_version: string | null
  latest_version: string | null; available: boolean; reason: string
  capabilities: { check: boolean; install: boolean; rollback: boolean }
}
export type TaskKind = 'network.scan' | 'network.connect' | 'cloud.test' | 'power.reboot' | 'power.shutdown' | 'update.check'
export type TaskState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
export interface ManagementTask {
  schema_version: 1; task_id: string; kind: TaskKind; request_id: string; state: TaskState; phase: string
  progress: number | null; created_utc: string; updated_utc: string
  result: Record<string, unknown> | null; error: { code: string; message: string } | null
}
