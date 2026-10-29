<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Activity, Camera, Settings2 } from '@lucide/vue'
import RadxaMark from './RadxaMark.vue'
import ConsolePreferences from './ConsolePreferences.vue'
import ConsoleSettings from './ConsoleSettings.vue'
import { useRobotConsole } from './useRobotConsole'
const { connection } = useRobotConsole()
const route = useRoute(), router = useRouter()
const settingsButton = ref<HTMLButtonElement>()
const settingsOpen = computed(() => route.query.settings === '1')
const settingsTab = computed(() => typeof route.query.tab === 'string' ? route.query.tab : 'identity')
const { t } = useI18n({ useScope:'local', messages:{
 'zh-CN':{state:'状态',view:'视角',settings:'设置',language:'语言',appearance:'外观',light:'浅色',dark:'深色',system:'跟随系统',online:'网页已连接',offline:'网页已断连',connecting:'正在连接',navigation:'主视图',skip:'跳到主要内容'},
 en:{state:'Status',view:'View',settings:'Settings',language:'Language',appearance:'Appearance',light:'Light',dark:'Dark',system:'System',online:'Web connected',offline:'Web disconnected',connecting:'Connecting',navigation:'Main views',skip:'Skip to content'}
} })
function openSettings() { void router.push({ query:{...route.query, settings:'1'} }) }
function closeSettings() { const query = {...route.query}; delete query.settings; delete query.tab; void router.replace({query}) }
</script>
<template>
  <div class="console-shell">
    <a class="console-skip" href="#console-main">{{ t('skip') }}</a>
    <header class="console-header">
      <RouterLink :to="{name:'overview'}" class="console-brand" aria-label="Radxa Duck"><RadxaMark /><span>Radxa Duck<span class="brand-dot">.</span></span></RouterLink>
      <nav class="view-switch" :data-view="route.name === 'vision' ? 'vision' : 'overview'" :aria-label="t('navigation')">
        <span class="switch-indicator" aria-hidden="true"></span>
        <RouterLink :to="{name:'overview'}" :aria-current="route.name === 'overview' ? 'page' : undefined"><Activity :size="17" aria-hidden="true" />{{ t('state') }}</RouterLink>
        <RouterLink :to="{name:'vision'}" :aria-current="route.name === 'vision' ? 'page' : undefined"><Camera :size="17" aria-hidden="true" />{{ t('view') }}</RouterLink>
      </nav>
      <div class="web-connection" :data-state="connection" role="status"><span aria-hidden="true"></span>{{ t(connection) }}</div>
    </header>
    <main id="console-main" class="console-main" tabindex="-1"><slot /></main>
    <div class="utility-dock" :aria-label="t('settings')" role="group">
      <button ref="settingsButton" type="button" class="dock-settings" :aria-label="t('settings')" :title="t('settings')" :aria-expanded="settingsOpen" aria-haspopup="dialog" @click="openSettings"><Settings2 :size="20" aria-hidden="true" /></button>
      <span class="dock-divider" aria-hidden="true"></span>
      <ConsolePreferences />
    </div>
    <ConsoleSettings v-if="settingsOpen" :initial-tab="settingsTab" :opener="settingsButton" @close="closeSettings" />
  </div>
</template>
<style scoped>
.console-shell { min-height:100dvh; position:relative; isolation:isolate; }
.console-shell::before { content:''; position:fixed; inset:0; z-index:-1; pointer-events:none; background:radial-gradient(ellipse at 50% 10%,color-mix(in srgb,var(--accent) 3%,transparent),transparent 65%); }
.console-header { display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:24px; padding:28px clamp(24px,4vw,64px); min-height:112px; }
.console-brand { display:inline-flex; align-items:center; gap:11px; width:fit-content; text-decoration:none; }
.console-brand svg { width:30px; height:30px; }
.console-brand > span { font-size:20px; font-weight:650; letter-spacing:-.8px; white-space:nowrap; }
.brand-dot { color:var(--accent); }
.view-switch { display:grid; grid-template-columns:repeat(2,minmax(100px,1fr)); position:relative; padding:5px; gap:4px; border:1px solid var(--border); border-radius:20px; background:color-mix(in srgb,var(--surface) 62%,var(--background)); }
.view-switch a { display:flex; justify-content:center; align-items:center; gap:9px; min-height:44px; position:relative; z-index:1; padding:0 22px; text-decoration:none; font-size:13px; font-weight:500; color:var(--muted); border-radius:15px; }
.view-switch a[aria-current="page"] { color:var(--text); }
.switch-indicator { position:absolute; left:5px; top:5px; bottom:5px; width:calc((100% - 14px)/2); background:var(--surface); border:1px solid var(--border); border-radius:15px; box-shadow:var(--shadow-soft); }
[data-view="vision"] .switch-indicator { transform:translateX(calc(100% + 4px)); }
.web-connection { justify-self:end; display:flex; align-items:center; gap:8px; color:var(--muted); font-size:12px; white-space:nowrap; }
.web-connection > span { width:6px; height:6px; border-radius:50%; background:var(--status); box-shadow:0 0 0 4px color-mix(in srgb,var(--status) 7%,transparent); }
.web-connection[data-state="online"] > span { background:var(--online); box-shadow:0 0 0 4px color-mix(in srgb,var(--online) 8%,transparent); }
.web-connection[data-state="offline"] > span { background:var(--offline); }
.console-main { padding:28px clamp(20px,4vw,64px) 120px; outline:none; }
.utility-dock { position:fixed; bottom:max(24px,env(safe-area-inset-bottom)); left:max(24px,env(safe-area-inset-left)); display:flex; gap:5px; align-items:center; z-index:50; padding:5px; border:1px solid var(--border); border-radius:20px; background:color-mix(in srgb,var(--surface) 90%,transparent); backdrop-filter:blur(20px); box-shadow:var(--shadow-soft); }
.dock-settings { display:grid; place-items:center; width:46px; height:46px; border:0; border-radius:15px; color:var(--muted); background:transparent; cursor:pointer; }
.dock-divider { width:1px; height:20px; background:var(--border); margin-inline:2px; }
.console-skip { position:fixed; top:10px; left:10px; padding:12px; border-radius:12px; background:var(--surface); color:var(--text); transform:translateY(-180%); z-index:200; }
.console-skip:focus { transform:none; }
@media (max-width:639px) {
 .console-header { grid-template-columns:1fr auto; gap:24px 12px; padding:24px 20px 12px; }
 .console-brand > span { font-size:18px; }
 .console-brand svg { width:26px; height:26px; }
 .view-switch { grid-column:1 / -1; grid-row:2; justify-self:center; }
 .view-switch a { min-width:110px; }
 .web-connection { font-size:11px; }
 .console-main { padding:24px 16px 112px; }
 .utility-dock { left:max(16px,env(safe-area-inset-left)); bottom:max(16px,env(safe-area-inset-bottom)); }
}
@media (hover:hover) and (pointer:fine) { .dock-settings:hover { color:var(--accent); background:var(--accent-soft); } .view-switch a:hover { color:var(--accent); } }
@media (prefers-reduced-motion:no-preference) { .switch-indicator { transition:transform .5s var(--ease-out); } .dock-settings { transition:transform .3s var(--ease-out),background .2s; } }
@media (hover:hover) and (pointer:fine) and (prefers-reduced-motion:no-preference) { .dock-settings:hover { transform:translateY(-2px); } }
</style>
