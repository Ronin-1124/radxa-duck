<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Camera, ChevronDown, Eye, EyeOff, Maximize, RefreshCw, ScanLine, Square } from '@lucide/vue'
import { useCameraObserver } from './useCameraObserver'
import { useRobotConsole } from './useRobotConsole'
import { sensorState } from './capability-state'
import { useViewPreferences } from './useViewPreferences'
import UiAlert from './UiAlert.vue'
const cameraImage=ref<HTMLImageElement>()
const { previewEnabled,showBoxes,showDetails:debug }=useViewPreferences()
const { status,frame,imageUrl,connection,error,pending,stale,observing,connect,preview }=useCameraObserver(()=>cameraImage.value,previewEnabled)
const { brain,fresh }=useRobotConsole()
const viewport=ref<HTMLElement>(),fullscreenError=ref(false)
const {t}=useI18n({useScope:'local',messages:{
 'zh-CN':{view:'小鸭的视角',live:'实时画面',idle:'摄像头空闲',notEnabled:'摄像头未启用',capabilityUnavailable:'摄像头能力不可用',sensorIdle:'已启用 · 空闲',sensorDisabled:'未启用',sensorUnavailable:'能力不可用',sensorUnknown:'状态未知',unknown:'摄像头状态未知',pending:'实时视角待接入',connecting:'正在连接视角',waitingFrame:'等待画面',start:'临时采集',stop:'结束临时采集',previewOn:'开启网页预览',previewOff:'关闭网页预览',previewDisabled:'网页预览已关闭',starting:'正在开启',stopping:'正在关闭',observer:'仅观察',temporary:'临时采集中',stale:'画面暂未更新',reconnect:'重新连接',fullscreen:'全屏查看',diagnostics:'画面详情',boxes:'目标标注',objects:'当前目标',none:'暂无检测结果',dimensions:'画面尺寸',frame:'帧编号',camera:'摄像头',active:'正在采集',disabled:'未采集',unreported:'未报告',unavailable:'预览暂不可用',busy:'摄像头正忙',permission_denied:'摄像头权限不足',invalid_response:'画面数据暂时无法识别',network_error:'画面连接已断开',timeout:'画面连接超时',decode_error:'当前帧无法显示',outcome_unknown:'预览操作结果未确认，连接已释放',fullscreenError:'暂时无法进入全屏',previewStartScope:'临时启用摄像头供本网页查看',previewScope:'仅释放本网页的临时摄像头占用'},
 en:{view:'Duck’s view',live:'Live view',idle:'Camera idle',notEnabled:'Camera not enabled',capabilityUnavailable:'Camera capability unavailable',sensorIdle:'Enabled · idle',sensorDisabled:'Not enabled',sensorUnavailable:'Capability unavailable',sensorUnknown:'State unknown',unknown:'Camera state unknown',pending:'Live view pending',connecting:'Connecting to view',waitingFrame:'Waiting for frames',start:'Temporary capture',stop:'End temporary capture',previewOn:'Open web preview',previewOff:'Close web preview',previewDisabled:'Web preview is off',starting:'Starting preview',stopping:'Closing preview',observer:'Observing',temporary:'Temporary capture',stale:'Frame is out of date',reconnect:'Reconnect',fullscreen:'View fullscreen',diagnostics:'Frame details',boxes:'Detection overlay',objects:'Current objects',none:'No detections',dimensions:'Dimensions',frame:'Frame ID',camera:'Camera',active:'Capturing',disabled:'Not capturing',unreported:'Not reported',unavailable:'Preview unavailable',busy:'Camera is busy',permission_denied:'Camera permission denied',invalid_response:'Frame data could not be read',network_error:'View disconnected',timeout:'View connection timed out',decode_error:'Frame could not be displayed',outcome_unknown:'Preview outcome unconfirmed. Connection released.',fullscreenError:'Fullscreen is unavailable',previewStartScope:'Temporarily capture camera frames for this browser',previewScope:'Releases this browser’s temporary camera lease'}
}})
const cameraState=computed(()=>{
 const camera=fresh.value?brain.value?.camera:undefined
 if(connection.value==='online'&&status.value){
  if(status.value.camera_active)return 'active'
  return camera?sensorState({...camera,active:false}):'notCapturing'
 }
 return sensorState(camera)
})
const cameraLabel=computed(()=>({active:'active',enabled:'sensorIdle',disabled:'sensorDisabled',unavailable:'sensorUnavailable',unknown:'sensorUnknown',notCapturing:'disabled'})[cameraState.value])
const headline=computed(()=>!previewEnabled.value?'previewDisabled':connection.value==='connecting'?'connecting':connection.value==='pending'?'pending':connection.value!=='online'?'unknown':status.value?.camera_active?(imageUrl.value?'live':'waitingFrame'):cameraState.value==='disabled'?'notEnabled':cameraState.value==='unavailable'?'capabilityUnavailable':'idle')
const boxes=computed(()=>showBoxes.value&&!stale.value?frame.value?.detections??[]:[])
async function fullscreen(){fullscreenError.value=false;try{if(document.fullscreenElement===viewport.value)await document.exitFullscreen();else await viewport.value?.requestFullscreen()}catch{fullscreenError.value=true}}
onBeforeUnmount(()=>{if(document.fullscreenElement===viewport.value)void document.exitFullscreen().catch(()=>{})})
</script>
<template>
 <div class="console-page robot-view">
  <section class="view-panel console-panel" aria-labelledby="view-title">
   <header class="view-header">
    <div><span class="console-label">RADXA DUCK</span><h1 id="view-title">{{ t('view') }}</h1></div>
    <div class="view-actions">
     <span v-if="connection==='online'" class="observer-label"><span aria-hidden="true"></span>{{ t(status?.preview_owned?'temporary':'observer') }}</span>
     <button v-if="connection==='online'&&(status?.preview_owned||status?.can_start_preview&&!status.camera_active)" type="button" class="ui-button temporary-capture" :disabled="!!pending" :title="t(status?.preview_owned?'previewScope':'previewStartScope')" @click="preview"><component :is="status?.preview_owned?Square:Camera" :size="16" aria-hidden="true" />{{ t(pending==='start'?'starting':pending==='stop'?'stopping':status?.preview_owned?'stop':'start') }}</button>
     <button v-if="connection==='offline'||connection==='pending'" class="ui-icon-button" type="button" :aria-label="t('reconnect')" @click="connect"><RefreshCw :size="17" aria-hidden="true" /></button>
     <button class="ui-button preview-toggle" type="button" :aria-pressed="previewEnabled" @click="previewEnabled=!previewEnabled"><component :is="previewEnabled?EyeOff:Eye" :size="16" aria-hidden="true" />{{ t(previewEnabled?'previewOff':'previewOn') }}</button>
    </div>
   </header>
   <div ref="viewport" class="camera-viewport" :data-live="!!imageUrl" :data-stale="stale">
    <template v-if="imageUrl&&frame"><img ref="cameraImage" :src="imageUrl" :width="frame.width" :height="frame.height" :alt="t('view')" class="camera-image" decoding="async" /><svg v-if="boxes.length" class="detection-overlay" :viewBox="`0 0 ${frame.width} ${frame.height}`" aria-hidden="true"><rect v-for="(box,index) in boxes" :key="index" :x="box.box_xyxy[0]" :y="box.box_xyxy[1]" :width="box.box_xyxy[2]-box.box_xyxy[0]" :height="box.box_xyxy[3]-box.box_xyxy[1]" vector-effect="non-scaling-stroke" /></svg></template>
    <div v-else class="camera-placeholder"><div class="camera-rings" aria-hidden="true"><span></span><span></span><span class="camera-symbol"><Camera :size="30" :stroke-width="1.25" /></span></div><p role="status">{{ t(headline) }}</p></div>
    <div class="viewport-top"><span class="camera-status" role="status"><span aria-hidden="true" :class="{active:observing&&!stale}"></span>{{ t(stale?'stale':headline) }}</span></div><button class="fullscreen-button" type="button" :disabled="!imageUrl" :aria-label="t('fullscreen')" @click="fullscreen"><Maximize :size="17" aria-hidden="true" /></button>
    <span v-for="corner in ['tl','tr','bl','br']" :key="corner" class="viewport-corner" :class="corner" aria-hidden="true"></span>
   </div>
   <footer class="view-footer"><span><Camera :size="14" aria-hidden="true" />{{ t('camera') }}<i aria-hidden="true">·</i>{{ t(cameraLabel) }}</span><button type="button" :aria-expanded="debug" aria-controls="frame-details" @click="debug=!debug">{{ t('diagnostics') }}<ChevronDown :size="14" :class="{rotated:debug}" aria-hidden="true" /></button></footer>
  </section>
  <UiAlert v-if="error&&error!=='not_supported'" role="status">{{ t(['network_error','timeout','decode_error','invalid_response','outcome_unknown','busy','permission_denied'].includes(error)?error:'unavailable') }}</UiAlert><UiAlert v-if="fullscreenError" role="status">{{ t('fullscreenError') }}</UiAlert>
  <section v-if="debug" id="frame-details" class="frame-details console-panel" :aria-label="t('diagnostics')"><div class="details-heading"><h2>{{ t('objects') }}</h2><button type="button" class="ui-button" :aria-pressed="showBoxes" :disabled="!frame||stale" @click="showBoxes=!showBoxes"><ScanLine :size="16" aria-hidden="true" />{{ t('boxes') }}</button></div><ul v-if="frame?.detections.length&&!stale" class="object-list"><li v-for="(object,index) in frame.detections" :key="index"><span>{{ object.label }}</span><span>{{ Math.round(object.score*100) }}%</span></li></ul><p v-else class="console-label">{{ t('none') }}</p><dl><div><dt>{{ t('dimensions') }}</dt><dd>{{ frame?`${frame.width} × ${frame.height}`:'—' }}</dd></div><div><dt>{{ t('frame') }}</dt><dd>{{ frame?.frame_id??'—' }}</dd></div></dl></section>
 </div>
</template>
<style scoped>
.robot-view { display:grid; gap:20px; }
.view-panel { padding:28px 28px 0; border-radius:32px; }
.view-header { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:20px; padding:0 4px 24px; }
.view-header .console-label { font-size:10px; letter-spacing:.16em; }
h1 { margin:8px 0 0; font-size:24px; line-height:1.3; font-weight:550; letter-spacing:-.7px; }
.view-actions { display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:10px; }
.preview-toggle[aria-pressed="true"] { color:var(--accent); background:var(--accent-soft); }
.fullscreen-button:disabled { opacity:.4; cursor:default; }
.observer-label { display:flex; align-items:center; gap:7px; font-size:12px; color:var(--muted); }
.observer-label > span { width:5px; height:5px; border-radius:50%; background:var(--status); }
.camera-viewport { position:relative; overflow:hidden; display:grid; place-items:center; width:100%; min-height:280px; aspect-ratio:16/9; border-radius:22px; background:#1b231f; color:#dce6d8; isolation:isolate; }
.camera-viewport[data-live="false"] { background:radial-gradient(ellipse at 50% 40%,#28372c,#1b231f 75%); }
.camera-image { width:100%; height:100%; position:absolute; inset:0; object-fit:contain; }
.detection-overlay { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
.detection-overlay rect { fill:rgb(140 220 100 / .06); stroke:#c8ff9e; stroke-width:2; }
.camera-placeholder { display:grid; justify-items:center; gap:24px; padding:24px; }
.camera-placeholder p { margin:0; color:#b9c5b3; font-size:14px; text-align:center; }
.camera-rings { position:relative; width:126px; height:126px; display:grid; place-items:center; }
.camera-rings > span { position:absolute; inset:0; border-radius:50%; border:1px solid rgb(195 221 177 / .09); }
.camera-rings > span:nth-child(2) { inset:16px; border-color:rgb(195 221 177 / .12); }
.camera-rings > .camera-symbol { inset:33px; display:grid; place-items:center; border:0; border-radius:20px; background:rgb(195 221 177 / .06); color:#b0cf9d; }
.viewport-top { position:absolute; left:24px; right:24px; top:24px; display:flex; justify-content:space-between; }
.camera-status { display:inline-flex; align-items:center; gap:7px; padding:7px 10px; border-radius:10px; background:rgb(17 25 18 / .7); backdrop-filter:blur(16px); color:#dce6d8; font-size:11px; }
.camera-status > span { width:5px; height:5px; border-radius:50%; background:#9aa794; }
.camera-status > span.active { background:#afe176; }
.fullscreen-button { position:absolute; right:16px; bottom:16px; display:grid; place-items:center; width:44px; height:44px; border:1px solid rgb(210 230 195 / .18); border-radius:14px; color:#dce6d8; background:rgb(17 25 18 / .4); cursor:pointer; z-index:2; }
.viewport-corner { position:absolute; width:18px; height:18px; border:solid rgb(195 221 177 / .16); pointer-events:none; }
.tl { left:18px;top:18px;border-width:1px 0 0 1px;border-top-left-radius:8px; }.tr { right:18px;top:18px;border-width:1px 1px 0 0;border-top-right-radius:8px; }.bl { left:18px;bottom:18px;border-width:0 0 1px 1px;border-bottom-left-radius:8px; }.br { right:18px;bottom:18px;border-width:0 1px 1px 0;border-bottom-right-radius:8px; }
.view-footer { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; padding:8px 4px; color:var(--muted); }
.view-footer > span,.view-footer > button { display:flex; align-items:center; gap:8px; font-size:12px; }
.view-footer i { font-style:normal; }
.view-footer > button { min-height:46px; border:0; border-radius:12px; padding:10px 0 10px 10px; color:var(--muted); background:transparent; cursor:pointer; }
.rotated { transform:rotate(180deg); }
.frame-details { padding:24px 28px; }
.details-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; }
h2 { margin:0; font-size:16px; font-weight:550; }
dl { display:flex; gap:32px; padding-top:20px; margin:20px 0 0; border-top:1px solid var(--border); }
dl > div { display:flex; gap:12px; font-size:12px; }dt { color:var(--muted); }dd { margin:0; }
.object-list { padding:0; margin:20px 0 0; list-style:none; display:grid; gap:10px; }
.object-list li { display:flex; justify-content:space-between; gap:20px; font-size:13px; }
.camera-viewport:fullscreen { width:100vw; height:100dvh; border-radius:0; }
@media (max-width:639px) { .view-panel { padding:20px 16px 0; border-radius:28px; } .view-header { padding:0 4px 20px; align-items:flex-start; } h1 { font-size:20px; } .view-actions { width:100%; justify-content:flex-start; gap:8px; } .observer-label { display:none; } .view-actions .ui-button { font-size:12px; padding-inline:12px; } .camera-viewport { border-radius:18px; min-height:320px; } .viewport-top { top:16px;left:16px;right:16px; } .view-footer > span,.view-footer > button { font-size:11px; } .frame-details { padding:20px; } }
@media (prefers-reduced-motion:no-preference) { .camera-rings > span:first-child { animation:camera-breathe 5s ease-in-out infinite; } @keyframes camera-breathe { 50% { transform:scale(1.1); opacity:.5; } } }
</style>
