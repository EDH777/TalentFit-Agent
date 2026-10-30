import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.', testMatch: ['job-category.spec.ts', 'staffing-position.spec.ts', 'staffing-employees-help.spec.ts', 'staffing-navigation.spec.ts', 'staffing.spec.ts'],
  outputDir: '../.test-data/staffing-ui-results', workers: 1, timeout: 25000,
  use: { baseURL: 'http://127.0.0.1:5199', channel: 'msedge', headless: true, screenshot: 'only-on-failure' },
  webServer: { command: 'npm run dev -- --host 127.0.0.1 --port 5199 --strictPort', cwd: '..', url: 'http://127.0.0.1:5199' },
})
