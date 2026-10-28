<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import AppShell from './AppShell.vue'

const route = useRoute()
const connection = ref<'unknown' | 'connecting' | 'online' | 'offline'>('unknown')

function updateConnection(value: unknown): void {
  if ((route.name === 'voice' || route.name === 'vision' || route.name === 'device' || route.name === 'settings')
    && (value === 'unknown' || value === 'connecting' || value === 'online' || value === 'offline')) {
    connection.value = value
  }
}

// Leaving the voice route unmounts the component and closes its owned socket.
// Do not keep this view alive: disconnecting is part of its capture-stop contract.
watch(() => route.name, () => { connection.value = 'unknown' }, { flush: 'sync' })
</script>

<template>
  <AppShell :connection="connection">
    <div :key="String(route.name)" class="route-content">
      <RouterView v-slot="{ Component }">
        <component :is="Component" @connection="updateConnection" />
      </RouterView>
    </div>
  </AppShell>
</template>
