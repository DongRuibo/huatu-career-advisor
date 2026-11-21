import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const target = (env.VITE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

  return {
    plugins: [vue()],
    define: { __SWAGGER_URL__: JSON.stringify(`${target}/docs`) },
    server: {
      host: '127.0.0.1',
      strictPort: true,
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
    preview: { host: '127.0.0.1', strictPort: true },
  }
})
