import { expect, test } from '@playwright/test'
import { mockConsole } from './console-fixture'
import { task } from './management-fixture'

test('sensor feedback distinguishes availability, policy and actual capture', async ({ page }) => {
  const io = await mockConsole(page)
  await page.goto('/overview')
  const items = page.locator('.capability-item')
  for (const state of [
    { sensor: { available: false, enabled: false, active: false }, mic: '能力不可用', camera: '能力不可用' },
    { sensor: { available: true, enabled: false, active: false }, mic: '未启用', camera: '未启用' },
    { sensor: { available: true, enabled: true, active: false }, mic: '已启用 · 空闲', camera: '已启用 · 空闲' },
    { sensor: { available: true, enabled: true, active: true }, mic: '正在收音', camera: '正在采集' },
    { sensor: { available: false, enabled: false, active: true }, mic: '正在收音', camera: '正在采集' },
    // Another consumer may capture while the brain's own camera policy is disabled.
    { sensor: { available: true, enabled: false, active: true }, mic: '正在收音', camera: '正在采集' },
  ]) {
    io.publish({ microphone: state.sensor, camera: state.sensor })
    await expect(items.filter({ hasText: '麦克风' }).locator('strong')).toHaveText(state.mic)
    await expect(items.filter({ hasText: '摄像头' }).locator('strong')).toHaveText(state.camera)
  }
  const cloud = items.filter({ hasText: '云端模型' })
  io.publish({ cloud: { configured: true, model: 'cloud-model', health: 'unknown' } })
  await expect(cloud.locator('strong')).toHaveText('已配置 · 待验证')
  await expect(cloud.locator('.capability-detail')).toHaveText('cloud-model')
  io.publish({ cloud: { configured: true, model: 'cloud-model', health: 'ready' } })
  await expect(cloud.locator('strong')).toHaveText('回答已验证')
  io.publish({ cloud: { configured: true, model: 'cloud-model', health: 'error' } })
  await expect(cloud.locator('strong')).toHaveText('模型调用失败')
  io.publish({ cloud: { configured: false, model: null, health: 'unknown' } })
  await expect(cloud.locator('strong')).toHaveText('未配置')
  expect([...io.requests, ...io.management.requests].every(r => r.method === 'GET')).toBe(true)
})

test('the view distinguishes a disabled policy from idle capture and observes other consumers', async ({ page }) => {
  const io = await mockConsole(page)
  io.brain.camera = { available: true, enabled: false, active: false }
  await page.goto('/vision')
  const footer = page.locator('.view-footer')
  await expect(footer).toContainText('未启用')
  await expect(page.locator('.camera-placeholder')).toContainText('摄像头未启用')
  io.publish({ camera: { available: true, enabled: true, active: false } })
  await expect(footer).toContainText('已启用 · 空闲')
  io.publish({ camera: { available: true, enabled: false, active: false } })
  io.observers[0]!.publish({ camera_active: true })
  await expect(footer).toContainText('正在采集')
  io.observers[0]!.publish({ camera_active: false })
  await expect(footer).toContainText('未启用')
  io.publish({ camera: { available: false, enabled: false, active: false } })
  await expect(footer).toContainText('能力不可用')
  expect(io.observers[0]!.commands).toEqual([])
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})

test('a connection check shows its time and outcome without verifying actual answers', async ({ page }) => {
  const io = await mockConsole(page)
  io.brain.cloud.health = 'unknown'
  const checked = task('cloud.test', { state: 'succeeded', updated_utc: '2026-10-29T02:05:00Z',
    result: { schema_version: 1, revision: 2, state: 'succeeded', elapsed_ms: 25, model_available: true, error: null } })
  io.management.reads['/api/tasks'] = { schema_version: 1, tasks: [checked] }
  await page.goto('/overview?settings=1&tab=brain')
  await expect(page.locator('.test-state')).toHaveText('服务可达 · 模型已列出')
  await expect(page.locator('.test-time time')).toHaveAttribute('datetime', checked.updated_utc)
  await expect(page.locator('.test-time')).toContainText('检查时间')
  const answer = page.locator('.answer-verification')
  await expect(answer).toHaveAttribute('data-state', 'verificationPending')
  await expect(answer).toContainText('已配置 · 待验证')
  io.publish({ cloud: { ...io.brain.cloud, health: 'ready' } })
  await expect(answer).toContainText('回答已验证')
  await expect(answer).toContainText('cloud-model')
  Object.assign(checked, { state: 'failed', error: { code: 'authentication_failed', message: 'private provider response' },
    updated_utc: '2026-10-29T02:06:00Z', result: { ...checked.result, state: 'failed', model_available: false,
      error: { code: 'authentication_failed', message: 'private provider response' } } })
  await page.getByRole('button', { name: '查询任务', exact: true }).click()
  await expect(page.locator('.test-result')).toContainText('云端服务未接受当前密钥')
  await expect(page.locator('.test-time time')).toHaveAttribute('datetime', checked.updated_utc)
  await expect(page.getByText('private provider response')).toHaveCount(0)
  // Failed connection checks do not fabricate a failed answer event, either.
  await expect(answer).toHaveAttribute('data-state', 'verified')
  io.setOffline(true)
  io.events[0]!.socket.close()
  await expect(answer).toHaveAttribute('data-state', 'unknown')
  await expect(answer).toContainText('状态未知')
  expect([...io.requests, ...io.management.requests].every(r => r.method === 'GET')).toBe(true)
})

test('an error state offers recovery when the backend permits it without resuming automatically', async ({ page }) => {
  const io = await mockConsole(page, { handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/brain/control') return false
    const body = route.request().postDataJSON()
    io.publish({ state: 'running', controls: { ...io.brain.controls, pause: true, resume: false } })
    await route.fulfill({ json: { schema_version: 1, request_id: body.request_id, status: io.brain } })
    return true
  } })
  io.brain.state = 'error'
  io.brain.controls = { ...io.brain.controls, pause: false, resume: true }
  await page.goto('/overview')
  await expect(page.locator('.robot-phase')).toHaveText('需要留意')
  const resume = page.getByRole('button', { name: '恢复交互', exact: true })
  await expect(resume).toBeEnabled()
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
  await resume.click()
  await expect(page.locator('.robot-phase')).toHaveText('自在待命')
  const writes = io.requests.filter(r => r.method === 'POST')
  expect(writes).toHaveLength(1)
  expect(writes[0]!.body!.action).toBe('resume')
  expect(writes[0]!.headers['x-radxa-duck-request']).toBe('1')
})

test('long capability feedback remains readable on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await page.addInitScript(() => localStorage.setItem('radxa-duck.ui.locale', 'en'))
  const io = await mockConsole(page)
  io.brain.cloud.health = 'unknown'
  await page.goto('/overview')
  const values = page.locator('.capability-item strong')
  await expect(values).toContainText(['Capturing audio', 'Enabled · idle', 'Configured · unverified', 'Capability unavailable'])
  const fits = await values.evaluateAll(items => items.every(item =>
    item.scrollWidth <= item.clientWidth + 1 && item.scrollHeight <= item.clientHeight + 1))
  expect(fits).toBe(true)
})
