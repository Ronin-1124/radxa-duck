<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AudioLines, Mic, Volume2 } from '@lucide/vue'
import { useRobotConsole } from './useRobotConsole'
const { brain, fresh } = useRobotConsole()
const { t, locale } = useI18n({ useScope: 'local', messages: {
  'zh-CN': { title: '语音反馈', input: '收音', synthesis: '语音合成', playback: '外放', unknown: '状态未知',
    idle: '待命', listening: '正在收音', synthesizing: '正在合成', playing: '正在播放', succeeded: '已完成', failed: '失败', cancelled: '已取消', skipped: '已跳过', unavailable: '语音回复不可用',
    reasons: { maintenance: '维护期间暂停', disabled: '麦克风未启用', error: '因异常暂停', interaction_paused: '交互已暂停', synthesis: '合成期间暂停', playback: '播放期间暂停', answer_generation: '等待模型回答', starting: '正在准备收音' },
    errors: { tts_unavailable: '语音合成未配置', missing_dependency: '语音依赖不可用', model_mismatch: '语音模型不匹配', synthesis_failed: '语音合成失败', synthesis_timeout: '语音合成超时', invalid_audio: '合成音频无效', device_busy: '音频设备被占用', device_unavailable: '音频设备不可用', playback_failed: '外放失败', cancelled: '已取消', fallback: '语音处理异常' } },
  en: { title: 'Voice feedback', input: 'Audio input', synthesis: 'Synthesis', playback: 'Playback', unknown: 'State unknown',
    idle: 'Idle', listening: 'Capturing audio', synthesizing: 'Synthesizing', playing: 'Playing', succeeded: 'Completed', failed: 'Failed', cancelled: 'Cancelled', skipped: 'Skipped', unavailable: 'Voice reply unavailable',
    reasons: { maintenance: 'Paused for maintenance', disabled: 'Microphone disabled', error: 'Paused after an error', interaction_paused: 'Interaction paused', synthesis: 'Paused during synthesis', playback: 'Paused during playback', answer_generation: 'Awaiting model answer', starting: 'Preparing audio input' },
    errors: { tts_unavailable: 'Synthesis not configured', missing_dependency: 'Voice dependency unavailable', model_mismatch: 'Voice model mismatch', synthesis_failed: 'Synthesis failed', synthesis_timeout: 'Synthesis timed out', invalid_audio: 'Invalid synthesized audio', device_busy: 'Audio device is busy', device_unavailable: 'Audio device unavailable', playback_failed: 'Playback failed', cancelled: 'Cancelled', fallback: 'Voice processing error' } },
} })
const rows = computed(() => {
  const b = fresh.value ? brain.value : null, voice = b?.voice
  const reason = voice?.input.pause_reason
  const input = !voice ? t('unknown') : b?.microphone.active ? t('listening') : reason ? t(`reasons.${reason}`,t('unknown')) : t('idle')
  return [
    { key: 'input', icon: Mic, value: input, error: voice?.input.error_code, updated: null, state: voice?.input.error_code ? 'failed' : b?.microphone.active ? 'running' : 'idle' },
    ...(['synthesis', 'playback'] as const).map(key => {
      const stage = voice?.[key]
      const related = !b?.task || stage?.task_id === b.task.id
      const state = !voice || !stage ? 'unknown' : !related ? 'idle' : stage.state
      return { key, icon: key === 'synthesis' ? AudioLines : Volume2,
        value: t(!voice ? 'unknown' : state === 'failed' ? 'failed' : !voice.reply_available ? 'unavailable' : state === 'running' ? key === 'synthesis' ? 'synthesizing' : 'playing' : state),
        error: related ? stage?.error_code : null, updated: related ? stage?.updated_at : null, state }
    }),
  ]
})
function time(value: string) { return new Intl.DateTimeFormat(locale.value,{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date(value)) }
</script>

<template>
  <section v-if="brain?.voice" class="voice-feedback console-panel" :aria-label="t('title')">
    <div v-for="row in rows" :key="row.key" class="voice-stage" :data-stage="row.key" :data-state="row.state">
      <span class="console-label"><component :is="row.icon" :size="15" :stroke-width="1.6" aria-hidden="true" />{{ t(row.key) }}</span>
      <strong role="status">{{ row.value }}</strong>
      <span v-if="row.error" class="voice-error" role="status">{{ t(`errors.${row.error}`,t('errors.fallback')) }}</span>
      <time v-else-if="row.updated" :datetime="row.updated">{{ time(row.updated) }}</time>
    </div>
  </section>
</template>

<style scoped>
.voice-feedback { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); padding:24px 28px; gap:24px; }
.voice-stage { min-width:0; }
.voice-stage + .voice-stage { padding-left:24px; border-left:1px solid var(--border); }
.console-label { display:flex; align-items:center; gap:8px; }
strong { display:block; margin-top:12px; font-size:13px; font-weight:500; line-height:1.6; overflow-wrap:anywhere; }
time,.voice-error { display:block; color:var(--muted); margin-top:6px; font-size:11px; line-height:1.6; overflow-wrap:anywhere; }
[data-state="running"] strong { color:var(--accent); }
[data-state="failed"] strong,.voice-error { color:var(--offline); }
@media (max-width:539px) { .voice-feedback { grid-template-columns:1fr; gap:16px; padding:20px; } .voice-stage { display:grid; grid-template-columns:1fr 1fr; align-items:start; gap:6px 16px; } .voice-stage + .voice-stage { padding:16px 0 0; border-left:0; border-top:1px solid var(--border); } strong { margin:0; text-align:end; } time,.voice-error { grid-column:1/-1; margin:0; text-align:end; } }
</style>
