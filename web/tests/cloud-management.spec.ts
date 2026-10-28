import { expect, test, type Page } from '@playwright/test'
import type { CloudConfig, ManagementTask } from '../src/management-types'
import { cloudConfig, mockManagement, task } from './management-fixture'

async function prepared(page: Page) {
  const config = structuredClone(cloudConfig)
  const tasks: ManagementTask[] = []
  const replies = { put: 'ok', post: 'ok' }
  const io = await mockManagement(page, { reads: { '/api/cloud/config': config, '/api/tasks': { schema_version: 1, tasks } },
    handle: async route => {
      const request = route.request(), path = new URL(request.url()).pathname
      if (request.method() === 'PUT' && path === '/api/cloud/config') {
        const body = request.postDataJSON() as Record<string, unknown>
        if (replies.put === 'conflict') {
          await route.fulfill({ status: 409, json: { schema_version: 1, error: { code: 'conflict', message: 'hidden provider details' } } }); return true
        }
        Object.assign(config, { revision: config.revision + 1, base_url: body.base_url, model: body.model,
          timeout_seconds: body.timeout_seconds, has_api_key: Object.hasOwn(body, 'api_key') ? body.api_key !== null : config.has_api_key,
          updated_utc: '2026-10-27T02:01:00+00:00', configured: true })
        if (replies.put === 'abort') await route.abort()
        else await route.fulfill({ json: config })
        return true
      }
      if (request.method() === 'POST' && path === '/api/cloud/test') {
        if (replies.post === 'abort') { await route.abort(); return true }
        const body = request.postDataJSON() as { request_id: string }
        const value = task('cloud.test', { request_id: body.request_id, state: 'running', phase: 'testing', progress: null })
        tasks.push(value)
        await route.fulfill({ status: 202, json: value })
        return true
      }
      return false
    } })
  await page.goto('/settings')
  await expect(page.getByLabel('模型名称', { exact: true })).toHaveValue(config.model!)
  await expect(page.getByRole('button', { name: '测试连接', exact: true })).toBeEnabled()
  return { ...io, config, tasks, replies, puts: () => io.requests.filter(r => r.method === 'PUT'), posts: () => io.requests.filter(r => r.method === 'POST') }
}
function loadedTaskResult(revision: number, success = true) {
  return { schema_version: 1, revision, state: success ? 'succeeded' : 'failed', elapsed_ms: 24.8,
    model_available: success, error: success ? null : { code: 'authentication_failed', message: 'hidden response' } }
}

test('saves the read revision with the write header and omits a key when keeping it', async ({ page }) => {
  const io = await prepared(page)
  await page.getByLabel('模型名称', { exact: true }).fill('new-model')
  await expect(page.getByRole('button', { name: '测试连接', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '保存到设备', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '配置已保存到设备' })).toBeVisible()
  expect(io.puts()).toHaveLength(1)
  expect(io.puts()[0]!.headers['x-radxa-duck-request']).toBe('1')
  expect(io.puts()[0]!.body).toEqual({ expected_revision: 2, provider: 'openai-compatible', base_url: cloudConfig.base_url, model: 'new-model', timeout_seconds: 8 })
  await expect(page.getByText('已读取设备配置', { exact: true })).toBeVisible()
  await expect(page.getByText('设备已配置密钥', { exact: true })).toBeVisible()
  expect(io.sockets).toEqual([])
})

test('replaces a key, clears it from page memory, and requires confirmation for an explicit null removal', async ({ page }) => {
  const io = await prepared(page)
  await page.getByRole('radio', { name: '替换密钥', exact: true }).check()
  await page.getByLabel('API 密钥', { exact: true }).fill('test-replacement-key')
  await page.getByRole('button', { name: '显示 API 密钥', exact: true }).click()
  await page.getByRole('button', { name: '保存到设备', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '配置已保存到设备' })).toBeVisible()
  expect(io.puts()[0]!.body!.api_key).toBe('test-replacement-key')
  await expect(page.getByLabel('API 密钥', { exact: true })).toHaveCount(0)
  await page.getByRole('radio', { name: '替换密钥', exact: true }).check()
  await expect(page.getByLabel('API 密钥', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('API 密钥', { exact: true })).toHaveAttribute('type', 'password')
  await page.getByRole('radio', { name: '清除密钥', exact: true }).check()
  await page.getByRole('button', { name: '保存到设备', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '确认从设备清除 API 密钥？', exact: true })).toBeVisible()
  expect(io.puts()).toHaveLength(1)
  const dialog = page.getByRole('dialog', { name: '确认从设备清除 API 密钥？', exact: true })
  await expect(dialog.getByRole('button', { name: '取消', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(page.getByRole('button', { name: '保存到设备', exact: true })).toBeFocused()
  expect(io.puts()).toHaveLength(1)
  await page.getByRole('button', { name: '保存到设备', exact: true }).click()
  await dialog.getByRole('button', { name: '确认清除并保存', exact: true }).click()
  await expect(page.getByText('设备未配置密钥', { exact: true })).toBeVisible()
  expect(io.puts()[1]!.body!.api_key).toBeNull()
  expect(io.puts()[1]!.body!.expected_revision).toBe(3)
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(storage).not.toContain('test-replacement-key')
})

test('preserves a conflicting draft and replaces it only after an explicit read confirmation', async ({ page }) => {
  const io = await prepared(page)
  io.replies.put = 'conflict'
  await page.getByLabel('模型名称', { exact: true }).fill('my-unsaved-model')
  await page.getByRole('radio', { name: '替换密钥', exact: true }).check()
  await page.getByLabel('API 密钥', { exact: true }).fill('test-unsaved-key')
  await page.getByRole('button', { name: '保存到设备', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('当前草稿已保留')
  await expect(page.getByLabel('模型名称', { exact: true })).toHaveValue('my-unsaved-model')
  await expect(page.getByLabel('API 密钥', { exact: true })).toHaveValue('test-unsaved-key')
  await expect(page.getByRole('button', { name: '保存到设备', exact: true })).toBeDisabled()
  expect(io.puts()).toHaveLength(1)
  Object.assign(io.config, { revision: 3, model: 'another-page-model' })
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await page.getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(page.getByLabel('模型名称', { exact: true })).toHaveValue('my-unsaved-model')
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await page.getByRole('button', { name: '读取并替换草稿', exact: true }).click()
  await expect(page.getByLabel('模型名称', { exact: true })).toHaveValue('another-page-model')
  await expect(page.getByLabel('API 密钥', { exact: true })).toHaveCount(0)
  expect(io.puts()).toHaveLength(1)
  await expect(page.getByText('hidden provider details')).toHaveCount(0)
})

test('does not repeat an unconfirmed save and reads back its eventual device configuration', async ({ page }) => {
  const io = await prepared(page)
  io.replies.put = 'abort'
  await page.getByLabel('模型名称', { exact: true }).fill('possibly-saved-model')
  await page.getByRole('button', { name: '保存到设备', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('保存结果尚未确认')
  await expect(page.getByRole('button', { name: '保存到设备', exact: true })).toBeDisabled()
  expect(io.puts()).toHaveLength(1)
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await page.getByRole('button', { name: '读取并替换草稿', exact: true }).click()
  await expect(page.getByText('已读取设备配置', { exact: true })).toBeVisible()
  await expect(page.getByLabel('模型名称', { exact: true })).toHaveValue('possibly-saved-model')
  expect(io.puts()).toHaveLength(1)
})

test('tests only saved configuration and displays the matching completed result', async ({ page }) => {
  const io = await prepared(page)
  await page.getByLabel('模型名称', { exact: true }).fill('changed-model')
  await expect(page.getByRole('button', { name: '测试连接', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '重置草稿', exact: true }).click()
  await page.getByRole('button', { name: '测试连接', exact: true }).click()
  await expect(page.locator('.test-state')).toHaveText('正在测试已保存配置')
  await expect(page.getByRole('button', { name: '测试连接', exact: true })).toBeDisabled()
  expect(io.posts()).toHaveLength(1)
  expect(io.posts()[0]!.headers['x-radxa-duck-request']).toBe('1')
  expect(io.posts()[0]!.body!.expected_revision).toBe(2)
  expect(io.posts()[0]!.body!.request_id).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
  Object.assign(io.tasks[0]!, { state: 'succeeded', phase: 'testing', progress: 100,
    updated_utc: '2026-10-27T02:01:00+00:00', result: loadedTaskResult(2) })
  await page.getByRole('button', { name: '查询任务', exact: true }).click()
  await expect(page.locator('.test-state')).toHaveText('连接成功，模型可用')
  await expect(page.locator('.test-result')).toHaveAttribute('data-tone', 'success')
  await expect(page.locator('.test-meta')).toContainText('当前保存的配置 · 用时 25 ms')
  expect(io.posts()).toHaveLength(1)
})

test('recovers a failed test from task history and does not expose provider response text', async ({ page }) => {
  const value = task('cloud.test', { state: 'failed', phase: 'testing', result: loadedTaskResult(2, false),
    error: { code: 'authentication_failed', message: 'private provider response' } })
  const io = await mockManagement(page, { reads: { '/api/tasks': { schema_version: 1, tasks: [value] } } })
  await page.goto('/settings')
  await expect(page.locator('.test-state')).toHaveText('连接测试失败')
  await expect(page.locator('.test-result')).toContainText('云端服务未接受当前密钥')
  await expect(page.getByText('private provider response')).toHaveCount(0)
  await page.reload()
  await expect(page.locator('.test-state')).toHaveText('连接测试失败')
  expect(io.requests.every(request => request.method === 'GET')).toBe(true)
})

test('labels an older successful result without validating the newly saved revision', async ({ page }) => {
  const value = task('cloud.test', { state: 'succeeded', phase: 'testing', result: loadedTaskResult(1) })
  await mockManagement(page, { reads: { '/api/tasks': { schema_version: 1, tasks: [value] } } })
  await page.goto('/settings')
  await expect(page.getByLabel('模型名称', { exact: true })).toHaveValue(cloudConfig.model!)
  await expect(page.locator('.test-result')).toHaveAttribute('data-tone', 'neutral')
  await expect(page.locator('.test-result')).toContainText('当前配置尚未通过本次测试验证')
})

test('retries an unconfirmed test with the same request ID instead of creating a new operation', async ({ page }) => {
  const io = await prepared(page)
  io.replies.post = 'abort'
  await page.getByRole('button', { name: '测试连接', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '设备是否已接收测试请求尚未确认' })).toBeVisible()
  expect(io.posts()).toHaveLength(1)
  const firstId = io.posts()[0]!.body!.request_id
  io.replies.post = 'ok'
  await page.getByRole('button', { name: '重试同一请求', exact: true }).click()
  await expect(page.locator('.test-state')).toHaveText('正在测试已保存配置')
  expect(io.posts()).toHaveLength(2)
  expect(io.posts()[1]!.body!.request_id).toBe(firstId)
  expect(io.tasks).toHaveLength(1)
})

test('rejects an unexpected secret in a config response and leaves the form unavailable', async ({ page }) => {
  const response: CloudConfig & { api_key: string } = { ...cloudConfig, api_key: 'unexpected-test-key' }
  await mockManagement(page, { reads: { '/api/cloud/config': response } })
  await page.goto('/settings')
  await expect(page.getByRole('alert')).toContainText('设备返回的数据暂时无法识别')
  await expect(page.getByLabel('模型名称', { exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '保存到设备', exact: true })).toBeDisabled()
  await expect(page.getByText('unexpected-test-key')).toHaveCount(0)
})

test('clears a revealed replacement key on pagehide while preserving other input', async ({ page }) => {
  const io = await prepared(page)
  await page.getByLabel('模型名称', { exact: true }).fill('unsaved-model')
  await page.getByRole('radio', { name: '替换密钥', exact: true }).check()
  const key = page.getByLabel('API 密钥', { exact: true })
  await key.fill('pagehide-test-key')
  await page.getByRole('button', { name: '显示 API 密钥', exact: true }).click()
  await expect(key).toHaveAttribute('type', 'text')
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })))
  await expect(key).toHaveValue('')
  await expect(key).toHaveAttribute('type', 'password')
  await expect(page.getByRole('button', { name: '显示 API 密钥', exact: true })).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByLabel('模型名称', { exact: true })).toHaveValue('unsaved-model')
  expect(io.puts()).toHaveLength(0)
  expect(io.posts()).toHaveLength(0)
})
