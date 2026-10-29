import { expect, test } from '@playwright/test'
import { mockManagement, openDeviceSection, networkStatus, powerStatus, task } from './management-fixture'
import type { ManagementTask } from '../src/management-types'

const allowed = { available: true, reason: null }
const network = { ...networkStatus, capabilities: { scan: allowed, connect: allowed } }
const power = { ...powerStatus, capabilities: { reboot: allowed, shutdown: allowed } }
const at = (second: number) => `2026-10-27T02:00:0${second}+00:00`

test('live capability reasons disable unavailable network, power and update operations', async ({ page }) => {
  const mock = await mockManagement(page)
  await page.goto('/overview?settings=1&tab=device')
  await openDeviceSection(page, 'network')
  await expect(page.locator('.network-card')).toContainText('Duck Lab')
  await expect(page.locator('.network-card')).toContainText('192.168.2.151')
  await expect(page.locator('.network-card')).toContainText('无交互配网权限')
  await expect(page.getByRole('button', { name: '搜索 Wi-Fi', exact: true })).toBeDisabled()
  await openDeviceSection(page, 'maintenance')
  await expect(page.locator('.maintenance-card')).toContainText('无交互电源权限')
  for (const name of ['重启设备', '关闭设备', '检查更新']) await expect(page.getByRole('button', { name, exact: true })).toBeDisabled()
  expect(mock.requests.filter(r => r.method !== 'GET')).toEqual([])
  expect(mock.sockets).toEqual([])
})

test('Wi-Fi scan, connection confirmation and task completion remain separate steps', async ({ page }) => {
  let scan: ManagementTask | null = null, connection: ManagementTask | null = null
  const mock = await mockManagement(page, { reads: { '/api/network/status': structuredClone(network) }, handle: async route => {
    const path = new URL(route.request().url()).pathname, body = route.request().postDataJSON()
    if (route.request().method() !== 'POST') return false
    if (path === '/api/network/scan') {
      scan = task('network.scan', { request_id: body.request_id })
      mock.reads['/api/tasks'] = { schema_version: 1, tasks: [scan] }
      await route.fulfill({ status: 202, json: scan }); return true
    }
    if (path === '/api/network/connect') {
      connection = task('network.connect', { task_id: 'b'.repeat(32), request_id: body.request_id, created_utc: at(2), updated_utc: at(2), state: 'running', phase: 'awaiting_confirmation', progress: 70 })
      mock.reads['/api/tasks'] = { schema_version: 1, tasks: [connection, scan] }
      mock.reads['/api/network/status'] = { ...network, ssid: 'New Duck', pending_task: { task_id: connection.task_id, awaiting_confirmation: true, confirmation_remaining_seconds: 40 } }
      await route.fulfill({ status: 202, json: connection }); return true
    }
    if (path === '/api/network/confirm') { await route.fulfill({ json: { task_id: body.task_id, confirmed: true } }); return true }
    return false
  } })
  await page.goto('/overview?settings=1&tab=device')
  await openDeviceSection(page, 'network')
  const card = page.locator('.network-card')
  await expect(card.getByRole('button', { name: '搜索 Wi-Fi', exact: true })).toBeEnabled()
  await card.getByRole('button', { name: '搜索 Wi-Fi', exact: true }).click()
  await expect(card.locator('.management-task')).toHaveAttribute('data-state', 'queued')
  scan = { ...scan!, state: 'succeeded', phase: 'succeeded', updated_utc: at(1), result: { interface: 'wlan0', truncated: false,
    networks: [{ ssid: 'New Duck', strength: 90, security: 'wpa-psk', connectable: true }, { ssid: 'Enterprise', strength: 70, security: 'unsupported', connectable: false }] } }
  mock.reads['/api/tasks'] = { schema_version: 1, tasks: [scan] }
  await card.getByRole('button', { name: '查询任务状态', exact: true }).click()
  await expect(card.getByRole('button', { name: /Enterprise/ })).toBeDisabled()
  await card.getByRole('button', { name: /New Duck/ }).click()
  const password = card.getByLabel('Wi-Fi 密码', { exact: true })
  await password.fill('short')
  await expect(card.getByRole('button', { name: '连接此网络', exact: true })).toBeDisabled()
  await password.fill('mock-password-123')
  await card.getByRole('button', { name: '连接此网络', exact: true }).click()
  const dialog = card.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '取消', exact: true }).press('Shift+Tab')
  await expect(dialog.getByRole('button', { name: '确认切换网络', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  expect(mock.requests.filter(r => r.path === '/api/network/connect')).toHaveLength(0)
  await expect(card.getByRole('button', { name: '连接此网络', exact: true })).toBeFocused()
  await card.getByRole('button', { name: '连接此网络', exact: true }).click()
  await dialog.getByRole('button', { name: '确认切换网络', exact: true }).click()
  await expect(card.getByRole('button', { name: '确认当前连接', exact: true })).toBeEnabled()
  const connectRequests = mock.requests.filter(r => r.path === '/api/network/connect')
  expect(connectRequests).toHaveLength(1)
  expect(connectRequests[0]!.headers['x-radxa-duck-request']).toBe('1')
  expect(connectRequests[0]!.body).toMatchObject({ ssid: 'New Duck', security: 'wpa-psk', password: 'mock-password-123' })
  await card.getByRole('button', { name: '确认当前连接', exact: true }).click()
  await expect(card).toContainText('确认已提交，正在等待设备保存网络')
  await expect(card.locator('.management-task')).toHaveAttribute('data-state', 'running')
  connection = { ...connection!, state: 'succeeded', phase: 'succeeded', updated_utc: at(4), progress: 100,
    result: { interface: 'wlan0', ssid: 'New Duck', ipv4: ['192.168.2.152'], confirmed: true, credentials_saved: true } }
  mock.reads['/api/tasks'] = { schema_version: 1, tasks: [connection, scan] }
  mock.reads['/api/network/status'] = { ...network, ssid: 'New Duck', ipv4: ['192.168.2.152'], pending_task: null }
  await card.getByRole('button', { name: '查询任务状态', exact: true }).click()
  await expect(card.locator('.management-task')).toHaveAttribute('data-state', 'succeeded')
  await expect(card).toContainText('192.168.2.152')
  await expect(card.getByRole('button', { name: '确认当前连接', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('mock-password')
  expect(mock.requests.filter(r => r.path === '/api/network/confirm')).toHaveLength(1)
})

test('power requires confirmation and recovers an accepted schedule without replaying it', async ({ page }) => {
  let accepted: ManagementTask | null = null
  const mock = await mockManagement(page, { reads: { '/api/power/status': power }, handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/power' || route.request().method() !== 'POST') return false
    const body = route.request().postDataJSON()
    accepted = task('power.reboot', { request_id: body.request_id, state: 'running', phase: 'stopping_capture', progress: 20 })
    await route.fulfill({ status: 202, json: accepted })
    mock.reads['/api/tasks'] = { schema_version: 1, tasks: [accepted] }
    return true
  } })
  await page.goto('/overview?settings=1&tab=device')
  await openDeviceSection(page, 'maintenance')
  const card = page.locator('.maintenance-card')
  const restart = card.getByRole('button', { name: '重启设备', exact: true })
  await expect(restart).toBeEnabled()
  await restart.click()
  const dialog = card.getByRole('dialog')
  await expect(dialog).toHaveAccessibleName('确认重启？')
  await page.keyboard.press('Escape')
  await expect(restart).toBeFocused()
  expect(mock.requests.filter(r => r.method === 'POST')).toEqual([])
  await restart.click()
  await dialog.getByRole('button', { name: '确认重启设备', exact: true }).click()
  await expect(card.locator('.management-task')).toHaveAttribute('data-state', 'running')
  await expect(restart).toBeDisabled()
  accepted = { ...accepted!, state: 'succeeded', phase: 'succeeded', updated_utc: at(2), progress: 100,
    result: { schema_version: 1, type: 'power_result', action: 'reboot', state: 'scheduled', scheduled_usec: 1793066405000000, scheduled_utc: at(5), grace_seconds: 5 } }
  mock.reads['/api/tasks'] = { schema_version: 1, tasks: [accepted] }
  mock.reads['/api/power/status'] = { ...power, scheduled: { action: 'reboot', type: 'reboot', scheduled_usec: 1793066405000000, scheduled_utc: at(5) } }
  await page.reload()
  await openDeviceSection(page, 'maintenance')
  await expect(card.locator('.management-task')).toHaveAttribute('data-state', 'succeeded')
  await expect(card.locator('.management-task')).toContainText('已安排重启')
  await expect(card).toContainText('网页断开不代表操作已完成')
  await expect(card).not.toContainText('已重启')
  await expect(restart).toBeDisabled()
  expect(mock.requests.filter(r => r.path === '/api/power')).toHaveLength(1)
})

test('an uncertain power response stays unconfirmed until the request ID is found', async ({ page }) => {
  let requestId = ''
  const mock = await mockManagement(page, { reads: { '/api/power/status': power }, handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/power') return false
    requestId = route.request().postDataJSON().request_id
    await route.abort(); return true
  } })
  await page.goto('/overview?settings=1&tab=device')
  await openDeviceSection(page, 'maintenance')
  const card = page.locator('.maintenance-card')
  await expect(card.getByRole('button', { name: '关闭设备', exact: true })).toBeEnabled()
  await card.getByRole('button', { name: '关闭设备', exact: true }).click()
  await card.getByRole('dialog').getByRole('button', { name: '确认关闭设备', exact: true }).click()
  await expect(card).toContainText('请求结果尚未确认')
  await expect(card.getByRole('button', { name: '关闭设备', exact: true })).toBeDisabled()
  mock.reads['/api/tasks'] = { schema_version: 1, tasks: [task('power.shutdown', { request_id: requestId, state: 'failed', phase: 'failed',
    error: { code: 'permission_denied', message: 'System permission required' } })] }
  await card.getByRole('button', { name: '查询任务状态', exact: true }).click()
  await expect(card).not.toContainText('请求结果尚未确认')
  await expect(card.locator('.management-task')).toHaveAttribute('data-state', 'failed')
  await expect(card.locator('.management-task')).toContainText('系统权限')
  expect(mock.requests.filter(r => r.path === '/api/power')).toHaveLength(1)
})
