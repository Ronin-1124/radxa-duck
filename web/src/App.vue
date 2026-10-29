<script setup lang="ts">
import { RouterView } from 'vue-router'
import AppShell from './AppShell.vue'
import { provideUiPreferences } from './useUiPreferences'
import { provideRobotConsole } from './useRobotConsole'
import { provideViewPreferences } from './useViewPreferences'
import './ui.css'
import './console.css'

provideUiPreferences()
provideRobotConsole()
provideViewPreferences()
</script>

<template>
  <AppShell>
    <RouterView v-slot="{ Component, route }">
      <Transition name="console-route" mode="out-in">
        <component :is="Component" :key="route.name" />
      </Transition>
    </RouterView>
  </AppShell>
</template>
