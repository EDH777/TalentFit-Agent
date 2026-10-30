import { expect, test, type Page } from '@playwright/test'

async function fixture(page: Page) {
  const user = { id: 1, username: 'synthetic-hr', role: 'hr' }
  await page.addInitScript(() => {
    if (location.pathname !== '/login') {
      localStorage.setItem('talentfit_token', 'synthetic-upload-token')
      localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: 'synthetic-hr', role: 'hr' }))
    }
    const original = window.fetch.bind(window)
    const control = window as any
    control.__uploadCancelled = false
    window.fetch = async (input, init) => {
      if (!String(input).endsWith('/candidates/upload/stream')) return original(input, init)
      return new Response(new ReadableStream({
        start(controller) { control.__uploadController = controller },
        cancel() { control.__uploadCancelled = true },
      }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    control.__pushUpload = (events: object[], close: boolean) => {
      for (const event of events) control.__uploadController.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
      if (close) control.__uploadController.close()
    }
  })
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = []
    if (path.endsWith('/auth/me')) body = user
    else if (path.endsWith('/knowledge/summary')) body = { documents: 0, enabled_documents: 0, hit_count: 0 }
    else if (path.endsWith('/search/status')) body = { mode: 'keyword', embedding_model: null, dimensions: 1024, indexed_chunks: 0, total_chunks: 0, pending_chunks: 0 }
    else if (path.endsWith('/dashboard/summary')) body = { total: 0, ready: 0, processing: 0, failed: 0 }
    await route.fulfill({ json: body })
  })
  return user
}

async function start(page: Page) {
  await page.goto('/candidates')
  await page.locator('input[type=file]').setInputFiles(['甲', '乙', '丙'].map(name => ({ name: name + '.txt', mimeType: 'text/plain', buffer: Buffer.from('合成简历') })))
  await page.getByRole('button', { name: '开始入库', exact: true }).click()
  await push(page, [{ type: 'file_progress', file_index: 0, filename: '甲.txt', stage: 'extract', label: '提取正文', progress: 15 }])
}

async function push(page: Page, events: object[], close = false) {
  await expect.poll(() => page.evaluate(() => Boolean((window as any).__uploadController))).toBe(true)
  await page.evaluate(({ events, close }) => (window as any).__pushUpload(events, close), { events, close })
}

const result = (index: number, status: 'ready' | 'failed' = 'ready') => ({ type: 'file_result', file_index: index, filename: ['甲.txt', '乙.txt', '丙.txt'][index], status, candidate: null, detail: status === 'failed' ? '文件无效' : null })
const beforeUnloadPrevented = (page: Page) => page.evaluate(() => !window.dispatchEvent(new Event('beforeunload', { cancelable: true })))

test('切换页面后仍继续三份简历，返回保留完成结果', async ({ page }) => {
  await fixture(page)
  await start(page)
  await page.getByRole('menuitem', { name: '人才助手' }).click()
  await expect(page.getByRole('button', { name: '查看入库进度' })).toBeVisible()
  await push(page, [result(0), { type: 'file_progress', file_index: 1, filename: '乙.txt', stage: 'embedding', label: '生成语义向量', progress: 80 }])
  await page.getByRole('button', { name: '查看入库进度' }).click()
  await expect(page.locator('.ingestion-file').filter({ hasText: '甲.txt' })).toContainText('已入库')
  await expect(page.locator('.ingestion-file').filter({ hasText: '乙.txt' })).toContainText('生成语义向量')
  await expect(page.locator('.ingestion-file')).toHaveCount(3)
  await page.getByRole('menuitem', { name: '工作台' }).click()
  await push(page, [result(1), result(2), { type: 'batch_done', total: 3, succeeded: 3, failed: 0 }], true)
  await expect(page.getByRole('button', { name: '查看入库结果' })).toBeVisible()
  await page.getByRole('button', { name: '查看入库结果' }).click()
  await expect(page.getByText('入库完成：成功3份，失败0份')).toBeVisible()
  await expect(page.locator('.ingestion-file').getByText('已入库', { exact: true })).toHaveCount(3)
  expect(await beforeUnloadPrevented(page)).toBe(false)
})

test('失败不显示100%，断流保留成功并将未完成文件标为中断', async ({ page }) => {
  await fixture(page)
  await start(page)
  await push(page, [result(0), { type: 'file_progress', file_index: 1, filename: '乙.txt', stage: 'profile', label: '字段抽取', progress: 40 }, result(1, 'failed')])
  const failed = page.locator('.ingestion-file').filter({ hasText: '乙.txt' })
  await expect(failed).toContainText('文件无效')
  await expect(failed.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '40')
  await page.getByRole('menuitem', { name: '工作台' }).click()
  await push(page, [{ type: 'file_progress', file_index: 2, filename: '丙.txt', stage: 'done', label: '等待提交', progress: 100 }], true)
  await page.getByRole('button', { name: '查看入库结果' }).click()
  await expect(page.locator('.ingestion-file').filter({ hasText: '甲.txt' })).toContainText('已入库')
  const interrupted = page.locator('.ingestion-file').filter({ hasText: '丙.txt' })
  await expect(interrupted).toContainText('中断')
  await expect(interrupted.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '99')
  await expect(page.getByRole('alert').filter({ hasText: '未收到完成确认' })).toBeVisible()
})

test('刷新关闭有保护，取消退出继续处理，确认退出清理并隔离下个账号', async ({ page }) => {
  const user = await fixture(page)
  await start(page)
  expect(await beforeUnloadPrevented(page)).toBe(true)
  await page.getByRole('banner').getByRole('button', { name: /退\s*出/ }).click()
  await expect(page.getByRole('dialog')).toContainText('仍有简历正在入库')
  await page.getByRole('button', { name: '继续入库', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page).toHaveURL(/\/candidates$/)
  await push(page, [result(0)])
  await expect(page.locator('.ingestion-file').first()).toContainText('已入库')
  expect(await page.evaluate(() => (window as any).__uploadCancelled)).toBe(false)
  await page.getByRole('banner').getByRole('button', { name: /退\s*出/ }).click()
  await page.getByRole('button', { name: '退出并中断', exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
  expect(await page.evaluate(() => localStorage.getItem('talentfit_token'))).toBeNull()
  expect(await page.evaluate(() => (window as any).__uploadCancelled)).toBe(true)
  expect(await beforeUnloadPrevented(page)).toBe(false)
  user.id = 2; user.username = 'another-hr'
  await page.evaluate(() => {
    localStorage.setItem('talentfit_token', 'another-synthetic-token')
    localStorage.setItem('talentfit_user', JSON.stringify({ id: 2, username: 'another-hr', role: 'hr' }))
    window.history.pushState({}, '', '/candidates')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect(page.getByRole('heading', { name: '人才知识库' })).toBeVisible()
  await expect(page.locator('.account-name')).toHaveText('another-hr')
  await expect(page.locator('.ingestion-file')).toHaveCount(0)
})

test('未开始的选择也在站内切页后保留', async ({ page }) => {
  await fixture(page)
  await page.goto('/candidates')
  await page.locator('input[type=file]').setInputFiles({ name: '待入库.txt', mimeType: 'text/plain', buffer: Buffer.from('合成简历') })
  await page.getByRole('menuitem', { name: '工作台' }).click()
  await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible()
  await page.getByRole('menuitem', { name: '人才知识库' }).click()
  await expect(page.locator('.ingestion-file')).toContainText('待入库.txt')
  await expect(page.getByRole('button', { name: '开始入库', exact: true })).toBeEnabled()
  expect(await beforeUnloadPrevented(page)).toBe(false)
})

test('浏览器刷新取消后保持流连接，确认离开后清理运行', async ({ page }) => {
  await fixture(page)
  await start(page)
  const refreshDialog = page.waitForEvent('dialog')
  const reloading = page.evaluate(() => window.location.reload())
  const dialog = await refreshDialog
  expect(dialog.type()).toBe('beforeunload')
  await dialog.dismiss()
  await reloading
  await push(page, [result(0)])
  await expect(page.locator('.ingestion-file').first()).toContainText('已入库')
  const closeDialog = page.waitForEvent('dialog')
  const closing = page.close({ runBeforeUnload: true })
  const confirmation = await closeDialog
  expect(confirmation.type()).toBe('beforeunload')
  await confirmation.accept()
  await closing
})

for (const change of ['logout', 'account-switch']) {
  test(`另一标签页${change}后中断并清理旧账号入库状态`, async ({ page }) => {
    await fixture(page)
    await start(page)
    const otherTab = await page.context().newPage()
    await otherTab.goto('/login')
    await otherTab.evaluate(change => {
      if (change === 'logout') {
        localStorage.removeItem('talentfit_token')
        localStorage.removeItem('talentfit_user')
      } else {
        localStorage.setItem('talentfit_token', 'new-account-token')
        localStorage.setItem('talentfit_user', JSON.stringify({ id: 2, username: 'new-hr', role: 'hr' }))
      }
    }, change)
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.locator('.ingestion-file')).toHaveCount(0)
    expect(await beforeUnloadPrevented(page)).toBe(false)
    expect(await page.evaluate(() => (window as any).__uploadCancelled)).toBe(true)
    await otherTab.close()
  })
}

for (const width of [1366, 390]) {
  test(`跨页进度提示在${width}px可见且不撑宽页面`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await fixture(page)
    await start(page)
    await page.getByRole('menuitem', { name: '工作台' }).click()
    await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible()
    const notice = page.locator('.ingestion-global-notice')
    await expect(notice).toBeVisible()
    const box = (await notice.boundingBox())!
    expect(box.x + box.width).toBeLessThanOrEqual(width)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: `tests/artifacts/ingestion-navigation-${width}-synthetic.png`, fullPage: true })
    await push(page, [result(0), result(1), result(2), { type: 'batch_done', total: 3, succeeded: 3, failed: 0 }], true)
    await expect(page.getByRole('button', { name: '查看入库结果' })).toBeVisible()
  })
}
