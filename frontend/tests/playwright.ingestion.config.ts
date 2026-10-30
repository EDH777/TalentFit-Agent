import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.', testMatch: 'ingestion-navigation.spec.ts', outputDir: '../.test-data/ingestion-results', workers: 1, timeout: 25000,
  use: { baseURL: 'http://127.0.0.1:5197', channel: 'msedge', headless: true, screenshot: 'only-on-failure' },
  webServer: { command: 'npm run dev -- --host 127.0.0.1 --port 5197 --strictPort', cwd: '..', url: 'http://127.0.0.1:5197' },
})
