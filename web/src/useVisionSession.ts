import { onBeforeUnmount, onMounted, shallowRef } from 'vue'
import { emptyVisionState, type VisionFrame, type VisionSettings, type VisionViewState } from './vision-types'
import { isVisionError, isVisionFrameHeader, isVisionSettings, isVisionSnapshot, MAX_JPEG_BYTES,
  type VisionFrameHeader, type VisionSnapshot } from './vision-protocol'

/** One route owns one control socket. Reconnecting never starts a new capture. */
export function useVisionSession() {
  const state = shallowRef<VisionViewState>({ ...emptyVisionState(), connection: 'connecting', ready: false, pending: null })
  let socket: WebSocket | null = null
  let snapshot: VisionSnapshot | null = null
  let header: VisionFrameHeader | null = null
  let awaitingDisplay: VisionFrame | null = null
  const urls = new Set<string>()
  let lastFrameId = -1
  let request: AbortController | null = null
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  let heartbeatTimer: ReturnType<typeof setTimeout> | undefined
  let commandTimer: ReturnType<typeof setTimeout> | undefined
  let binaryTimer: ReturnType<typeof setTimeout> | undefined
  let displayTimer: ReturnType<typeof setTimeout> | undefined
  let retries = 0
  let disposed = false
  let suspended = false

  function release(frame: VisionFrame): void {
    if (urls.delete(frame.url)) URL.revokeObjectURL(frame.url)
  }

  function clearFrames(): void {
    clearTimeout(binaryTimer)
    clearTimeout(displayTimer)
    header = null
    awaitingDisplay = null
    state.value = { ...state.value, frame: null, result: null }
    for (const url of urls) URL.revokeObjectURL(url)
    urls.clear()
  }

  function clearPending(): void {
    clearTimeout(commandTimer)
    state.value = { ...state.value, pending: null }
  }

  function disconnect(): void {
    clearTimeout(heartbeatTimer)
    request?.abort()
    request = null
    const previous = socket
    socket = null
    if (previous) {
      previous.onopen = previous.onmessage = previous.onclose = previous.onerror = null
      previous.close()
    }
    clearPending()
    clearFrames()
    snapshot = null
    lastFrameId = -1
  }

  function fail(code?: string): void {
    disconnect()
    state.value = { ...state.value, connection: 'offline', owned: false, ready: false,
      errorCode: code ?? state.value.errorCode }
    if (!disposed && !suspended && retryTimer === undefined) {
      retryTimer = setTimeout(() => { retryTimer = undefined; connect() }, Math.min(8000, 500 * 2 ** Math.min(retries++, 4)))
    }
  }

  function heartbeat(): void {
    clearTimeout(heartbeatTimer)
    heartbeatTimer = setTimeout(() => fail('connection_timeout'), 12_000)
  }

  function send(command: object): boolean {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false
    try { socket.send(JSON.stringify(command)); return true } catch { fail(); return false }
  }

  function applySnapshot(next: VisionSnapshot): void {
    if (snapshot && next.revision < snapshot.revision) return
    const firstSnapshot = snapshot === null
    const changedSession = snapshot?.session_id !== next.session_id
    snapshot = next
    if (changedSession) { clearFrames(); lastFrameId = -1 }
    if ((state.value.pending === 'start' && ((next.owned && ['starting', 'streaming'].includes(next.state))
      || ['error', 'closed'].includes(next.state) || !next.ready))
      || (state.value.pending === 'stop' && ['stopping', 'idle', 'error', 'closed'].includes(next.state))) clearPending()
    if (!next.owned || next.state !== 'streaming' || state.value.pending === 'stop') clearFrames()
    state.value = { ...state.value, connection: 'online', status: next.state, owned: next.owned,
      ready: next.ready, settings: next.settings, capabilities: next.capabilities,
      errorCode: next.error?.code ?? (firstSnapshot || (next.owned && next.state === 'streaming') ? null : state.value.errorCode) }
    retries = 0
    heartbeat()
  }

  function receiveBinary(data: unknown): void {
    const meta = header
    header = null
    clearTimeout(binaryTimer)
    if (!meta || !(data instanceof ArrayBuffer) || data.byteLength !== meta.image_bytes || data.byteLength > MAX_JPEG_BYTES) {
      fail('protocol'); return
    }
    // A stop may race a complete header/JPEG pair already sent by the service.
    if (state.value.pending === 'stop' || !snapshot?.owned || snapshot.state !== 'streaming') return
    if (meta.session_id !== snapshot.session_id || awaitingDisplay) { fail('protocol'); return }
    const bytes = new Uint8Array(data)
    if (bytes[0] !== 0xff || bytes[1] !== 0xd8 || bytes.at(-2) !== 0xff || bytes.at(-1) !== 0xd9) { fail('decode_failed'); return }
    const url = URL.createObjectURL(new Blob([data], { type: 'image/jpeg' }))
    urls.add(url)
    const frame: VisionFrame = { sessionId: meta.session_id, id: meta.frame_id, url,
      width: meta.width, height: meta.height, receivedAt: performance.now() }
    awaitingDisplay = frame
    lastFrameId = meta.frame_id
    state.value = { ...state.value, frame, result: meta.result ? { sessionId: meta.session_id, data: meta.result } : null }
    // Includes decoder + paint scheduling. A failed frame never receives an ACK.
    displayTimer = setTimeout(() => fail('decode_failed'), 2500)
    heartbeat()
  }

  function receive(data: unknown): void {
    if (typeof data !== 'string') { receiveBinary(data); return }
    if (header || data.length > 65_536) { fail('protocol'); return }
    let message: unknown
    try { message = JSON.parse(data) } catch { fail('protocol'); return }
    if (isVisionSnapshot(message)) { applySnapshot(message); return }
    if (isVisionFrameHeader(message)) {
      if (!snapshot || message.image_bytes > snapshot.capabilities.max_jpeg_bytes) { fail('protocol'); return }
      const [width, height] = snapshot.capabilities.capture_size
      const rotated = snapshot.settings.rotation % 180 !== 0
      if (message.width !== (rotated ? height : width) || message.height !== (rotated ? width : height)) { fail('protocol'); return }
      if (state.value.pending !== 'stop' && snapshot.owned && snapshot.state === 'streaming') {
        if (awaitingDisplay || message.session_id !== snapshot.session_id || message.frame_id <= lastFrameId
          || (snapshot.settings.detection_enabled ? message.result === null : message.result !== null)
          || (message.result && message.result.model_name !== snapshot.settings.model)) { fail('protocol'); return }
      }
      header = message
      binaryTimer = setTimeout(() => fail('protocol'), 2500)
      return
    }
    if (message && typeof message === 'object' && (message as { type?: unknown }).type === 'error'
      && isVisionError((message as { error?: unknown }).error)) {
      const code = (message as { error: { code: string } }).error.code
      if (state.value.pending === 'stop') { fail(code); return }
      clearPending()
      state.value = { ...state.value, errorCode: code }
      heartbeat()
      return
    }
    fail('protocol')
  }

  function connect(): void {
    if (disposed || suspended) return
    disconnect()
    state.value = { ...state.value, connection: 'connecting', owned: false, ready: false }
    const current = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/vision/live`)
    socket = current
    current.binaryType = 'arraybuffer'
    heartbeatTimer = setTimeout(() => fail('connection_timeout'), 8000)
    current.onmessage = event => { if (socket === current && !disposed) receive(event.data) }
    current.onclose = () => { if (socket === current && !disposed) fail() }
    current.onerror = () => { if (socket === current && !disposed) fail() }

    // HTTP supplies capabilities early, but never overrides WS ownership or readiness.
    const controller = new AbortController()
    request = controller
    const timeout = setTimeout(() => controller.abort(), 4000)
    void fetch('/api/vision/status', { signal: controller.signal, cache: 'no-store' }).then(async response => {
      if (!response.ok) throw new Error('status_unavailable')
      const message: unknown = await response.json()
      if (!isVisionSnapshot(message)) throw new Error('protocol')
      if (socket === current && !snapshot && !disposed) {
        state.value = { ...state.value, capabilities: message.capabilities, settings: message.settings }
      }
    }).catch(() => {
      // The WebSocket also supplies the full snapshot; an HTTP failure must not
      // tear down a healthy control session or replace its error/ownership state.
    }).finally(() => { clearTimeout(timeout); if (request === controller) request = null })
  }

  function start(settings: VisionSettings): void {
    if (state.value.connection !== 'online' || !snapshot?.ready || snapshot.owned || snapshot.session_id !== null
      || !['idle', 'error'].includes(snapshot.state) || state.value.pending || !isVisionSettings(settings)) return
    const caps = snapshot.capabilities
    if (settings.max_fps > caps.max_fps || !caps.rotations.includes(settings.rotation)
      || (settings.detection_enabled && !caps.available_models.includes(settings.model))) return
    state.value = { ...state.value, pending: 'start', errorCode: null }
    if (!send({ type: 'start', settings })) { fail(); return }
    commandTimer = setTimeout(() => fail('command_timeout'), 12_000)
  }

  function stop(): void {
    if (state.value.connection !== 'online' || !snapshot?.owned || state.value.pending
      || !['starting', 'streaming'].includes(snapshot.state)) return
    state.value = { ...state.value, pending: 'stop', errorCode: null }
    // Preserve a header already received so its in-flight JPEG can be drained.
    const inFlight = header
    clearFrames()
    header = inFlight
    if (header) binaryTimer = setTimeout(() => fail('protocol'), 2500)
    if (!send({ type: 'stop' })) { fail(); return }
    commandTimer = setTimeout(() => fail('command_timeout'), 12_000)
  }

  function displayed(frame: VisionFrame): void {
    if (disposed || suspended || document.hidden || state.value.pending === 'stop' || !snapshot?.owned
      || snapshot.state !== 'streaming' || snapshot.session_id !== frame.sessionId || awaitingDisplay?.url !== frame.url
      || !urls.has(frame.url)) return
    if (send({ type: 'ack', session_id: frame.sessionId, frame_id: frame.id })) {
      awaitingDisplay = null
      clearTimeout(displayTimer)
    }
  }

  function decodeError(frame: VisionFrame): void {
    if (awaitingDisplay?.url === frame.url && !disposed && !suspended) fail('decode_failed')
  }

  function suspend(): void {
    suspended = true
    clearTimeout(retryTimer)
    retryTimer = undefined
    disconnect()
    state.value = { ...state.value, connection: 'offline', owned: false, ready: false }
  }
  function resume(): void {
    if (!suspended || disposed || document.hidden) return
    suspended = false
    connect()
  }
  function visibilityChanged(): void { if (document.hidden) suspend(); else resume() }

  onMounted(() => {
    window.addEventListener('pagehide', suspend)
    window.addEventListener('pageshow', resume)
    document.addEventListener('visibilitychange', visibilityChanged)
    if (document.hidden) suspend(); else connect()
  })
  onBeforeUnmount(() => {
    disposed = true
    window.removeEventListener('pagehide', suspend)
    window.removeEventListener('pageshow', resume)
    document.removeEventListener('visibilitychange', visibilityChanged)
    suspend()
  })

  return { state, start, stop, displayed, release, decodeError }
}
