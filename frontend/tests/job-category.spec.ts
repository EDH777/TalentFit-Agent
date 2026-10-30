import { expect, test, type Page } from '@playwright/test'

const categories = ['产品经理', '前端开发工程师', '后端开发工程师', '测试工程师', 'AI应用开发工程师', '现场实施工程师', '硬件工程师', '采购经理']
const timestamp = '2026-10-26T08:00:00Z'
const candidate = (id: number, job_category?: string | null) => ({ id, name: `合成员工${id}`, job_category, years_experience: '3年', tags: ['React'], source_filename: '合成简历.txt', status: 'ready', enabled: true, created_at: timestamp, updated_at: timestamp, raw_text: null })

async function fixture(page: Page, role = 'hr') {
  const state = { items: [candidate(1, '前端开发工程师'), candidate(2, null), candidate(3)], queries: [] as (string | null)[], writes: [] as Record<string, unknown>[] }
  await page.addInitScript(role => {
    localStorage.setItem('talentfit_token', 'synthetic-job-category-token')
    localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: `synthetic-${role}`, role }))
  }, role)
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname, method = route.request().method()
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { id: 1, username: `synthetic-${role}`, role } })
    if (path.endsWith('/knowledge/summary')) return route.fulfill({ json: { documents: state.items.length, enabled_documents: state.items.length, hit_count: 0 } })
    if (path.endsWith('/dashboard/summary')) return route.fulfill({ json: { total: state.items.length, ready: state.items.length, processing: 0, failed: 0 } })
    if (path.endsWith('/search/status')) return route.fulfill({ json: { mode: 'keyword', embedding_model: null, dimensions: 1024, indexed_chunks: 0, total_chunks: 0, pending_chunks: 0 } })
    if (path === '/api/candidates' && method === 'GET') {
      const category = url.searchParams.get('job_category')
      state.queries.push(category)
      return route.fulfill({ json: state.items.filter(item => !category || (category === 'unclassified' ? item.job_category == null : item.job_category === category)) })
    }
    if (path.endsWith('/candidates/upload/stream')) {
      const uploaded = candidate(4, 'AI应用开发工程师')
      state.items.push(uploaded)
      const events = [{ type: 'file_result', file_index: 0, filename: '合成上传.txt', status: 'ready', candidate: uploaded, detail: null }, { type: 'batch_done', total: 1, succeeded: 1, failed: 0 }]
      return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('') })
    }
    if (path === '/api/candidates/manual' && method === 'POST') {
      const body = route.request().postDataJSON(); state.writes.push(body)
      const created = { ...candidate(5), ...body }; state.items.push(created)
      return route.fulfill({ status: 201, json: created })
    }
    const match = path.match(/^\/api\/candidates\/(\d+)$/)
    if (match) {
      const item = state.items.find(item => item.id === Number(match[1]))!
      if (method === 'PUT') { const body = route.request().postDataJSON(); state.writes.push(body); Object.assign(item, body) }
      return route.fulfill({ json: item })
    }
    const documentMatch = path.match(/^\/api\/knowledge\/documents\/(\d+)$/)
    if (documentMatch) return route.fulfill({ json: { candidate: state.items.find(item => item.id === Number(documentMatch[1])), stats: { parent_chunks: 0, child_chunks: 0, enabled_child_chunks: 0, indexed_child_chunks: 0, hit_count: 0 }, source_file_size: null } })
    if (path.endsWith('/chunks')) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } })
    return route.fulfill({ status: 404, json: { detail: '合成测试不存在此接口' } })
  })
  return state
}

test('列表显示岗位分类和待确认，筛选发送枚举或unclassified且可返回全部', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/candidates')
  await expect(page.getByRole('columnheader', { name: '岗位分类' })).toBeVisible()
  await expect(page.getByRole('row').filter({ hasText: '合成员工1' })).toContainText('前端开发工程师')
  await expect(page.getByRole('row').filter({ hasText: '合成员工2' })).toContainText('待确认')
  await expect(page.getByRole('row').filter({ hasText: '合成员工3' })).toContainText('待确认')
  expect(state.queries.at(-1)).toBeNull()
  const filter = page.getByRole('combobox', { name: '岗位分类筛选' })
  await filter.focus(); await filter.press('ArrowDown')
  await expect(page.locator('.ant-select-dropdown:visible .ant-select-item-option')).toHaveCount(10)
  await page.locator('.ant-select-dropdown:visible').getByTitle('前端开发工程师', { exact: true }).click()
  await expect.poll(() => state.queries.at(-1)).toBe('前端开发工程师')
  await expect(page.getByRole('button', { name: '合成员工2', exact: true })).toHaveCount(0)
  await filter.focus(); await filter.press('ArrowDown')
  await page.locator('.ant-select-dropdown:visible').getByTitle('待确认', { exact: true }).click()
  await expect.poll(() => state.queries.at(-1)).toBe('unclassified')
  await expect(page.getByRole('button', { name: '合成员工1', exact: true })).toHaveCount(0)
  await filter.focus(); await filter.press('ArrowDown')
  await page.locator('.ant-select-dropdown:visible').hover()
  await page.mouse.wheel(0, -600)
  await page.locator('.ant-select-dropdown:visible').getByTitle('全部岗位分类', { exact: true }).click()
  await expect(page.getByRole('button', { name: '合成员工1', exact: true })).toBeVisible()
  expect(state.queries.at(-1)).toBeNull()
})

test('人事可选择八类岗位并清空，资料表单不再发送目标职位', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/candidates')
  const row = page.getByRole('row').filter({ hasText: '合成员工1' })
  await row.getByRole('button', { name: '编辑' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByLabel('目标职位', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('columnheader', { name: '目标职位', exact: true })).toHaveCount(0)
  await dialog.getByRole('combobox', { name: '岗位分类', exact: true }).focus()
  await dialog.getByRole('combobox', { name: '岗位分类', exact: true }).press('ArrowDown')
  await expect(page.locator('.ant-select-dropdown:visible .ant-select-item-option')).toHaveCount(8)
  for (const category of categories) await expect(page.locator('.ant-select-dropdown:visible').getByTitle(category, { exact: true })).toHaveCount(1)
  await page.locator('.ant-select-dropdown:visible').getByTitle('采购经理', { exact: true }).click()
  await dialog.getByRole('button', { name: '保 存', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(1)
  expect(state.writes[0]).toMatchObject({ job_category: '采购经理', expected_updated_at: timestamp })
  expect(state.writes[0]).not.toHaveProperty('raw_text')
  expect(state.writes[0]).not.toHaveProperty('desired_position')
  await expect(row).toContainText('采购经理')
  await row.getByRole('button', { name: '编辑' }).click()
  await dialog.locator('.ant-form-item').filter({ hasText: '岗位分类' }).locator('.ant-select-clear').click()
  await dialog.getByRole('button', { name: '保 存', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(2)
  expect(state.writes[1]).toMatchObject({ job_category: null })
  expect(state.writes[1]).not.toHaveProperty('desired_position')
  await expect(row).toContainText('待确认')
})

test('知识库旧编辑表单不会覆盖员工页更新的岗位，409保留编辑内容并提示刷新', async ({ page }) => {
  const state = await fixture(page)
  await page.route('**/api/candidates/1', async route => {
    if (route.request().method() !== 'PUT') return route.fallback()
    const body = route.request().postDataJSON(); state.writes.push(body)
    if (body.expected_updated_at !== state.items[0].updated_at) return route.fulfill({ status: 409, json: { detail: '资料已被更新，请刷新后重新编辑，避免覆盖新的岗位分类' } })
    Object.assign(state.items[0], body)
    await route.fulfill({ json: state.items[0] })
  })
  await page.goto('/candidates')
  const row = page.getByRole('row').filter({ hasText: '合成员工1' })
  await row.getByRole('button', { name: '编辑' }).click()
  const dialog = page.getByRole('dialog')
  const saveButton = dialog.getByRole('button', { name: /保\s*存/ })
  await dialog.getByLabel('姓名', { exact: true }).fill('合成保留编辑')
  await dialog.getByRole('combobox', { name: '岗位分类', exact: true }).press('ArrowDown')
  await page.locator('.ant-select-dropdown:visible').getByTitle('采购经理', { exact: true }).click()
  state.items[0].job_category = '后端开发工程师'
  state.items[0].updated_at = '2026-10-27T09:00:00Z'
  await expect(saveButton).toBeEnabled()
  await expect(saveButton).not.toHaveClass(/\bant-btn-loading\b/)
  await saveButton.click()
  await expect.poll(() => state.writes.length).toBe(1)
  expect(state.writes[0]).toMatchObject({ expected_updated_at: '2026-10-26T08:00:00Z', job_category: '采购经理' })
  await expect(page.getByText('资料已被更新，请刷新后重新编辑，避免覆盖新的岗位分类', { exact: true })).toBeVisible()
  await expect(dialog).toBeVisible()
  await expect(dialog.getByLabel('姓名', { exact: true })).toHaveValue('合成保留编辑')
  await expect(dialog.locator('.ant-select-selection-item')).toHaveText('采购经理')
  expect(state.items[0].job_category).toBe('后端开发工程师')
  await dialog.getByRole('button', { name: /^取\s*消$/ }).click()
  await page.getByRole('button', { name: '刷新', exact: false }).click()
  await expect(row).toContainText('后端开发工程师')
  await row.getByRole('button', { name: '编辑' }).click()
  await expect(dialog.locator('.ant-select-selection-item')).toHaveText('后端开发工程师')
  await expect(saveButton).toBeEnabled()
  await expect(saveButton).not.toHaveClass(/\bant-btn-loading\b/)
  await saveButton.click()
  await expect.poll(() => state.writes.length).toBe(2)
  expect(state.writes[1].expected_updated_at).toBe('2026-10-27T09:00:00Z')
  await expect(dialog).toBeHidden()
})

test('手动入库可选择分类，上传完成后列表显示模型返回分类', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/candidates')
  await page.getByRole('button', { name: '手动入库' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('姓名', { exact: true }).fill('合成手动员工')
  await expect(dialog.getByLabel('目标职位', { exact: true })).toHaveCount(0)
  await dialog.getByRole('combobox', { name: '岗位分类', exact: true }).focus()
  await dialog.getByRole('combobox', { name: '岗位分类', exact: true }).press('ArrowDown')
  await page.locator('.ant-select-dropdown:visible').getByTitle('硬件工程师', { exact: true }).click()
  await dialog.getByLabel('简历正文', { exact: true }).fill('经人事确认的合成经历，用于页面测试。')
  await dialog.getByRole('button', { name: '保 存', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(1)
  expect(state.writes[0]).toMatchObject({ job_category: '硬件工程师' })
  expect(state.writes[0]).not.toHaveProperty('expected_updated_at')
  await page.locator('input[type=file]').setInputFiles({ name: '合成上传.txt', mimeType: 'text/plain', buffer: Buffer.from('合成上传测试') })
  await page.getByRole('button', { name: '开始入库', exact: true }).click()
  await expect(page.getByRole('row').filter({ hasText: '合成员工4' })).toContainText('AI应用开发工程师')
})

test('文档信息显示当前分类和旧资料待确认', async ({ page }) => {
  await fixture(page)
  await page.goto('/candidates/1')
  await expect(page.locator('.knowledge-sidebar')).toContainText('岗位分类')
  await expect(page.locator('.knowledge-sidebar')).toContainText('前端开发工程师')
  await page.goto('/candidates/3')
  await expect(page.locator('.knowledge-sidebar')).toContainText('待确认')
})

test('管理员仍无人才维护入口和分类编辑权限', async ({ page }) => {
  const state = await fixture(page, 'admin')
  await page.goto('/candidates')
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('menuitem', { name: '人才知识库' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '手动入库', exact: true })).toHaveCount(0)
  expect(state.writes).toHaveLength(0)
})

for (const width of [1366, 390]) {
  test(`分类控件在${width}px保持紧凑且页面不溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await fixture(page)
    await page.goto('/candidates')
    if (width === 390) await expect.poll(async () => Math.round((await page.locator('.ant-layout-sider').boundingBox())!.width)).toBe(80)
    await expect(page.getByRole('combobox', { name: '岗位分类筛选' })).toBeVisible()
    await expect(page.getByRole('button', { name: '合成员工1', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: `tests/artifacts/job-category-${width}-synthetic.png`, fullPage: true })
  })
}
