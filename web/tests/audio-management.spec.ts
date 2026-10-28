import { expect, test, type Page } from '@playwright/test'
import { audioStatus, mockManagement } from './management-fixture'

function panel(page: Page) { return page.getByRole('region', { name: '音频设备', exact: true }) }
function volume(page: Page) { return panel(page).getByRole('spinbutton', { name: '扬声器音量', exact: true }) }
function apply(page: Page) { return panel(page).getByRole('button', { name: '应用音频设置', exact: true }) }
async function freezeClock(page: Page): Promise<void> {
  const time = new Date('2026-10-27T02:00:00Z')
  await page.clock.install({ time })
  await page.clock.pauseAt(new Date(time.getTime() + 1000))
}

test.describe('board audio management', () => {
  test('shows detected hardware and sends only an explicit apply, using the confirmed quantized level', async ({ page }) => {
    const snapshot = structuredClone(audioStatus)
    const mock = await mockManagement(page, { handle: async route => {
      if (new URL(route.request().url()).pathname !== '/api/audio') return false
      if (route.request().method() === 'PATCH') {
        const body = route.request().postDataJSON()
        snapshot.output.volume_percent = body.volume_percent - 1
        snapshot.output.muted = body.muted
      }
      await route.fulfill({ json: snapshot }); return true
    } })
    await page.goto('/device')
    await expect(volume(page)).toHaveValue('30')
    await expect(panel(page)).toContainText('Pollen HAT microphone')
    await expect(panel(page)).toContainText('Pollen HAT speaker')
    await expect(panel(page)).toContainText('48000 Hz · 2 声道')
    await expect(panel(page)).toContainText('有效麦克风：左声道')
    await expect(apply(page)).toBeDisabled()
    await volume(page).fill('47')
    await panel(page).getByRole('switch', { name: '静音扬声器', exact: true }).click()
    expect(mock.requests.filter(request => request.method !== 'GET')).toEqual([])
    await apply(page).click()
    await expect(volume(page)).toHaveValue('46')
    await expect(panel(page).getByRole('switch')).toHaveAttribute('aria-checked', 'true')
    await expect(panel(page)).toContainText('音频设置已由设备确认')
    await expect(apply(page)).toBeDisabled()
    const writes = mock.requests.filter(request => request.method !== 'GET')
    expect(writes).toHaveLength(1)
    expect(writes[0]).toMatchObject({ method: 'PATCH', path: '/api/audio', body: { volume_percent: 47, muted: true } })
    expect(writes[0]!.headers['x-radxa-duck-request']).toBe('1')
    expect(writes[0]!.headers['content-type']).toBe('application/json')
    expect(mock.sockets).toEqual([])
  })

  test('a busy output preserves the draft, reports failure, and can be retried after refreshing', async ({ page }) => {
    let busy = true
    const snapshot = structuredClone(audioStatus)
    const mock = await mockManagement(page, { handle: async route => {
      if (new URL(route.request().url()).pathname !== '/api/audio') return false
      if (route.request().method() === 'PATCH') {
        if (busy) {
          await route.fulfill({ status: 409, json: { schema_version: 1, error: { code: 'device_busy', message: 'Audio busy' } } })
          return true
        }
        snapshot.output.volume_percent = route.request().postDataJSON().volume_percent
      }
      await route.fulfill({ json: snapshot }); return true
    } })
    await page.goto('/device')
    await expect(volume(page)).toHaveValue('30')
    await volume(page).fill('66')
    await apply(page).click()
    await expect(panel(page)).toContainText('音频正在被使用')
    await expect(volume(page)).toHaveValue('66')
    await expect(panel(page)).not.toContainText('音频设置已由设备确认')
    busy = false
    await panel(page).getByRole('button', { name: '刷新音频状态', exact: true }).click()
    await expect(apply(page)).toBeEnabled()
    await expect(volume(page)).toHaveValue('66')
    await apply(page).click()
    await expect(panel(page)).toContainText('音频设置已由设备确认')
    await expect(volume(page)).toHaveValue('66')
    expect(mock.requests.filter(request => request.method === 'PATCH')).toHaveLength(2)
  })

  test('background reads preserve edits and discard restores the latest device values', async ({ page }) => {
    await freezeClock(page)
    const snapshot = structuredClone(audioStatus)
    const mock = await mockManagement(page, { handle: async route => {
      if (new URL(route.request().url()).pathname !== '/api/audio') return false
      await route.fulfill({ json: snapshot }); return true
    } })
    await page.goto('/device')
    await expect(volume(page)).toHaveValue('30')
    await volume(page).fill('65')
    snapshot.output.volume_percent = 35
    await page.clock.runFor(15000)
    await expect.poll(() => mock.requests.filter(request => request.path === '/api/audio').length).toBe(2)
    await expect(panel(page).getByRole('button', { name: '刷新音频状态', exact: true })).toBeEnabled()
    await expect(volume(page)).toHaveValue('65')
    await expect(apply(page)).toBeEnabled()
    await panel(page).getByRole('button', { name: '放弃修改', exact: true }).click()
    await expect(volume(page)).toHaveValue('35')
    await expect(apply(page)).toBeDisabled()
    expect(mock.requests.every(request => request.method === 'GET')).toBe(true)
  })

  test('a volume draft adopts an external mute change and does not send an unintended unmute', async ({ page }) => {
    const snapshot = structuredClone(audioStatus)
    const mock = await mockManagement(page, { handle: async route => {
      if (new URL(route.request().url()).pathname !== '/api/audio') return false
      if (route.request().method() === 'PATCH') {
        snapshot.output.volume_percent = route.request().postDataJSON().volume_percent
      }
      await route.fulfill({ json: snapshot }); return true
    } })
    await page.goto('/device')
    await expect(volume(page)).toHaveValue('30')
    await volume(page).fill('40')
    snapshot.output.volume_percent = 0
    snapshot.output.muted = true
    await panel(page).getByRole('button', { name: '刷新音频状态', exact: true }).click()
    await expect(apply(page)).toBeEnabled()
    await expect(volume(page)).toHaveValue('40')
    await expect(panel(page).getByRole('switch')).toHaveAttribute('aria-checked', 'true')
    await apply(page).click()
    await expect(panel(page)).toContainText('音频设置已由设备确认')
    expect(mock.requests.filter(request => request.method === 'PATCH').map(request => request.body)).toEqual([{ volume_percent: 40 }])
    await expect(panel(page).getByRole('switch')).toHaveAttribute('aria-checked', 'true')
  })

  test('missing output capabilities keep unsupported controls disabled without inventing device states', async ({ page }) => {
    const snapshot = structuredClone(audioStatus)
    snapshot.inputs = []; snapshot.outputs = []
    snapshot.output = { device: null, volume_percent: null, muted: null }
    snapshot.capabilities = { output_volume: false, output_mute: false }
    const mock = await mockManagement(page, { reads: { '/api/audio': snapshot } })
    await page.goto('/device')
    await expect(panel(page)).toContainText('未发现输入设备')
    await expect(panel(page)).toContainText('未发现输出设备')
    await expect(volume(page)).toHaveValue('')
    await expect(volume(page)).toBeDisabled()
    await expect(panel(page).getByRole('slider')).toBeDisabled()
    await expect(panel(page).getByRole('switch')).toBeDisabled()
    await expect(apply(page)).toBeDisabled()
    await expect(panel(page)).toContainText('当前输出设备不支持音量或静音控制')
    expect(mock.requests.every(request => request.method === 'GET')).toBe(true)
  })

  test('validates integer bounds and accepts zero as an explicit volume setting', async ({ page }) => {
    const snapshot = structuredClone(audioStatus)
    const mock = await mockManagement(page, { handle: async route => {
      if (new URL(route.request().url()).pathname !== '/api/audio') return false
      if (route.request().method() === 'PATCH') snapshot.output.volume_percent = route.request().postDataJSON().volume_percent
      await route.fulfill({ json: snapshot }); return true
    } })
    await page.goto('/device')
    await expect(volume(page)).toHaveValue('30')
    for (const invalid of ['101', '-1', '10.5', '']) {
      await volume(page).fill(invalid)
      await volume(page).blur()
      await expect(volume(page)).toHaveAttribute('aria-invalid', 'true')
      await expect(apply(page)).toBeDisabled()
      await expect(panel(page)).toContainText('请输入 0–100 之间的整数音量')
    }
    await volume(page).fill('0')
    await volume(page).blur()
    await expect(volume(page)).toHaveAttribute('aria-invalid', 'false')
    await expect(apply(page)).toBeEnabled()
    await apply(page).click()
    await expect(panel(page)).toContainText('音频设置已由设备确认')
    expect(mock.requests.filter(request => request.method === 'PATCH').map(request => request.body)).toEqual([{ volume_percent: 0 }])
  })
})
