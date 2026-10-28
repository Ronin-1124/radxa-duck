import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'

type Kind = 'speech' | 'vision'
type Command = { type: string }
type MockConnection = {
  kind: Kind
  socket: WebSocketRoute
  commands: Command[]
  closed: boolean
  revision: number
  publish(state: string): void
}
const settings = { model: 'yolov8n', rotation: 90, detection_enabled: true, max_fps: 10, score_threshold: 0.35 }
const capabilities = {
  transport: 'jpeg-websocket-v1', available_models: ['yolov8n'], rotations: [0, 90, 180, 270],
  default_settings: settings, max_fps: 15, max_jpeg_bytes: 262144, jpeg_quality: 75,
  capture_size: [640, 480], ack_timeout_seconds: 3,
}

function snapshot(kind: Kind, state = 'idle', revision = 1) {
  const active = ['starting', 'listening', 'streaming', 'stopping'].includes(state)
  const base = { type: 'state', schema_version: 1, state, ready: true, session_id: active ? 'control-test-session' : null,
    owned: active, error: null }
  return kind === 'speech'
    ? { ...base, partial_text: '', segments: [], audio_seconds: 0 }
    : { ...base, revision, settings, stats: { frames: 0 }, capabilities }
}

async function mockServices(page: Page): Promise<MockConnection[]> {
  const connections: MockConnection[] = []
  // Every service request is intercepted; these tests never connect to hardware.
  await page.route('**/api/**', async route => {
    if (new URL(route.request().url()).pathname === '/api/vision/status') await route.fulfill({ json: snapshot('vision') })
    else await route.abort()
  })
  await page.routeWebSocket(/\/api\//, socket => {
    const path = new URL(socket.url()).pathname
    if (!['/api/speech/live', '/api/vision/live'].includes(path)) { socket.close(); return }
    const kind: Kind = path.includes('/speech/') ? 'speech' : 'vision'
    const connection: MockConnection = { kind, socket, commands: [], closed: false, revision: 0,
      publish(state) { socket.send(JSON.stringify(snapshot(kind, state, ++this.revision))) } }
    connections.push(connection)
    socket.onMessage(raw => {
      if (typeof raw !== 'string') throw new Error('Control commands must be JSON text')
      connection.commands.push(JSON.parse(raw) as Command)
    })
    socket.onClose(() => { connection.closed = true })
    connection.publish('idle')
  })
  return connections
}

test.describe('shared interaction controls', () => {
  for (const { width, theme, locale } of [
    { width: 1440, theme: 'light', locale: 'zh-CN' },
    { width: 1440, theme: 'dark', locale: 'en' },
    { width: 375, theme: 'light', locale: 'zh-CN' },
    { width: 375, theme: 'dark', locale: 'en' },
  ]) {
    test(`voice and vision use the same idle layout at ${width}px in ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await page.addInitScript(({ locale, theme }) => {
        localStorage.setItem('radxa-duck.ui.locale', locale)
        localStorage.setItem('radxa-duck.ui.theme', theme)
      }, { locale, theme })
      const errors: string[] = []
      page.on('pageerror', error => errors.push(error.message))
      const connections = await mockServices(page)
      const measurements: Array<{ buttonHeight: number; headerGap: number; radius: string }> = []
      for (const route of ['/voice', '/vision']) {
        await page.goto(route)
        const control = page.locator('.interaction-control')
        const action = control.getByRole('button')
        await expect(action).toBeEnabled()
        await expect(control.locator('.status-label')).not.toBeEmpty()
        await expect(control.locator('.status-hint')).not.toBeEmpty()
        const geometry = await control.evaluate(element => {
          const rect = (selector: string) => element.querySelector<HTMLElement>(selector)!.getBoundingClientRect()
          const header = rect('.interaction-heading')
          const copy = rect('.interaction-copy')
          const button = rect('.action-button')
          const status = rect('.capture-status')
          return { buttonHeight: button.height, headerGap: status.top - header.bottom,
            radius: getComputedStyle(element.querySelector('.capture-status')!).borderRadius,
            rightAligned: Math.abs(button.right - header.right) < 1,
            buttonBelowCopy: button.top >= copy.bottom,
            fullWidth: Math.abs(button.width - header.width) < 1,
            sameRow: button.top < copy.bottom && copy.top < button.bottom }
        })
        expect(geometry.rightAligned).toBe(true)
        expect(width < 540 ? geometry.buttonBelowCopy && geometry.fullWidth : geometry.sameRow).toBe(true)
        expect(geometry.headerGap).toBeGreaterThan(0)
        measurements.push(geometry)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      }
      expect(measurements[0]!.buttonHeight).toBe(measurements[1]!.buttonHeight)
      expect(measurements[0]!.headerGap).toBe(measurements[1]!.headerGap)
      expect(measurements[0]!.radius).toBe(measurements[1]!.radius)
      expect(connections.flatMap(connection => connection.commands)).toEqual([])
      expect(errors).toEqual([])
    })
  }

  for (const kind of ['speech', 'vision'] as const) {
    test(`${kind} keeps start, stop, pending and disconnect behavior with the shared button`, async ({ page }) => {
      const connections = await mockServices(page)
      await page.goto(kind === 'speech' ? '/voice' : '/vision')
      const action = page.locator('.interaction-control').getByRole('button')
      await expect(action).toBeEnabled()
      const connection = connections[0]!
      expect(connection.commands).toEqual([])
      await action.click()
      await expect(action).toBeDisabled()
      await expect(action).toHaveAttribute('aria-busy', 'true')
      // A native disabled button must reject a second activation while awaiting confirmation.
      await action.evaluate(button => (button as HTMLButtonElement).click())
      expect(connection.commands.map(command => command.type)).toEqual(['start'])
      connection.publish('starting')
      await expect(action).toBeEnabled()
      connection.publish(kind === 'speech' ? 'listening' : 'streaming')
      await expect(action).toHaveText(kind === 'speech' ? '关闭收音' : '停止视觉')
      await action.click()
      await expect(action).toBeDisabled()
      connection.publish('stopping')
      await expect(action).toBeDisabled()
      connection.publish('idle')
      await expect(action).toBeEnabled()
      expect(connection.commands.map(command => command.type)).toEqual(['start', 'stop'])
      await page.getByRole('link', { name: '总览', exact: true }).click()
      await expect.poll(() => connection.closed).toBe(true)
      await page.goBack()
      await expect(action).toBeEnabled()
      expect(connections[1]!.commands).toEqual([])
      connections[1]!.socket.close()
      await expect(action).toBeDisabled()
      await expect(page.locator('.capture-status')).toHaveAttribute('data-tone', 'error')
      await expect(page.locator('.status-hint')).not.toBeEmpty()
    })
  }
})
