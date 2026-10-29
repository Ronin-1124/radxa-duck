/** Resident robot observation. These messages never transfer ownership of the brain. */
export type BrainState = 'starting' | 'running' | 'paused' | 'stopped' | 'error'
export type BrainActivity = 'idle' | 'listening' | 'thinking' | 'speaking' | 'acting'
type LegacyBrainAction = 'pause' | 'resume' | 'enable_microphone' | 'disable_microphone' | 'cancel_task'
export type BrainAction = LegacyBrainAction | 'enable_camera' | 'disable_camera'
export interface CloudCheck {
  state: 'unknown' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  revision: number | null; checked_at: string | null; error_code: string | null
}
export interface VoiceStage {
  state: 'idle' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'skipped'
  task_id: string | null; updated_at: string | null; error_code: string | null
}
export interface VoiceFeedback {
  reply_available: boolean; synthesis: VoiceStage; playback: VoiceStage
  input: { state: string; pause_reason: string | null; error_code: string | null }
}
export interface BrainStatus {
  schema_version: 1; type: 'brain_status'; instance_id: string; revision: number; sampled_at: string
  state: BrainState; activity: BrainActivity; events_available: boolean
  identity: { name: string; revision: number; active_revision: number }
  microphone: { available: boolean; enabled: boolean; active: boolean }
  camera: { available: boolean; enabled: boolean; active: boolean }
  cloud: { configured: boolean; model: string | null; health: 'ready' | 'error' | 'unknown'
    revision?: number | null; connection_check?: CloudCheck; answer?: CloudCheck }
  voice?: VoiceFeedback
  motion: { available: boolean; state: 'ready' | 'busy' | 'unavailable' | 'error' }
  task: null | { id: string; kind: string; state: 'running' | 'cancelling' | 'succeeded' | 'failed' | 'cancelled'; summary: string }
  controls: Record<LegacyBrainAction, boolean> & Partial<Record<'enable_camera' | 'disable_camera', boolean>>
}
export interface IdentityProfile { name: string; description: string; personality: string; expression_style: string }
export interface IdentityConfig {
  schema_version: 1; type: 'identity_config'; revision: number; active_revision: number; profile: IdentityProfile
}
export interface ObserverStatus {
  schema_version: 1; type: 'observer_status'; subscription_id: string; revision: number
  camera_active: boolean; preview_owned: boolean; can_start_preview: boolean; request_id?: string
}
export interface ObserverFrame {
  schema_version: 1; type: 'frame'; subscription_id: string; frame_id: number
  width: number; height: number; jpeg_bytes: number
  detections: { label: string; score: number; box_xyxy: [number, number, number, number] }[]
}
export const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const text = (v: unknown, max = 256): v is string => typeof v === 'string' && v.length <= max
const id = (v: unknown): v is string => text(v, 128) && /^[A-Za-z0-9_.:-]{1,128}$/.test(v)
const integer = (v: unknown, max = Number.MAX_SAFE_INTEGER): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 && v <= max
const sensor = (v: unknown): boolean => object(v) && ['available', 'enabled', 'active'].every(k => typeof v[k] === 'boolean')
const timestamp = (v: unknown): v is string => text(v, 64) && Number.isFinite(Date.parse(v))
const errorCode = (v: unknown): boolean => v === null || typeof v === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(v)
const cloudCheck = (v: unknown): boolean => object(v) && ['unknown', 'running', 'succeeded', 'failed', 'cancelled'].includes(v.state as string)
  && (v.revision === null || integer(v.revision)) && (v.checked_at === null || timestamp(v.checked_at)) && errorCode(v.error_code)
const voiceStage = (v: unknown): boolean => object(v) && ['idle', 'running', 'succeeded', 'failed', 'cancelled', 'skipped'].includes(v.state as string)
  && (v.task_id === null || id(v.task_id)) && (v.updated_at === null || timestamp(v.updated_at)) && errorCode(v.error_code)
const voiceFeedback = (v: unknown): boolean => object(v) && typeof v.reply_available === 'boolean' && voiceStage(v.synthesis) && voiceStage(v.playback)
  && object(v.input) && text(v.input.state, 64) && errorCode(v.input.pause_reason) && errorCode(v.input.error_code)
export function isBrainStatus(v: unknown): v is BrainStatus {
  return object(v) && v.schema_version === 1 && v.type === 'brain_status' && id(v.instance_id) && integer(v.revision)
    && text(v.sampled_at, 64) && Number.isFinite(Date.parse(v.sampled_at))
    && ['starting', 'running', 'paused', 'stopped', 'error'].includes(v.state as string)
    && ['idle', 'listening', 'thinking', 'speaking', 'acting'].includes(v.activity as string) && typeof v.events_available === 'boolean'
    && object(v.identity) && text(v.identity.name, 40) && integer(v.identity.revision) && integer(v.identity.active_revision)
    && v.identity.active_revision <= v.identity.revision && sensor(v.microphone) && sensor(v.camera)
    && object(v.cloud) && typeof v.cloud.configured === 'boolean' && (v.cloud.model === null || text(v.cloud.model, 128))
    && ['ready', 'error', 'unknown'].includes(v.cloud.health as string)
    && (v.cloud.revision === undefined || v.cloud.revision === null || integer(v.cloud.revision))
    && (v.cloud.connection_check === undefined || cloudCheck(v.cloud.connection_check))
    && (v.cloud.answer === undefined || cloudCheck(v.cloud.answer))
    && (v.voice === undefined || voiceFeedback(v.voice))
    && object(v.motion) && typeof v.motion.available === 'boolean' && ['ready', 'busy', 'unavailable', 'error'].includes(v.motion.state as string)
    && (v.task === null || object(v.task) && id(v.task.id) && text(v.task.kind, 64)
      && ['running', 'cancelling', 'succeeded', 'failed', 'cancelled'].includes(v.task.state as string) && text(v.task.summary, 1000))
    && object(v.controls) && ['pause', 'resume', 'enable_microphone', 'disable_microphone', 'cancel_task'].every(k => typeof (v.controls as Record<string, unknown>)[k] === 'boolean')
    && ['enable_camera', 'disable_camera'].every(k => (v.controls as Record<string, unknown>)[k] === undefined || typeof (v.controls as Record<string, unknown>)[k] === 'boolean')
}
export function isIdentityConfig(v: unknown): v is IdentityConfig {
  return object(v) && v.schema_version === 1 && v.type === 'identity_config' && integer(v.revision) && integer(v.active_revision)
    && v.active_revision <= v.revision && object(v.profile) && text(v.profile.name, 40) && v.profile.name.trim().length > 0
    && text(v.profile.description, 2000) && text(v.profile.personality, 1000) && text(v.profile.expression_style, 1000)
}
export function isObserverStatus(v: unknown): v is ObserverStatus {
  return object(v) && v.schema_version === 1 && v.type === 'observer_status' && id(v.subscription_id) && integer(v.revision)
    && ['camera_active', 'preview_owned', 'can_start_preview'].every(k => typeof v[k] === 'boolean')
    && (v.request_id === undefined || id(v.request_id))
}
export function isObserverFrame(v: unknown): v is ObserverFrame {
  if (!object(v) || v.schema_version !== 1 || v.type !== 'frame' || !id(v.subscription_id) || !integer(v.frame_id)
    || !integer(v.width, 4096) || !integer(v.height, 4096) || !v.width || !v.height || v.width * v.height > 8388608
    || !integer(v.jpeg_bytes, 2097152) || v.jpeg_bytes < 4 || !Array.isArray(v.detections) || v.detections.length > 256) return false
  return v.detections.every(d => object(d) && text(d.label, 128) && typeof d.score === 'number' && Number.isFinite(d.score) && d.score >= 0 && d.score <= 1
    && Array.isArray(d.box_xyxy) && d.box_xyxy.length === 4 && d.box_xyxy.every(n => typeof n === 'number' && Number.isFinite(n) && n >= 0)
    && d.box_xyxy[2] > d.box_xyxy[0] && d.box_xyxy[3] > d.box_xyxy[1] && d.box_xyxy[2] <= (v.width as number) && d.box_xyxy[3] <= (v.height as number))
}
