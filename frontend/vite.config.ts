import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  base: '/static/app/',
  plugins: [vue(), tailwindcss()],
  build: {
    outDir: '../gpt_reg/web/static/app',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
  },
})
