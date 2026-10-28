import { expect, test, type Page } from '@playwright/test'
import type { SampleOptions } from './vision-fixture'

const fixturePath = '/tests/vision-fixture.html'
const image = (page: Page) => page.locator('.vision-frame img')
const boxes = (page: Page) => page.locator('.detection-box')

async function loadFixture(page: Page): Promise<void> {
  // Pause browser timers so slower CI cannot age a sample while asserting it.
  await page.clock.install({ time: new Date('2026-01-01T00:00:00Z') })
  await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'))
  await page.goto(fixturePath)
  await page.waitForFunction(() => !!window.visionFixture)
}

async function showSample(page: Page, options: SampleOptions = {}): Promise<void> {
  await page.evaluate(options => window.visionFixture.setState(window.visionFixture.sample(options)), options)
  await expect(image(page)).toBeVisible()
}

test('the real vision route stays inactive when its service cannot be reached', async ({ page }) => {
  // A failed connection must not trigger capture or reach real board services.
  await page.route('**/api/**', route => route.abort())
  await page.routeWebSocket(/\/api\//, socket => socket.close({ code: 1008 }))
  await page.goto('/vision')
  await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeDisabled()
  await expect(page.locator('.capture-status')).toContainText('视觉连接已断开')
  await expect(image(page)).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeDisabled()
  await expect(image(page)).toHaveCount(0)
})

test.describe('controlled vision workspace with simulated frames', () => {
  test.beforeEach(async ({ page }) => { await loadFixture(page) })

  test('emits chosen settings only on start and waits for confirmed state', async ({ page }) => {
    await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeDisabled()
    expect(await page.evaluate(() => window.visionFixture.events())).toEqual([])
    await page.getByRole('combobox', { name: /^检测模型:/ }).click()
    await page.getByRole('option', { name: 'YOLOv5n', exact: true }).click()
    await page.getByRole('combobox', { name: /^画面方向:/ }).click()
    await page.getByRole('option', { name: '顺时针 90°', exact: true }).click()
    await page.evaluate(() => window.visionFixture.patchState({ connection: 'online' }))
    expect(await page.evaluate(() => window.visionFixture.events())).toEqual([])
    await page.getByRole('button', { name: '开启视觉', exact: true }).click()
    await expect(page.getByRole('button', { name: '正在请求开启', exact: true })).toBeDisabled()
    expect(await page.evaluate(() => window.visionFixture.events())).toEqual([{ type: 'start', settings: { model: 'yolov5n', rotation: 90, detection_enabled: true, max_fps: 10, score_threshold: 0.35 } }])
    await expect(image(page)).toHaveCount(0)

    // Replacing presentation data alone must not unlock a request awaiting its acknowledgement.
    await page.evaluate(() => window.visionFixture.patchState({ frame: window.visionFixture.sample().frame }))
    await expect(page.getByRole('button', { name: '正在请求开启', exact: true })).toBeDisabled()
    await showSample(page)
    await page.getByRole('button', { name: '停止视觉', exact: true }).click()
    await expect(page.getByRole('button', { name: '正在请求停止', exact: true })).toBeDisabled()
    await page.evaluate(() => window.visionFixture.setState(window.visionFixture.sample({ frameId: 2 })))
    await expect(image(page)).toHaveCount(0)
    await expect(page.getByRole('button', { name: '正在请求停止', exact: true })).toBeDisabled()
    expect(await page.evaluate(() => window.visionFixture.events())).toHaveLength(2)
    await page.evaluate(() => window.visionFixture.patchState({ status: 'stopping' }))
    await expect(page.locator('.capture-status')).toContainText('正在停止视觉任务')
    await expect(page.getByRole('button', { name: '停止视觉', exact: true })).toBeDisabled()
    await expect(image(page)).toHaveCount(0)
    await page.evaluate(() => window.visionFixture.patchState({ status: 'idle', owned: false, frame: null, result: null }))
    await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeEnabled()
  })

  for (const mismatch of ['session', 'frame', 'size'] as const) {
    test(`rejects detections from a mismatched ${mismatch}`, async ({ page }) => {
      await showSample(page)
      await expect(boxes(page)).toHaveCount(1)
      await page.evaluate(mismatch => {
        const sample = window.visionFixture.sample({ frameId: 2 })
        if (mismatch === 'session') sample.result!.sessionId = 'old-session'
        if (mismatch === 'frame') sample.result!.data.frame_id = 1
        if (mismatch === 'size') sample.result!.data.image_size = [480, 640]
        return window.visionFixture.setState(sample)
      }, mismatch)
      await expect(image(page)).toBeVisible()
      await expect(boxes(page)).toHaveCount(0)
      await expect(page.locator('.targets-panel')).toContainText('等待同帧结果')
      await expect(page.locator('.object-list')).toHaveCount(0)
    })
  }

  test('waits for image decoding and removes labels on a corrupt frame', async ({ page }) => {
    let releaseImage!: () => void
    const imageGate = new Promise<void>(resolve => { releaseImage = resolve })
    await page.route('**/tests/delayed-camera.svg', async route => {
      await imageGate
      await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#2b4534"/></svg>' })
    })
    await page.evaluate(() => window.visionFixture.setState(window.visionFixture.sample({ url: '/tests/delayed-camera.svg' })))
    await expect(page.locator('.placeholder-title')).toHaveText('正在加载画面…')
    await expect(boxes(page)).toHaveCount(0)
    releaseImage()
    await expect(image(page)).toBeVisible()
    await expect(boxes(page)).toHaveCount(1)
    await page.evaluate(() => window.visionFixture.setState(window.visionFixture.sample({ frameId: 2, url: 'data:image/png;base64,broken' })))
    await expect(page.locator('.stale-hint')).toHaveText('等待下一帧画面。')
    await expect(image(page)).toHaveAttribute('src', '/tests/delayed-camera.svg')
    await expect(boxes(page)).toHaveCount(0)
    await expect(page.locator('.object-list')).toHaveCount(0)
  })

  test('rejects an image whose decoded dimensions differ from its frame envelope', async ({ page }) => {
    await page.evaluate(() => {
      const state = window.visionFixture.sample()
      state.frame!.url = window.visionFixture.sample({ width: 480, height: 640 }).frame!.url
      return window.visionFixture.setState(state)
    })
    await expect(page.locator('.placeholder-title')).toHaveText('这一帧无法显示')
    await expect(boxes(page)).toHaveCount(0)
    await expect(page.locator('.object-list')).toHaveCount(0)
  })

  test('keeps the displayed frame and its labels until the next image decodes', async ({ page }) => {
    await showSample(page)
    const previousSource = await image(page).getAttribute('src')
    let releaseImage!: () => void
    const imageGate = new Promise<void>(resolve => { releaseImage = resolve })
    await page.route('**/tests/next-camera.svg', async route => {
      await imageGate
      await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#35516b"/></svg>' })
    })
    await page.evaluate(() => window.visionFixture.setState(window.visionFixture.sample({
      frameId: 2, url: '/tests/next-camera.svg',
      detections: [{ class_id: 16, label: 'dog', score: 0.8, box_xyxy: [100, 100, 300, 300], center_normalized: [0.3125, 0.4167] }],
    })))
    await expect(image(page)).toHaveAttribute('src', previousSource!)
    await expect(page.locator('.object-name')).toHaveText('人')
    await expect(boxes(page)).toContainText('人')
    releaseImage()
    await expect(image(page)).toHaveAttribute('src', '/tests/next-camera.svg')
    await expect(page.locator('.object-name')).toHaveText('狗')
    await expect(boxes(page)).toContainText('狗')
  })

  test('does not display a late image from a previous session', async ({ page }) => {
    let releaseImage!: () => void
    const imageGate = new Promise<void>(resolve => { releaseImage = resolve })
    await page.route('**/tests/previous-session.svg', async route => {
      await imageGate
      await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#734433"/></svg>' })
    })
    await page.evaluate(() => window.visionFixture.setState(window.visionFixture.sample({ sessionId: 'old-session', url: '/tests/previous-session.svg' })))
    await expect(page.locator('.placeholder-title')).toHaveText('正在加载画面…')
    await page.evaluate(() => window.visionFixture.setState(window.visionFixture.sample({
      sessionId: 'new-session',
      detections: [{ class_id: 15, label: 'cat', score: 0.8, box_xyxy: [100, 100, 300, 300], center_normalized: [0.3125, 0.4167] }],
    })))
    releaseImage()
    await expect(page.locator('.object-name')).toHaveText('猫')
    await expect(boxes(page)).toContainText('猫')
    await expect(image(page)).not.toHaveAttribute('src', '/tests/previous-session.svg')
  })

  test('returning to the displayed frame cancels a pending replacement without refreshing its age', async ({ page }) => {
    await showSample(page)
    const original = await page.evaluate(() => window.visionFixture.state())
    let releaseImage!: () => void
    const imageGate = new Promise<void>(resolve => { releaseImage = resolve })
    await page.route('**/tests/cancelled-camera.svg', async route => {
      await imageGate
      await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"/>' })
    })
    await page.evaluate(() => window.visionFixture.setState(window.visionFixture.sample({ frameId: 2, url: '/tests/cancelled-camera.svg' })))
    await page.clock.fastForward(1000)
    await page.evaluate(original => window.visionFixture.setState({
      ...original, frame: { ...original.frame!, receivedAt: performance.now() },
    }), original)
    releaseImage()
    await expect(image(page)).toHaveAttribute('src', original.frame!.url)
    await expect(boxes(page)).toHaveCount(1)
    await page.clock.fastForward(1100)
    await expect(image(page)).toHaveAttribute('src', original.frame!.url)
    await expect(page.locator('.frame-status')).toContainText('画面暂未更新')
    await expect(boxes(page)).toHaveCount(0)
  })

  test('marks a two-second-old image stale and hides its detections', async ({ page }) => {
    await showSample(page)
    await expect(boxes(page)).toHaveCount(1)
    await page.clock.fastForward(2100)
    await expect(page.locator('.frame-status')).toContainText('画面暂未更新')
    await expect(boxes(page)).toHaveCount(0)
    await expect(page.locator('.object-list')).toHaveCount(0)
    await expect(page.locator('.targets-panel')).toContainText('检测结果已暂停')
    await expect(image(page)).toBeVisible()
    await showSample(page, { frameId: 2 })
    await expect(boxes(page)).toHaveCount(1)
  })

  test('hides images after disconnection and treats empty detections as success', async ({ page }) => {
    await showSample(page, { detections: [] })
    await expect(page.locator('.targets-panel')).toContainText('未检测到目标')
    await expect(page.locator('.object-count')).toHaveText('0 个目标')
    await page.locator('.processing-details summary').click()
    await expect(page.locator('.metrics-grid')).toContainText('17 ms')
    await page.evaluate(() => window.visionFixture.patchState({ connection: 'offline' }))
    await expect(image(page)).toHaveCount(0)
    await expect(boxes(page)).toHaveCount(0)
    await expect(page.locator('.capture-status')).toContainText('视觉连接已断开')
    await expect(page.getByRole('button', { name: '停止视觉', exact: true })).toBeDisabled()
    await expect(page.locator('.metrics-grid')).not.toContainText('17 ms')
  })

  test('overlay and language switches preserve settings without sending operations', async ({ page }) => {
    await page.getByRole('combobox', { name: /^检测模型:/ }).click()
    await page.getByRole('option', { name: 'YOLOv5n', exact: true }).click()
    await page.getByRole('combobox', { name: /^画面方向:/ }).click()
    await page.getByRole('option', { name: '顺时针 270°', exact: true }).click()
    await page.getByRole('button', { name: '目标标注', exact: true }).click()
    await page.getByRole('combobox', { name: /^语言:/ }).click()
    await page.getByRole('option', { name: 'English', exact: true }).click()
    await expect(page.getByRole('combobox', { name: /^Detection model:/ })).toContainText('YOLOv5n')
    await expect(page.getByRole('combobox', { name: /^Image orientation:/ })).toContainText('270° clockwise')
    await expect(page.getByRole('button', { name: 'Object labels', exact: true })).toHaveAttribute('aria-pressed', 'false')
    await showSample(page)
    await expect(boxes(page)).toHaveCount(0)
    await expect(page.locator('.object-list')).toContainText('person')
    await page.getByRole('button', { name: 'Object labels', exact: true }).click()
    await expect(boxes(page)).toHaveCount(1)
    expect(await page.evaluate(() => window.visionFixture.events())).toEqual([])
  })

  test('blocks another owner and refuses to restart after fatal cleanup failure', async ({ page }) => {
    await page.evaluate(() => window.visionFixture.setState({ ...window.visionFixture.sample(), owned: false }))
    await expect(page.locator('.capture-status')).toContainText('视觉任务由其他页面控制')
    await expect(page.getByRole('button', { name: '开启视觉', exact: true })).toBeDisabled()
    await expect(image(page)).toHaveCount(0)
    await page.evaluate(() => window.visionFixture.patchState({ owned: true, status: 'error', errorCode: 'cleanup_failed', ready: false }))
    await expect(page.getByRole('alert')).toContainText('设备资源还未释放')
    await expect(page.locator('.action-button')).toBeDisabled()
    await expect(page.getByRole('button', { name: '重试停止', exact: true })).toHaveCount(0)
    expect(await page.evaluate(() => window.visionFixture.events())).toEqual([])
  })

  test('the object list can be scrolled with the keyboard', async ({ page }) => {
    await page.evaluate(() => {
      const state = window.visionFixture.sample()
      state.result!.data.detections = Array.from({ length: 20 }, () => ({ ...state.result!.data.detections[0] }))
      return window.visionFixture.setState(state)
    })
    const list = page.getByRole('list', { name: '当前目标' })
    await expect(list.locator('li')).toHaveCount(20)
    await list.focus()
    await expect(list).toBeFocused()
    await list.press('End')
    await expect.poll(() => list.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  })

  for (const width of [375, 1440]) {
    test(`fits portrait camera frames at a ${width}px viewport`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await showSample(page, { width: 480, height: 640 })
      await expect(boxes(page)).toHaveCount(1)
      const bounds = await page.locator('.image-plane').boundingBox()
      expect(bounds).not.toBeNull()
      expect(bounds!.width / bounds!.height).toBeCloseTo(480 / 640, 2)
      await expect(page.locator('.annotations')).toHaveAttribute('viewBox', '0 0 480 640')
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow).toBeLessThanOrEqual(1)
    })
  }
})

for (const width of [375, 1440]) {
  for (const reducedMotion of ['no-preference', 'reduce'] as const) {
    test(`capture menus remain clickable above later cards at ${width}px with ${reducedMotion} motion`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await page.emulateMedia({ reducedMotion })
      // Keep real animation timing: the normal-motion bug persisted after the
      // card's entrance animation finished and retained its stacking context.
      await page.goto(fixturePath)
      await page.waitForFunction(() => !!window.visionFixture)
      await page.locator('.settings-panel').evaluate(async panel => {
        await Promise.all(panel.getAnimations().map(animation => animation.finished.catch(() => {})))
      })

      for (const [label, choice] of [['检测模型', 'YOLOv5n'], ['画面方向', '顺时针 270°']]) {
        const trigger = page.getByRole('combobox', { name: new RegExp(`^${label}:`) })
        await trigger.evaluate(element => window.scrollBy(0, element.getBoundingClientRect().top - 120))
        await trigger.click()
        const option = page.getByRole('option', { name: choice, exact: true })
        await expect(option).toBeVisible()
        await page.locator('.settings-panel').evaluate(async panel => {
          await Promise.all(panel.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => {})))
        })
        const hit = await option.evaluate(element => {
          const bounds = element.getBoundingClientRect()
          const points = [.25, .5, .75].map(fraction => ({
            x: bounds.left + bounds.width / 2, y: bounds.top + bounds.height * fraction,
          }))
          return points.every(point => element.contains(document.elementFromPoint(point.x, point.y)))
        })
        expect(hit, `${label} options must receive pointer hits, not the later detail card`).toBe(true)
        await option.click()
        await expect(trigger).toContainText(choice)
        await expect(trigger).toHaveAttribute('aria-expanded', 'false')
      }
      if (width === 375) {
        await page.getByRole('combobox', { name: /^画面方向:/ }).click()
        await page.locator('.settings-panel .preference-panel').evaluate(panel => {
          const menu = panel.getBoundingClientRect()
          const navigation = document.querySelector('.mobile-nav')!.getBoundingClientRect()
          window.scrollBy(0, menu.top + menu.height / 2 - navigation.top - navigation.height / 2)
        })
        const navigationWins = await page.locator('.mobile-nav').evaluate(navigation => {
          const nav = navigation.getBoundingClientRect()
          const menu = document.querySelector('.settings-panel .preference-panel')!.getBoundingClientRect()
          const x = Math.max(menu.left, nav.left) + Math.min(menu.width, nav.width) / 2
          const y = nav.top + nav.height / 2
          return menu.top < y && menu.bottom > y && navigation.contains(document.elementFromPoint(x, y))
        })
        expect(navigationWins, 'the fixed mobile navigation must remain above capture menus').toBe(true)
      }
      expect(await page.evaluate(() => window.visionFixture.events())).toEqual([])
    })
  }
}
