/** Detector output mirrors YoloDetector.detect(); transport is supplied separately. */
export type VisionModel = 'yolov8n' | 'yolov5n' | 'yolo26n' | 'yolo26s'
export type CameraRotation = 0 | 90 | 180 | 270
export type VisionConnection = 'unavailable' | 'connecting' | 'online' | 'offline'
export type VisionStatus = 'idle' | 'starting' | 'streaming' | 'stopping' | 'error' | 'closed'

export interface ObjectDetection {
  class_id: number
  label: string
  score: number
  box_xyxy: readonly [number, number, number, number]
  center_normalized: readonly [number, number]
}

export interface DetectionResult {
  schema_version: 1
  type: 'detections'
  provider: 'npu'
  backend: 'rknn-lite'
  model_name: VisionModel
  model_sha256: string
  precision: 'int8'
  input_size: readonly [number, number]
  initialization_ms: number
  frame_id: number | null
  captured_monotonic: number | null
  image_size: readonly [number, number]
  detections: readonly ObjectDetection[]
  preprocess_ms: number
  inference_call_ms: number
  postprocess_ms: number
  total_ms: number
  frame_age_ms: number | null
}

/** One immutable, individually decodable frame, released by the display when no longer used. */
export interface VisionFrame {
  sessionId: string
  id: number
  /** The session adapter owns this URL; the display emits release before it is revoked. */
  url: string
  width: number
  height: number
  /** Browser performance.now() when this frame was received, never a board timestamp. */
  receivedAt: number
}

export interface VisionResult {
  sessionId: string
  data: DetectionResult
}

export interface VisionSettings {
  model: VisionModel
  rotation: CameraRotation
  detection_enabled: boolean
  max_fps: number
  score_threshold: number
}

export interface VisionCapabilities {
  transport: 'jpeg-websocket-v1'
  available_models: readonly VisionModel[]
  rotations: readonly CameraRotation[]
  default_settings: VisionSettings
  max_fps: number
  max_jpeg_bytes: number
  jpeg_quality: number
  capture_size: readonly [number, number]
  ack_timeout_seconds: number
}

/** Frontend presentation state; it does not imply an existing network protocol. */
export interface VisionViewState {
  connection: VisionConnection
  status: VisionStatus
  owned: boolean
  frame: VisionFrame | null
  result: VisionResult | null
  errorCode: string | null
  ready?: boolean
  settings?: VisionSettings
  capabilities?: VisionCapabilities
  /** Request state belongs to the adapter; standalone display fixtures can omit it. */
  pending?: 'start' | 'stop' | null
}

export function emptyVisionState(): VisionViewState {
  return { connection: 'unavailable', status: 'idle', owned: false, frame: null, result: null, errorCode: null }
}

export function matchingDetections(frame: VisionFrame | null, result: VisionResult | null): DetectionResult | null {
  if (!frame || !result || frame.sessionId !== result.sessionId || result.data.frame_id !== frame.id
    || result.data.image_size[0] !== frame.width || result.data.image_size[1] !== frame.height) return null
  return result.data
}
