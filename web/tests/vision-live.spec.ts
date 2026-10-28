import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'
import type { DetectionResult, VisionModel, VisionSettings } from '../src/vision-types'

const defaults: VisionSettings = { model: 'yolov8n', rotation: 90, detection_enabled: true, max_fps: 10, score_threshold: 0.35 }
const capabilities = {
  transport: 'jpeg-websocket-v1', available_models: ['yolov8n'], rotations: [0, 90, 180, 270],
  default_settings: defaults, max_fps: 15, max_jpeg_bytes: 262144, jpeg_quality: 75,
  capture_size: [640, 480], ack_timeout_seconds: 3,
}
type StateName = 'idle' | 'starting' | 'streaming' | 'stopping' | 'error' | 'closed'
type Command = { type: string; settings?: VisionSettings; session_id?: string; frame_id?: number }
type MockConnection = { socket: WebSocketRoute; commands: Command[]; closed: boolean; sessionId: string; settings: VisionSettings; revision: number; availableModels: VisionModel[] }
const image = (page: Page) => page.locator('.vision-frame img')
const acks = (connection: MockConnection) => connection.commands.filter(command => command.type === 'ack')

function snapshot(state: StateName = 'idle', connection?: MockConnection, extra: Record<string, unknown> = {}) {
  const active = ['starting', 'streaming', 'stopping'].includes(state)
  return { type: 'state', schema_version: 1, revision: connection ? ++connection.revision : 1, state, ready: true,
    session_id: active ? connection?.sessionId ?? null : null, owned: active && !!connection,
    settings: connection?.settings ?? defaults, stats: { frames: 0 }, error: null,
    capabilities: { ...capabilities, available_models: connection?.availableModels ?? capabilities.available_models }, ...extra }
}

async function mockVision(page: Page, options: { autoStop?: boolean; httpGate?: Promise<void>; availableModels?: VisionModel[] } = {}) {
  const connections: MockConnection[] = []
  const ackDisplays: Array<{ frameId: number; displayed: boolean; width: number; height: number }> = []
  await page.addInitScript(() => {
    const originalCreate = URL.createObjectURL.bind(URL)
    const originalRevoke = URL.revokeObjectURL.bind(URL)
    const active = new Set<string>()
    ;(window as unknown as { visionURLs: { active: () => number; created: number; revoked: number } }).visionURLs = {
      active: () => active.size, created: 0, revoked: 0,
    }
    const counts = (window as unknown as { visionURLs: { created: number; revoked: number } }).visionURLs
    URL.createObjectURL = blob => { const url = originalCreate(blob); active.add(url); counts.created++; return url }
    URL.revokeObjectURL = url => { if (active.delete(url)) counts.revoked++; originalRevoke(url) }
  })
  // No test is permitted to fall through to the real board.
  await page.route('**/api/**', async route => {
    if (new URL(route.request().url()).pathname !== '/api/vision/status') { await route.abort(); return }
    if (options.httpGate) await options.httpGate
    await route.fulfill({ json: snapshot('idle', undefined, {
      capabilities: { ...capabilities, available_models: options.availableModels ?? capabilities.available_models },
    }) })
  })
  await page.routeWebSocket(/\/api\//, socket => {
    if (new URL(socket.url()).pathname !== '/api/vision/live') { socket.close(); return }
    const connection: MockConnection = { socket, commands: [], closed: false, revision: 0,
      sessionId: `mock-session-${connections.length + 1}`, settings: { ...defaults },
      availableModels: options.availableModels ?? ['yolov8n'] }
    connections.push(connection)
    socket.onClose(() => { connection.closed = true })
    socket.onMessage(raw => {
      if (typeof raw !== 'string') throw new Error('Client commands must be JSON text')
      const command = JSON.parse(raw) as Command
      connection.commands.push(command)
      if (command.type === 'start') {
        connection.settings = { ...defaults, ...command.settings }
        socket.send(JSON.stringify(snapshot('starting', connection)))
      } else if (command.type === 'stop' && options.autoStop !== false) {
        socket.send(JSON.stringify(snapshot('stopping', connection)))
        socket.send(JSON.stringify(snapshot('idle', connection)))
      } else if (command.type === 'ack') {
        void page.evaluate(() => {
          const img = document.querySelector<HTMLImageElement>('.vision-frame img')
          return { displayed: !!img?.isConnected && img.complete && img.getBoundingClientRect().width > 0,
            width: img?.naturalWidth ?? 0, height: img?.naturalHeight ?? 0 }
        }).then(display => ackDisplays.push({ frameId: command.frame_id!, ...display }))
      }
    })
    socket.send(JSON.stringify(snapshot('idle', connection)))
  })
  return { connections, ackDisplays }
}

async function jpeg(page: Page, width = 480, height = 640): Promise<Buffer> {
  const base64 = await page.evaluate(({ width, height }) => {
    const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height
    const context = canvas.getContext('2d')!
    context.fillStyle = '#2b4534'; context.fillRect(0, 0, width, height)
    context.fillStyle = '#b1df83'; context.fillRect(width / 4, height / 4, width / 2, height / 2)
    return canvas.toDataURL('image/jpeg', 0.75).split(',')[1]!
  }, { width, height })
  return Buffer.from(base64, 'base64')
}

function frameHeader(connection: MockConnection, bytes: Buffer, frameId = 1, extra: Record<string, unknown> = {}) {
  const rotated = connection.settings.rotation % 180 !== 0
  const width = rotated ? 480 : 640; const height = rotated ? 640 : 480
  const result: DetectionResult | null = connection.settings.detection_enabled ? {
    schema_version: 1, type: 'detections', provider: 'npu', backend: 'rknn-lite', model_name: connection.settings.model,
    model_sha256: 'a'.repeat(64), precision: 'int8', input_size: [640, 640], initialization_ms: 10,
    frame_id: frameId, captured_monotonic: 100, image_size: [width, height],
    detections: [{ class_id: 0, label: 'person', score: 0.9, box_xyxy: [50, 60, 200, 300], center_normalized: [125 / width, 180 / height] }],
    preprocess_ms: 2, inference_call_ms: 12, postprocess_ms: 3, total_ms: 17, frame_age_ms: 25,
  } : null
  return { type: 'frame', schema_version: 1, session_id: connection.sessionId, frame_id: frameId,
    width, height, mime_type: 'image/jpeg', image_bytes: bytes.length, result, frame_age_ms: 30, ...extra }
}

async function connected(page: Page, mock: Awaited<ReturnType<typeof mockVision>>): Promise<MockConnection> {
  await page.goto('/vision')
  await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeEnabled()
  expect(mock.connections).toHaveLength(1)
  return mock.connections[0]!
}

async function start(page: Page, connection: MockConnection): Promise<void> {
  await page.getByRole('button', { name: '开启视觉', exact: true }).click()
  await expect.poll(() => connection.commands.filter(command => command.type === 'start').length).toBe(1)
}

function sendFrame(connection: MockConnection, bytes: Buffer, id = 1, extra: Record<string, unknown> = {}): void {
  connection.socket.send(JSON.stringify(snapshot('streaming', connection, { stats: { frames: id } })))
  connection.socket.send(JSON.stringify(frameHeader(connection, bytes, id, extra)))
  connection.socket.send(bytes)
}

async function activeURLs(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as { visionURLs: { active: () => number } }).visionURLs.active())
}

test('connects without starting, uses server defaults, and reload never restarts capture', async ({ page }) => {
  const mock = await mockVision(page)
  const connection = await connected(page, mock)
  expect(connection.commands).toEqual([])
  await expect(image(page)).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: /^画面方向:/ })).toContainText('90°')
  await page.getByRole('combobox', { name: /^检测模型:/ }).click()
  await expect(page.getByRole('option', { name: 'YOLOv8n', exact: true })).toHaveCount(1)
  await expect(page.getByRole('option', { name: 'YOLOv5n', exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await start(page, connection)
  expect(connection.commands[0]).toEqual({ type: 'start', settings: defaults })
  const bytes = await jpeg(page)
  sendFrame(connection, bytes)
  await expect.poll(() => acks(connection).length).toBe(1)
  await page.reload()
  await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeEnabled()
  await expect.poll(() => connection.closed).toBe(true)
  expect(mock.connections).toHaveLength(2)
  expect(mock.connections[1]!.commands).toEqual([])
  await expect(image(page)).toHaveCount(0)
})

test('ACKs only a displayed JPEG and releases replaced and stopped image URLs', async ({ page }) => {
  const mock = await mockVision(page)
  const connection = await connected(page, mock)
  await start(page, connection)
  const bytes = await jpeg(page)
  for (let id = 1; id <= 3; id++) {
    sendFrame(connection, bytes, id)
    await expect.poll(() => acks(connection).length).toBe(id)
    expect(acks(connection).at(-1)).toEqual({ type: 'ack', session_id: connection.sessionId, frame_id: id })
    await expect.poll(() => mock.ackDisplays.length).toBe(id)
    expect(mock.ackDisplays.at(-1)).toMatchObject({ frameId: id, displayed: true, width: 480, height: 640 })
    await expect(page.locator('.object-name')).toHaveText('人')
    await expect(page.locator('.detection-box')).toHaveCount(1)
    await expect.poll(() => activeURLs(page)).toBe(1)
  }
  await page.getByRole('button', { name: '停止视觉', exact: true }).click()
  await expect(image(page)).toHaveCount(0)
  await expect.poll(() => activeURLs(page)).toBe(0)
  await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeEnabled()
})

for (const [model, label] of [['yolo26n', 'YOLO26n'], ['yolo26s', 'YOLO26s']] as const) {
  test(`selects an advertised ${label} model and displays its detections before ACK`, async ({ page }) => {
    const mock = await mockVision(page, { availableModels: ['yolov8n', 'yolov5n', 'yolo26n', 'yolo26s'] })
    const connection = await connected(page, mock)
    await page.getByRole('combobox', { name: /^检测模型:/ }).click()
    await page.getByRole('option', { name: label, exact: true }).click()
    await start(page, connection)
    expect(connection.commands[0]).toEqual({ type: 'start', settings: { ...defaults, model } })
    sendFrame(connection, await jpeg(page))
    await expect.poll(() => acks(connection).length).toBe(1)
    await expect.poll(() => mock.ackDisplays.length).toBe(1)
    expect(mock.ackDisplays[0]).toMatchObject({ frameId: 1, displayed: true, width: 480, height: 640 })
    await expect(image(page)).toBeVisible()
    await expect(page.locator('.object-name')).toHaveText('人')
    await expect(page.locator('.detection-box')).toHaveCount(1)
    await expect(page.locator('.locked-value').filter({ hasText: label })).toHaveCount(1)
    await page.locator('.processing-details > summary').click()
    await expect(page.locator('.metrics-grid').getByText(label, { exact: true })).toBeVisible()
    expect(connection.closed).toBe(false)
  })
}

test('late HTTP status never overwrites WebSocket ownership', async ({ page }) => {
  let releaseStatus!: () => void
  const gate = new Promise<void>(resolve => { releaseStatus = resolve })
  const mock = await mockVision(page, { httpGate: gate })
  const connection = await connected(page, mock)
  await start(page, connection)
  const bytes = await jpeg(page); sendFrame(connection, bytes)
  await expect.poll(() => acks(connection).length).toBe(1)
  releaseStatus()
  await expect(page.getByRole('button', { name: '停止视觉', exact: true })).toBeEnabled()
  await expect(image(page)).toBeVisible()
})

test('a healthy service reconnect clears an earlier fatal cleanup error', async ({ page }) => {
  const mock = await mockVision(page)
  const connection = await connected(page, mock)
  connection.socket.send(JSON.stringify(snapshot('error', connection, {
    ready: false, owned: true, session_id: connection.sessionId,
    error: { code: 'cleanup_failed', message: 'Resource cleanup failed' },
  })))
  await expect(page.getByRole('alert')).toContainText('设备资源还未释放')
  await expect(page.locator('.action-button')).toBeDisabled()
  connection.socket.close()
  await expect.poll(() => mock.connections.length).toBe(2)
  await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeEnabled()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(mock.connections[1]!.commands).toEqual([])
})

test('plain preview displays JPEGs without waiting for detections', async ({ page }) => {
  const mock = await mockVision(page)
  const connection = await connected(page, mock)
  await page.getByRole('switch', { name: /目标检测/ }).click()
  await start(page, connection)
  expect(connection.commands[0]!.settings!.detection_enabled).toBe(false)
  sendFrame(connection, await jpeg(page))
  await expect.poll(() => acks(connection).length).toBe(1)
  await expect(image(page)).toBeVisible()
  await expect(page.locator('.detection-box')).toHaveCount(0)
  await expect(page.locator('.targets-panel')).not.toContainText('等待同帧结果')
  await expect(page.locator('.targets-panel')).toContainText('目标检测已关闭')
})

for (const fault of ['wrong-session', 'mismatched-result', 'corrupt-jpeg', 'oversize', 'binary-without-header', 'state-between-pair'] as const) {
  test(`rejects ${fault} without ACK and releases image resources`, async ({ page }) => {
    const mock = await mockVision(page)
    const connection = await connected(page, mock)
    await start(page, connection)
    const bytes = await jpeg(page)
    connection.socket.send(JSON.stringify(snapshot('streaming', connection)))
    const header = frameHeader(connection, bytes)
    if (fault === 'wrong-session') header.session_id = 'old-session'
    if (fault === 'mismatched-result') header.result!.frame_id = 900
    if (fault === 'oversize') header.image_bytes = 262145
    if (fault === 'corrupt-jpeg') header.image_bytes = 4
    if (fault !== 'binary-without-header') connection.socket.send(JSON.stringify(header))
    if (fault === 'state-between-pair') connection.socket.send(JSON.stringify(snapshot('streaming', connection)))
    connection.socket.send(fault === 'corrupt-jpeg' ? Buffer.from([255, 216, 255, 217]) : bytes)
    await expect.poll(() => connection.closed).toBe(true)
    expect(acks(connection)).toEqual([])
    await expect(image(page)).toHaveCount(0)
    await expect.poll(() => activeURLs(page)).toBe(0)
  })
}

test('does not ACK a frame whose binary message arrives after stop', async ({ page }) => {
  const mock = await mockVision(page, { autoStop: false })
  const connection = await connected(page, mock)
  await start(page, connection)
  const bytes = await jpeg(page)
  connection.socket.send(JSON.stringify(snapshot('streaming', connection)))
  connection.socket.send(JSON.stringify(frameHeader(connection, bytes)))
  await page.getByRole('button', { name: '停止视觉', exact: true }).click()
  connection.socket.send(bytes)
  connection.socket.send(JSON.stringify(snapshot('idle', connection)))
  await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeEnabled()
  await expect(image(page)).toHaveCount(0)
  await expect.poll(() => activeURLs(page)).toBe(0)
  expect(acks(connection)).toEqual([])
})

test('disconnect and reconnect do not restart capture', async ({ page }) => {
  const mock = await mockVision(page)
  const connection = await connected(page, mock)
  await start(page, connection)
  sendFrame(connection, await jpeg(page))
  await expect.poll(() => acks(connection).length).toBe(1)
  connection.socket.close({ code: 1013, reason: 'test_disconnect' })
  await expect(image(page)).toHaveCount(0)
  await expect.poll(() => activeURLs(page)).toBe(0)
  await expect.poll(() => mock.connections.length).toBe(2)
  await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeEnabled()
  expect(mock.connections[1]!.commands).toEqual([])
})

for (const exit of ['navigate', 'pagehide'] as const) {
  test(`${exit} closes the control socket and releases all images`, async ({ page }) => {
    const mock = await mockVision(page)
    const connection = await connected(page, mock)
    await start(page, connection)
    sendFrame(connection, await jpeg(page))
    await expect.poll(() => acks(connection).length).toBe(1)
    if (exit === 'navigate') await page.getByRole('link', { name: '总览', exact: true }).click()
    else await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })))
    await expect.poll(() => connection.closed).toBe(true)
    await expect.poll(() => activeURLs(page)).toBe(0)
    await expect(image(page)).toHaveCount(0)
    expect(connection.commands.filter(command => command.type === 'start')).toHaveLength(1)
  })
}

test('a missing binary message times out without granting frame credit', async ({ page }) => {
  const mock = await mockVision(page)
  const connection = await connected(page, mock)
  await start(page, connection)
  const bytes = await jpeg(page)
  connection.socket.send(JSON.stringify(snapshot('streaming', connection)))
  connection.socket.send(JSON.stringify(frameHeader(connection, bytes)))
  await expect.poll(() => connection.closed, { timeout: 5000 }).toBe(true)
  expect(acks(connection)).toEqual([])
  await expect.poll(() => activeURLs(page)).toBe(0)
})

test('leaving during decode cancels the pending image and never ACKs it', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLImageElement.prototype.decode
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    ;(window as unknown as { releaseVisionDecode: () => void }).releaseVisionDecode = release
    HTMLImageElement.prototype.decode = function () {
      return this.src.startsWith('blob:') ? gate.then(() => original.call(this)) : original.call(this)
    }
  })
  const mock = await mockVision(page)
  const connection = await connected(page, mock)
  await start(page, connection)
  sendFrame(connection, await jpeg(page))
  await expect.poll(() => activeURLs(page)).toBe(1)
  await expect(image(page)).toHaveCount(0)
  expect(acks(connection)).toEqual([])
  await page.getByRole('link', { name: '总览', exact: true }).click()
  await expect.poll(() => connection.closed).toBe(true)
  await expect.poll(() => activeURLs(page)).toBe(0)
  await page.evaluate(() => (window as unknown as { releaseVisionDecode: () => void }).releaseVisionDecode())
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  expect(acks(connection)).toEqual([])
  await expect(image(page)).toHaveCount(0)
})

test('language and appearance changes preserve the control connection and current settings', async ({ page }) => {
  const mock = await mockVision(page)
  const connection = await connected(page, mock)
  await page.getByRole('combobox', { name: /^画面方向:/ }).click()
  await page.getByRole('option', { name: '顺时针 270°', exact: true }).click()
  await page.getByRole('combobox', { name: /^语言:/ }).click()
  await page.getByRole('option', { name: 'English', exact: true }).click()
  await expect(page.getByRole('combobox', { name: /^Image orientation:/ })).toContainText('270°')
  await page.getByRole('combobox', { name: /^Appearance:/ }).click()
  await page.getByRole('option', { name: 'Dark', exact: true }).click()
  expect(mock.connections).toHaveLength(1)
  expect(connection.commands).toEqual([])
  await page.getByRole('button', { name: 'Start vision', exact: true }).click()
  await expect.poll(() => connection.commands.length).toBe(1)
  expect(connection.commands[0]).toEqual({ type: 'start', settings: { ...defaults, rotation: 270 } })
})
