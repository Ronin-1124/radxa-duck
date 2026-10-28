import { computed, inject, onBeforeUnmount, onMounted, provide, ref, watch, type ComputedRef, type InjectionKey, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'

type UiLocale = 'zh-CN' | 'en'
type UiTheme = 'system' | 'light' | 'dark'
interface UiPreferences {
  locale: Ref<UiLocale>
  theme: Ref<UiTheme>
  resolvedTheme: ComputedRef<'light' | 'dark'>
  selectLocale(value: string): void
  selectTheme(value: string): void
}
const key: InjectionKey<UiPreferences> = Symbol('ui-preferences')
const localeKey = 'radxa-duck.ui.locale'
const themeKey = 'radxa-duck.ui.theme'
function isLocale(value: unknown): value is UiLocale { return value === 'zh-CN' || value === 'en' }
function isTheme(value: unknown): value is UiTheme { return value === 'system' || value === 'light' || value === 'dark' }
function readPreference(name: string): string | null {
  try { return window.localStorage.getItem(name) } catch { return null }
}
function savePreference(name: string, value: string): void {
  try { window.localStorage.setItem(name, value) } catch { /* Preferences still work in memory. */ }
}
function browserLocale(): UiLocale {
  for (const language of navigator.languages) {
    if (/^zh(?:-|$)/i.test(language)) return 'zh-CN'
    if (/^en(?:-|$)/i.test(language)) return 'en'
  }
  return 'en'
}

/** The shell owns these effects; every settings control uses the same state. */
export function provideUiPreferences(): UiPreferences {
  const { locale: globalLocale } = useI18n({ useScope: 'global' })
  const savedLocale = readPreference(localeKey)
  globalLocale.value = isLocale(savedLocale) ? savedLocale : browserLocale()
  const locale = computed<UiLocale>({
    get: () => isLocale(globalLocale.value) ? globalLocale.value : 'en',
    set: value => { globalLocale.value = value },
  })
  const savedTheme = readPreference(themeKey)
  const theme = ref<UiTheme>(isTheme(savedTheme) ? savedTheme : 'system')
  const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')
  const systemDark = ref(darkQuery.matches)
  const resolvedTheme = computed(() => theme.value === 'system' ? (systemDark.value ? 'dark' : 'light') : theme.value)
  const selectLocale = (value: string): void => {
    if (isLocale(value)) { locale.value = value; savePreference(localeKey, value) }
  }
  const selectTheme = (value: string): void => {
    if (isTheme(value)) { theme.value = value; savePreference(themeKey, value) }
  }
  function syncStorage(event: StorageEvent): void {
    try { if (event.storageArea !== window.localStorage) return } catch { return }
    if (event.key === localeKey || event.key === null) {
      locale.value = isLocale(event.newValue) ? event.newValue : browserLocale()
    }
    if (event.key === themeKey || event.key === null) {
      theme.value = isTheme(event.newValue) ? event.newValue : 'system'
    }
  }
  function syncSystemTheme(event: MediaQueryListEvent): void { systemDark.value = event.matches }
  watch([locale, resolvedTheme], ([language, appearance]) => {
    document.documentElement.lang = language
    document.documentElement.dataset.theme = appearance
    document.documentElement.style.colorScheme = appearance
  }, { immediate: true, flush: 'sync' })
  onMounted(() => {
    darkQuery.addEventListener('change', syncSystemTheme)
    window.addEventListener('storage', syncStorage)
  })
  onBeforeUnmount(() => {
    darkQuery.removeEventListener('change', syncSystemTheme)
    window.removeEventListener('storage', syncStorage)
  })
  const preferences = { locale, theme, resolvedTheme, selectLocale, selectTheme }
  provide(key, preferences)
  return preferences
}

export function useUiPreferences(): UiPreferences {
  const preferences = inject(key)
  if (!preferences) throw new Error('UI preferences require AppShell.')
  return preferences
}
