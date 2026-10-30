import { expect, test, type Page } from '@playwright/test'
import { mockConsole } from './console-fixture'

test.use({ reducedMotion: 'no-preference' })

async function openSettings(page: Page) {
  await page.locator('.dock-settings').click()
  const dialog = page.locator('dialog.console-settings')
  await expect(dialog).toBeVisible()
  await dialog.locator('.settings-panel').evaluate(async element => {
    await Promise.all(element.getAnimations().map(animation => animation.finished.catch(() => {})))
  })
  return dialog
}

async function pauseMotion(page: Page, name = 'settings-close', progress = .5) {
  await page.evaluate(({ name, progress }) => {
    document.addEventListener('animationstart', event => {
      if (!event.animationName.startsWith(name) || !(event.target instanceof HTMLElement)) return
      for (const animation of event.target.getAnimations()) {
        if (animation instanceof CSSAnimation && animation.animationName.startsWith(name)) {
          animation.pause()
          animation.currentTime = Number(animation.effect!.getTiming().duration) * progress
        }
      }
      const dock = document.querySelector('.utility-dock')
      for (const animation of dock?.getAnimations({ subtree: true }) || []) {
        if (!(animation instanceof CSSTransition)) continue
        const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null
        if (animation.transitionProperty !== 'opacity' && !(animation.transitionProperty === 'transform' && target !== dock?.querySelector('.dock-settings'))) continue
        animation.pause()
        animation.currentTime = Number(animation.effect!.getTiming().duration) * progress
      }
    })
  }, { name, progress })
}

async function motionPaused(page: Page, name = 'settings-close') {
  return page.locator('.settings-panel').evaluate((element, name) => element.getAnimations().some(animation =>
    animation instanceof CSSAnimation && animation.animationName.startsWith(name) && animation.playState === 'paused'), name)
}

async function sampleMotion(page: Page, name: string, progress: number) {
  return page.locator('.settings-panel').evaluate((element, { name, progress }) => {
    const animation = element.getAnimations().find(item => item instanceof CSSAnimation && item.animationName.startsWith(name))!
    const duration = Number(animation.effect!.getTiming().duration)
    animation.currentTime = duration * progress
    const r = element.getBoundingClientRect()
    return { x: r.x, y: r.y, width: r.width, height: r.height, opacity: Number(getComputedStyle(element).opacity), duration }
  }, { name, progress })
}

async function finishMotion(page: Page, name = 'settings-close') {
  await page.locator('.settings-panel').evaluate((element, name) => {
    for (const animation of element.getAnimations()) {
      if (animation instanceof CSSAnimation && animation.animationName.startsWith(name)) animation.finish()
    }
  }, name)
  if (name === 'settings-close') await finishDock(page)
}

async function sampleDock(page: Page, progress: number) {
  return page.locator('.utility-dock').evaluate((element, progress) => {
    for (const animation of element.getAnimations({ subtree: true })) {
      if (!(animation instanceof CSSTransition)) continue
      const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null
      if (animation.transitionProperty !== 'opacity' && !(animation.transitionProperty === 'transform' && target !== element.querySelector('.dock-settings'))) continue
      animation.pause()
      animation.currentTime = Number(animation.effect!.getTiming().duration) * progress
    }
    const button = element.querySelector('.preference-trigger')!.getBoundingClientRect()
    return { x: button.x, gearOpacity: Number(getComputedStyle(element.querySelector('.dock-settings')!).opacity) }
  }, progress)
}

async function finishDock(page: Page) {
  await page.locator('.utility-dock').evaluate(element => {
    for (const animation of element.getAnimations({ subtree: true })) {
      if (animation instanceof CSSTransition) animation.finish()
    }
  })
}

for (const viewport of [{ width: 1440, height: 1000 }, { width: 393, height: 873 }]) {
  test(`settings and the persistent dock reverse the same path at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    const io = await mockConsole(page)
    await page.goto('/overview')
    const opener = page.locator('.dock-settings'), target = (await opener.boundingBox())!
    const language = await page.locator('.preference-trigger').first().elementHandle()
    const originalLanguage = (await language!.boundingBox())!
    await pauseMotion(page, 'settings-open', 0)
    await opener.click()
    const dialog = page.locator('dialog.console-settings')
    await expect.poll(() => motionPaused(page, 'settings-open')).toBe(true)
    const origin = await sampleMotion(page, 'settings-open', 0)
    expect(Math.abs(origin.x + origin.width / 2 - target.x - target.width / 2)).toBeLessThan(1)
    expect(Math.abs(origin.y + origin.height / 2 - target.y - target.height / 2)).toBeLessThan(1)
    expect(origin.width).toBeLessThanOrEqual(target.width + 1)
    const leftward = await sampleDock(page, .5)
    expect(leftward.x).toBeLessThan(originalLanguage.x)
    expect(leftward.x).toBeGreaterThan(target.x)
    expect(leftward.gearOpacity).toBeGreaterThan(0)
    expect(leftward.gearOpacity).toBeLessThan(1)
    const entry = []
    for (const progress of [.25, .5, .75]) entry.push({ progress, frame: await sampleMotion(page, 'settings-open', progress) })
    await finishDock(page)
    const compactLanguage = (await language!.boundingBox())!
    expect(Math.abs(compactLanguage.x - target.x)).toBeLessThan(1)
    expect(await language!.evaluate(element => element === document.querySelector('.preference-trigger') && !!element.closest('dialog'))).toBe(true)
    await expect(page.locator('.console-preferences')).toHaveCount(1)
    await finishMotion(page, 'settings-open')
    await pauseMotion(page)
    await dialog.getByRole('button', { name: '关闭设置', exact: true }).click()
    await expect.poll(() => motionPaused(page)).toBe(true)
    await expect(dialog).toHaveAttribute('open', '')
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden')
    const rightward = await sampleDock(page, .5)
    expect(rightward.x).toBeGreaterThan(compactLanguage.x)
    expect(rightward.x).toBeLessThan(originalLanguage.x)
    expect(rightward.gearOpacity).toBeGreaterThan(0)
    expect(rightward.gearOpacity).toBeLessThan(1)
    for (const { progress, frame } of entry) {
      const reverse = await sampleMotion(page, 'settings-close', 1 - progress)
      for (const key of ['x', 'y', 'width', 'height'] as const) expect(Math.abs(reverse[key] - frame[key])).toBeLessThan(1)
      expect(Math.abs(reverse.opacity - frame.opacity)).toBeLessThan(.01)
      expect(reverse.duration).toBeLessThan(frame.duration)
    }
    const end = await sampleMotion(page, 'settings-close', .999999)
    expect(Math.abs(end.x + end.width / 2 - target.x - target.width / 2)).toBeLessThan(1)
    expect(Math.abs(end.y + end.height / 2 - target.y - target.height / 2)).toBeLessThan(1)
    await finishDock(page)
    await finishMotion(page)
    await expect(dialog).toHaveCount(0)
    await expect(opener).toBeFocused()
    expect(await language!.evaluate(element => element === document.querySelector('.preference-trigger') && !element.closest('dialog'))).toBe(true)
    expect(Math.abs((await language!.boundingBox())!.x - originalLanguage.x)).toBeLessThan(1)
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('')
    expect([...io.requests, ...io.management.requests].every(request => request.method === 'GET')).toBe(true)
  })
}

for (const viewport of [{ width: 1440, height: 1000 }, { width: 393, height: 873 }]) {
  test(`device disclosure refresh stays in its heading and rapid toggles clean up at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    const io = await mockConsole(page)
    await page.goto('/overview')
    const dialog = await openSettings(page)
    await dialog.getByRole('button', { name: '设备', exact: true }).click()
    for (const section of [
      { name: '音频设备', refresh: '刷新音频状态', path: '/api/audio' },
      { name: '网络', refresh: '刷新网络状态', path: '/api/network/status' },
      { name: '系统维护', refresh: '刷新维护状态', path: '/api/power/status' },
    ]) {
      const toggle = dialog.getByRole('button', { name: section.name, exact: true })
      await toggle.click()
      const refresh = dialog.getByRole('button', { name: section.refresh, exact: true })
      await expect(refresh).toBeEnabled()
      const [heading, button] = await Promise.all([toggle.boundingBox(), refresh.boundingBox()])
      expect(Math.abs(heading!.y + heading!.height / 2 - button!.y - button!.height / 2)).toBeLessThan(1)
      expect(button!.width).toBeGreaterThanOrEqual(44)
      expect(button!.height).toBeGreaterThanOrEqual(44)
      expect(await refresh.evaluate(button => !!button.closest('.disclosure-heading') && !button.closest('.disclosure-reveal'))).toBe(true)
      const before = io.management.requests.filter(request => request.path === section.path).length
      await refresh.click()
      await expect.poll(() => io.management.requests.filter(request => request.path === section.path).length).toBe(before + 1)
      await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    }
    const audio = dialog.getByRole('button', { name: '音频设备', exact: true })
    await audio.evaluate(async button => {
      for (let index = 0; index < 5; index++) {
        ;(button as HTMLButtonElement).click()
        await new Promise(requestAnimationFrame)
      }
    })
    await expect(audio).toHaveAttribute('aria-expanded', 'true')
    await expect(dialog.locator('.device-disclosure-enter-active,.device-disclosure-leave-active')).toHaveCount(0)
    await expect(dialog.locator('.disclosure-reveal')).toHaveCount(1)
    await expect(dialog.getByRole('region', { name: '音频设备', exact: true })).toBeVisible()
    await expect(dialog.getByRole('button', { name: '刷新音频状态', exact: true })).toBeEnabled()
    expect(await dialog.locator('.disclosure-reveal').evaluate(element => (element as HTMLElement).inert)).toBe(false)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await audio.click()
    await expect(dialog.locator('.disclosure-reveal')).toHaveCount(0)
    expect([...io.requests, ...io.management.requests].every(request => request.method === 'GET')).toBe(true)
  })
}

for (const method of ['escape', 'backdrop'] as const) {
  test(`${method} uses the settings exit animation and restores focus`, async ({ page }) => {
    await mockConsole(page)
    await page.goto('/overview')
    const dialog = await openSettings(page)
    await pauseMotion(page)
    if (method === 'escape') await page.keyboard.press('Escape')
    else await page.mouse.click(8, 8)
    await expect.poll(() => motionPaused(page)).toBe(true)
    await finishMotion(page)
    await expect(dialog).toHaveCount(0)
    await expect(page.locator('.dock-settings')).toBeFocused()
  })
}

test('unsaved edits remain guarded before the animated discard', async ({ page }) => {
  const io = await mockConsole(page)
  await page.goto('/overview')
  const dialog = await openSettings(page)
  await expect(dialog.getByLabel('名字', { exact: true })).toHaveValue('小鸭')
  await dialog.getByLabel('名字', { exact: true }).fill('临时草稿')
  await page.keyboard.press('Escape')
  await expect(dialog.getByRole('alert')).toContainText('放弃未保存的更改？')
  await expect(dialog).not.toHaveClass(/is-closing/)
  await expect(dialog.getByRole('button', { name: '继续编辑', exact: true })).toBeFocused()
  await pauseMotion(page)
  await dialog.getByRole('button', { name: '放弃并关闭', exact: true }).click()
  await expect.poll(() => motionPaused(page)).toBe(true)
  await finishMotion(page)
  await expect(dialog).toHaveCount(0)
  await openSettings(page)
  await expect(dialog.getByLabel('名字', { exact: true })).toHaveValue('小鸭')
  expect([...io.requests, ...io.management.requests].every(request => request.method === 'GET')).toBe(true)
})

test('an interrupted opening returns without a geometry jump', async ({ page }) => {
  await mockConsole(page)
  await page.goto('/overview')
  await pauseMotion(page, 'settings-open', .35)
  await pauseMotion(page, 'settings-close', 0)
  await page.locator('.dock-settings').click()
  await expect.poll(() => motionPaused(page, 'settings-open')).toBe(true)
  const before = await sampleMotion(page, 'settings-open', .35)
  await page.keyboard.press('Escape')
  await expect.poll(() => motionPaused(page)).toBe(true)
  const after = await sampleMotion(page, 'settings-close', 0)
  for (const key of ['x', 'y', 'width', 'height', 'opacity'] as const) expect(Math.abs(after[key] - before[key])).toBeLessThan(.1)
  await finishMotion(page)
  await expect(page.locator('dialog.console-settings')).toHaveCount(0)
  await expect(page.locator('.dock-settings')).toBeFocused()
})

test('enabling reduced motion during exit completes cleanup without waiting', async ({ page }) => {
  await mockConsole(page)
  await page.goto('/overview')
  const dialog = await openSettings(page)
  await pauseMotion(page)
  await dialog.getByRole('button', { name: '关闭设置', exact: true }).click()
  await expect.poll(() => motionPaused(page)).toBe(true)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('.dock-settings')).toBeFocused()
  expect(await page.evaluate(() => document.body.style.overflow)).toBe('')
})

for (const viewport of [{ width: 1440, height: 1000 }, { width: 393, height: 873 }]) {
  test(`subpages switch without overlap and retain drafts at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    const io = await mockConsole(page)
    await page.goto('/overview')
    const dialog = await openSettings(page)
    await expect(dialog.getByLabel('名字', { exact: true })).toHaveValue('小鸭')
    await dialog.getByLabel('名字', { exact: true }).fill('切换后的草稿')
    await dialog.getByRole('button', { name: '大脑', exact: true }).click()
    await dialog.getByRole('radio', { name: '替换密钥', exact: true }).check()
    await dialog.getByLabel('API 密钥', { exact: true }).fill('test-only-draft')
    const states = await dialog.locator('.settings-tabs').evaluate(async nav => {
      const states: { active: number; visible: number; interactive: number; animations: number }[] = []
      for (const label of ['设备', '角色身份', '大脑', '设备', '大脑', '角色身份']) {
        Array.from(nav.querySelectorAll('button')).find(button => button.textContent === label)!.click()
        await new Promise(requestAnimationFrame)
        const pages = Array.from(nav.closest('dialog')!.querySelectorAll<HTMLElement>('.settings-page'))
        states.push({
          active: pages.filter(page => page.getAttribute('aria-hidden') !== 'true').length,
          visible: pages.filter(page => getComputedStyle(page).display !== 'none').length,
          interactive: pages.filter(page => !page.inert).length,
          animations: pages.flatMap(page => page.getAnimations()).length,
        })
      }
      return states
    })
    expect(states).toEqual(Array.from({ length: 6 }, () => ({ active: 1, visible: 1, interactive: 1, animations: 0 })))
    await expect(dialog.getByLabel('名字', { exact: true })).toHaveValue('切换后的草稿')
    await dialog.getByRole('button', { name: '大脑', exact: true }).click()
    await expect(dialog.getByLabel('API 密钥', { exact: true })).toHaveValue('test-only-draft')
    expect(io.requests.filter(request => request.path === '/api/identity')).toHaveLength(1)
    expect(io.management.requests.filter(request => request.path === '/api/cloud/config')).toHaveLength(1)
    expect([...io.requests, ...io.management.requests].every(request => request.method === 'GET')).toBe(true)
  })
}
