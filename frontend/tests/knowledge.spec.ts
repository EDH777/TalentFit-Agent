import { test, expect, type Page } from '@playwright/test'

const timestamp = '2026-10-25T08:00:00Z'
const candidate = { id: 41, name: '合成候选人', job_category: '测试工程师', years_experience: '3年', tags: ['自动化'], source_filename: null, status: 'ready', enabled: true, created_at: timestamp, updated_at: timestamp, raw_text: '合成原文：自动化测试经验。原文保持不变。' }
const chunk = (id: number, content: string, parent_id: number | null = null) => ({ id, candidate_id: 41, parent_id, chunk_type: parent_id ? 'child' : 'parent', position: id - 1, content, enabled: true, effective_enabled: true, char_count: content.length, hit_count: 2, child_count: parent_id ? 0 : 1, enabled_child_count: parent_id ? 0 : 1, indexed: Boolean(parent_id), created_at: timestamp, updated_at: timestamp })

async function fixture(page: Page, role = 'hr') {
  const state = { candidate: { ...candidate }, chunks: [chunk(1, '父段测试内容'), chunk(2, '子段测试内容', 1)], fail: '', writes: 0, lastRecall: null as any, waitMutation: Promise.resolve() }
  await page.addInitScript((role) => {
    localStorage.setItem('talentfit_token', 'synthetic-fixture-token')
    localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: role, role }))
  }, role)
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url()), path = url.pathname, method = route.request().method()
    const body = method === 'POST' || method === 'PATCH' ? route.request().postDataJSON() : null
    const effective = (c: typeof state.chunks[number]) => c.enabled && state.candidate.enabled && (!c.parent_id || state.chunks.find(p => p.id === c.parent_id)?.enabled === true)
    const detail = () => { const { raw_text, ...metadata } = state.candidate; return { candidate: metadata, stats: { parent_chunks: state.chunks.filter(c => !c.parent_id).length, child_chunks: state.chunks.filter(c => c.parent_id).length, enabled_child_chunks: state.chunks.filter(c => c.parent_id && effective(c)).length, indexed_child_chunks: state.chunks.filter(c => c.parent_id).length, character_count: raw_text.length, hit_count: 2 }, source_file_size: null } }
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { id: 1, username: role, role } })
    if (path.endsWith('/dashboard/summary')) return route.fulfill({ json: { total: 1, ready: 1, processing: 0, failed: 0 } })
    if (path.endsWith('/search/status')) return route.fulfill({ json: { mode: 'keyword', embedding_model: null, dimensions: 1024, indexed_chunks: 0, total_chunks: 1, pending_chunks: 1 } })
    if (path === '/api/candidates') return route.fulfill({ json: [state.candidate] })
    if (path === '/api/knowledge/summary') {
      if (state.fail === 'summary') return route.fulfill({ status: 503, json: { detail: '统计服务暂不可用' } })
      return route.fulfill({ json: { documents: 1, enabled_documents: Number(state.candidate.enabled), parent_chunks: 1, child_chunks: 1, enabled_child_chunks: 1, indexed_child_chunks: 1, hit_count: 2 } })
    }
    if (path === '/api/knowledge/documents/41' && method === 'GET') return route.fulfill({ json: detail() })
    if (path.endsWith('/retrieval-test')) {
      state.lastRecall = body
      if (state.fail === 'recall') return route.fulfill({ status: 503, json: { detail: '召回服务暂不可用' } })
      return route.fulfill({ json: { mode: 'keyword', embedding_model: null, warning: '向量服务暂不可用，已使用关键词检索', elapsed_ms: 12, hits: [{ chunk_id: 2, parent_id: 1, candidate_id: 41, name: candidate.name, position: 1, content: '子段测试内容', parent_content: '父段测试内容', score: 0.0164, keyword_rank: 1, vector_rank: null }] } })
    }
    if (path.endsWith('/enabled') || path.endsWith('/documents/batch')) {
      state.writes++; await state.waitMutation
      if (state.fail === 'toggle') return route.fulfill({ status: 503, json: { detail: '启停失败，请重试' } })
      state.candidate.enabled = path.endsWith('/enabled') ? body.enabled : body.action === 'enable'
      return route.fulfill({ json: path.endsWith('/enabled') ? state.candidate : { updated: 1 } })
    }
    if (path.endsWith('/chunks') && method === 'GET') {
      const parent = Number(url.searchParams.get('parent_id')) || null, q = url.searchParams.get('q') || '', enabled = url.searchParams.get('enabled') || 'all'
      const all = state.chunks.filter(c => c.parent_id === parent && c.content.includes(q) && (enabled === 'all' || c.enabled === (enabled === 'true'))).map(c => ({ ...c, effective_enabled: effective(c), child_count: state.chunks.filter(child => child.parent_id === c.id).length, enabled_child_count: state.chunks.filter(child => child.parent_id === c.id && effective(child)).length }))
      const pageNumber = Number(url.searchParams.get('page')) || 1, size = Number(url.searchParams.get('page_size')) || 10
      return route.fulfill({ json: { items: all.slice((pageNumber - 1) * size, pageNumber * size), total: all.length, page: pageNumber, page_size: size } })
    }
    if (path.endsWith('/chunks') && method === 'POST') {
      state.writes++
      const item = chunk(Math.max(...state.chunks.map(c => c.id)) + 1, body.content, body.parent_id || null)
      state.chunks.push(item)
      return route.fulfill({ status: 201, json: item })
    }
    const match = path.match(/\/chunks\/(\d+)$/)
    if (match) {
      state.writes++; await state.waitMutation
      if (state.fail === 'chunk') return route.fulfill({ status: 503, json: { detail: '分段保存失败' } })
      const id = Number(match[1]), item = state.chunks.find(c => c.id === id)!
      if (method === 'DELETE') { state.chunks = state.chunks.filter(c => c.id !== id && c.parent_id !== id); return route.fulfill({ status: 204 }) }
      Object.assign(item, body, { char_count: (body.content || item.content).length })
      return route.fulfill({ json: item })
    }
    if (path.endsWith('/chunks/batch')) {
      state.writes++
      if (body.action === 'delete') state.chunks = state.chunks.filter(c => !body.ids.includes(c.id) && !body.ids.includes(c.parent_id))
      else state.chunks.forEach(c => { if (body.ids.includes(c.id)) c.enabled = body.action === 'enable' })
      return route.fulfill({ json: { updated: body.ids.length } })
    }
    return route.fulfill({ status: 404, json: { detail: '合成测试不存在此接口' } })
  })
  return state
}

test('文档列表启停等待服务确认，失败保留原状态，支持批量启用', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/candidates')
  const toggle = page.getByRole('switch', { name: '启用文档：合成候选人' })
  await expect(toggle).toBeChecked()
  state.fail = 'toggle'
  await toggle.click()
  await expect(page.getByText('启停失败，请重试')).toBeVisible()
  await expect(toggle).toBeChecked()
  state.fail = ''
  let release!: () => void
  state.waitMutation = new Promise<void>(resolve => { release = resolve })
  await toggle.click()
  await expect(toggle).toBeDisabled()
  await expect(toggle).toBeChecked()
  release()
  await expect(toggle).not.toBeChecked()
  await page.getByRole('checkbox', { name: '选择文档：合成候选人' }).check()
  await page.getByRole('button', { name: '批量启用', exact: true }).click()
  await expect(toggle).toBeChecked()
  await page.getByRole('button', { name: '合成候选人', exact: true }).click()
  await expect(page).toHaveURL(/\/candidates\/41$/)
  await expect(page.getByRole('heading', { name: '合成候选人' })).toBeVisible()
})

test('文档页移除原文、处理文本和分段设置，保留父子分段管理', async ({ page }) => {
  await fixture(page)
  await page.goto('/candidates/41')
  await expect(page.getByText('父段测试内容', { exact: true })).toBeVisible()
  for (const name of ['原始正文', '入库处理文本', '分段设置']) await expect(page.getByRole('tab', { name, exact: true })).toHaveCount(0)
  for (const name of ['预览分段', '重新分段', '重新预处理入库']) await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '展开父分段 1详情' }).click()
  await expect(page.getByText('子段测试内容', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '编辑子分段 2' })).toBeVisible()
  await page.screenshot({ path: 'tests/artifacts/document-management-only-synthetic.png', animations: 'disabled' })
})

test('上传文档编辑只维护人才字段，不展示或发送提取原文', async ({ page }) => {
  const state = await fixture(page)
  Object.assign(state.candidate, { source_filename: 'synthetic.pdf' })
  let submitted: any = null
  await page.route('**/api/candidates/41', route => {
    if (route.request().method() === 'PUT') submitted = route.request().postDataJSON()
    return route.fulfill({ json: { ...state.candidate, raw_text: null } })
  })
  await page.goto('/candidates')
  await page.getByRole('button', { name: /编辑$/ }).click()
  await expect(page.getByRole('dialog', { name: '编辑人才资料' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: '简历正文' })).toHaveCount(0)
  await page.getByRole('button', { name: /^保\s*存$/ }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(submitted).not.toHaveProperty('raw_text')
})

test('父子分段可展开、增改删，失败保留编辑内容', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/candidates/41')
  await page.getByRole('button', { name: /^展开父分段 \d+详情$/ }).click()
  await expect(page.getByText('子段测试内容', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '编辑子分段 2' }).click()
  await page.getByRole('textbox', { name: '分段正文' }).fill('已修改子分段')
  state.fail = 'chunk'
  await page.getByRole('button', { name: '保存分段' }).click()
  await expect(page.getByText('分段保存失败', { exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: '分段正文' })).toHaveValue('已修改子分段')
  state.fail = ''
  await page.getByRole('button', { name: '保存分段' }).click()
  await expect(page.locator('.knowledge-child-card .knowledge-content').filter({ hasText: '已修改子分段' })).toBeVisible()
  await page.getByRole('button', { name: /新增子分段$/ }).click()
  await page.getByRole('textbox', { name: '分段正文' }).fill('新增合成子段')
  await page.getByRole('button', { name: '保存分段' }).click()
  await expect(page.locator('.knowledge-child-card .knowledge-content').filter({ hasText: '新增合成子段' })).toBeVisible()
  await page.getByRole('button', { name: '删除子分段 3' }).click()
  await page.getByRole('button', { name: /^取\s*消$/ }).click()
  await expect(page.locator('.knowledge-child-card .knowledge-content').filter({ hasText: '新增合成子段' })).toBeVisible()
  await page.getByRole('button', { name: '删除子分段 3' }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(page.locator('.knowledge-child-card .knowledge-content').filter({ hasText: '新增合成子段' })).toHaveCount(0)
  await page.getByRole('button', { name: /^收起父分段 \d+详情$/ }).click()
})

test('停用父段显示实际不可检索且保留子段开关；可按正文搜索与筛选', async ({ page }) => {
  await fixture(page)
  await page.goto('/candidates/41')
  await page.getByRole('switch', { name: '启用父分段 1' }).click()
  await page.getByRole('button', { name: /^展开父分段 \d+详情$/ }).click()
  await expect(page.getByRole('switch', { name: '启用子分段 2' })).toBeChecked()
  await expect(page.getByText('父分段已停用，子分段当前不参与检索')).toBeVisible()
  await page.getByRole('button', { name: /^收起父分段 \d+详情$/ }).click()
  await page.getByRole('searchbox', { name: '搜索父分段正文' }).fill('不存在')
  await page.getByRole('searchbox', { name: '搜索父分段正文' }).press('Enter')
  await expect(page.getByText('暂无符合条件的父分段')).toBeVisible()
})


test('召回测试按文档发送真实参数并展示命中来源、排名和降级提示', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/knowledge/retrieval-test?candidate_id=41')
  await page.getByRole('textbox', { name: '测试问题' }).fill('自动化测试')
  await page.getByRole('spinbutton', { name: '返回条数' }).fill('4')
  await page.getByRole('button', { name: /开始测试$/ }).click()
  await expect(page.getByText('子段测试内容', { exact: true })).toBeVisible()
  await expect(page.getByText('向量服务暂不可用，已使用关键词检索')).toBeVisible()
  await expect(page.getByText('父分段编号 #1', { exact: true })).toBeVisible()
  expect(state.lastRecall).toEqual({ query: '自动化测试', limit: 4, candidate_id: 41 })
  await page.screenshot({ path: 'tests/artifacts/knowledge-retrieval-synthetic.png', fullPage: true, animations: 'disabled' })
})

test('统计失败明确报错，管理员无法进入知识库管理和召回页面', async ({ page }) => {
  const state = await fixture(page)
  state.fail = 'summary'
  await page.goto('/candidates')
  await expect(page.getByText('统计服务暂不可用')).toBeVisible()
  await page.unroute('**/api/**')
  await fixture(page, 'admin')
  await page.goto('/candidates/41')
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('button', { name: '新增父分段', exact: true })).toHaveCount(0)
  await page.goto('/knowledge/retrieval-test')
  await expect(page).toHaveURL('/')
})

test('父段和子段分页独立，批量停用保留分段内容与子段开关', async ({ page }) => {
  const state = await fixture(page)
  state.chunks.push(...Array.from({ length: 10 }, (_, index) => chunk(index + 10, `合成父段${index + 10}`)), ...Array.from({ length: 10 }, (_, index) => chunk(index + 30, `合成子段${index + 30}`, 1)))
  await page.goto('/candidates/41')
  await page.getByTitle('2', { exact: true }).click()
  await expect(page.getByText('合成父段19', { exact: true })).toBeVisible()
  await page.getByRole('checkbox', { name: '选择本页', exact: true }).check()
  await page.getByRole('button', { name: '批量停用', exact: true }).click()
  await expect(page.getByRole('switch', { name: '启用父分段 19' })).not.toBeChecked()
  await page.getByRole('combobox', { name: '父分段开关状态' }).focus()
  await page.getByRole('combobox', { name: '父分段开关状态' }).press('ArrowDown')
  await page.getByTitle('自身已停用', { exact: true }).click()
  await expect(page.locator('.knowledge-parent-card')).toHaveCount(1)
  await expect(page.getByText('合成父段19', { exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: '父分段开关状态' }).focus()
  await page.getByRole('combobox', { name: '父分段开关状态' }).press('ArrowDown')
  await page.getByTitle('全部开关状态', { exact: true }).click()
  await page.getByRole('button', { name: /^展开父分段 \d+详情$/ }).first().click()
  const inline = page.locator('.knowledge-inline-children')
  await inline.getByTitle('2', { exact: true }).click()
  await expect(inline.getByText('合成子段39', { exact: true })).toBeVisible()
  await expect(inline.getByRole('switch', { name: '启用子分段 39' })).toBeChecked()
  await inline.getByRole('button', { name: '查看子分段 39详情' }).click()
  await expect(page.getByRole('dialog', { name: '子分段 #39 详情' })).toContainText('合成子段39')
})

test('父分段新增、编辑、删除均等待确认', async ({ page }) => {
  await fixture(page)
  await page.goto('/candidates/41')
  await page.getByRole('button', { name: /新增父分段$/ }).click()
  await page.getByRole('textbox', { name: '分段正文' }).fill('新增父分段合成正文')
  await page.getByRole('button', { name: '保存分段', exact: true }).click()
  await expect(page.locator('.knowledge-parent-card .knowledge-content').filter({ hasText: '新增父分段合成正文' })).toBeVisible()
  await page.getByRole('button', { name: '编辑父分段 3' }).click()
  await expect(page.getByText(/修改父分段正文会重新生成/)).toBeVisible()
  await page.getByRole('textbox', { name: '分段正文' }).fill('编辑后的合成父段')
  await page.getByRole('button', { name: '保存分段', exact: true }).click()
  await expect(page.locator('.knowledge-parent-card .knowledge-content').filter({ hasText: '编辑后的合成父段' })).toBeVisible()
  await page.getByRole('button', { name: '删除父分段 3' }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(page.locator('.knowledge-parent-card .knowledge-content').filter({ hasText: '编辑后的合成父段' })).toHaveCount(0)

})

test('旧搜索响应不能覆盖新结果，召回失败不会显示上次命中', async ({ page }) => {
  const state = await fixture(page)
  let release!: () => void
  const oldResponse = new Promise<void>(resolve => { release = resolve })
  let first = true
  await page.route('**/api/knowledge/documents/41/chunks?**', async route => {
    if (!first) return route.fallback()
    first = false
    await oldResponse
    return route.fulfill({ json: { items: [chunk(90, '过时响应')], total: 1, page: 1, page_size: 10 } })
  })
  await page.goto('/candidates/41')
  await page.getByRole('searchbox', { name: '搜索父分段正文' }).fill('父段测试内容')
  await page.getByRole('searchbox', { name: '搜索父分段正文' }).press('Enter')
  await expect(page.getByText('父段测试内容', { exact: true })).toBeVisible()
  const lateResponse = page.waitForResponse(response => response.url().includes('/documents/41/chunks?') && !new URL(response.url()).searchParams.get('q'))
  release()
  await lateResponse
  await expect(page.getByText('过时响应', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '召回测试', exact: true }).click()
  await page.getByRole('textbox', { name: '测试问题' }).fill('自动化')
  await page.getByRole('button', { name: /开始测试$/ }).click()
  await expect(page.getByText('子段测试内容', { exact: true })).toBeVisible()
  state.fail = 'recall'
  await page.getByRole('button', { name: /开始测试$/ }).click()
  await expect(page.getByText('召回服务暂不可用', { exact: true })).toBeVisible()
  await expect(page.getByText('子段测试内容', { exact: true })).toHaveCount(0)
})

test('合成父子分段页面截图', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 })
  const state = await fixture(page)
  state.chunks.push(chunk(3, '合成项目经验：建设接口自动化测试流水线，维护回归用例与测试报告。'), chunk(4, '合成教育与技能：软件工程、测试设计、SQL 与 Python。'))
  await page.goto('/candidates/41')
  await expect(page.locator('.knowledge-parent-card')).toHaveCount(3)
  await expect(page.getByRole('button', { name: '召回测试', exact: true })).toBeEnabled()
  await page.screenshot({ path: 'tests/artifacts/knowledge-document-synthetic.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: /^展开父分段 \d+详情$/ }).first().click()
  await expect(page.getByText('子段测试内容', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'tests/artifacts/knowledge-children-synthetic.png', fullPage: true, animations: 'disabled' })
})

test('紧凑父段仅首行预览，子段在对应卡片内展开收起且不重复父段正文', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  const state = await fixture(page)
  const firstLine = '首行预览内容'.repeat(50)
  state.chunks[0].content = firstLine + '\n不应在列表重复显示的完整父段第二行'
  state.chunks.push(...Array.from({ length: 5 }, (_, index) => chunk(index + 10, `合成父段预览${index} `.repeat(30))))
  await page.goto('/candidates/41')
  const cards = page.locator('.knowledge-parent-card')
  await expect(cards).toHaveCount(6)
  const preview = cards.first().locator('.knowledge-parent-preview')
  await expect(preview).toHaveText(firstLine)
  const metrics = await preview.evaluate(element => ({ height: element.getBoundingClientRect().height, lineHeight: parseFloat(getComputedStyle(element).lineHeight), clipped: element.scrollWidth > element.clientWidth }))
  expect(metrics.height).toBeLessThanOrEqual(metrics.lineHeight + 1)
  expect(metrics.clipped).toBe(true)
  expect((await cards.first().boundingBox())!.height).toBeLessThanOrEqual(124)
  expect((await cards.nth(3).boundingBox())!.y + (await cards.nth(3).boundingBox())!.height).toBeLessThanOrEqual(768)
  await page.screenshot({ path: 'tests/artifacts/knowledge-compact-desktop-synthetic.png', animations: 'disabled' })
  await cards.first().getByRole('button', { name: '展开父分段 1详情' }).click()
  await expect(cards.first().locator('.knowledge-child-card')).toContainText('子段测试内容')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('不应在列表重复显示的完整父段第二行', { exact: true })).toHaveCount(0)
  await cards.first().getByRole('button', { name: '收起父分段 1详情' }).click()
  await expect(cards.first().locator('.knowledge-child-card')).toHaveCount(0)
})

test('切换展开父段时，迟到子段不能进入另一张父段卡片', async ({ page }) => {
  const state = await fixture(page)
  state.chunks.push(chunk(3, '第二组父段'), chunk(4, '第二组子段', 3))
  let release!: () => void
  const waiting = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/knowledge/documents/41/chunks?**', async route => {
    if (new URL(route.request().url()).searchParams.get('parent_id') !== '1') return route.fallback()
    await waiting
    return route.fulfill({ json: { items: [chunk(2, '迟到的第一组子段', 1)], total: 1, page: 1, page_size: 10 } })
  })
  await page.goto('/candidates/41')
  const request = page.waitForRequest(request => new URL(request.url()).searchParams.get('parent_id') === '1')
  await page.getByRole('button', { name: '展开父分段 1详情' }).click()
  await request
  await page.getByRole('button', { name: '展开父分段 3详情' }).click()
  const secondCard = page.locator('.knowledge-parent-card').filter({ hasText: '第二组父段' })
  await expect(secondCard.locator('.knowledge-child-card')).toContainText('第二组子段')
  const response = page.waitForResponse(response => new URL(response.url()).searchParams.get('parent_id') === '1')
  release()
  await response
  await expect(page.getByText('迟到的第一组子段', { exact: true })).toHaveCount(0)
  await secondCard.getByRole('button', { name: '收起父分段 3详情' }).click()
  await expect(page.locator('.knowledge-inline-children')).toHaveCount(0)
})

test('窄屏内联子段和长首行不产生横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await fixture(page)
  state.chunks[0].content = '很长的合成父段首行'.repeat(80) + '\n第二行正文'
  state.chunks[1].content = '合成子段长内容 '.repeat(100)
  await page.goto('/candidates/41')
  await page.getByRole('button', { name: '展开父分段 1详情' }).click()
  await expect(page.locator('.knowledge-inline-children .knowledge-child-card')).toBeVisible()
  const widths = await page.locator('.knowledge-document').evaluate(element => ({ client: element.clientWidth, scroll: element.scrollWidth }))
  expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1)
  await page.screenshot({ path: 'tests/artifacts/knowledge-inline-mobile-synthetic.png', fullPage: true, animations: 'disabled' })
})

test('人才列表紧凑显示入库时间、去掉来源且表头与内容同对齐', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await fixture(page)
  await page.route('**/api/candidates?**', route => route.fulfill({ json: Array.from({ length: 12 }, (_, index) => ({ ...candidate, id: index + 1, name: `合成列表人才${index + 1}` })) }))
  await page.goto('/candidates')
  await expect(page.getByRole('columnheader', { name: '来源', exact: true })).toHaveCount(0)
  await expect(page.getByRole('columnheader', { name: '入库时间', exact: true })).toBeVisible()
  const heads = await page.locator('.ant-table-thead th').evaluateAll(elements => elements.map(element => ({ text: (element.textContent || '').trim(), align: getComputedStyle(element).textAlign })))
  // 文本列表头跟随正文左对齐；状态/开关/操作这组控件列居中。
  for (const text of ['姓名', '岗位分类', '工作年限', '标签', '入库时间']) expect(heads.find(item => item.text === text)?.align).toBe('start')
  for (const text of ['状态', '参与检索', '操作']) expect(heads.find(item => item.text === text)?.align).toBe('center')
  await expect(page.getByRole('cell', { name: '2026-10-25 16:00', exact: true }).first()).toBeVisible()
  const rows = page.locator('.ant-table-tbody > tr[data-row-key]')
  await expect(rows).toHaveCount(10)
  const sixth = (await rows.nth(5).boundingBox())!
  expect(sixth.y + sixth.height).toBeLessThanOrEqual(768)
  await page.screenshot({ path: 'tests/artifacts/knowledge-list-compact-synthetic.png', animations: 'disabled' })
})

test('手工编辑使用父4000子2048字符限制', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/candidates/41')
  await page.getByRole('button', { name: '编辑父分段 1' }).click()
  await expect(page.getByRole('textbox', { name: '分段正文' })).toHaveAttribute('maxlength', '4000')
  await page.getByRole('button', { name: /^取\s*消$/ }).click()
  await page.getByRole('button', { name: '展开父分段 1详情' }).click()
  await page.getByRole('button', { name: '编辑子分段 2' }).click()
  await expect(page.getByRole('textbox', { name: '分段正文' })).toHaveAttribute('maxlength', '2048')
})

test.describe('入库时间时区', () => {
  test.use({ timezoneId: 'Asia/Shanghai' })
  test('列表与文档详情将无时区UTC时间一致显示为北京时间', async ({ page }) => {
    const state = await fixture(page)
    state.candidate.created_at = '2026-10-25T08:00:00'
    await page.goto('/candidates')
    await expect(page.getByRole('cell', { name: '2026-10-25 16:00', exact: true })).toBeVisible({ timeout: 15000 })
    await page.getByRole('button', { name: '合成候选人', exact: true }).click()
    await expect(page.locator('.knowledge-sidebar .ant-descriptions-item').filter({ hasText: '入库时间' })).toContainText('2026-10-25 16:00')
  })
})

test('工作台、人才助手和知识库使用统一字号与44px顶部栏', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await fixture(page)
  await page.route('**/api/chat/sessions', route => route.fulfill({ json: [] }))
  for (const [path, heading] of [['/', '工作台'], ['/chat', '人才助手'], ['/candidates', '人才知识库'], ['/knowledge/retrieval-test', '召回测试']]) {
    await page.goto(path)
    const title = page.getByRole('heading', { name: heading, exact: true })
    await expect(title).toBeVisible()
    const fonts = await title.evaluate(element => ({ size: getComputedStyle(element).fontSize, line: getComputedStyle(element).lineHeight }))
    expect(fonts).toEqual({ size: '20px', line: '28px' })
    await expect(page.locator('.page-heading .ant-typography-secondary').first()).toHaveCSS('font-size', '13px')
    expect((await page.locator('.topbar').boundingBox())!.height).toBe(44)
    expect((await page.locator('.brand').boundingBox())!.height).toBe(44)
    if (path === '/' || path === '/chat') await page.screenshot({ path: `tests/artifacts/unified-${path === '/' ? 'dashboard' : 'chat'}-synthetic.png`, animations: 'disabled' })
  }
})

test('空知识库不显示关键词模式或分段索引计数，有资料后仍显示实际模式', async ({ page }) => {
  await fixture(page)
  let documents = 0
  let activeChunks = 0
  await page.route('**/api/knowledge/summary', route => route.fulfill({ json: { documents, enabled_documents: documents, parent_chunks: 5, child_chunks: 8, enabled_child_chunks: activeChunks, indexed_child_chunks: activeChunks, hit_count: 0 } }))
  await page.route('**/api/search/status', route => route.fulfill({ json: { mode: activeChunks ? 'hybrid' : 'keyword', embedding_model: 'text-embedding-v4', dimensions: 1024, indexed_chunks: activeChunks, total_chunks: activeChunks, pending_chunks: 0 } }))
  await page.route('**/api/candidates?**', route => route.fulfill({ json: documents ? [candidate] : [] }))
  await page.goto('/candidates')
  await expect(page.getByText('暂无知识文档，入库后可查看检索状态。', { exact: true })).toBeVisible()
  const overview = page.locator('.knowledge-overview')
  await expect(overview.getByText('关键词检索', { exact: true })).toHaveCount(0)
  await expect(overview).not.toContainText('父分段')
  await expect(overview).not.toContainText('子分段')
  await expect(overview).not.toContainText('已索引')
  await expect(overview).not.toContainText('可检索子分段')
  await page.screenshot({ path: 'tests/artifacts/knowledge-empty-synthetic.png', animations: 'disabled' })
  documents = 1
  await page.getByRole('button', { name: /刷\s*新$/ }).click()
  await expect(page.getByText('暂无可检索内容，请检查文档和分段是否已启用。', { exact: true })).toBeVisible()
  activeChunks = 8
  await page.getByRole('button', { name: /刷\s*新$/ }).click()
  await expect(page.getByText('混合检索：关键词 + 语义向量', { exact: true })).toBeVisible()
  await expect(overview).not.toContainText('个文本块')
  await expect(overview).not.toContainText('父分段')
  activeChunks = 0
  await page.route('**/api/chat/sessions', route => route.fulfill({ json: [] }))
  await page.goto('/chat')
  await expect(page.getByText('暂无可检索内容', { exact: true })).toBeVisible()
  await expect(page.locator('.page-heading').getByText('关键词检索', { exact: true })).toHaveCount(0)
})

test('聊天仅一份空态，输入区贴近会话面板底部', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 })
  await fixture(page)
  await page.route('**/api/chat/sessions', route => route.fulfill({ json: [] }))
  await page.goto('/chat')
  await expect(page.locator('.chat-card .ant-empty')).toHaveCount(1)
  const panel = (await page.locator('.chat-card').boundingBox())!
  const composer = (await page.locator('.chat-input').boundingBox())!
  expect(panel.y + panel.height - composer.y - composer.height).toBeLessThanOrEqual(22)
  expect(composer.y).toBeGreaterThan(panel.y + panel.height / 2)
  await page.screenshot({ path: 'tests/artifacts/impeccable-chat-desktop-synthetic.png', animations: 'disabled' })
})

test('390px登录表单在窗口内完整呈现', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '企业人才助手', exact: true })).toBeVisible()
  const card = (await page.locator('.login-card').boundingBox())!
  expect(card.x).toBeGreaterThanOrEqual(16)
  expect(card.x + card.width).toBeLessThanOrEqual(374)
  const width = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }))
  expect(width.page).toBeLessThanOrEqual(width.viewport)
  await expect(page.getByLabel('密码', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'tests/artifacts/impeccable-login-mobile-synthetic.png', animations: 'disabled' })
})

test('知识库失败原因省略后仍保留完整提示', async ({ page }) => {
  const state = await fixture(page)
  const reason = '合成解析失败原因：文件中文字映射缺失，请检查原文件或重新上传。'
  Object.assign(state.candidate, { status: 'failed', error_message: reason })
  await page.goto('/candidates')
  await expect(page.getByText(`失败：${reason}`, { exact: true })).toHaveAttribute('title', reason)
})

for (const width of [1024, 390]) {
  test(`知识库长姓名与长账号在${width}px不撑宽整页`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const state = await fixture(page)
    state.candidate.name = '合成长姓名'.repeat(30)
    await page.route('**/api/auth/me', route => route.fulfill({ json: { id: 1, username: 'synthetic-long-account-name-for-layout', role: 'hr' } }))
    await page.goto('/candidates')
    await expect(page.getByRole('button', { name: state.candidate.name, exact: true })).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
    await page.screenshot({ path: `tests/artifacts/impeccable-list-${width}-synthetic.png`, fullPage: true, animations: 'disabled' })
  })
}


