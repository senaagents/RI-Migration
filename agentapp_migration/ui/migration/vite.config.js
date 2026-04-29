import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

const DEFAULT_DEV_PORT = 5175
const DEFAULT_FRAPPE_URL = 'http://avinash.localhost:8081'

function normalizeBaseUrl(url) {
  return String(url || DEFAULT_FRAPPE_URL).replace(/\/+$/, '')
}

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const devPort = Number(env.VITE_MIGRATION_DEV_PORT || DEFAULT_DEV_PORT)
  const frappeUrl = normalizeBaseUrl(env.VITE_FRAPPE_BASE_URL)

  return {
    plugins: [vue()],
    base: command === 'serve' ? '/' : '/assets/agentapp_migration/migration/',
    build: {
      outDir: path.resolve(__dirname, '../../public/migration'),
      emptyOutDir: true,
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      host: '0.0.0.0',
      port: devPort,
      strictPort: true,
      proxy: {
        '/api': {
          target: frappeUrl,
          changeOrigin: true,
        },
        '/assets/agentapp_migration': {
          target: frappeUrl,
          changeOrigin: true,
        },
      },
    },
  }
})
