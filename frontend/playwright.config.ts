import { defineConfig } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const directory = resolve('.test-data')
mkdirSync(directory, { recursive: true })

export default defineConfig({
  testDir: './tests', workers: 1, timeout: 45000,
  use: { baseURL: 'http://127.0.0.1:5174', channel: 'msedge', headless: true, screenshot: 'only-on-failure' },
  webServer: [
    { command: '..\\chAgent\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001', cwd: '../backend', url: 'http://127.0.0.1:8001/health',
      env: { DATABASE_URL: 'sqlite:///' + resolve(directory, 'e2e-' + Date.now() + '.db').replaceAll('\\', '/'), UPLOAD_DIR: directory,
        OPENAI_API_KEY: '', ADMIN_USERNAME: 'admin', ADMIN_PASSWORD: 'test-admin', HR_USERNAME: 'hr', HR_PASSWORD: 'test-hr', CORS_ORIGINS: 'http://127.0.0.1:5174' } },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5174 --strictPort', url: 'http://127.0.0.1:5174', env: { VITE_API_BASE_URL: 'http://127.0.0.1:8001/api' } },
  ],
})
