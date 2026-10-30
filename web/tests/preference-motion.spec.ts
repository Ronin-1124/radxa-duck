import { expect, test, type Page } from '@playwright/test'
import { mockConsole } from './console-fixture'

type Frame = { x: number; y: number; width: number; height: number; opacity: number }
type Sample = { phase: string; duration: number; properties: string[]; frames: Frame[] }
type SampleWindow = Window & { menuMotionSamples: Sample[] }
test.use({ reducedMotion: 'no-preference' })

async function recordMotion(page: Page) {
  await page.addInitScript(() => {
    const samples: Sample[] = []
    Object.assign(window, { menuMotionSamples: samples })
    const recorded = new WeakMap<HTMLElement, Set<string>>()
    document.addEventListener('transitionstart', event => {
      const surface = event.target
      if (!(surface instanceof HTMLElement) || !surface.classList.contains('preference-panel')) return
      const phase = surface.classList.contains('preference-panel-leave-active') ? 'leave' : 'enter'
      const phases = recorded.get(surface) || new Set<string>()
      if (phases.has(phase)) return
      phases.add(phase); recorded.set(surface, phases)
      const animations = surface.getAnimations().filter((animation): animation is CSSTransition =>
        animation instanceof CSSTransition && ['opacity', 'transform'].includes(animation.transitionProperty))
      if (!animations.length) return
      const times = animations.map(animation => animation.currentTime)
      animations.forEach(animation => animation.pause())
      const frames = [0, .25, .5, .75, .999999].map(progress => {
        for (const animation of animations) animation.currentTime = Number(animation.effect!.getTiming().duration) * progress
        const r = surface.getBoundingClientRect()
        return { x: r.x, y: r.y, width: r.width, height: r.height, opacity: Number(getComputedStyle(surface).opacity) }
      })
      samples.push({ phase, frames, duration: Number(animations[0]!.effect!.getTiming().duration), properties: animations.map(animation => animation.transitionProperty) })
      animations.forEach((animation, index) => { animation.currentTime = times[index]!; animation.play() })
    })
  })
}

for (const width of [320, 1440]) for (const settings of [false, true]) {
  test(`preference menus expand from their own buttons at ${width}px, settings=${settings}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const io = await mockConsole(page)
    await recordMotion(page)
    await page.goto(settings ? '/overview?settings=1' : '/overview')
    if (settings) {
      await expect(page.locator('.settings-panel')).toBeVisible()
      await expect(page.locator('.utility-dock')).toHaveClass(/is-compact/)
      await page.locator('.utility-dock').evaluate(async element => {
        await Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => {})))
      })
    }
    const samples = () => page.evaluate(() => (window as unknown as SampleWindow).menuMotionSamples)
    for (const label of [/^语言:/, /^外观:/]) {
      const trigger = page.getByRole('combobox', { name: label })
      await page.mouse.move(width - 10, 10)
      await trigger.evaluate(async element => { await Promise.all(element.getAnimations().map(animation => animation.finished.catch(() => {}))) })
      const target = (await trigger.boundingBox())!, previous = (await samples()).length
      await trigger.click()
      await expect.poll(async () => (await samples()).length).toBe(previous + 1)
      const panel = page.locator('.preference-panel')
      await expect(panel).not.toHaveClass(/preference-panel-enter-active/)
      const entry = (await samples())[previous]!
      expect(entry.properties.sort()).toEqual(['opacity', 'transform'])
      expect(entry.phase).toBe('enter')
      expect(Math.abs(entry.frames[0]!.x + entry.frames[0]!.width / 2 - target.x - target.width / 2)).toBeLessThan(1)
      expect(Math.abs(entry.frames[0]!.y + entry.frames[0]!.height / 2 - target.y - target.height / 2)).toBeLessThan(1)
      expect(entry.frames[0]!.width).toBeLessThanOrEqual(target.width + 1)
      await page.keyboard.press('Escape')
      await expect.poll(async () => (await samples()).length).toBe(previous + 2)
      await expect(panel).toHaveCount(0)
      const exit = (await samples())[previous + 1]!
      expect(exit.phase).toBe('leave')
      expect(exit.duration).toBeLessThan(entry.duration)
      for (const index of [1, 2, 3]) {
        const open = entry.frames[index]!, close = exit.frames[4 - index]!
        for (const key of ['x', 'y', 'width', 'height'] as const) expect(Math.abs(open[key] - close[key])).toBeLessThan(1)
        expect(Math.abs(open.opacity - close.opacity)).toBeLessThan(.01)
      }
      await expect(trigger).toBeFocused()
      if (settings) await expect(page.locator('dialog.console-settings')).toBeVisible()
    }
    expect([...io.requests, ...io.management.requests].every(request => request.method === 'GET')).toBe(true)
  })
}
