<script setup lang="ts">
import { useId } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowRight, ArrowUpRight, Blocks, Camera, Cpu, Mic, ShieldCheck } from '@lucide/vue'

const titleId = useId()
const capabilitiesId = useId()
const { t } = useI18n({
  useScope: 'local',
  inheritLocale: true,
  messages: {
    'zh-CN': {
      title: '等待连接机器人',
      introduction: '连接同一局域网中的小鸭，开始语音、视觉与应用交互。',
      device: '查看设备',
      start: '从这里开始',
      startHint: '连接后，选择一种与小鸭互动的方式。',
      voice: { title: '语音交互', description: '说出想法，让小鸭听见并回应。' },
      vision: { title: '视觉交互', description: '看看小鸭的视角，探索画面中的信息。' },
      apps: { title: '应用空间', description: '对话、看图与陪伴，找到适合你的场景。' },
      privacy: '由你开启，每一次交互',
      privacyHint: '录音与摄像头由你主动开启，媒体与会话不长期保存。',
    },
    en: {
      title: 'Waiting for your robot',
      introduction: 'Connect your Duck on the same local network to explore voice, vision and apps.',
      device: 'View device',
      start: 'Start here',
      startHint: 'Once connected, choose how you would like to interact.',
      voice: { title: 'Voice interaction', description: 'Share a thought and let your Duck respond.' },
      vision: { title: 'Vision interaction', description: 'See through your Duck’s camera and explore the scene.' },
      apps: { title: 'App space', description: 'Find your moment for conversation, images or companionship.' },
      privacy: 'Every interaction starts with you',
      privacyHint: 'You choose when audio and camera capture begin. Media and conversations are temporary.',
    },
  },
})

const capabilities = [
  { name: 'voice', icon: Mic },
  { name: 'vision', icon: Camera },
  { name: 'apps', icon: Blocks },
] as const
</script>

<template>
  <div class="connection-overview">
    <section class="welcome-card" :aria-labelledby="titleId">
      <div class="welcome-copy">
        <p class="welcome-kicker">RADXA DUCK</p>
        <h2 :id="titleId">{{ t('title') }}</h2>
        <p class="welcome-description">{{ t('introduction') }}</p>
        <RouterLink :to="{ name: 'device' }" class="device-link">
          <span>{{ t('device') }}</span><ArrowRight :size="18" aria-hidden="true" />
        </RouterLink>
      </div>
      <div class="device-art" aria-hidden="true">
        <div class="art-orbit"></div>
        <div class="art-orbit art-orbit-inner"></div>
        <div class="art-base"></div>
        <div class="art-device">
          <Cpu :size="58" :stroke-width="1.25" />
          <div class="art-detail"><span></span><span></span><span></span></div>
        </div>
        <div class="art-satellite art-mic"><Mic :size="25" :stroke-width="1.5" /></div>
        <div class="art-satellite art-camera"><Camera :size="25" :stroke-width="1.5" /></div>
        <span class="art-dot art-dot-one"></span><span class="art-dot art-dot-two"></span>
      </div>
    </section>

    <section class="capabilities" :aria-labelledby="capabilitiesId">
      <header class="section-heading">
        <h2 :id="capabilitiesId">{{ t('start') }}</h2>
        <p>{{ t('startHint') }}</p>
      </header>
      <div class="capability-grid">
        <RouterLink v-for="capability in capabilities" :key="capability.name"
          :to="{ name: capability.name }" class="capability-card">
          <span class="capability-icon"><component :is="capability.icon" :size="24" :stroke-width="1.6" aria-hidden="true" /></span>
          <div class="capability-copy">
            <h3>{{ t(`${capability.name}.title`) }}</h3>
            <p>{{ t(`${capability.name}.description`) }}</p>
          </div>
          <ArrowUpRight :size="19" class="capability-arrow" aria-hidden="true" />
        </RouterLink>
      </div>
    </section>

    <div class="privacy-note">
      <span class="privacy-icon"><ShieldCheck :size="20" :stroke-width="1.6" aria-hidden="true" /></span>
      <div><p class="privacy-title">{{ t('privacy') }}</p><p class="privacy-description">{{ t('privacyHint') }}</p></div>
    </div>
  </div>
</template>

<style scoped>
.connection-overview { display: grid; gap: 32px; }
.welcome-card {
  position: relative; display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(220px, .75fr);
  align-items: center; gap: 28px; min-height: 300px; padding: 36px 40px;
  border: 1px solid var(--border); border-radius: var(--radius-panel, 24px);
  background: linear-gradient(115deg, var(--surface) 35%, color-mix(in srgb, var(--accent-soft) 70%, var(--surface)));
  box-shadow: var(--shadow-soft, 0 4px 20px rgb(22 40 16 / .04));
}
.welcome-copy { position: relative; z-index: 1; }
.welcome-kicker { margin: 0 0 14px; color: var(--accent); font-size: 11px; font-weight: 650; letter-spacing: .15em; }
.welcome-copy h2 { margin: 0; max-width: 18ch; color: var(--text); font-size: clamp(26px, 2.4vw, 34px); font-weight: 600; letter-spacing: -.035em; line-height: 1.3; text-wrap: balance; }
.welcome-description { max-width: 40ch; margin: 16px 0 24px; color: var(--muted); font-size: 14px; line-height: 1.8; text-wrap: pretty; }
.device-link {
  display: inline-flex; align-items: center; justify-content: center; gap: 22px; min-height: 46px;
  padding: 12px 18px; border: 1px solid color-mix(in srgb, var(--accent) 22%, var(--border));
  border-radius: var(--radius-control, 16px); background: var(--accent-soft); color: var(--accent);
  font-size: 13px; font-weight: 600; line-height: 1.5; text-decoration: none; touch-action: manipulation;
}
.device-link:focus-visible, .capability-card:focus-visible { outline: 2px solid var(--focus); outline-offset: 4px; }
.device-link:active, .capability-card:active {
  background: color-mix(in srgb, var(--accent-soft) 75%, var(--surface));
  border-color: color-mix(in srgb, var(--accent) 45%, var(--border));
}
.device-art { position: relative; justify-self: center; display: grid; place-items: center; width: 260px; max-width: 100%; height: 240px; }
.art-orbit { position: absolute; width: 232px; height: 232px; max-width: 100%; border: 1px solid color-mix(in srgb, var(--accent) 10%, transparent); border-radius: 50%; }
.art-orbit-inner { width: 186px; height: 186px; border: 0; background: color-mix(in srgb, var(--accent-soft) 75%, transparent); }
.art-base { position: absolute; width: 130px; height: 130px; border-radius: 34px; background: color-mix(in srgb, var(--accent) 10%, var(--surface)); transform: translate(9px, 10px) rotate(-9deg); }
.art-device {
  position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px;
  width: 130px; height: 130px; border: 1px solid color-mix(in srgb, var(--accent) 14%, var(--border)); border-radius: 34px;
  background: var(--surface); color: var(--accent); box-shadow: 0 14px 30px -18px color-mix(in srgb, var(--accent) 45%, transparent);
  transform: rotate(-9deg);
}
.art-detail { display: flex; gap: 5px; }
.art-detail span { width: 5px; height: 5px; border-radius: 50%; background: color-mix(in srgb, var(--accent) 35%, var(--surface)); }
.art-detail span:first-child { width: 17px; border-radius: 5px; background: var(--accent); }
.art-satellite { position: absolute; display: grid; place-items: center; width: 56px; height: 56px; border: 1px solid var(--border); border-radius: 19px; background: var(--surface); color: var(--accent); box-shadow: var(--shadow-soft, 0 4px 20px rgb(22 40 16 / .04)); }
.art-mic { left: 0; top: 32px; transform: rotate(-8deg); }
.art-camera { right: 0; bottom: 24px; transform: rotate(8deg); }
.art-dot { position: absolute; width: 8px; height: 8px; border-radius: 50%; background: color-mix(in srgb, var(--accent) 35%, var(--surface)); }
.art-dot-one { top: 30px; right: 33px; }
.art-dot-two { left: 42px; bottom: 27px; width: 5px; height: 5px; }
.section-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 20px; margin-bottom: 18px; }
.section-heading h2 { margin: 0; color: var(--text); font-size: 18px; line-height: 1.5; font-weight: 600; letter-spacing: -.02em; }
.section-heading p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.7; }
.capability-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.capability-card {
  position: relative; display: flex; flex-direction: column; align-items: flex-start; gap: 22px; min-width: 0;
  padding: 26px; border: 1px solid var(--border); border-radius: var(--radius-panel, 24px);
  background: var(--surface); color: var(--text); text-decoration: none; touch-action: manipulation;
  box-shadow: var(--shadow-soft, 0 4px 20px rgb(22 40 16 / .04));
}
.capability-icon { display: grid; place-items: center; width: 50px; height: 50px; flex-shrink: 0; border-radius: 17px; color: var(--accent); background: var(--accent-soft); }
.capability-copy h3 { margin: 0 0 8px; font-size: 16px; line-height: 1.5; font-weight: 600; letter-spacing: -.015em; }
.capability-copy p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.75; text-wrap: pretty; }
.capability-arrow { position: absolute; right: 26px; top: 27px; color: var(--muted); }
.privacy-note { display: flex; align-items: center; gap: 14px; padding: 0 8px; }
.privacy-icon { flex-shrink: 0; display: grid; place-items: center; width: 40px; height: 40px; border-radius: 14px; background: var(--accent-soft); color: var(--accent); }
.privacy-title { margin: 0 0 3px; color: var(--text); font-size: 12px; font-weight: 500; line-height: 1.6; }
.privacy-description { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.7; text-wrap: pretty; }
@media (max-width: 1100px) and (min-width: 960px), (max-width: 760px) {
  .welcome-card { grid-template-columns: minmax(0, 1fr) 180px; padding: 30px; gap: 12px; }
  .device-art { width: 180px; height: 200px; }
  .art-orbit { width: 180px; height: 180px; }
  .art-orbit-inner { width: 148px; height: 148px; }
  .art-base, .art-device { width: 106px; height: 106px; border-radius: 28px; }
  .art-device > svg { width: 46px; height: 46px; }
  .art-satellite { width: 46px; height: 46px; border-radius: 16px; }
  .art-mic { left: -4px; top: 17px; }
  .art-camera { right: -4px; bottom: 14px; }
  .capability-card { padding: 22px; }
  .capability-arrow { right: 22px; top: 25px; }
}
@media (max-width: 620px) {
  .connection-overview { gap: 28px; }
  .welcome-card { grid-template-columns: minmax(0, 1fr); padding: 28px 24px; gap: 22px; }
  .welcome-copy h2 { max-width: 100%; }
  .welcome-description { margin-block: 12px 20px; }
  .device-art { width: 230px; height: 160px; }
  .art-orbit { width: 160px; height: 160px; }
  .art-orbit-inner { width: 130px; height: 130px; }
  .art-mic { left: 6px; top: 14px; }
  .art-camera { right: 6px; bottom: 10px; }
  .section-heading { display: block; }
  .section-heading p { margin-top: 5px; }
  .capability-grid { grid-template-columns: minmax(0, 1fr); gap: 14px; }
  .capability-card { flex-direction: row; align-items: center; gap: 16px; padding: 22px 20px; }
  .capability-icon { width: 46px; height: 46px; border-radius: 16px; }
  .capability-copy { flex: 1; min-width: 0; }
  .capability-copy h3 { margin-bottom: 5px; font-size: 15px; }
  .capability-arrow { position: static; flex-shrink: 0; width: 17px; }
  .privacy-note { align-items: flex-start; padding-inline: 2px; }
}
@media (hover: hover) and (pointer: fine) {
  .device-link:hover { background: color-mix(in srgb, var(--accent) 9%, var(--accent-soft)); border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); }
  .capability-card:hover { border-color: color-mix(in srgb, var(--accent) 26%, var(--border)); box-shadow: var(--shadow-float, 0 12px 28px -12px rgb(22 40 16 / .14)); }
  .capability-card:hover .capability-arrow { color: var(--accent); }
}
@media (prefers-reduced-motion: no-preference) {
  .welcome-card, .capabilities, .privacy-note { animation: overview-arrive 400ms var(--ease-out, cubic-bezier(.22, 1, .36, 1)) both; }
  .capabilities { animation-delay: 25ms; }
  .privacy-note { animation-delay: 50ms; }
  .device-link, .capability-card { transition: transform 220ms var(--ease-out, cubic-bezier(.22, 1, .36, 1)), box-shadow 220ms ease, background-color var(--motion-fast, 180ms) ease, border-color var(--motion-fast, 180ms) ease; }
  .capability-arrow, .device-link > svg { transition: transform 220ms var(--ease-out, cubic-bezier(.22, 1, .36, 1)), color var(--motion-fast, 180ms) ease; }
  @keyframes overview-arrive { from { opacity: 0; translate: 0 8px; } to { opacity: 1; translate: 0 0; } }
}
@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .capability-card:hover { transform: translateY(-4px); }
  .capability-card:hover .capability-arrow { transform: translate(2px, -2px); }
  .device-link:hover { transform: translateY(-2px); }
  .device-link:hover > svg { transform: translateX(2px); }
  .capability-card:active, .device-link:active { transform: translateY(0); }
}
</style>
