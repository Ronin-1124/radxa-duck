import { expect, test, type Page, type Route } from '@playwright/test'
import { mockManagement } from './management-fixture'

type Reply = 'ok' | 'failure' | 'invalid' | 'html' | 'hang'
type VisionModel = 'yolo26n' | 'yolo26s'
const speechHealth = { schema_version: 1, state: 'idle', ready: true, provider: 'cpu' }
function visionStatus(models: VisionModel[] = ['yolo26n', 'yolo26s']) {
  const settings = { model: models[0]!, rotation: 90, detection_enabled: true, max_fps: 10, score_threshold: 0.35 }
  return {
    type: 'state', schema_version: 1, revision: 1, state: 'idle', ready: true,
    session_id: null, owned: false, settings, stats: { frames: 0 }, error: null,
    capabilities: { transport: 'jpeg-websocket-v1', available_models: models,
      rotations: [0, 90, 180, 270], default_settings: settings, max_fps: 15,
      max_jpeg_bytes: 262144, jpeg_quality: 75, capture_size: [640, 480], ack_timeout_seconds: 3 },
  }
}

declare global {
  interface Window { deviceStatusFetches: Array<{ path: string; aborted: boolean }> }
}

async function mockStatus(page: Page) {
  const state = { deviceFailure: false, speech: 'ok' as Reply, vision: 'ok' as Reply, models: ['yolo26n', 'yolo26s'] as VisionModel[] }
  const requests: Array<{ path: string; method: string }> = []
  const held: Route[] = []
  await page.addInitScript(() => {
    window.deviceStatusFetches = []
    const nativeFetch = window.fetch.bind(window)
    window.fetch = (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href)
      if (['/api/health', '/api/vision/status'].includes(url.pathname)) {
        const record = { path: url.pathname, aborted: false }
        window.deviceStatusFetches.push(record)
        init?.signal?.addEventListener('abort', () => { record.aborted = true }, { once: true })
      }
      return nativeFetch(input, init)
    }
  })
  // The shared fixture contains every management API; override only the two capture-service reads.
  const management = await mockManagement(page, { handle: async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/device/status' && state.deviceFailure) {
      await route.fulfill({ status: 503, json: { schema_version: 1, error: { code: 'unavailable', message: 'Unavailable' } } })
      return true
    }
    if (!['/api/health', '/api/vision/status'].includes(path)) return false
    requests.push({ path, method: route.request().method() })
    const kind = path === '/api/health' ? 'speech' : 'vision'
    const reply = state[kind]
    if (reply === 'hang') { held.push(route); return true }
    if (reply === 'failure') { await route.fulfill({ status: 503, json: { error: 'unavailable' } }); return true }
    if (reply === 'html') { await route.fulfill({ contentType: 'text/html', body: '<html>SPA fallback</html>' }); return true }
    if (reply === 'invalid') {
      await route.fulfill({ json: kind === 'speech' ? { ...speechHealth, schema_version: 2 } : { ...visionStatus(state.models), ready: 'yes' } })
      return true
    }
    await route.fulfill({ json: kind === 'speech' ? speechHealth : visionStatus(state.models) })
    return true
  } })
  return { state, requests, allRequests: management.requests, sockets: management.sockets, release: async () => {
    for (const route of held.splice(0)) await route.abort().catch(() => {})
  } }
}

function speechCard(page: Page) { return page.getByRole('article', { name: '语音识别', exact: true }) }
function visionCard(page: Page) { return page.getByRole('article', { name: '视觉交互', exact: true }) }
async function freezeClock(page: Page): Promise<void> {
  const time = new Date('2026-10-27T02:00:00Z')
  await page.clock.install({ time })
  await page.clock.pauseAt(new Date(time.getTime() + 1000))
}

test.describe('device service status', () => {
  test('reads real metrics and service snapshots without writes or capture connections', async ({ page }) => {
    const mock = await mockStatus(page)
    await page.goto('/device')
    await expect(page.getByText('已读取 2 / 2 项服务状态')).toBeVisible()
    await expect(visionCard(page)).toContainText('YOLO26n · YOLO26s')
    await expect(visionCard(page)).toContainText('640 × 480')
    await expect(speechCard(page).getByRole('status')).toHaveText('待机')
    await expect(page.locator('.metric strong')).toHaveText(['23.4%', '424 MiB', '6 GiB', '48.2 °C'])
    await expect(page.locator('.hardware-meta')).toHaveText('duck-test')
    expect(mock.allRequests.every(request => request.method === 'GET')).toBe(true)
    expect(mock.requests).toEqual([
      { path: '/api/health', method: 'GET' }, { path: '/api/vision/status', method: 'GET' },
    ])
    expect(mock.sockets).toEqual([])
  })

  test('shows each read independently when only one service responds successfully', async ({ page }) => {
    const mock = await mockStatus(page)
    mock.state.speech = 'failure'
    await page.goto('/device')
    await expect(page.getByText('已读取 1 / 2 项服务状态')).toBeVisible()
    await expect(speechCard(page).getByRole('status')).toHaveText('状态读取失败')
    await expect(speechCard(page)).toContainText('请检查设备连接后重试')
    await expect(visionCard(page).getByRole('status')).toHaveText('待机')
    await expect(visionCard(page)).toContainText('YOLO26n · YOLO26s')
    await expect(page.getByRole('button', { name: '刷新状态', exact: true })).toBeEnabled()
    expect(mock.sockets).toEqual([])
  })

  test('labels stale data after a failed refresh and replaces it after recovery', async ({ page }) => {
    const mock = await mockStatus(page)
    await page.goto('/device')
    await expect(page.getByText('已读取 2 / 2 项服务状态')).toBeVisible()
    await expect(visionCard(page)).toContainText('YOLO26n · YOLO26s')
    mock.state.vision = 'failure'
    await page.getByRole('button', { name: '刷新状态', exact: true }).click()
    await expect(page.getByText('已读取 1 / 2 项服务状态')).toBeVisible()
    await expect(visionCard(page)).toContainText('上次读取：待机')
    await expect(visionCard(page)).not.toContainText('YOLO26')
    await expect(visionCard(page).getByRole('status')).toHaveText('状态读取失败')
    mock.state.vision = 'ok'
    mock.state.models = ['yolo26s']
    await page.getByRole('button', { name: '刷新状态', exact: true }).click()
    await expect(page.getByText('已读取 2 / 2 项服务状态')).toBeVisible()
    await expect(visionCard(page)).toContainText('YOLO26s')
    await expect(visionCard(page)).not.toContainText('YOLO26n')
    await expect(visionCard(page)).not.toContainText('上次读取')
  })

  test('rejects invalid schemas and HTML fallback pages rather than showing a successful read', async ({ page }) => {
    const mock = await mockStatus(page)
    mock.state.deviceFailure = true
    mock.state.speech = 'invalid'
    mock.state.vision = 'html'
    await page.goto('/device')
    await expect(page.getByText('已读取 0 / 2 项服务状态')).toBeVisible()
    for (const card of [speechCard(page), visionCard(page)]) {
      await expect(card.getByRole('status')).toHaveText('状态读取失败')
      await expect(card).toContainText('设备返回的状态暂时无法识别')
      await expect(card).not.toContainText('上次读取')
    }
    await expect(page.locator('.connection')).toHaveAttribute('data-state', 'offline')
    mock.state.speech = 'html'
    mock.state.vision = 'invalid'
    await page.getByRole('button', { name: '刷新状态', exact: true }).click()
    await expect(page.getByRole('button', { name: '刷新状态', exact: true })).toBeEnabled()
    await expect(page.getByText('已读取 0 / 2 项服务状态')).toBeVisible()
    await expect(visionCard(page)).not.toContainText('YOLO26')
  })

  test('times out a stalled service after five seconds and permits a successful retry', async ({ page }) => {
    await freezeClock(page)
    const mock = await mockStatus(page)
    mock.state.speech = 'hang'
    await page.goto('/device')
    await expect(visionCard(page)).toContainText('YOLO26n')
    await expect(page.getByRole('button', { name: '正在刷新', exact: true })).toBeDisabled()
    await page.clock.runFor(4999)
    await expect(speechCard(page)).not.toContainText('设备响应超时')
    await page.clock.runFor(1)
    await expect(speechCard(page)).toContainText('设备响应超时，请稍后重试')
    await expect(page.getByText('已读取 1 / 2 项服务状态')).toBeVisible()
    expect(await page.evaluate(() => window.deviceStatusFetches.find(request => request.path === '/api/health')?.aborted)).toBe(true)
    mock.state.speech = 'ok'
    await mock.release()
    await page.getByRole('button', { name: '刷新状态', exact: true }).click()
    await expect(page.getByText('已读取 2 / 2 项服务状态')).toBeVisible()
    await expect(speechCard(page)).not.toContainText('设备响应超时')
  })

  test('polls every fifteen seconds, aborts pending reads on departure, and stops polling', async ({ page }) => {
    await freezeClock(page)
    const mock = await mockStatus(page)
    await page.goto('/device')
    await expect(page.getByText('已读取 2 / 2 项服务状态')).toBeVisible()
    expect(mock.requests).toHaveLength(2)
    await page.clock.runFor(14999)
    expect(mock.requests).toHaveLength(2)
    mock.state.speech = 'hang'
    mock.state.vision = 'hang'
    await page.clock.runFor(1)
    await expect.poll(() => mock.requests.length).toBe(4)
    await expect(page.getByRole('button', { name: '正在刷新', exact: true })).toBeDisabled()
    await page.getByRole('link', { name: '总览', exact: true }).click()
    await expect(page).toHaveURL(/\/overview$/)
    expect(await page.evaluate(() => window.deviceStatusFetches.slice(-2).every(request => request.aborted))).toBe(true)
    await mock.release()
    await page.clock.runFor(45000)
    expect(mock.requests).toHaveLength(4)
    expect(mock.sockets).toEqual([])
  })
})
