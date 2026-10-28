import { expect, test, type Page } from '@playwright/test'

import { cloudConfig, mockManagement } from './management-fixture'

async function isolate(page: Page, locale = 'zh-CN', theme = 'light') {
  await page.addInitScript(({ locale, theme }) => {
    if (!localStorage.getItem('radxa-duck.ui.locale')) localStorage.setItem('radxa-duck.ui.locale', locale)
    if (!localStorage.getItem('radxa-duck.ui.theme')) localStorage.setItem('radxa-duck.ui.theme', theme)
  }, { locale, theme })
  return mockManagement(page)
}

for (const { width, locale, theme } of [
  { width: 320, locale: 'zh-CN', theme: 'light' },
  { width: 375, locale: 'en', theme: 'dark' },
  { width: 768, locale: 'en', theme: 'light' },
  { width: 1440, locale: 'zh-CN', theme: 'dark' },
]) {
  test(`device and settings fit ${width}px in ${locale}/${theme}`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: 1000 })
    const io = await isolate(page, locale, theme)
    for (const route of ['/device', '/settings']) {
      await page.goto(route)
      await expect(page.locator(route === '/device' ? '.device-page' : '.settings-page')).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('lang', locale)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    }
    // These management pages inspect services and edit drafts without capture connections or writes.
    expect(io.sockets).toEqual([])
    expect(io.requests.every(request => request.method === 'GET')).toBe(true)
    expect(errors).toEqual([])
  })
}

test('settings sync with the header, persist preferences and discard a cloud draft on leave', async ({ page }) => {
  const io = await isolate(page)
  await page.goto('/settings')
  await expect(page.locator('.connection')).toHaveAttribute('data-state', 'online')
  await expect(page.getByLabel('模型名称', { exact: true })).toHaveValue(cloudConfig.model!)
  await page.getByRole('radio', { name: '替换密钥', exact: true }).check()
  const key = page.getByLabel('API 密钥', { exact: true })
  await key.fill('test-only-secret')
  await page.getByLabel('模型名称', { exact: true }).fill('temporary-model')
  await expect(key).toHaveAttribute('type', 'password')
  await page.getByRole('button', { name: '显示 API 密钥', exact: true }).click()
  await expect(key).toHaveAttribute('type', 'text')

  await page.locator('.preferences-card').getByRole('combobox', { name: /^界面语言:/ }).click()
  await page.getByRole('option', { name: 'English', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('.topbar').getByRole('combobox', { name: /^Language:/ })).toContainText('English')
  await expect(page.getByLabel('API key', { exact: true })).toHaveValue('test-only-secret')
  await expect(page.getByLabel('Model name', { exact: true })).toHaveValue('temporary-model')

  await page.locator('.topbar').getByRole('combobox', { name: /^Appearance:/ }).click()
  await page.getByRole('option', { name: 'Dark', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('.preferences-card').getByRole('combobox', { name: /^Appearance:/ })).toContainText('Dark')
  await expect(page.getByRole('button', { name: 'Save to device', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Test connection', exact: true })).toBeDisabled()
  const storage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(storage).toEqual({ local: { 'radxa-duck.ui.locale': 'en', 'radxa-duck.ui.theme': 'dark' }, session: {} })

  await page.getByRole('link', { name: 'Overview', exact: true }).click()
  await page.getByRole('link', { name: 'Settings', exact: true }).click()
  await expect(page.getByLabel('Model name', { exact: true })).toHaveValue(cloudConfig.model!)
  await expect(page.getByRole('radio', { name: 'Keep existing key', exact: true })).toBeChecked()
  await page.getByRole('radio', { name: 'Replace key', exact: true }).check()
  await expect(page.getByLabel('API key', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('API key', { exact: true })).toHaveAttribute('type', 'password')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(io.requests.every(request => request.method === 'GET')).toBe(true)
  expect(io.sockets).toEqual([])
})

test('cloud draft validates nearby and resets without sending credentials', async ({ page }) => {
  const io = await isolate(page)
  await page.goto('/settings')
  const endpoint = page.getByLabel('服务地址', { exact: true })
  const model = page.getByLabel('模型名称', { exact: true })
  const timeout = page.getByLabel('请求超时', { exact: true })
  await expect(endpoint).toHaveValue(cloudConfig.base_url!)
  for (const invalid of ['ftp://example.com/v1', 'https://user:password@example.com/v1', 'https://example.com/v1?key=secret', 'https://example.com/v1#fragment']) {
    await endpoint.fill(invalid)
    await endpoint.blur()
    await expect(endpoint).toHaveAttribute('aria-invalid', 'true')
    await expect(page.locator('.field-error')).toContainText('HTTPS')
  }
  await endpoint.fill('https://example.com/v1')
  await endpoint.blur()
  await expect(endpoint).toHaveAttribute('aria-invalid', 'false')
  await model.fill('has spaces')
  await model.blur()
  await timeout.fill('121')
  await timeout.blur()
  await expect(model).toHaveAttribute('aria-invalid', 'true')
  await expect(timeout).toHaveAttribute('aria-invalid', 'true')
  await expect(page.locator('.field-error')).toHaveCount(2)
  await page.getByRole('radio', { name: '替换密钥', exact: true }).check()
  await page.getByLabel('API 密钥', { exact: true }).fill('another-test-key')
  await page.getByRole('button', { name: '显示 API 密钥', exact: true }).click()
  await page.getByRole('button', { name: '重置草稿', exact: true }).click()
  await expect(page.getByLabel('API 密钥', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('radio', { name: '保留现有密钥', exact: true })).toBeChecked()
  await expect(endpoint).toHaveValue(cloudConfig.base_url!)
  await expect(timeout).toHaveValue('8')
  await expect(model).toHaveValue(cloudConfig.model!)
  await expect(page.locator('.field-error')).toHaveCount(0)
  await expect(page.getByRole('status').filter({ hasText: '已恢复上次读取的配置' })).toBeVisible()
  expect(io.requests.every(request => request.method === 'GET')).toBe(true)
})

test('system appearance and cross-tab preference changes update both controls', async ({ page, context }) => {
  await isolate(page, 'zh-CN', 'system')
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/settings')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  const other = await context.newPage()
  await isolate(other)
  await other.goto('/overview')
  await other.evaluate(() => {
    localStorage.setItem('radxa-duck.ui.locale', 'en')
    localStorage.setItem('radxa-duck.ui.theme', 'light')
  })
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.locator('.preferences-card').getByRole('combobox', { name: /^Appearance:/ })).toContainText('Light')
  await expect(page.locator('.topbar').getByRole('combobox', { name: /^Language:/ })).toContainText('English')
})

test('preferences still work when persistent storage is unavailable', async ({ page }) => {
  await isolate(page)
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new Error('Storage unavailable') }
    Storage.prototype.setItem = () => { throw new Error('Storage unavailable') }
  })
  await page.goto('/settings')
  await page.locator('.preferences-card').getByRole('combobox', { name: /^外观主题:/ }).click()
  await page.getByRole('option', { name: '深色', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('.topbar').getByRole('combobox', { name: /^外观:/ })).toContainText('深色')
})
