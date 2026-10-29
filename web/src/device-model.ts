/** Device-tree identifies the ZERO 3W as “Radxa ZERO 3”. Other boards retain their reported model. */
export function displayDeviceModel(model: string | null | undefined): string | null {
  const name = model?.trim()
  if (!name) return null
  return /^radxa[\s_-]+zero[\s_-]*3(?:[\s_-]*w)?$/i.test(name) ? 'Radxa ZERO 3W' : name
}
