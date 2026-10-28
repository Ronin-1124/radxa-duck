export interface SpeechHealth {
  schema_version: 1
  state: 'loading' | 'idle' | 'starting' | 'listening' | 'stopping' | 'error' | 'closed'
  ready: boolean
  provider: 'cpu'
}

/** Health reports recognition readiness, not conversation or microphone capture capability. */
export function isSpeechHealth(value: unknown): value is SpeechHealth {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const health = value as Record<string, unknown>
  return health.schema_version === 1 && health.provider === 'cpu' && typeof health.ready === 'boolean'
    && typeof health.state === 'string'
    && ['loading', 'idle', 'starting', 'listening', 'stopping', 'error', 'closed'].includes(health.state)
}
