import { expect, test } from '@playwright/test'
import { mockManagement } from './management-fixture'

test('system appearance and cross-tab changes update settings and dock preferences', async ({ page, context }) => {
  await mockManagement(page)
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/overview?settings=1&tab=brain')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  const other = await context.newPage()
  await mockManagement(other)
  await other.goto('/overview')
  await other.evaluate(() => {
    localStorage.setItem('radxa-duck.ui.locale', 'en')
    localStorage.setItem('radxa-duck.ui.theme', 'light')
  })
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true })
  await expect(dialog.getByRole('combobox', { name: /^Appearance:/ })).toHaveAccessibleName('Appearance: Light')
  await expect(dialog.getByRole('combobox', { name: /^Language:/ })).toHaveAccessibleName('Language: English')
  await dialog.getByRole('button', { name: 'Close settings', exact: true }).click()
  await expect(page.locator('.utility-dock').getByRole('combobox', { name: /^Language:/ })).toHaveAccessibleName('Language: English')
})

test('preferences still work when persistent storage is unavailable', async ({ page }) => {
  const io = await mockManagement(page)
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new Error('Storage unavailable') }
    Storage.prototype.setItem = () => { throw new Error('Storage unavailable') }
  })
  await page.goto('/overview?settings=1&tab=brain')
  const dialog = page.getByRole('dialog', { name: '设置', exact: true })
  await dialog.getByRole('combobox', { name: /^外观:/ }).click()
  await page.getByRole('option', { name: '深色', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await dialog.getByRole('button', { name: '关闭设置', exact: true }).click()
  await expect(page.locator('.utility-dock').getByRole('combobox', { name: /^外观:/ })).toHaveAccessibleName('外观: 深色')
  expect(io.requests.every(request => request.method === 'GET')).toBe(true)
})
