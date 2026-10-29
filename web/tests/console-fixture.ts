import type { Page, Route, WebSocketRoute } from '@playwright/test'
import type { BrainStatus, IdentityConfig, ObserverStatus } from '../src/console-protocol'
import { deviceStatus, mockManagement } from './management-fixture'

export const brainStatus: BrainStatus = {
  schema_version: 1, type: 'brain_status', instance_id: 'brain-1', revision: 1, sampled_at: '2026-10-28T02:00:00Z',
  state: 'running', activity: 'idle', events_available: true,
  identity: { name: '小鸭', revision: 1, active_revision: 1 },
  microphone: { available: true, enabled: true, active: true },
  camera: { available: true, enabled: true, active: false },
  cloud: { configured: true, model: 'cloud-model', health: 'ready' },
  motion: { available: false, state: 'unavailable' }, task: null,
  controls: { pause: true, resume: false, enable_microphone: false, disable_microphone: true, cancel_task: false },
}
export const identityConfig: IdentityConfig = { schema_version: 1, type: 'identity_config', revision: 1, active_revision: 1,
  profile: { name: '小鸭', description: 'Radxa 的小鸭机器人', personality: '温和、好奇', expression_style: '自然简短' } }
export type ObserverSocket = { socket: WebSocketRoute; closed: boolean; commands: Record<string, unknown>[]; status: ObserverStatus; publish(patch?: Partial<ObserverStatus>): void }
export async function mockConsole(page: Page, options: {
  missing?: boolean; handle?: (route: Route) => Promise<boolean>; cameraActive?: boolean; events?: boolean
} = {}) {
  const management = await mockManagement(page, { reads: { '/api/device/status': { ...structuredClone(deviceStatus), model: 'Radxa ZERO 3' } } })
  const brain = structuredClone(brainStatus), identity = structuredClone(identityConfig)
  if (options.events === false) brain.events_available = false
  const requests: { method: string; path: string; body: Record<string, unknown> | null; headers: Record<string, string> }[] = []
  const observers: ObserverSocket[] = [], events: { socket: WebSocketRoute; closed: boolean }[] = []
  let offline = false
  await page.route(/\/api\/(brain\/|identity(?:$|\?)|vision\/observer\/)/, async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    requests.push({ method: request.method(), path, body: request.postData() ? request.postDataJSON() : null, headers: request.headers() })
    if (options.handle && await options.handle(route)) return
    if (offline) { await route.abort(); return }
    if (options.missing) { await route.fulfill({ status: 404, json: { error: { code: 'not_found' } } }); return }
    if (path === '/api/brain/status') await route.fulfill({ json: brain })
    else if (path === '/api/identity') await route.fulfill({ json: identity })
    else if (path === '/api/vision/observer/status') await route.fulfill({ json: { schema_version: 1, type: 'vision_observer_info', available: true, max_jpeg_bytes: 262144 } })
    else await route.fulfill({ status: 404, json: { error: { code: 'not_found' } } })
  })
  await page.routeWebSocket(/\/api\/brain\/events/, socket => {
    const entry = { socket, closed: false }; events.push(entry)
    socket.onClose(() => { entry.closed = true })
    socket.onMessage(() => { throw new Error('Brain observations must never send control messages') })
    socket.send(JSON.stringify(brain))
  })
  await page.routeWebSocket(/\/api\/vision\/observe(?:$|\?)/, socket => {
    const entry: ObserverSocket = { socket, closed: false, commands: [],
      status: { schema_version: 1, type: 'observer_status', revision: 1, subscription_id: `observer-${observers.length + 1}`,
        camera_active: options.cameraActive ?? false, preview_owned: false, can_start_preview: true },
      publish(patch = {}) { Object.assign(this.status, patch, { revision: this.status.revision + 1 }); socket.send(JSON.stringify(this.status)) } }
    observers.push(entry)
    socket.onClose(() => { entry.closed = true })
    socket.onMessage(raw => {
      const command = JSON.parse(String(raw)) as Record<string, unknown>; entry.commands.push(command)
      if (command.type === 'preview.start') entry.publish({ preview_owned: true, camera_active: true, request_id: command.request_id as string })
      if (command.type === 'preview.stop') entry.publish({ preview_owned: false, camera_active: options.cameraActive ?? false, request_id: command.request_id as string })
    })
    socket.send(JSON.stringify(entry.status))
  })
  function publish(patch: Partial<BrainStatus> = {}) {
    Object.assign(brain, patch, { revision: brain.revision + 1 })
    for (const e of events.filter(e => !e.closed)) e.socket.send(JSON.stringify(brain))
  }
  return { management, brain, identity, requests, observers, events, publish, setOffline(value: boolean) { offline = value } }
}
export async function sendFrame(page: Page, observer: ObserverSocket, id = 1) {
  const base64 = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480
    const ctx = canvas.getContext('2d')!; ctx.fillStyle = '#294330'; ctx.fillRect(0, 0, 640, 480)
    ctx.fillStyle = '#a8d58a'; ctx.fillRect(160, 120, 320, 240)
    return canvas.toDataURL('image/jpeg', .75).split(',')[1]!
  })
  const bytes = Buffer.from(base64, 'base64')
  observer.socket.send(JSON.stringify({ schema_version: 1, type: 'frame', subscription_id: observer.status.subscription_id,
    frame_id: id, width: 640, height: 480, jpeg_bytes: bytes.length,
    detections: [{ label: 'test object', score: .9, box_xyxy: [160, 120, 480, 360] }] }))
  observer.socket.send(bytes)
}
