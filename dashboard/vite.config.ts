import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/tempo': {
        target: 'http://192.168.1.70:31989',
        rewrite: (path) => path.replace(/^\/tempo/, ''),
        changeOrigin: true,
      },
      '/prometheus': {
        target: 'http://192.168.1.70:30300/api/datasources/proxy/uid/prometheus',
        rewrite: (path) => path.replace(/^\/prometheus/, ''),
        changeOrigin: true,
        headers: {
          Authorization: 'Basic YWRtaW46b2JzZXJ2YWJpbGl0eTEyMw==',
        },
      },
    },
  },
})
