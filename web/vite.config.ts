import type { IncomingMessage } from 'node:http'
import type { Duplex } from 'node:stream'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite'
import vue from '@vitejs/plugin-vue'

const root = fileURLToPath(new URL('.', import.meta.url))
const speechPath = '/api/speech/live'

function speechTarget(value: string | undefined): string | undefined {
  if (!value?.trim()) return undefined
  let target: URL
  try { target = new URL(value) } catch { throw new Error('RADXA_DUCK_SPEECH_TARGET must be an HTTP(S) service origin.') }
  if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password
    || target.pathname !== '/' || target.search || target.hash) {
    throw new Error('RADXA_DUCK_SPEECH_TARGET must be an HTTP(S) origin without credentials, path or query.')
  }
  return target.origin
}

/** Fail unavailable speech connections promptly instead of leaving an upgrade hanging. */
function unconfiguredSpeech(): Plugin {
  const rejectUpgrade = (request: IncomingMessage, socket: Duplex): void => {
    if (request.url?.split('?')[0] !== speechPath) return
    socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
  }
  return {
    name: 'unconfigured-speech-service',
    configureServer(server) { server.httpServer?.on('upgrade', rejectUpgrade) },
    configurePreviewServer(server) { server.httpServer.on('upgrade', rejectUpgrade) },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, root, 'RADXA_DUCK_')
  const target = speechTarget(process.env.RADXA_DUCK_SPEECH_TARGET ?? env.RADXA_DUCK_SPEECH_TARGET)
  const proxy: Record<string, ProxyOptions> = target ? {
    '^/api/speech/live(?:\\?|$)': {
      target,
      ws: true,
      changeOrigin: true,
      // Preserve the browser Origin: the speech service owns its origin allowlist.
      rewriteWsOrigin: false,
    },
  } : {}
  return {
    root,
    plugins: [vue(), ...(target ? [] : [unconfiguredSpeech()])],
    server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
    preview: { host: '127.0.0.1', proxy },
  }
})
