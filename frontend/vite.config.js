import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发代理：/api、/ws 转发到 FastAPI（8000 端口）
export default defineConfig({
  plugins: [vue()],
  // 已打开的页面仍会按旧入口加载带哈希的异步组件，更新时保留这些资源。
  build: { emptyOutDir: false },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})
