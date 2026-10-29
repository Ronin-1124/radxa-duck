import { expect, test } from '@playwright/test'
import type { BrainStatus } from '../src/console-protocol'
import { mockConsole } from './console-fixture'

const at = '2026-10-29T08:30:00Z'
function cloud(): BrainStatus['cloud'] {
  return { configured: true, model: 'cloud-model', health: 'ready', revision: 2,
    connection_check: { state: 'succeeded', revision: 2, checked_at: at, error_code: null },
    answer: { state: 'unknown', revision: null, checked_at: null, error_code: null } }
}

test('live connection and answer checks have independent outcomes, times and configuration revisions', async ({ page }) => {
  const io = await mockConsole(page)
  io.brain.cloud = cloud()
  io.brain.cloud.answer = { state: 'failed', revision: 2, checked_at: '2026-10-29T08:31:00Z', error_code: 'rate_limited' }
  await page.goto('/overview?settings=1&tab=brain')
  await expect(page.locator('.test-state')).toHaveText('服务可达 · 模型已列出')
  await expect(page.locator('.test-time time')).toHaveAttribute('datetime', at)
  const answer = page.locator('.answer-verification')
  await expect(answer).toHaveAttribute('data-state', 'verificationFailed')
  await expect(answer).toContainText('云端服务请求受限')
  await expect(answer.locator('time')).toHaveAttribute('datetime', '2026-10-29T08:31:00Z')
  io.publish({ cloud: { ...io.brain.cloud, connection_check: { state: 'failed', revision: 2, checked_at: at, error_code: 'authentication_failed' },
    answer: { state: 'succeeded', revision: 2, checked_at: '2026-10-29T08:32:00Z', error_code: null } } })
  await expect(page.locator('.test-result')).toContainText('云端服务未接受当前密钥')
  await expect(answer).toHaveAttribute('data-state', 'verified')
  io.publish({ cloud: { ...io.brain.cloud, revision: 3, health: 'ready' } })
  await expect(answer).toHaveAttribute('data-state', 'verificationPending')
  await expect(answer).toContainText('此前保存的配置')
  await expect(page.locator('.test-result')).toHaveAttribute('data-tone', 'error')
  await expect(page.locator('.test-result')).toContainText('此前保存的配置')
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})

test('changing cloud configuration invalidates previous answer success on the overview', async ({ page }) => {
  const io = await mockConsole(page)
  io.brain.cloud = { ...cloud(), answer: { state: 'succeeded', revision: 2, checked_at: at, error_code: null } }
  await page.goto('/overview')
  const model = page.locator('.capability-item').filter({ hasText: '云端模型' }).locator('strong')
  await expect(model).toHaveText('回答已验证')
  io.publish({ cloud: { ...io.brain.cloud, revision: 3, health: 'ready' } })
  await expect(model).toHaveText('已配置 · 待验证')
  io.publish({ cloud: { ...io.brain.cloud, answer: { state: 'running', revision: 3, checked_at: null, error_code: null } } })
  await expect(model).toHaveText('正在生成回答')
})

test('live verification becomes unknown after disconnect and hides unrecognized provider details', async ({ page }) => {
  const io = await mockConsole(page)
  io.brain.cloud = cloud()
  io.brain.cloud.answer = { state: 'failed', revision: 2, checked_at: at, error_code: 'private_provider_response' }
  await page.goto('/overview?settings=1&tab=brain')
  await expect(page.locator('.answer-verification')).toContainText('操作未完成')
  await expect(page.getByText('private_provider_response')).toHaveCount(0)
  io.setOffline(true)
  io.events[0]!.socket.close()
  await expect(page.locator('.test-state')).toHaveText('状态未知')
  await expect(page.locator('.answer-verification')).toHaveAttribute('data-state', 'unknown')
  expect(io.requests.every(r => r.method === 'GET')).toBe(true)
})
