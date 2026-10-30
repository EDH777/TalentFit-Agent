import { defineConfig } from '@playwright/test'

export default defineConfig({ testDir: './tests', testMatch: 'stream-parser.spec.ts', workers: 1 })
