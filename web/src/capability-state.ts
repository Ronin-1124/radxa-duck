import type { BrainStatus } from './console-protocol'

export type SensorState = 'unknown' | 'unavailable' | 'active' | 'enabled' | 'disabled'
export type CloudAnswerState = 'unknown' | 'unconfigured' | 'verified' | 'verificationFailed' | 'verificationPending' | 'answerRunning'

export function sensorState(sensor: BrainStatus['camera'] | undefined): SensorState {
  if (!sensor) return 'unknown'
  // Capture can still be draining when accepting a new consumer is unavailable.
  if (sensor.active) return 'active'
  if (!sensor.available) return 'unavailable'
  return sensor.enabled ? 'enabled' : 'disabled'
}

/** Brain health reflects a real model answer; connection checks do not verify answers. */
export function cloudAnswerState(cloud: BrainStatus['cloud'] | undefined): CloudAnswerState {
  if (!cloud) return 'unknown'
  if (!cloud.configured) return 'unconfigured'
  if (cloud.answer !== undefined) {
    if (cloud.revision == null || cloud.answer.revision !== cloud.revision) return 'verificationPending'
    return cloud.answer.state === 'succeeded' ? 'verified' : cloud.answer.state === 'failed' ? 'verificationFailed'
      : cloud.answer.state === 'running' ? 'answerRunning' : 'verificationPending'
  }
  if (cloud.health === 'ready') return 'verified'
  if (cloud.health === 'error') return 'verificationFailed'
  return 'verificationPending'
}
