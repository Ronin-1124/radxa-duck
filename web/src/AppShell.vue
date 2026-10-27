<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, useSlots, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Blocks, Camera, ChevronRight, Cpu, Globe2, LayoutDashboard, Mic,
  Monitor, Moon, MoreHorizontal, Settings2, Sun, X } from '@lucide/vue'
import PreferenceSelect from './PreferenceSelect.vue'

type Locale = 'zh-CN' | 'en'
type Theme = 'system' | 'light' | 'dark'
type Connection = 'unknown' | 'connecting' | 'online' | 'offline'

const props = withDefaults(defineProps<{
  deviceName?: string
  connection?: Connection
}>(), { deviceName: 'Radxa ZERO 3W', connection: 'unknown' })

const messages = {
  'zh-CN': {
    console: '机器人控制台', navigation: '主导航', workspace: '工作空间',
    skip: '跳到主要内容', more: '更多', close: '关闭', currentDevice: '当前设备',
    preferences: '界面偏好', language: '语言', appearance: '外观',
    system: '跟随系统', light: '浅色', dark: '深色', local: '局域网管理',
    themeHints: { system: '与设备外观保持一致', light: '明亮清晰，适合日间使用', dark: '柔和暗色，适合低光环境' },
    status: { unknown: '状态未获取', connecting: '正在连接', online: '已连接', offline: '已断开' },
    pages: {
      overview: '总览', voice: '语音', vision: '视觉', apps: '应用', device: '设备', settings: '设置',
    },
    descriptions: {
      overview: '了解机器人的状态，开始一次新的交互。',
      voice: '与机器人对话，管理收音与播放。',
      vision: '查看机器人视角，探索画面中的信息。',
      apps: '选择一个应用，让机器人回应你的需求。',
      device: '查看设备健康，管理网络与系统服务。',
      settings: '配置云端服务与机器人的运行偏好。',
    },
  },
  en: {
    console: 'Robot console', navigation: 'Main navigation', workspace: 'Workspace',
    skip: 'Skip to main content', more: 'More', close: 'Close', currentDevice: 'Current device',
    preferences: 'Interface preferences', language: 'Language', appearance: 'Appearance',
    system: 'System', light: 'Light', dark: 'Dark', local: 'Local network',
    themeHints: { system: 'Match device appearance', light: 'A clear view for daytime', dark: 'A softer view in low light' },
    status: { unknown: 'Status unavailable', connecting: 'Connecting', online: 'Connected', offline: 'Disconnected' },
    pages: {
      overview: 'Overview', voice: 'Voice', vision: 'Vision', apps: 'Apps', device: 'Device', settings: 'Settings',
    },
    descriptions: {
      overview: 'See how your robot is doing and start an interaction.',
      voice: 'Talk with your robot and manage audio input and playback.',
      vision: 'See through your robot’s camera and explore what’s in view.',
      apps: 'Choose an app and let your robot respond to you.',
      device: 'Check device health and manage network and system services.',
      settings: 'Configure cloud services and your robot’s preferences.',
    },
  },
}

const { locale } = useI18n({ useScope: 'global' })
const { t } = useI18n({ useScope: 'local', inheritLocale: true, messages })
const route = useRoute()
const slots = useSlots()
const mainId = useId()
const moreId = useId()
const dialogTitleId = useId()
const titleElement = ref<HTMLHeadingElement>()
const moreDialog = ref<HTMLDialogElement>()
const moreOpen = ref(false)
const localeKey = 'radxa-duck.ui.locale'
const themeKey = 'radxa-duck.ui.theme'

const pages = [
  { id: 'overview', icon: LayoutDashboard }, { id: 'voice', icon: Mic },
  { id: 'vision', icon: Camera }, { id: 'apps', icon: Blocks },
  { id: 'device', icon: Cpu }, { id: 'settings', icon: Settings2 },
] as const
const currentPage = computed(() => pages.find(page =>
  route.matched.some(record => record.name === page.id)) ?? pages[0])
const moreActive = computed(() => ['device', 'settings'].includes(currentPage.value.id))

function readPreference(key: string): string | null {
  try { return window.localStorage.getItem(key) } catch { return null }
}

function savePreference(key: string, value: string): void {
  // Preferences remain usable in memory when browser storage is unavailable.
  try { window.localStorage.setItem(key, value) } catch { /* No persistent storage. */ }
}

function isLocale(value: unknown): value is Locale {
  return value === 'zh-CN' || value === 'en'
}

function isTheme(value: unknown): value is Theme {
  return value === 'system' || value === 'light' || value === 'dark'
}

function browserLocale(): Locale {
  if (typeof navigator !== 'undefined') {
    for (const language of navigator.languages) {
      if (/^zh(?:-|$)/i.test(language)) return 'zh-CN'
      if (/^en(?:-|$)/i.test(language)) return 'en'
    }
  }
  return 'en'
}

const savedLocale = readPreference(localeKey)
locale.value = isLocale(savedLocale) ? savedLocale : browserLocale()
const savedTheme = readPreference(themeKey)
const theme = ref<Theme>(isTheme(savedTheme) ? savedTheme : 'system')
const darkQuery = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null
const desktopQuery = typeof window !== 'undefined' ? window.matchMedia('(min-width: 960px)') : null
const systemDark = ref(darkQuery?.matches ?? false)
const resolvedTheme = computed(() => theme.value === 'system'
  ? (systemDark.value ? 'dark' : 'light') : theme.value)
const languageOptions = [
  { value: 'zh-CN', label: '简体中文', description: 'Simplified Chinese', lang: 'zh-CN' },
  { value: 'en', label: 'English', description: '英语', lang: 'en' },
]
const themeOptions = computed(() => [
  { value: 'light', label: t('light'), description: t('themeHints.light'), icon: Sun },
  { value: 'dark', label: t('dark'), description: t('themeHints.dark'), icon: Moon },
  { value: 'system', label: t('system'), description: t('themeHints.system'), icon: Monitor },
])

function selectLocale(value: string): void {
  if (isLocale(value)) { locale.value = value; savePreference(localeKey, value) }
}

function selectTheme(value: string): void {
  if (isTheme(value)) { theme.value = value; savePreference(themeKey, value) }
}

function syncStorage(event: StorageEvent): void {
  if (event.key === localeKey || event.key === null) {
    locale.value = isLocale(event.newValue) ? event.newValue : browserLocale()
  }
  if (event.key === themeKey || event.key === null) {
    theme.value = isTheme(event.newValue) ? event.newValue : 'system'
  }
}

function syncSystemTheme(event: MediaQueryListEvent): void { systemDark.value = event.matches }
function closeMore(): void { moreDialog.value?.close(); moreOpen.value = false }
function openMore(): void { moreDialog.value?.showModal(); moreOpen.value = true }
function dismissBackdrop(event: MouseEvent): void {
  if (event.target === event.currentTarget) closeMore()
}
function keepMoreFocus(event: KeyboardEvent): void {
  if (event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey) return
  const controls = moreDialog.value?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href]')
  if (!controls?.length) return
  const first = controls[0]
  const last = controls[controls.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault(); last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first?.focus()
  }
}
function syncDesktop(event: MediaQueryListEvent): void {
  if (event.matches && moreOpen.value) { closeMore(); titleElement.value?.focus({ preventScroll: true }) }
}

watch([locale, resolvedTheme], ([language, appearance]) => {
  if (typeof document === 'undefined') return
  document.documentElement.lang = language
  document.documentElement.dataset.theme = appearance
  document.documentElement.style.colorScheme = appearance
}, { immediate: true, flush: 'sync' })

let hadRoute = route.matched.length > 0
watch(() => route.fullPath, async () => {
  const moveFocus = hadRoute
  hadRoute = route.matched.length > 0
  closeMore()
  if (!moveFocus) return
  await nextTick()
  titleElement.value?.focus({ preventScroll: true })
})

onMounted(() => {
  darkQuery?.addEventListener('change', syncSystemTheme)
  desktopQuery?.addEventListener('change', syncDesktop)
  window.addEventListener('storage', syncStorage)
})
onBeforeUnmount(() => {
  darkQuery?.removeEventListener('change', syncSystemTheme)
  desktopQuery?.removeEventListener('change', syncDesktop)
  window.removeEventListener('storage', syncStorage)
  closeMore()
})
</script>

<template>
  <div class="app-shell" :data-theme="resolvedTheme">
    <a class="skip-link" :href="`#${mainId}`">{{ t('skip') }}</a>
    <aside class="sidebar">
      <RouterLink class="brand" :to="{ name: 'overview' }">
        <!-- Radxa X: official 2023 media kit, radxa-X/radxa_x_green.svg.
             https://dl.radxa.com/media/logo/radxa-media-kit-2023.zip
             Original geometry, transforms and color retained; editor metadata omitted. -->
        <svg class="brand-mark" viewBox="0 0 48 45" aria-hidden="true" focusable="false" fill="#79bd28">
          <path transform="matrix(.35277777,0,0,-.35277777,13.458508,1.8330813)" d="M 0,0 C 4.784,-6.043 25.336,-32.017 29.694,-37.522 L 9.921,-57.295 c -11.812,9.349 -35.896,28.406 -47.831,37.852 -0.138,0.109 -0.139,0.292 -0.014,0.417 l 21.095,21.095 c 4.516,4.516 11.96,4.083 15.923,-0.925 z" />
          <path transform="matrix(.35277777,0,0,-.35277777,3.7214527,32.520554)" d="M0 0C6.043 4.784 32.017 25.336 37.522 29.694L57.295 9.921C49.953.646 36.626-16.197 26.667-28.782c-3.953-4.996-11.369-5.414-15.873-.91L-10.549-8.348Z" />
          <path transform="matrix(.35277777,0,0,-.35277777,34.408961,42.257604)" d="M0 0C-4.784 6.043-25.336 32.017-29.694 37.522L-9.921 57.295C1.891 47.946 25.975 28.889 37.91 19.443c.138-.109.139-.292.014-.417L16.829-2.068C12.313-6.585 4.869-6.152.906-1.144Z" />
          <path transform="matrix(.35277777,0,0,-.35277777,44.146011,11.570101)" d="M0 0c-6.043-4.784-32.017-25.336-37.522-29.694l-19.773 19.773c7.288 9.208 20.476 25.874 30.411 38.429 4.203 5.31 11.549 5.724 16.338.935L10.549 8.348Z" />
        </svg>
        <span><strong>Radxa Duck<span class="brand-period">.</span></strong><small>{{ t('console') }}</small></span>
      </RouterLink>
      <nav class="desktop-nav" :aria-label="t('navigation')">
        <span class="eyebrow nav-label">{{ t('workspace') }}</span>
        <RouterLink v-for="page in pages" :key="page.id" :to="{ name: page.id }" class="nav-link" active-class="is-active">
          <component :is="page.icon" :size="20" :stroke-width="1.7" aria-hidden="true" />
          <span>{{ t(`pages.${page.id}`) }}</span>
          <span class="nav-marker" aria-hidden="true"></span>
        </RouterLink>
      </nav>
      <div class="sidebar-device">
        <span class="eyebrow">{{ t('currentDevice') }}</span>
        <div class="device-name"><Cpu :size="19" aria-hidden="true" /><span>{{ props.deviceName }}</span></div>
        <small>{{ t('local') }}</small>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumb"><span>Radxa Duck</span><ChevronRight :size="14" aria-hidden="true" /><span>{{ t(`pages.${currentPage.id}`) }}</span></div>
        <div class="preferences" role="group" :aria-label="t('preferences')">
          <PreferenceSelect :label="t('language')" :model-value="locale" :options="languageOptions" :icon="Globe2" align="start" @update:model-value="selectLocale" />
          <PreferenceSelect :label="t('appearance')" :model-value="theme" :options="themeOptions" @update:model-value="selectTheme" />
        </div>
      </header>
      <div v-if="slots.activity" class="activity"><slot name="activity" /></div>
      <main :id="mainId" class="main" tabindex="-1">
        <div class="page-heading">
          <div class="page-intro"><p class="eyebrow">{{ t('workspace') }}</p><h1 ref="titleElement" tabindex="-1">{{ t(`pages.${currentPage.id}`) }}</h1><p class="page-description">{{ t(`descriptions.${currentPage.id}`) }}</p></div>
          <div class="page-heading-end">
            <span class="connection" :data-state="props.connection" role="status"><span class="connection-dot" aria-hidden="true"></span>{{ t(`status.${props.connection}`) }}</span>
            <slot name="actions" />
          </div>
        </div>
        <slot />
      </main>
      <footer class="workspace-footer"><span>Radxa Duck</span><span>{{ t('local') }}</span></footer>
    </div>

    <nav class="mobile-nav" :aria-label="t('navigation')">
      <RouterLink v-for="page in pages.slice(0, 4)" :key="page.id" :to="{ name: page.id }" class="mobile-link" active-class="is-active">
        <component :is="page.icon" :size="21" :stroke-width="1.7" aria-hidden="true" /><span>{{ t(`pages.${page.id}`) }}</span>
      </RouterLink>
      <button class="mobile-link" :class="{ 'is-active': moreActive }" :aria-expanded="moreOpen" :aria-controls="moreId" aria-haspopup="dialog" @click="openMore">
        <MoreHorizontal :size="21" aria-hidden="true" /><span>{{ t('more') }}</span>
      </button>
    </nav>

    <dialog :id="moreId" ref="moreDialog" class="more-dialog" :aria-labelledby="dialogTitleId" @close="moreOpen = moreDialog?.open ?? false" @click="dismissBackdrop" @keydown="keepMoreFocus">
      <div class="more-panel">
        <div class="more-heading"><h2 :id="dialogTitleId">{{ t('more') }}</h2><button class="icon-button" :aria-label="t('close')" @click="closeMore"><X :size="22" aria-hidden="true" /></button></div>
        <nav :aria-label="t('more')">
          <RouterLink v-for="page in pages.slice(4)" :key="page.id" :to="{ name: page.id }" class="more-link" @click="closeMore">
            <component :is="page.icon" :size="22" aria-hidden="true" /><span>{{ t(`pages.${page.id}`) }}</span><ChevronRight :size="18" aria-hidden="true" />
          </RouterLink>
        </nav>
      </div>
    </dialog>
  </div>
</template>

<style scoped>
:global(body) { margin: 0; }
.app-shell {
  --background: #f4f6f2; --surface: #fff; --sidebar: #fcfdfb; --text: #243020;
  --muted: #64705e; --border: #e1e7db; --accent: #426f25; --accent-soft: #eaf3df;
  --focus: #477f1c; --status: #65705f; --online: #387b28; --offline: #b44632;
  --radius-control: 16px; --radius-panel: 24px;
  --shadow-soft: 0 4px 18px -8px rgb(36 48 25 / .09), 0 1px 3px rgb(36 48 25 / .025);
  --shadow-float: 0 16px 32px -12px rgb(36 48 25 / .16), 0 4px 10px -5px rgb(36 48 25 / .06);
  --ease-out: cubic-bezier(.22, 1, .36, 1); --motion-fast: 180ms;
  --font: Inter, "Noto Sans CJK SC", "Microsoft YaHei", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  display: flex; gap: 8px; min-height: 100dvh; padding: 16px; color: var(--text); background: var(--background);
  font: 400 14px/1.6 var(--font); color-scheme: light;
}
.app-shell[data-theme="dark"] {
  --background: #141913; --surface: #1d241a; --sidebar: #1b2218; --text: #edf2e8;
  --muted: #afbaa8; --border: #35412e; --accent: #b1df83; --accent-soft: #2b3c20;
  --focus: #b1df83; --status: #acb7a5; --online: #9bd775; --offline: #ff9d88; color-scheme: dark;
  --shadow-soft: 0 4px 18px -8px rgb(0 0 0 / .24), 0 1px 3px rgb(0 0 0 / .08);
  --shadow-float: 0 16px 32px -12px rgb(0 0 0 / .4), 0 4px 10px -5px rgb(0 0 0 / .18);
}
.app-shell { box-sizing: border-box; }
.app-shell :deep(*) { box-sizing: border-box; }
a { color: inherit; text-decoration: none; }
button { font: inherit; color: inherit; cursor: pointer; touch-action: manipulation; }
button { border: 0; }
a:focus-visible, button:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
.skip-link { position: fixed; left: 20px; top: -100px; z-index: 50; padding: 12px 20px; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-control); }
.skip-link:focus { top: 12px; }
.sidebar { position: sticky; top: 16px; display: flex; flex-direction: column; flex: 0 0 232px; height: calc(100dvh - 32px); padding: 30px 16px 16px; border: 1px solid var(--border); border-radius: 28px; background: var(--sidebar); box-shadow: var(--shadow-soft); overflow-y: auto; }
.brand { display: flex; gap: 12px; align-items: center; margin: 0 8px 44px; border-radius: 12px; }
.brand-mark { width: 32px; height: 30px; flex-shrink: 0; }
.brand strong { font-size: 20px; font-weight: 650; letter-spacing: -.7px; white-space: nowrap; }
.brand-period { color: var(--accent); }
.brand small { display: block; margin-top: 2px; font-size: 12px; color: var(--muted); letter-spacing: .04em; }
.eyebrow { color: var(--muted); font-size: 12px; font-weight: 500; letter-spacing: .08em; }
.nav-label { display: block; margin: 0 14px 14px; }
.nav-link { display: flex; align-items: center; gap: 13px; min-height: 50px; margin-bottom: 8px; padding: 11px 14px; border: 1px solid transparent; border-radius: var(--radius-control); color: var(--muted); }
.nav-link.is-active { color: var(--accent); background: var(--accent-soft); border-color: color-mix(in srgb, var(--accent) 10%, transparent); font-weight: 600; }
.nav-marker { width: 5px; height: 5px; margin-left: auto; border-radius: 50%; background: transparent; }
.is-active .nav-marker { background: currentColor; }
.sidebar-device { margin-top: auto; padding: 18px 14px; border: 1px solid var(--border); border-radius: 20px; background: var(--background); }
.device-name { display: flex; align-items: flex-start; gap: 9px; margin-top: 12px; font-weight: 600; overflow-wrap: anywhere; }
.device-name svg { flex-shrink: 0; margin-top: 3px; }
.sidebar-device small { display: block; margin: 5px 0 0 28px; color: var(--muted); font-size: 12px; }
.workspace { display: flex; flex: 1; min-width: 0; flex-direction: column; }
.topbar { min-height: 72px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px 24px; width: 100%; max-width: 1320px; margin-inline: auto; padding: 10px 28px; }
.breadcrumb { display: flex; align-items: center; gap: 12px; font-size: 13px; color: var(--muted); }
.breadcrumb > span:last-child { color: var(--text); font-weight: 500; }
.preferences { display: flex; flex-wrap: wrap; gap: 10px; }
.activity { width: calc(100% - 56px); max-width: 1264px; margin: 10px auto 0; padding: 12px 16px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-control); font-size: 12px; color: var(--muted); }
.main { flex: 1; width: 100%; max-width: 1320px; padding: 32px 28px 24px; margin-inline: auto; }
.page-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 20px; margin-bottom: 32px; }
.page-intro { min-width: 0; flex: 1 1 260px; }
.page-intro > .eyebrow { margin: 0 0 8px; }
h1 { width: fit-content; max-width: 100%; margin: 0; font-size: 32px; line-height: 1.3; font-weight: 650; letter-spacing: -.035em; overflow-wrap: anywhere; }
.page-description { margin: 10px 0 0; color: var(--muted); font-size: 14px; line-height: 1.75; }
.page-heading-end { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.connection { display: inline-flex; align-items: center; gap: 8px; max-width: 100%; padding: 8px 14px; background: var(--surface); border: 1px solid var(--border); border-radius: 100px; box-shadow: var(--shadow-soft); color: var(--status); font-size: 12px; }
.connection-dot { flex-shrink: 0; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.connection[data-state="online"] { color: var(--online); }
.connection[data-state="offline"] { color: var(--offline); }
.workspace-footer { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px; width: 100%; max-width: 1320px; margin-inline: auto; padding: 18px 28px; color: var(--muted); font-size: 12px; }
.mobile-nav { display: none; }
.more-dialog { position: fixed; inset: auto 0 12px; width: min(calc(100% - 24px), 520px); max-width: 100%; max-height: calc(100dvh - 24px); margin: 0 auto; padding: 0; overflow-y: auto; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 28px; box-shadow: var(--shadow-float); }
.more-dialog::backdrop { background: rgb(9 15 7 / .5); }
.more-panel { padding: 20px 20px calc(24px + env(safe-area-inset-bottom)); }
.more-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.more-heading h2 { margin: 0; font-size: 20px; font-weight: 600; }
.icon-button { display: grid; place-items: center; width: 44px; height: 44px; border-radius: var(--radius-control); background: var(--background); }
.more-link { display: flex; align-items: center; gap: 14px; min-height: 60px; padding: 12px; border-radius: var(--radius-control); }
.nav-link:active, .mobile-link:active, .more-link:active, .icon-button:active { background: var(--accent-soft); color: var(--accent); }
.more-link svg:last-child { margin-left: auto; }
@media (max-width: 959px) {
  :global(html) { scroll-padding-bottom: calc(100px + env(safe-area-inset-bottom)); }
  .app-shell { gap: 0; padding: 0 0 calc(100px + env(safe-area-inset-bottom)); }
  .sidebar { display: none; }
  .topbar { min-height: 80px; padding: 18px 24px 12px; }
  .breadcrumb > span:first-child { font-size: 16px; font-weight: 650; letter-spacing: -.5px; color: var(--text); }
  .main { padding: 28px 24px; }
  .activity { width: calc(100% - 48px); margin-top: 8px; padding: 12px 16px; }
  .workspace-footer { padding: 16px 24px; }
  .mobile-nav { position: fixed; z-index: 20; inset: auto 12px calc(12px + env(safe-area-inset-bottom)); max-width: 560px; margin-inline: auto; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 4px; padding: 8px; background: var(--surface); border: 1px solid var(--border); border-radius: 24px; box-shadow: var(--shadow-float); }
  .mobile-link { display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 4px; min-height: 56px; padding: 6px 2px; background: transparent; color: var(--muted); border-radius: var(--radius-control); font-size: 11px; line-height: 1.5; text-align: center; overflow-wrap: anywhere; }
  .mobile-link.is-active { color: var(--accent); background: var(--accent-soft); font-weight: 600; }
  .page-heading { align-items: flex-start; gap: 16px; margin-bottom: 24px; }
}
@media (max-width: 539px) {
  .topbar { padding: 22px 20px 12px; gap: 16px; }
  .breadcrumb { flex-basis: 100%; }
  .preferences { width: 100%; }
  .preferences > * { flex: 1; min-width: 0; }
  .activity { width: calc(100% - 40px); }
  .main { padding: 26px 20px; }
  h1 { font-size: 28px; }
  .page-intro { flex-basis: 100%; }
  .mobile-nav { padding-inline: 8px; }
}
@media (prefers-reduced-motion: no-preference) {
  .nav-link, .mobile-link, .more-link, .icon-button {
    transition: transform var(--motion-fast) var(--ease-out), box-shadow var(--motion-fast) ease, background-color var(--motion-fast) ease, color var(--motion-fast) ease;
  }
  .page-heading { animation: shell-enter 360ms var(--ease-out) both; }
  .more-dialog[open] .more-panel { animation: shell-enter 240ms var(--ease-out) both; }
  @keyframes shell-enter { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
}
@media (hover: hover) and (pointer: fine) {
  .nav-link:hover, .more-link:hover, .icon-button:hover { background: var(--accent-soft); color: var(--accent); box-shadow: var(--shadow-soft); }
  .mobile-link:hover { color: var(--accent); background: var(--accent-soft); }
}
@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .nav-link:hover, .more-link:hover, .icon-button:hover, .mobile-link:hover { transform: translateY(-2px); }
  .nav-link:active, .more-link:active, .icon-button:active, .mobile-link:active { transform: translateY(0); }
}
</style>
