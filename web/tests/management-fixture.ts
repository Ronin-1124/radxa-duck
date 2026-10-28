import type { Page, Route } from '@playwright/test'
import type { AudioStatus, CloudConfig, DeviceStatus, ManagementTask, NetworkStatus, PowerStatus, TaskKind, UpdateStatus } from '../src/management-types'

export const deviceStatus: DeviceStatus = {
  schema_version: 1, type: 'device_status', sampled_at: '2026-10-27T02:00:00+00:00',
  hostname: 'duck-test', model: 'Radxa ZERO 3W', os: 'Debian 12', kernel: '6.1-test', uptime_seconds: 90061,
  app_version: '0.1.0', cpu: { usage_percent: 23.4, sample_seconds: 15, cores: 4, load_average: [0.5, 0.4, 0.3] },
  memory: { total_bytes: 1024 ** 3, available_bytes: 600 * 1024 ** 2, used_bytes: 424 * 1024 ** 2 },
  storage: { path: '/', total_bytes: 16 * 1024 ** 3, available_bytes: 10 * 1024 ** 3, used_bytes: 6 * 1024 ** 3 },
  temperature: { celsius: 48.2, source: 'soc-thermal' }, warnings: [],
}
export const audioStatus: AudioStatus = {
  schema_version: 1, type: 'audio_devices', sampled_at: '2026-10-27T02:00:00+00:00',
  inputs: [{ id: 'hw:CARD=pollen,DEV=0', card: 'pollen', name: 'Pollen HAT microphone', rate: 48000, channels: 2, active_channel: 'left', read_only: true }],
  outputs: [{ id: 'hw:CARD=pollen,DEV=0', card: 'pollen', name: 'Pollen HAT speaker' }],
  output: { device: 'hw:CARD=pollen,DEV=0', volume_percent: 30, muted: false },
  capabilities: { output_volume: true, output_mute: true }, warnings: [],
}
const denied = { available: false, reason: 'permission_denied' }
export const networkStatus: NetworkStatus = {
  schema_version: 1, type: 'network_status', available: true, backend: 'NetworkManager', backend_version: '1.42',
  interface: 'wlan0', state: 'connected', state_code: 100, ssid: 'Duck Lab', ipv4: ['192.168.2.151'], strength: 82,
  security: 'wpa-psk', radio_enabled: true, capabilities: { scan: denied, connect: denied }, pending_task: null,
}
export const powerStatus: PowerStatus = { schema_version: 1, type: 'power_status', capabilities: { reboot: denied, shutdown: denied }, scheduled: null }
export const updateStatus: UpdateStatus = { schema_version: 1, type: 'update_status', package: 'radxa-duck', installed: false,
  installed_version: null, latest_version: null, available: false, reason: 'package_not_installed', capabilities: { check: false, install: false, rollback: false } }
export const cloudConfig: CloudConfig = { schema_version: 1, revision: 2, configured: true, provider: 'openai-compatible',
  base_url: 'https://api.example.com/v1', model: 'test-model', timeout_seconds: 8, has_api_key: true, updated_utc: '2026-10-27T02:00:00+00:00' }
export const speechHealth = { schema_version: 1, state: 'idle', ready: true, provider: 'cpu' }
const settings = { model: 'yolo26n', rotation: 90, detection_enabled: true, max_fps: 10, score_threshold: 0.35 }
export const visionStatus = { type: 'state', schema_version: 1, revision: 1, state: 'idle', ready: true, session_id: null,
  owned: false, settings, stats: { frames: 0 }, error: null,
  capabilities: { transport: 'jpeg-websocket-v1', available_models: ['yolo26n', 'yolo26s'], rotations: [0, 90, 180, 270],
    default_settings: settings, max_fps: 15, max_jpeg_bytes: 262144, jpeg_quality: 75, capture_size: [640, 480], ack_timeout_seconds: 3 } }

export function task(kind: TaskKind, patch: Partial<ManagementTask> = {}): ManagementTask {
  return { schema_version: 1, task_id: 'a'.repeat(32), request_id: 'test-request-0001', kind, state: 'queued', phase: 'queued',
    progress: null, created_utc: '2026-10-27T02:00:00+00:00', updated_utc: '2026-10-27T02:00:00+00:00', result: null, error: null, ...patch }
}
export async function mockManagement(page: Page, options: {
  reads?: Record<string, unknown>
  handle?: (route: Route) => Promise<boolean>
} = {}) {
  const reads: Record<string, unknown> = {
    '/api/device/status': structuredClone(deviceStatus), '/api/audio': structuredClone(audioStatus),
    '/api/network/status': structuredClone(networkStatus), '/api/power/status': structuredClone(powerStatus),
    '/api/update/status': structuredClone(updateStatus), '/api/cloud/config': structuredClone(cloudConfig),
    '/api/tasks': { schema_version: 1, tasks: [] }, '/api/health': speechHealth, '/api/vision/status': visionStatus,
    ...options.reads,
  }
  const requests: { method: string; path: string; headers: Record<string, string>; body: Record<string, unknown> | null }[] = []
  const sockets: string[] = []
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    requests.push({ method: request.method(), path, headers: request.headers(), body: request.postData() ? request.postDataJSON() : null })
    if (options.handle && await options.handle(route)) return
    if (request.method() === 'GET' && Object.hasOwn(reads, path)) { await route.fulfill({ json: reads[path] }); return }
    await route.fulfill({ status: 404, json: { schema_version: 1, error: { code: 'not_found', message: 'Unmocked endpoint' } } })
  })
  await page.routeWebSocket(/\/api\//, socket => { sockets.push(socket.url()); socket.close() })
  return { reads, requests, sockets }
}
