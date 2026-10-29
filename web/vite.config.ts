import type { IncomingMessage } from 'node:http'
import type { Duplex } from 'node:stream'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite'
import vue from '@vitejs/plugin-vue'

const root = fileURLToPath(new URL('.', import.meta.url))

function serviceTarget(value: string | undefined): string | undefined {
  if (!value?.trim()) return undefined
  let target: URL
  try { target = new URL(value) } catch { throw new Error('RADXA_DUCK_SERVICE_TARGET must be an HTTP(S) service origin.') }
  if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password
    || target.pathname !== '/' || target.search || target.hash) {
    throw new Error('RADXA_DUCK_SERVICE_TARGET must be an HTTP(S) origin without credentials, path or query.')
  }
  return target.origin
}

/** Missing configuration must return an error rather than an SPA page or hanging upgrade. */
function unconfiguredService(): Plugin {
  const rejectUpgrade = (request: IncomingMessage, socket: Duplex): void => {
    if (!request.url?.startsWith('/api/')) return
    socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
  }
  const rejectStatus: import('vite').Connect.NextHandleFunction = (request, response, next) => {
    if (!request.url?.startsWith('/api/')) { next(); return }
    response.writeHead(503, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
    response.end('{"error":"service_unconfigured"}')
  }
  return {
    name: 'unconfigured-board-service',
    configureServer(server) { server.httpServer?.on('upgrade', rejectUpgrade); server.middlewares.use(rejectStatus) },
    configurePreviewServer(server) { server.httpServer.on('upgrade', rejectUpgrade); server.middlewares.use(rejectStatus) },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, root, 'RADXA_DUCK_')
  // Keep existing speech development environments working with the combined board service.
  const target = serviceTarget(process.env.RADXA_DUCK_SERVICE_TARGET ?? env.RADXA_DUCK_SERVICE_TARGET
    ?? process.env.RADXA_DUCK_SPEECH_TARGET ?? env.RADXA_DUCK_SPEECH_TARGET)
  const proxy: Record<string, ProxyOptions> = target ? {
    '^/api/': {
      target,
      ws: true,
      changeOrigin: true,
      // The board service validates the original browser Origin for event and camera sockets.
      rewriteWsOrigin: false,
    },
  } : {}
  return {
    root,
    plugins: [vue(), ...(target ? [] : [unconfiguredService()])],
    server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
    preview: { host: '127.0.0.1', proxy },
  }
})
