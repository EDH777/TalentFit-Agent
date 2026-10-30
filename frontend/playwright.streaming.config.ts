import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests', testMatch: 'streaming.spec.ts', workers: 1, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:5199', channel: 'msedge', headless: true, screenshot: 'only-on-failure' },
  webServer: { command: 'npm run preview -- --host 127.0.0.1 --port 5199 --strictPort', url: 'http://127.0.0.1:5199' },
})
