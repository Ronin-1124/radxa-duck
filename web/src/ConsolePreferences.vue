<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Globe2, Monitor, Moon, Sun } from '@lucide/vue'
import PreferenceSelect from './PreferenceSelect.vue'
import { useUiPreferences } from './useUiPreferences'
const { locale,theme,selectLocale,selectTheme }=useUiPreferences()
const {t}=useI18n({useScope:'local',messages:{
 'zh-CN':{language:'语言',appearance:'外观',light:'浅色',dark:'深色',system:'跟随系统'},
 en:{language:'Language',appearance:'Appearance',light:'Light',dark:'Dark',system:'System'}
}})
const languages=[{value:'zh-CN',label:'简体中文',lang:'zh-CN'},{value:'en',label:'English',lang:'en'}]
const themes=computed(()=>[{value:'light',label:t('light'),icon:Sun},{value:'dark',label:t('dark'),icon:Moon},{value:'system',label:t('system'),icon:Monitor}])
</script>
<template>
 <div class="console-preferences">
  <PreferenceSelect compact placement="top" align="start" :label="t('language')" :model-value="locale" :options="languages" :icon="Globe2" @update:model-value="selectLocale" />
  <PreferenceSelect compact placement="top" align="start" :label="t('appearance')" :model-value="theme" :options="themes" @update:model-value="selectTheme" />
 </div>
</template>
<style scoped>
.console-preferences { display:flex; align-items:center; gap:5px; }
</style>
