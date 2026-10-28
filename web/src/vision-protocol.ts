import type { DetectionResult, VisionCapabilities, VisionSettings, VisionStatus } from './vision-types'

export interface VisionSnapshot {
  type: 'state'
  schema_version: 1
  revision: number
  state: VisionStatus
  ready: boolean
  session_id: string | null
  owned: boolean
  settings: VisionSettings
  stats: { frames: number }
  error: { code: string; message: string } | null
  capabilities: VisionCapabilities
}

export interface VisionFrameHeader {
  type: 'frame'
  schema_version: 1
  session_id: string
  frame_id: number
  width: number
  height: number
  mime_type: 'image/jpeg'
  image_bytes: number
  result: DetectionResult | null
  frame_age_ms: number
}

export const MAX_JPEG_BYTES = 256 * 1024
const models = ['yolov8n', 'yolov5n', 'yolo26n', 'yolo26s']
const rotations = [0, 90, 180, 270]
const statuses = ['idle', 'starting', 'streaming', 'stopping', 'error', 'closed']
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function text(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= max
}
function integer(value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= min && value <= max
}
function number(value: unknown, min = 0, max = Number.MAX_VALUE): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max
}
function pair(value: unknown, min: number, max: number): value is [number, number] {
  return Array.isArray(value) && value.length === 2 && value.every(v => number(v, min, max))
}
export function isVisionError(value: unknown): value is { code: string; message: string } {
  return record(value) && text(value.code, 128) && typeof value.message === 'string' && value.message.length <= 4096
}
export function isVisionSettings(value: unknown): value is VisionSettings {
  return record(value) && models.includes(value.model as string) && rotations.includes(value.rotation as number)
    && typeof value.detection_enabled === 'boolean' && integer(value.max_fps, 1, 15)
    && number(value.score_threshold, 0.05, 0.95)
}
function isCapabilities(value: unknown): value is VisionCapabilities {
  if (!record(value) || value.transport !== 'jpeg-websocket-v1' || !isVisionSettings(value.default_settings)) return false
  return Array.isArray(value.available_models) && value.available_models.length <= models.length
    && value.available_models.every(v => models.includes(v))
    && Array.isArray(value.rotations) && value.rotations.length > 0 && value.rotations.length <= 4
    && value.rotations.every(v => rotations.includes(v))
    && integer(value.max_fps, 1, 15) && integer(value.max_jpeg_bytes, 1, MAX_JPEG_BYTES)
    && integer(value.jpeg_quality, 1, 100) && pair(value.capture_size, 1, 1920)
    && value.capture_size.every(v => Number.isInteger(v)) && number(value.ack_timeout_seconds, 1, 30)
    && value.default_settings.max_fps <= value.max_fps
    && value.rotations.includes(value.default_settings.rotation)
    && (!value.default_settings.detection_enabled || value.available_models.includes(value.default_settings.model))
}
export function isVisionSnapshot(value: unknown): value is VisionSnapshot {
  return record(value) && value.type === 'state' && value.schema_version === 1 && integer(value.revision)
    && statuses.includes(value.state as string) && typeof value.ready === 'boolean'
    && (value.session_id === null || text(value.session_id, 64)) && typeof value.owned === 'boolean'
    && (!value.owned || value.session_id !== null) && isVisionSettings(value.settings)
    && record(value.stats) && integer(value.stats.frames)
    && (value.error === null || isVisionError(value.error)) && isCapabilities(value.capabilities)
}

function isDetectionResult(value: unknown, width: number, height: number, frameId: number): value is DetectionResult {
  if (!record(value) || value.schema_version !== 1 || value.type !== 'detections'
    || value.provider !== 'npu' || value.backend !== 'rknn-lite' || value.precision !== 'int8'
    || !models.includes(value.model_name as string) || !text(value.model_sha256, 128)
    || !pair(value.input_size, 1, 1920) || value.frame_id !== frameId
    || !number(value.captured_monotonic) || !pair(value.image_size, 1, 1920)
    || value.image_size[0] !== width || value.image_size[1] !== height
    || !['initialization_ms', 'preprocess_ms', 'inference_call_ms', 'postprocess_ms', 'total_ms', 'frame_age_ms'].every(key => number(value[key]))
    || !Array.isArray(value.detections) || value.detections.length > 100) return false
  return value.detections.every(d => record(d) && integer(d.class_id, 0, 79) && text(d.label, 128)
    && number(d.score, 0, 1) && pair(d.center_normalized, 0, 1)
    && Array.isArray(d.box_xyxy) && d.box_xyxy.length === 4
    && d.box_xyxy.every(v => number(v)) && d.box_xyxy[0] < d.box_xyxy[2] && d.box_xyxy[1] < d.box_xyxy[3]
    && d.box_xyxy[2] <= width && d.box_xyxy[3] <= height)
}

export function isVisionFrameHeader(value: unknown): value is VisionFrameHeader {
  return record(value) && value.type === 'frame' && value.schema_version === 1 && text(value.session_id, 64)
    && integer(value.frame_id) && integer(value.width, 1, 1920) && integer(value.height, 1, 1920)
    && value.mime_type === 'image/jpeg' && integer(value.image_bytes, 4, MAX_JPEG_BYTES)
    && number(value.frame_age_ms)
    && (value.result === null || isDetectionResult(value.result, value.width, value.height, value.frame_id))
}
