import { test, expect, type Page } from '@playwright/test'

const timestamp = '2026-10-25T08:00:00Z'
const session = { id: 1, title: '测试会话', created_at: timestamp, updated_at: timestamp }
const candidate = { id: 10, name: '测试人才', job_category: '后端开发工程师', years_experience: '5年', tags: ['Java'], status: 'ready', created_at: timestamp, updated_at: timestamp, raw_text: '测试简历' }
const status = { mode: 'keyword', embedding_model: 'text-embedding-v4', dimensions: 1024, indexed_chunks: 1, total_chunks: 3, pending_chunks: 2 }

async function fixture(page: Page, role = 'hr', responseStatus = 200) {
  const state = { messages: [] as object[], candidates: [] as object[], status: { ...status } }
  await page.addInitScript(({ role, responseStatus }) => {
    if (location.pathname !== '/login') {
      localStorage.setItem('talentfit_token', 'fixture-token')
      localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: role, role }))
    }
    const original = window.fetch.bind(window)
    const control = window as any
    window.fetch = async (input, init) => {
      const url = String(input)
      if (!url.endsWith('/stream')) return original(input, init)
      control.__streamRequest = {
        url, authorization: new Headers(init?.headers).get('Authorization'),
        files: init?.body instanceof FormData ? init.body.getAll('files').map((file) => (file as File).name) : [],
      }
      if (responseStatus !== 200) return new Response(JSON.stringify({ detail: '登录已失效' }), { status: responseStatus, headers: { 'Content-Type': 'application/json' } })
      return new Response(new ReadableStream({ start(controller) { control.__streamController = controller } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    control.__push = (events: object[], close: boolean) => {
      for (const event of events) control.__streamController.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
      if (close) control.__streamController.close()
    }
  }, { role, responseStatus })
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = []
    if (path.endsWith('/auth/me')) body = { id: 1, username: role, role }
    else if (path.endsWith('/chat/sessions')) body = [session]
    else if (path.endsWith('/messages')) body = state.messages
    else if (path.endsWith('/search/status')) body = state.status
    else if (path.endsWith('/candidates')) body = state.candidates
    await route.fulfill({ json: body })
  })
  return state
}

async function push(page: Page, events: object[], close = false) {
  await expect.poll(() => page.evaluate(() => Boolean((window as any).__streamController))).toBe(true)
  await page.evaluate(({ events, close }) => (window as any).__push(events, close), { events, close })
}

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`用户气泡靠右、助手靠左，流式与历史一致 ${viewport.width}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    const state = await fixture(page, 'admin')
    await page.goto('/chat')
    await page.getByLabel('聊天消息').fill('用户消息')
    await page.getByRole('button', { name: /发送/ }).click()
    await push(page, [{ type: 'delta', content: '助手回复' }])
    for (const phase of ['stream', 'history']) {
      const user = page.locator('.message-row.user .message-bubble')
      const assistant = page.locator('.message-row.assistant .message-bubble')
      await expect(assistant).toContainText('助手回复')
      const userBox = (await user.boundingBox())!
      const assistantBox = (await assistant.boundingBox())!
      const userAvatar = (await page.locator('.message-row.user .message-avatar').boundingBox())!
      const assistantAvatar = (await page.locator('.message-row.assistant .message-avatar').boundingBox())!
      await testInfo.attach(`${phase}-geometry`, { body: JSON.stringify({ userBox, assistantBox, userAvatar, assistantAvatar }), contentType: 'application/json' })
      expect.soft(userBox.x + userBox.width / 2).toBeGreaterThan(assistantBox.x + assistantBox.width / 2)
      expect.soft(userAvatar.x).toBeGreaterThanOrEqual(userBox.x + userBox.width)
      expect.soft(assistantAvatar.x + assistantAvatar.width).toBeLessThanOrEqual(assistantBox.x)
      if (phase === 'stream') {
        const answer = { id: 2, role: 'assistant', content: '助手回复', sources: [], created_at: timestamp }
        state.messages = [{ id: 1, role: 'user', content: '用户消息', sources: [], created_at: timestamp }, answer]
        await push(page, [{ type: 'done', message: answer, intent: 'general', sources: [] }], true)
        await page.reload()
      }
    }
  })
}

const node = (id: string, title: string, nodeId = id, nodeType = 'custom-tool') => ({ id, node_id: nodeId, title, node_type: nodeType, status: 'running', started_at: new Date().toISOString() })

test('Agent按真实事件展示任意节点、并行与重复执行，完成后恢复历史', async ({ page }) => {
  const state = await fixture(page, 'admin')
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/chat')
  await page.getByLabel('聊天消息').fill('推荐Java人才')
  await page.getByRole('button', { name: /发送/ }).click()
  const nodes = [node('a', '理解岗位', 'router', 'router'), node('b', '检索候选人证据', 'lookup', 'retrieval'), node('c', '并行评分', 'score', 'llm'), node('d', '外部核验', 'verify', 'future-tool'), node('e', '再次检索', 'lookup', 'retrieval')]
  await push(page, [
    ...nodes.slice(0, 3).map((item) => ({ type: 'node_started', node: item })),
    { type: 'node_finished', node: { ...nodes[1], status: 'succeeded', elapsed_ms: 120 } },
    ...nodes.slice(3).map((item) => ({ type: 'node_started', node: item })),
    { type: 'retrieval', mode: 'hybrid', embedding_model: 'text-embedding-v4', warning: null },
    { type: 'delta', content: '第一段答案' },
  ])
  await expect(page.getByRole('button', { name: '执行过程', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('.trace-node')).toHaveCount(5)
  await expect(page.locator('.trace-node-title')).toHaveText(['理解岗位', '检索候选人证据', '并行评分', '外部核验', '再次检索'])
  await expect(page.locator('.trace-node[data-status="running"]')).toHaveCount(4)
  await expect(page.locator('.trace-node[data-execution-id="d"] .ant-tag')).toHaveText('节点')
  await expect(page.getByText('检索候选人证据', { exact: true })).toBeVisible()
  await expect(page.locator('.message-row.assistant')).toContainText('第一段答案')
  await expect(page.getByRole('button', { name: '新对话' })).toBeDisabled()
  await expect(page.getByText(/text-embedding-v4/).first()).toBeVisible()
  await page.screenshot({ path: 'test-results/chat-streaming.png', fullPage: true })
  const trace = nodes.map((item, index) => ({ ...item, status: 'succeeded', elapsed_ms: (index + 1) * 120, summary: index === 3 ? '已完成外部核验' : undefined }))
  const answer = { id: 2, role: 'assistant', content: '第一段答案，完整结论', intent: 'talent_match', sources: [{ candidate_id: 10, name: '测试人才' }], created_at: timestamp, trace }
  state.messages = [{ id: 1, role: 'user', content: '推荐Java人才', sources: [], created_at: timestamp }, answer]
  await push(page, [
    ...trace.slice().reverse().map((item) => ({ type: 'node_finished', node: item })),
    { type: 'delta', content: '，完整结论' }, { type: 'done', message: answer, intent: 'talent_match', sources: answer.sources },
  ], true)
  await expect(page.locator('.message-row.assistant')).toHaveCount(1)
  await expect(page.locator('.message-row.assistant')).toContainText('参考简历：测试人才')
  await expect(page.getByRole('button', { name: '新对话' })).toBeEnabled()
  expect(await page.evaluate(() => (window as any).__streamRequest.authorization)).toBe('Bearer fixture-token')
  await page.reload()
  await expect(page.locator('.message-row.assistant')).toContainText('第一段答案，完整结论')
  await expect(page.getByRole('button', { name: '执行过程', exact: true })).toContainText('5 个节点')
  await expect(page.getByRole('button', { name: '执行过程', exact: true })).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('button', { name: '执行过程', exact: true }).click()
  await expect(page.locator('.trace-node')).toHaveCount(5)
  await expect(page.locator('.trace-node-title')).toHaveText(['理解岗位', '检索候选人证据', '并行评分', '外部核验', '再次检索'])
  await page.locator('.trace-node[data-execution-id="d"] summary').click()
  await expect(page.getByText('已完成外部核验')).toBeVisible()
  await expect(page.locator('.execution-trace')).toContainText('0.6s')
})

for (const end of ['disconnect', 'error']) {
  test(`执行${end}保留失败节点并中断其余运行节点`, async ({ page }) => {
    await fixture(page)
    await page.goto('/chat')
    await page.getByLabel('聊天消息').fill('执行失败测试')
    await page.getByRole('button', { name: /发送/ }).click()
    const failed = node('failed', '核验服务')
    const running = node('pending', '并行分析')
    await push(page, [{ type: 'node_started', node: failed }, { type: 'node_started', node: running }])
    await expect(page.locator('.trace-node[data-status="running"]')).toHaveCount(2)
    await push(page, [
      { type: 'node_finished', node: { ...failed, status: 'failed', elapsed_ms: 38, summary: '核验暂不可用', inputs: '不得显示的输入', reasoning: '不得显示的推理' } },
      { type: 'delta', content: '保留的部分答案' },
      ...(end === 'error' ? [{ type: 'error', detail: '执行服务失败' }] : []),
    ], true)
    await expect(page.locator('.trace-node[data-execution-id="failed"]')).toHaveAttribute('data-status', 'failed')
    await expect(page.locator('.trace-node[data-execution-id="pending"]')).toHaveAttribute('data-status', 'interrupted')
    await expect(page.locator('.trace-node[data-status="running"]')).toHaveCount(0)
    await expect(page.locator('.message-row.assistant')).toContainText('保留的部分答案')
    await page.locator('.trace-node[data-execution-id="failed"] summary').click()
    await expect(page.getByText('核验暂不可用')).toBeVisible()
    await expect(page.getByText('不得显示的输入')).toHaveCount(0)
    await expect(page.getByText('不得显示的推理')).toHaveCount(0)
  })
}

test('每条助手回复保留自己的节点，新消息不会改写之前的记录', async ({ page }) => {
  const state = await fixture(page)
  const oldNode = { ...node('same-execution-id', '上一轮检索'), status: 'succeeded', elapsed_ms: 80 }
  state.messages = [
    { id: 1, role: 'user', content: '上一轮问题', sources: [], created_at: timestamp },
    { id: 2, role: 'assistant', content: '上一轮答案', sources: [], created_at: timestamp, trace: [oldNode] },
  ]
  await page.goto('/chat')
  await expect(page.locator('.message-row.assistant')).toHaveCount(1)
  await page.getByLabel('聊天消息').fill('下一轮问题')
  await page.getByRole('button', { name: /发送/ }).click()
  await push(page, [{ type: 'node_started', node: node('same-execution-id', '本轮自定义工具') }, { type: 'delta', content: '本轮答案' }])
  const assistants = page.locator('.message-row.assistant')
  await expect(assistants).toHaveCount(2)
  await assistants.first().getByRole('button', { name: '执行过程', exact: true }).click()
  await expect(assistants.first().locator('.trace-node-title')).toHaveText('上一轮检索')
  await expect(assistants.first().locator('.trace-node')).toHaveAttribute('data-status', 'succeeded')
  await expect(assistants.last().locator('.trace-node-title')).toHaveText('本轮自定义工具')
  await expect(assistants.last().locator('.trace-node')).toHaveAttribute('data-status', 'running')
})

test('手机端长节点标题与摘要纵向排布且不溢出气泡', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fixture(page, 'admin')
  await page.goto('/chat')
  await page.getByLabel('聊天消息').fill('检查移动端')
  await page.getByRole('button', { name: /发送/ }).click()
  const first = node('mobile-a', '核验具有较长名称的自定义岗位能力证据节点', 'arbitrary-graph-node', 'custom-evidence-check')
  await push(page, [{ type: 'node_started', node: first }, { type: 'node_started', node: node('mobile-b', '分析候选人') }, { type: 'delta', content: '逐步生成回答' }])
  await expect(page.locator('.trace-node')).toHaveCount(2)
  await push(page, [{ type: 'node_finished', node: { ...first, status: 'succeeded', elapsed_ms: 225, summary: '已核验岗位能力证据，仅展示执行摘要。' } }])
  await page.locator('.trace-node').first().locator('summary').click()
  await expect(page.getByText('已核验岗位能力证据，仅展示执行摘要。')).toBeVisible()
  const bubble = (await page.locator('.message-row.assistant .message-bubble').boundingBox())!
  const firstBox = (await page.locator('.trace-node').first().boundingBox())!
  const secondBox = (await page.locator('.trace-node').last().boundingBox())!
  expect(secondBox.y).toBeGreaterThanOrEqual(firstBox.y + firstBox.height)
  for (const box of [firstBox, secondBox]) {
    expect(box.x).toBeGreaterThanOrEqual(bubble.x)
    expect(box.x + box.width).toBeLessThanOrEqual(bubble.x + bubble.width)
    expect(box.x + box.width).toBeLessThanOrEqual(390)
  }
  expect(await page.locator('.execution-trace').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: 'test-results/chat-execution-mobile.png', fullPage: true })
})

test('检索降级显示真实模式且断流保留部分答案并提示中断', async ({ page }) => {
  await fixture(page)
  await page.goto('/chat')
  await page.getByLabel('聊天消息').fill('推荐工程师')
  await page.getByRole('button', { name: /发送/ }).click()
  await push(page, [{ type: 'retrieval', mode: 'keyword', embedding_model: null, warning: '向量服务暂不可用，已使用关键词检索' }, { type: 'delta', content: '部分答案' }], true)
  await expect(page.getByRole('alert')).toContainText('中断')
  await expect(page.locator('.message-row.assistant')).toContainText('部分答案')
  await expect(page.getByText('关键词检索', { exact: true })).toBeVisible()
  await expect(page.getByText('向量服务暂不可用，已使用关键词检索')).toBeVisible()
  await expect(page.getByRole('button', { name: '新对话' })).toBeEnabled()
})

test('增量更新不能污染同一毫秒附近创建的用户消息', async ({ page }) => {
  await fixture(page)
  await page.goto('/chat')
  await expect(page.getByRole('button', { name: '新对话' })).toBeEnabled()
  await page.getByLabel('聊天消息').fill('保留这条用户消息')
  await page.evaluate(() => {
    const original = Date.now
    let time = 1800000000000
    Date.now = () => time++
    document.querySelector('textarea')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }))
    Date.now = original
  })
  await push(page, [{ type: 'delta', content: '助手答案' }])
  await expect(page.locator('.message-row.assistant')).toContainText('助手答案')
  await expect(page.locator('.message-row.user .markdown')).toHaveText('保留这条用户消息')
  await push(page, [{ type: 'error', detail: '测试结束' }], true)
})

test('最多选择三份且显式启动，逐份失败不遮蔽其余入库结果', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/candidates')
  const files = ['甲', '乙', '丙', '丁'].map((name) => ({ name: name + '.txt', mimeType: 'text/plain', buffer: Buffer.from('姓名：测试人才') }))
  await page.locator('input[type=file]').setInputFiles(files)
  await expect(page.getByText('每批最多选择3份简历')).toBeVisible()
  expect(await page.evaluate(() => Boolean((window as any).__streamRequest))).toBe(false)
  await page.locator('input[type=file]').setInputFiles(files.slice(0, 3))
  await expect(page.getByRole('button', { name: '开始入库', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => Boolean((window as any).__streamRequest))).toBe(false)
  await page.getByRole('button', { name: '开始入库', exact: true }).click()
  await push(page, [{ type: 'batch_start', total: 3 }, { type: 'file_progress', file_index: 0, filename: '甲.txt', stage: 'embedding', label: '生成语义向量', progress: 80 }])
  await expect(page.locator('.ingestion-file').filter({ hasText: '甲.txt' })).toContainText('生成语义向量')
  await expect(page.locator('input[type=file]')).toBeDisabled()
  await expect(page.getByRole('button', { name: '移除 甲.txt' })).toBeDisabled()
  await page.screenshot({ path: 'test-results/batch-ingestion.png', fullPage: true })
  expect(await page.evaluate(() => (window as any).__streamRequest.files)).toEqual(['甲.txt', '乙.txt', '丙.txt'])
  state.candidates = [candidate]
  await push(page, [
    { type: 'file_result', file_index: 0, filename: '甲.txt', status: 'ready', candidate, detail: null },
    { type: 'file_result', file_index: 1, filename: '乙.txt', status: 'failed', candidate: null, detail: '重复文件' },
    { type: 'file_result', file_index: 2, filename: '丙.txt', status: 'ready', candidate: { ...candidate, id: 11 }, detail: null },
    { type: 'batch_done', total: 3, succeeded: 2, failed: 1 },
  ], true)
  await expect(page.getByText('入库完成：成功2份，失败1份')).toBeVisible()
  await expect(page.locator('.ingestion-file').filter({ hasText: '乙.txt' })).toContainText('重复文件')
  await expect(page.getByRole('button', { name: '测试人才', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '移除 甲.txt' }).click()
  await page.locator('input[type=file]').setInputFiles(files.slice(3))
  await expect(page.locator('.ingestion-file')).toHaveCount(1)
  await expect(page.locator('.ingestion-file')).toContainText('丁.txt')
  await expect(page.getByRole('button', { name: '开始入库', exact: true })).toBeEnabled()
})

test('没有批次完成帧时明确标记未完成文件并允许重新选择', async ({ page }) => {
  await fixture(page)
  await page.goto('/candidates')
  await page.locator('input[type=file]').setInputFiles({ name: '中断.txt', mimeType: 'text/plain', buffer: Buffer.from('内容') })
  await page.getByRole('button', { name: '开始入库', exact: true }).click()
  await push(page, [{ type: 'batch_start', total: 1 }, { type: 'file_progress', file_index: 0, filename: '中断.txt', stage: 'extract', label: '提取正文', progress: 20 }], true)
  await expect(page.getByRole('alert').filter({ hasText: '中断' })).toBeVisible()
  await expect(page.locator('.ingestion-file')).toContainText('中断')
  await expect(page.locator('input[type=file]')).toBeEnabled()
})

test('未索引旧库可补建并显示真实向量进度', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/candidates')
  await expect(page.getByText(/text-embedding-v4/)).toBeVisible()
  await expect(page.getByText('部分文档待建立向量索引')).toBeVisible()
  await expect(page.getByText('关键词检索', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '补建向量索引' }).click()
  await push(page, [{ type: 'index_progress', completed: 1, total: 2 }])
  await expect(page.getByText('补建进度：1 / 2')).toBeVisible()
  state.status = { ...status, mode: 'hybrid', indexed_chunks: 3, pending_chunks: 0 }
  await push(page, [{ type: 'index_done', indexed_chunks: 3, total_chunks: 3 }], true)
  await expect(page.getByText('混合检索：关键词 + 语义向量', { exact: true })).toBeVisible()
  await expect(page.getByText('部分文档待建立向量索引')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '补建向量索引' })).toHaveCount(0)
})

test('流式请求401清除登录并返回登录页', async ({ page }) => {
  await fixture(page, 'admin', 401)
  await page.goto('/chat')
  await page.getByLabel('聊天消息').fill('你好')
  await page.getByRole('button', { name: /发送/ }).click()
  await expect(page).toHaveURL(/\/login$/)
  expect(await page.evaluate(() => localStorage.getItem('talentfit_token'))).toBeNull()
})
