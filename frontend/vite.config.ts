// 前端开发与构建配置，启用 Vue，并生成指向后端的 Swagger 地址。
// 开发时把相对的 /api 请求转发到 FastAPI，后端地址可由 VITE_BACKEND_URL 指定。

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

// 根据 Vite 模式加载前端配置，设置 Vue 插件、同源代理和 Swagger 链接。
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
          // /api 是工作台的代理前缀，FastAPI 按去掉前缀后的路由处理。
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
    preview: { host: '127.0.0.1', strictPort: true },
  }
})
