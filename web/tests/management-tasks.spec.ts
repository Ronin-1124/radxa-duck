import { expect, test, type Page, type Route } from '@playwright/test'
import { mockManagement, networkStatus, powerStatus, task } from './management-fixture'

const allowed = { available: true, reason: null }
const availableReads = {
  '/api/network/status': { ...networkStatus, capabilities: { scan: allowed, connect: allowed } },
  '/api/power/status': { ...powerStatus, capabilities: { reboot: allowed, shutdown: allowed } },
}
const network = (page: Page) => page.locator('.network-card')
const maintenance = (page: Page) => page.locator('.maintenance-card')
const scanButton = (page: Page) => network(page).getByRole('button', { name: '搜索 Wi-Fi', exact: true })
const rebootButton = (page: Page) => maintenance(page).getByRole('button', { name: '重启设备', exact: true })
const queryButton = (element: ReturnType<Page['locator']>) => element.getByRole('button', { name: '查询任务状态', exact: true })

async function freezeClock(page: Page): Promise<void> {
  const time = new Date('2026-10-27T02:00:00Z')
  await page.clock.install({ time })
  await page.clock.pauseAt(new Date(time.getTime() + 1000))
}

test('new network and power actions wait for a successful initial task recovery', async ({ page }) => {
  const held: Route[] = []
  let hold = true
  const mock = await mockManagement(page, { reads: availableReads, handle: async route => {
    if (new URL(route.request().url()).pathname === '/api/tasks' && hold) { held.push(route); return true }
    return false
  } })
  await page.goto('/device')
  await expect(network(page)).toContainText('Duck Lab')
  await expect.poll(() => held.length).toBe(2)
  await expect(scanButton(page)).toBeDisabled()
  await expect(rebootButton(page)).toBeDisabled()
  hold = false
  await Promise.all(held.splice(0).map(route => route.fulfill({ status: 503,
    json: { schema_version: 1, error: { code: 'tasks_unavailable', message: 'Task history unavailable' } } })))
  await expect(queryButton(network(page))).toBeEnabled()
  await expect(queryButton(maintenance(page))).toBeEnabled()
  await expect(scanButton(page)).toBeDisabled()
  await expect(rebootButton(page)).toBeDisabled()
  await queryButton(network(page)).click()
  await expect(scanButton(page)).toBeEnabled()
  await expect(rebootButton(page)).toBeDisabled()
  await queryButton(maintenance(page)).click()
  await expect(rebootButton(page)).toBeEnabled()
  expect(mock.requests.every(request => request.method === 'GET')).toBe(true)
  expect(mock.sockets).toEqual([])
})

test('an authoritative empty history clears recovered active tasks and unlocks controls', async ({ page }) => {
  const connecting = task('network.connect', { state: 'running', phase: 'connecting', progress: 20 })
  const rebooting = task('power.reboot', { task_id: 'b'.repeat(32), request_id: 'recovered-power-0001',
    state: 'running', phase: 'stopping_capture', progress: 20 })
  const mock = await mockManagement(page, { reads: { ...availableReads,
    '/api/tasks': { schema_version: 1, tasks: [connecting, rebooting] } } })
  await page.goto('/device')
  await expect(network(page).locator('.management-task')).toHaveAttribute('data-state', 'running')
  await expect(maintenance(page).locator('.management-task')).toHaveAttribute('data-state', 'running')
  await expect(scanButton(page)).toBeDisabled()
  await expect(rebootButton(page)).toBeDisabled()
  mock.reads['/api/tasks'] = { schema_version: 1, tasks: [] }
  await queryButton(network(page)).click()
  await expect(network(page).locator('.management-task')).toHaveCount(0)
  await expect(scanButton(page)).toBeEnabled()
  await queryButton(maintenance(page)).click()
  await expect(maintenance(page).locator('.management-task')).toHaveCount(0)
  await expect(rebootButton(page)).toBeEnabled()
  expect(mock.requests.every(request => request.method === 'GET')).toBe(true)
})

test('leaving the device view aborts pending task reads and stops their polling', async ({ page }) => {
  await freezeClock(page)
  await page.addInitScript(() => {
    const taskReads: { aborted: boolean }[] = []
    Object.assign(window, { managementTaskReads: taskReads })
    const nativeFetch = window.fetch.bind(window)
    window.fetch = (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href)
      if (url.pathname === '/api/tasks') {
        const record = { aborted: false }
        taskReads.push(record)
        init?.signal?.addEventListener('abort', () => { record.aborted = true }, { once: true })
      }
      return nativeFetch(input, init)
    }
  })
  const held: Route[] = []
  const mock = await mockManagement(page, { reads: availableReads, handle: async route => {
    if (new URL(route.request().url()).pathname === '/api/tasks') { held.push(route); return true }
    return false
  } })
  await page.goto('/device')
  await expect.poll(() => held.length).toBe(2)
  await page.getByRole('link', { name: '总览', exact: true }).click()
  await expect(page).toHaveURL(/\/overview$/)
  const aborted = await page.evaluate(() => (window as unknown as {
    managementTaskReads: { aborted: boolean }[]
  }).managementTaskReads)
  expect(aborted).toEqual([{ aborted: true }, { aborted: true }])
  await Promise.all(held.splice(0).map(route => route.abort().catch(() => {})))
  const count = mock.requests.filter(request => request.path === '/api/tasks').length
  await page.clock.runFor(45_000)
  expect(mock.requests.filter(request => request.path === '/api/tasks')).toHaveLength(count)
  expect(mock.sockets).toEqual([])
})

test('a task tracked after a status read began survives that older response', async ({ page }) => {
  await freezeClock(page)
  const previous = task('network.scan', { state: 'failed', phase: 'scanning',
    error: { code: 'scan_failed', message: 'A previous scan failed' } })
  let holdRead = false
  let heldRead: Route | undefined
  let heldPost: Route | undefined
  const mock = await mockManagement(page, { reads: { ...availableReads,
    '/api/tasks': { schema_version: 1, tasks: [previous] } }, handle: async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (path === '/api/network/scan' && request.method() === 'POST') { heldPost = route; return true }
    if (path === '/api/tasks' && holdRead) { heldRead = route; return true }
    return false
  } })
  await page.goto('/device')
  await expect(scanButton(page)).toBeEnabled()
  await scanButton(page).click()
  await expect.poll(() => Boolean(heldPost)).toBe(true)
  holdRead = true
  await queryButton(network(page)).click()
  await expect.poll(() => Boolean(heldRead)).toBe(true)
  const requestId = heldPost!.request().postDataJSON().request_id as string
  const submitted = task('network.scan', { task_id: 'c'.repeat(32), request_id: requestId,
    created_utc: '2026-10-27T02:01:00+00:00', updated_utc: '2026-10-27T02:01:00+00:00' })
  await heldPost!.fulfill({ status: 202, json: submitted })
  const visibleTask = network(page).locator('.management-task')
  await expect(visibleTask).toHaveAttribute('data-task-id', submitted.task_id)
  await expect(visibleTask).toHaveAttribute('data-state', 'queued')
  holdRead = false
  await heldRead!.fulfill({ json: { schema_version: 1, tasks: [previous] } })
  await expect(queryButton(network(page))).toBeEnabled()
  await expect(visibleTask).toHaveAttribute('data-task-id', submitted.task_id)
  await expect(visibleTask).toHaveAttribute('data-state', 'queued')
  expect(mock.requests.filter(request => request.method === 'POST')).toHaveLength(1)
  expect(mock.sockets).toEqual([])
})

test('an unconfirmed power request can be retried explicitly with the same ID and a second confirmation', async ({ page }) => {
  await freezeClock(page)
  const history = { schema_version: 1, tasks: [] as ReturnType<typeof task>[] }
  let posts = 0
  let failHistory = false
  const mock = await mockManagement(page, { reads: { ...availableReads, '/api/tasks': history }, handle: async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (path === '/api/tasks' && failHistory) {
      await route.fulfill({ status: 503, json: { schema_version: 1, error: { code: 'tasks_unavailable', message: 'Task history unavailable' } } })
      return true
    }
    if (path !== '/api/power' || request.method() !== 'POST') return false
    posts++
    if (posts === 1) { failHistory = true; await route.abort(); return true }
    const body = request.postDataJSON()
    const accepted = task('power.reboot', { task_id: 'd'.repeat(32), request_id: body.request_id })
    history.tasks = [accepted]
    await route.fulfill({ status: 202, json: accepted })
    return true
  } })
  await page.goto('/device')
  await expect(rebootButton(page)).toBeEnabled()
  await rebootButton(page).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认重启设备', exact: true }).click()
  const retry = maintenance(page).getByRole('button', { name: '重试原操作', exact: true })
  await expect(retry).toBeVisible()
  await expect(maintenance(page)).toContainText('任务记录暂时不可用')
  await expect(retry).toBeDisabled()
  await expect(maintenance(page).getByRole('button', { name: '关闭设备', exact: true })).toBeDisabled()
  failHistory = false
  await queryButton(maintenance(page)).click()
  await expect(retry).toBeEnabled()
  await retry.click()
  await expect(page.getByRole('dialog')).toContainText('确认重启')
  expect(posts).toBe(1)
  await page.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).click()
  expect(posts).toBe(1)
  await retry.click()
  await page.getByRole('dialog').getByRole('button', { name: '确认重启设备', exact: true }).click()
  await expect(maintenance(page).locator('.management-task')).toHaveAttribute('data-state', 'queued')
  const writes = mock.requests.filter(request => request.path === '/api/power' && request.method === 'POST')
  expect(writes).toHaveLength(2)
  expect(writes[1]!.body).toEqual(writes[0]!.body)
  expect(writes.every(request => request.headers['x-radxa-duck-request'] === '1')).toBe(true)
  await expect(retry).toHaveCount(0)
  expect(mock.sockets).toEqual([])
})

test('an unconfirmed Wi-Fi scan retries only after an explicit click and reuses its request ID', async ({ page }) => {
  await freezeClock(page)
  const history = { schema_version: 1, tasks: [] as ReturnType<typeof task>[] }
  let posts = 0
  const mock = await mockManagement(page, { reads: { ...availableReads, '/api/tasks': history }, handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/network/scan' || route.request().method() !== 'POST') return false
    posts++
    if (posts === 1) { await route.abort(); return true }
    const accepted = task('network.scan', { task_id: 'e'.repeat(32), request_id: route.request().postDataJSON().request_id })
    history.tasks = [accepted]
    await route.fulfill({ status: 202, json: accepted })
    return true
  } })
  await page.goto('/device')
  await expect(scanButton(page)).toBeEnabled()
  await scanButton(page).click()
  const retry = network(page).getByRole('button', { name: '重试原操作', exact: true })
  await expect(retry).toBeEnabled()
  await queryButton(network(page)).click()
  await expect(retry).toBeEnabled()
  expect(posts).toBe(1)
  await retry.click()
  await expect(network(page).locator('.management-task')).toHaveAttribute('data-state', 'queued')
  const writes = mock.requests.filter(request => request.path === '/api/network/scan' && request.method === 'POST')
  expect(writes).toHaveLength(2)
  expect(writes[1]!.body).toEqual(writes[0]!.body)
  expect(mock.sockets).toEqual([])
})

test('a Wi-Fi connection retry keeps its network and ID, requires a fresh password, and clears it on pagehide', async ({ page }) => {
  await freezeClock(page)
  const scan = task('network.scan', { state: 'succeeded', phase: 'scanning', progress: 100,
    result: { interface: 'wlan0', truncated: false, networks: [
      { ssid: 'Original Wi-Fi', strength: 80, security: 'wpa-psk', connectable: true },
      { ssid: 'Different Wi-Fi', strength: 70, security: 'wpa-psk', connectable: true },
    ] } })
  const history = { schema_version: 1, tasks: [scan] }
  let posts = 0
  const mock = await mockManagement(page, { reads: { ...availableReads, '/api/tasks': history }, handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/network/connect' || route.request().method() !== 'POST') return false
    posts++
    if (posts === 1) { await route.abort(); return true }
    const accepted = task('network.connect', { task_id: 'f'.repeat(32), request_id: route.request().postDataJSON().request_id,
      created_utc: '2026-10-27T02:01:00+00:00', updated_utc: '2026-10-27T02:01:00+00:00' })
    history.tasks = [accepted, scan]
    await route.fulfill({ status: 202, json: accepted })
    return true
  } })
  await page.goto('/device')
  await network(page).getByRole('button', { name: /Original Wi-Fi/ }).click()
  const password = network(page).getByLabel('Wi-Fi 密码', { exact: true })
  await password.fill('first-test-password')
  await network(page).getByRole('button', { name: '连接此网络', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认切换网络', exact: true }).click()
  const retry = network(page).getByRole('button', { name: '重试原操作', exact: true })
  await expect(retry).toBeVisible()
  await expect(password).toHaveValue('')
  await expect(retry).toBeDisabled()
  await expect(network(page).getByRole('button', { name: /Different Wi-Fi/ })).toBeDisabled()
  await password.fill('second-test-password')
  await expect(retry).toBeEnabled()
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })))
  await expect(password).toHaveValue('')
  await expect(retry).toBeDisabled()
  await password.fill('second-test-password')
  await retry.click()
  await expect(page.getByRole('dialog')).toContainText('Original Wi-Fi')
  expect(posts).toBe(1)
  await page.getByRole('dialog').getByRole('button', { name: '确认切换网络', exact: true }).click()
  await expect(network(page).locator('.management-task')).toHaveAttribute('data-task-id', 'f'.repeat(32))
  const writes = mock.requests.filter(request => request.path === '/api/network/connect' && request.method === 'POST')
  expect(writes).toHaveLength(2)
  expect(writes[1]!.body).toEqual({ ...writes[0]!.body, password: 'second-test-password' })
  expect(writes[0]!.body?.ssid).toBe('Original Wi-Fi')
  expect(writes[0]!.body?.security).toBe('wpa-psk')
  await expect(password).toHaveCount(0)
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(storage).not.toContain('test-password')
  expect(mock.sockets).toEqual([])
})
