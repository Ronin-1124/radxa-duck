import { expect, test, type Route } from '@playwright/test'
import { deviceStatus, mockManagement } from './management-fixture'

test('shares the reported identity across routes and updates it from a device refresh', async ({ page }) => {
  const status = { ...structuredClone(deviceStatus), model: 'Radxa test board', hostname: 'duck-lab' }
  const io = await mockManagement(page, { reads: { '/api/device/status': status } })
  await page.goto('/overview')
  await expect(page.locator('.sidebar-device .device-name')).toHaveText('Radxa test board')
  await expect(page.locator('.sidebar-device small')).toHaveText('duck-lab')
  await page.getByRole('link', { name: '设备', exact: true }).click()
  await expect(page.locator('.summary-copy h2')).toHaveText('Radxa test board')
  status.model = 'Radxa updated board'
  status.hostname = 'duck-updated'
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(page.locator('.sidebar-device .device-name')).toHaveText('Radxa updated board')
  await expect(page.locator('.summary-copy h2')).toHaveText('Radxa updated board')
  await page.getByRole('link', { name: '总览', exact: true }).click()
  await expect(page.locator('.sidebar-device small')).toHaveText('duck-updated')
  expect(io.requests.every(request => request.method === 'GET')).toBe(true)
  expect(io.sockets).toEqual([])
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))
  expect(storage).not.toContain('duck-updated')
})

test('does not invent a board model before a successful read and retains a labelled last read on failure', async ({ page }) => {
  let unavailable = true
  const io = await mockManagement(page, { handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/device/status' || !unavailable) return false
    await route.fulfill({ status: 503, json: { schema_version: 1, error: { code: 'unavailable', message: 'Unavailable' } } })
    return true
  } })
  await page.goto('/overview')
  await expect(page.locator('.sidebar-device .device-name')).toHaveText('尚未读取设备')
  await expect(page.locator('.sidebar-device')).not.toContainText('Radxa ZERO 3W')
  unavailable = false
  await page.getByRole('link', { name: '设备', exact: true }).click()
  await expect(page.locator('.sidebar-device .device-name')).toHaveText(deviceStatus.model!)
  unavailable = true
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(page.locator('.sidebar-device .device-name')).toHaveAttribute('title', '上次读取的设备信息')
  await expect(page.locator('.sidebar-device .device-name')).toHaveText(deviceStatus.model!)
  expect(io.requests.every(request => request.method === 'GET')).toBe(true)
})

for (const oldReply of ['success', 'failure'] as const) {
  test(`ignores an older background identity ${oldReply} after a fresh device-page read`, async ({ page }) => {
    let held: Route | undefined
    let reads = 0
    const fresh = { ...structuredClone(deviceStatus), model: 'Fresh device' }
    const io = await mockManagement(page, { reads: { '/api/device/status': fresh }, handle: async route => {
      if (new URL(route.request().url()).pathname !== '/api/device/status' || ++reads !== 1) return false
      held = route
      return true
    } })
    await page.goto('/overview')
    await expect.poll(() => held !== undefined).toBe(true)
    await page.getByRole('link', { name: '设备', exact: true }).click()
    await expect(page.locator('.sidebar-device .device-name')).toHaveText('Fresh device')
    const completed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/device/status')
    if (oldReply === 'success') await held!.fulfill({ json: { ...fresh, model: 'Older device' } })
    else await held!.fulfill({ status: 503, json: { schema_version: 1, error: { code: 'unavailable', message: 'Unavailable' } } })
    await completed
    // A subsequent browser task lets the asynchronous fetch continuation publish, if it is still valid.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    await expect(page.locator('.sidebar-device .device-name')).toHaveText('Fresh device')
    await expect(page.locator('.sidebar-device .device-name')).not.toHaveAttribute('title', '上次读取的设备信息')
    await expect(page.locator('.summary-copy h2')).toHaveText('Fresh device')
    expect(io.requests.every(request => request.method === 'GET')).toBe(true)
  })
}
