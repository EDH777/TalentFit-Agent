import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.', testMatch: 'chat-delete.spec.ts', workers: 1, timeout: 15000,
  use: { baseURL: 'http://127.0.0.1:5198', channel: 'msedge', headless: true, screenshot: 'only-on-failure' },
  webServer: { command: 'npm run preview -- --host 127.0.0.1 --port 5198 --strictPort', cwd: '..', url: 'http://127.0.0.1:5198' },
})
