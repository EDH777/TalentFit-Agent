import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.', testMatch: 'knowledge*.spec.ts', workers: 1, timeout: 20000,
  use: { baseURL: 'http://127.0.0.1:5196', channel: 'msedge', headless: true, screenshot: 'only-on-failure' },
  webServer: { command: 'npm run dev -- --host 127.0.0.1 --port 5196 --strictPort', cwd: '..', url: 'http://127.0.0.1:5196' },
})
