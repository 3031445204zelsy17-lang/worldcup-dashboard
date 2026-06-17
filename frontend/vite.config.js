import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev proxy: 前端 /api → 后端 8001(本机 8000 被另一服务占用, API 跑 8001)
// 生产 build 后走同源反代或 VITE_API_BASE(P1-7 部署定)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
