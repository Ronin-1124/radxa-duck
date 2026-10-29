import { expect, test } from '@playwright/test'
import { mockConsole, sendFrame } from './console-fixture'

interface PreviewProbe { previewUrls: { created: string[]; revoked: string[] }; finishPreviewDecode: () => void }

test('web preview can be closed independently and its off choice survives navigation', async ({ page }) => {
  const io = await mockConsole(page, { cameraActive: true })
  io.brain.camera.active = true
  await page.goto('/vision')
  await expect.poll(() => io.observers.length).toBe(1)
  const first = io.observers[0]!
  await sendFrame(page, first)
  await expect.poll(() => first.commands.length).toBe(1)
  await page.getByRole('button', { name: '关闭网页预览', exact: true }).click()
  await expect.poll(() => first.closed).toBe(true)
  await expect(page.locator('.camera-image')).toHaveCount(0)
  await expect(page.locator('.camera-placeholder')).toHaveText('网页预览已关闭')
  await expect(page.locator('.view-footer')).toContainText('正在采集')
  await page.getByRole('link', { name: '状态', exact: true }).click()
  await expect(page.locator('.capability-item').filter({ hasText: '摄像头' })).toContainText('正在采集')
  await page.getByRole('link', { name: '视角', exact: true }).click()
  await expect(page.getByRole('button', { name: '开启网页预览', exact: true })).toHaveAttribute('aria-pressed', 'false')
  expect(io.observers).toHaveLength(1)
  await page.getByRole('button', { name: '开启网页预览', exact: true }).click()
  await expect.poll(() => io.observers.length).toBe(2)
  await sendFrame(page, io.observers[1]!)
  await expect(page.locator('.camera-image')).toBeVisible()
  expect(first.commands.map(c => c.type)).toEqual(['ack'])
  expect(io.observers[1]!.commands.every(c => c.type === 'ack')).toBe(true)
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
  expect(io.events.every(e => !e.closed)).toBe(true)
})

test('preview and detection choices survive main views while images and leases are released', async ({ page }) => {
  const io = await mockConsole(page, { cameraActive: true })
  await page.goto('/vision')
  await expect.poll(() => io.observers.length).toBe(1)
  await sendFrame(page, io.observers[0]!)
  await page.getByRole('button', { name: '画面详情', exact: true }).click()
  await page.getByRole('button', { name: '目标标注', exact: true }).click()
  await expect(page.locator('.detection-overlay rect')).toHaveCount(1)
  await page.getByRole('link', { name: '状态', exact: true }).click()
  await expect.poll(() => io.observers[0]!.closed).toBe(true)
  await page.getByRole('link', { name: '视角', exact: true }).click()
  await expect.poll(() => io.observers.length).toBe(2)
  await expect(page.locator('.camera-image')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '目标标注', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await sendFrame(page, io.observers[1]!)
  await expect(page.locator('.detection-overlay rect')).toHaveCount(1)
  expect(io.observers[1]!.commands.every(c => c.type === 'ack')).toBe(true)
})

test('closing preview invalidates unfinished decoding and releases its image URLs', async ({ page }) => {
  await page.addInitScript(() => {
    const urls = { created: [] as string[], revoked: [] as string[] }
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL)
    URL.createObjectURL = blob => { const u = create(blob); urls.created.push(u); return u }
    URL.revokeObjectURL = u => { urls.revoked.push(u); revoke(u) }
    Object.assign(window, { previewUrls: urls })
    const decode = HTMLImageElement.prototype.decode
    HTMLImageElement.prototype.decode = function () {
      return decode.call(this).then(() => new Promise<void>(resolve => Object.assign(window, { finishPreviewDecode: resolve })))
    }
  })
  const io = await mockConsole(page, { cameraActive: true })
  await page.goto('/vision')
  await expect.poll(() => io.observers.length).toBe(1)
  await sendFrame(page, io.observers[0]!)
  await expect.poll(() => page.evaluate(() => typeof (window as unknown as PreviewProbe).finishPreviewDecode)).toBe('function')
  await page.getByRole('button', { name: '关闭网页预览', exact: true }).click()
  await page.evaluate(() => (window as unknown as PreviewProbe).finishPreviewDecode())
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await expect(page.locator('.camera-image')).toHaveCount(0)
  const urls = await page.evaluate(() => (window as unknown as PreviewProbe).previewUrls)
  expect(urls.created.length).toBeGreaterThan(0)
  expect(urls.revoked).toEqual(urls.created)
  expect(io.observers[0]!.commands).toEqual([])
})

test('closing a temporary capture preview and opening it again does not reacquire a lease', async ({ page }) => {
  const io = await mockConsole(page)
  await page.goto('/vision')
  await page.getByRole('button', { name: '临时采集', exact: true }).click()
  await expect(page.getByRole('button', { name: '结束临时采集', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '关闭网页预览', exact: true }).click()
  await expect.poll(() => io.observers[0]!.closed).toBe(true)
  await page.getByRole('button', { name: '开启网页预览', exact: true }).click()
  await expect.poll(() => io.observers.length).toBe(2)
  expect(io.observers[0]!.commands.map(c => c.type)).toEqual(['preview.start'])
  expect(io.observers[1]!.commands).toEqual([])
  await expect(page.getByRole('button', { name: '临时采集', exact: true })).toBeEnabled()
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})

test('network reconnection observes without restarting the previous temporary capture', async ({ page }) => {
  const io = await mockConsole(page)
  await page.goto('/vision')
  await page.getByRole('button', { name: '临时采集', exact: true }).click()
  await expect(page.getByRole('button', { name: '结束临时采集', exact: true })).toBeVisible()
  io.observers[0]!.socket.close()
  await page.getByRole('button', { name: '重新连接', exact: true }).click()
  await expect.poll(() => io.observers.length).toBe(2)
  expect(io.observers[1]!.commands).toEqual([])
  await expect(page.getByRole('button', { name: '关闭网页预览', exact: true })).toHaveAttribute('aria-pressed', 'true')
})
