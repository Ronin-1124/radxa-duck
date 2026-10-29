import { expect, test } from '@playwright/test'
import type { VoiceFeedback } from '../src/console-protocol'
import { mockConsole } from './console-fixture'

const at = '2026-10-29T08:30:00Z'
function voice(): VoiceFeedback {
  return { reply_available: true,
    input: { state: 'listening', pause_reason: null, error_code: null },
    synthesis: { state: 'idle', task_id: null, updated_at: null, error_code: null },
    playback: { state: 'idle', task_id: null, updated_at: null, error_code: null } }
}

test('resident camera control waits for authoritative confirmation and preserves other capture', async ({ page }) => {
  let release!: () => void
  const accepted = new Promise<void>(resolve => { release = resolve })
  const io = await mockConsole(page, { cameraActive: true, handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/brain/control') return false
    const body = route.request().postDataJSON()
    expect(body).toMatchObject({ action: 'disable_camera', expected_instance_id: 'brain-1', expected_revision: 1 })
    expect(route.request().headers()['x-radxa-duck-request']).toBe('1')
    await accepted
    io.publish({ camera: { available: true, enabled: false, active: true }, controls: { ...io.brain.controls, enable_camera: true, disable_camera: false } })
    await route.fulfill({ json: { schema_version: 1, request_id: body.request_id, status: io.brain } })
    return true
  } })
  io.brain.controls = { ...io.brain.controls, enable_camera: false, disable_camera: true }
  io.brain.camera.active = true
  await page.goto('/vision?settings=1&tab=brain')
  const camera = page.getByRole('switch', { name: '常驻视觉', exact: true })
  await expect(camera).toHaveAttribute('aria-checked', 'true')
  await camera.click()
  await expect(camera).toHaveAttribute('aria-busy', 'true')
  await expect(camera).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('switch', { name: '语音交互', exact: true })).toBeDisabled()
  release()
  await expect(camera).toHaveAttribute('aria-checked', 'false')
  await expect(camera).toHaveAttribute('aria-busy', 'false')
  await expect(page.locator('.runtime-row').filter({ hasText: '常驻视觉' })).toContainText('正在采集')
  expect(io.observers[0]!.commands).toEqual([])
  expect(io.requests.filter(r => r.method === 'POST')).toHaveLength(1)
})

test('uncertain camera control is not repeated and requires a state read before another change', async ({ page }) => {
  const io = await mockConsole(page, { handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/brain/control') return false
    await route.abort(); return true
  } })
  io.brain.controls = { ...io.brain.controls, enable_camera: false, disable_camera: true }
  await page.goto('/overview?settings=1&tab=brain')
  const camera = page.getByRole('switch', { name: '常驻视觉', exact: true })
  await camera.click()
  await expect(page.locator('.runtime-settings')).toContainText('操作结果未确认')
  await expect(camera).toBeDisabled()
  await expect(camera).toHaveAttribute('aria-checked', 'true')
  await page.locator('.runtime-settings').getByRole('button', { name: '重新读取', exact: true }).click()
  await expect(camera).toBeEnabled()
  expect(io.requests.filter(r => r.method === 'POST')).toHaveLength(1)
})

test('resident vision is enabled only by its own explicit control', async ({ page }) => {
  const io = await mockConsole(page, { handle: async route => {
    if (new URL(route.request().url()).pathname !== '/api/brain/control') return false
    const body = route.request().postDataJSON()
    expect(body.action).toBe('enable_camera')
    io.publish({ camera: { available: true, enabled: true, active: false }, controls: { ...io.brain.controls, enable_camera: false, disable_camera: true } })
    await route.fulfill({ json: { schema_version: 1, request_id: body.request_id, status: io.brain } })
    return true
  } })
  io.brain.camera.enabled = false
  io.brain.controls = { ...io.brain.controls, enable_camera: true, disable_camera: false }
  await page.goto('/overview?settings=1&tab=brain')
  const camera = page.getByRole('switch', { name: '常驻视觉', exact: true })
  await expect(camera).toHaveAttribute('aria-checked', 'false')
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
  await camera.click()
  await expect(camera).toHaveAttribute('aria-checked', 'true')
  expect(io.requests.filter(r => r.method === 'POST')).toHaveLength(1)
  expect(io.observers).toHaveLength(0)
})

test('older services remain observable and do not advertise an unsupported camera control', async ({ page }) => {
  const io = await mockConsole(page)
  await page.goto('/overview?settings=1&tab=brain')
  await expect(page.getByRole('switch', { name: '常驻视觉', exact: true })).toBeDisabled()
  await expect(page.locator('.runtime-row').filter({ hasText: '常驻视觉' })).toContainText('当前服务不支持切换')
  await expect(page.getByRole('switch', { name: '语音交互', exact: true })).toBeEnabled()
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})

test('voice stages distinguish synthesis, playback, microphone coordination and a failed output', async ({ page }) => {
  const io = await mockConsole(page)
  const feedback = voice()
  feedback.input = { state: 'idle', pause_reason: 'synthesis', error_code: null }
  feedback.synthesis = { state: 'running', task_id: 'turn-1', updated_at: at, error_code: null }
  io.brain.voice = feedback
  io.brain.microphone.active = false
  io.brain.task = { id: 'turn-1', kind: 'voice', state: 'running', summary: '语音交互' }
  io.brain.state = 'paused'
  await page.goto('/overview')
  await expect(page.locator('.brain-badge')).toContainText('交互已暂停')
  await expect(page.locator('.robot-phase')).toHaveText('正在准备声音')
  await expect(page.locator('[data-stage="input"]')).toContainText('合成期间暂停')
  await expect(page.locator('[data-stage="synthesis"]')).toContainText('正在合成')
  feedback.input.pause_reason = 'playback'
  feedback.synthesis.state = 'succeeded'
  feedback.playback = { state: 'running', task_id: 'turn-1', updated_at: at, error_code: null }
  io.publish({ voice: feedback })
  await expect(page.locator('.robot-phase')).toHaveText('正在回答')
  await expect(page.locator('[data-stage="input"]')).toContainText('播放期间暂停')
  await expect(page.locator('[data-stage="playback"]')).toContainText('正在播放')
  feedback.playback = { state: 'failed', task_id: 'turn-1', updated_at: at, error_code: 'device_unavailable' }
  feedback.input.pause_reason = 'error'
  io.publish({ voice: feedback, state: 'error', task: { ...io.brain.task!, state: 'failed', summary: '语音交互失败' } })
  await expect(page.locator('.robot-phase')).toHaveText('需要留意')
  await expect(page.locator('[data-stage="playback"]')).toContainText('音频设备不可用')
  await expect(page.locator('[data-stage="synthesis"]')).toContainText('已完成')
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})

test('disconnected voice feedback becomes unknown without assuming that the robot stopped', async ({ page }) => {
  const io = await mockConsole(page)
  io.brain.voice = voice()
  await page.goto('/overview')
  await expect(page.locator('[data-stage="input"]')).toContainText('正在收音')
  io.setOffline(true)
  io.events[0]!.socket.close()
  await expect(page.locator('.voice-stage strong')).toHaveText(['状态未知', '状态未知', '状态未知'])
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})

test('malformed extended status cannot enable controls or appear as verified output', async ({ page }) => {
  const io = await mockConsole(page)
  io.brain.voice = voice()
  io.brain.voice.playback.updated_at = 'not-a-timestamp'
  await page.goto('/overview?settings=1&tab=brain')
  await expect(page.getByRole('switch', { name: '常驻视觉', exact: true })).toBeDisabled()
  await expect(page.getByRole('switch', { name: '语音交互', exact: true })).toBeDisabled()
  await expect(page.locator('.answer-verification')).toContainText('状态未知')
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})
