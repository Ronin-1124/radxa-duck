/** Share one trigger geometry for an expanding panel and its return animation. */
export function setMotionAnchor(panel: HTMLElement, trigger?: HTMLElement): void {
  const bounds = panel.getBoundingClientRect()
  if (!panel.offsetWidth || !panel.offsetHeight) return
  const transform = getComputedStyle(panel).transform
  const current = transform === 'none' ? null : new DOMMatrixReadOnly(transform)
  const target = trigger?.getBoundingClientRect()
  const triggerTransform = trigger ? getComputedStyle(trigger).transform : 'none'
  const hover = triggerTransform === 'none' ? null : new DOMMatrixReadOnly(triggerTransform)
  const width = target?.width || 46, height = target?.height || 46
  const targetX = target?.width ? target.left + width / 2 - (hover?.m41 || 0) : 39
  const targetY = target?.height ? target.top + height / 2 - (hover?.m42 || 0) : innerHeight - 39
  const x = targetX - (bounds.left + bounds.width / 2 - (current?.m41 || 0))
  const y = targetY - (bounds.top + bounds.height / 2 - (current?.m42 || 0))
  const scale = Math.min(width / panel.offsetWidth, height / panel.offsetHeight)
  panel.style.setProperty('--anchored-collapsed', `translate3d(${x}px,${y}px,0) scale(${scale})`)
}

export function captureMotionStart(panel: HTMLElement): void {
  const style = getComputedStyle(panel)
  panel.style.setProperty('--anchored-from-transform', style.transform)
  panel.style.setProperty('--anchored-from-opacity', style.opacity)
}
