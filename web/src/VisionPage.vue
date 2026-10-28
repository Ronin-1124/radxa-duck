<script setup lang="ts">
import VisionWorkspace from './VisionWorkspace.vue'
import { useVisionSession } from './useVisionSession'

const emit = defineEmits<{ connection: [value: 'unknown' | 'connecting' | 'online' | 'offline'] }>()
const { state, start, stop, displayed, release, decodeError } = useVisionSession()
</script>

<template>
  <VisionWorkspace :state="state" @start="start" @stop="stop" @displayed="displayed"
    @release="release" @decode-error="decodeError" @connection="emit('connection', $event)" />
</template>
