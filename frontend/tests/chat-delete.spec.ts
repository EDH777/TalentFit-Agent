import { test, expect, type Page } from '@playwright/test'

const timestamp = '2026-10-25T08:00:00Z'
const initialSessions = [
  { id: 1, title: '第一条会话', created_at: timestamp, updated_at: timestamp },
  { id: 2, title: '第二条会话', created_at: timestamp, updated_at: timestamp },
]

async function fixture(page: Page, role = 'hr', count = 2) {
  const state = {
    sessions: initialSessions.slice(0, count),
    failDelete: false,
    waitForDelete: Promise.resolve(),
    waitForHistory: Promise.resolve(),
  }
  await page.addInitScript((role) => {
    localStorage.setItem('talentfit_token', 'fixture-token')
    localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: role, role }))
    const original = window.fetch.bind(window)
    const control = window as any
    window.fetch = async (input, init) => {
      if (!String(input).endsWith('/stream')) return original(input, init)
      return new Response(new ReadableStream({ start(controller) { control.__streamController = controller } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    control.__finish = () => {
      const message = { id: 99, role: 'assistant', content: '新会话回答', sources: [], created_at: '2026-10-25T08:00:00Z' }
      control.__streamController.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ type: 'done', message, intent: 'general', sources: [] })}\n\n`))
      control.__streamController.close()
    }
  }, role)
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { id: 1, username: role, role } })
    if (path.endsWith('/search/status')) return route.fulfill({ json: { mode: 'keyword', embedding_model: '', dimensions: 1024, indexed_chunks: 0, total_chunks: 0, pending_chunks: 0 } })
    if (path.endsWith('/chat/sessions')) {
      if (method === 'POST') {
        const session = { id: 3, title: route.request().postDataJSON().title, created_at: timestamp, updated_at: timestamp }
        state.sessions.unshift(session)
        return route.fulfill({ status: 201, json: session })
      }
      return route.fulfill({ json: state.sessions })
    }
    const match = path.match(/\/chat\/sessions\/(\d+)(\/messages)?$/)
    if (match) {
      const id = Number(match[1])
      if (method === 'DELETE') {
        await state.waitForDelete
        if (state.failDelete) return route.fulfill({ status: 503, json: { detail: '删除服务暂不可用，请重试' } })
        state.sessions = state.sessions.filter((item) => item.id !== id)
        return route.fulfill({ status: 204 })
      }
      await state.waitForHistory
      return route.fulfill({ json: [{ id: id * 10, role: 'assistant', content: `会话${id}的历史回答`, sources: [], created_at: timestamp }] })
    }
    return route.fulfill({ json: [] })
  })
  return state
}

const deleteButton = (page: Page, title: string) => page.getByRole('button', { name: `删除会话：${title}`, exact: true })
const confirmDelete = (page: Page) => page.getByRole('button', { name: /^删\s*除$/ })

for (const role of ['hr', 'admin']) {
  test(`${role}可取消删除，再删除当前会话并在刷新后保留另一会话`, async ({ page }) => {
    await fixture(page, role)
    await page.goto('/chat')
    await expect(page.locator('.message-bubble')).toContainText('会话1的历史回答')
    await deleteButton(page, '第一条会话').click()
    await page.getByRole('button', { name: /^取\s*消$/ }).click()
    await expect(deleteButton(page, '第一条会话')).toBeVisible()
    await expect(page.locator('.message-bubble')).toContainText('会话1的历史回答')
    await deleteButton(page, '第一条会话').click()
    await confirmDelete(page).click()
    await expect(deleteButton(page, '第一条会话')).toHaveCount(0)
    await expect(page.locator('.message-bubble')).toHaveText(/会话2的历史回答/)
    await expect(page.getByRole('button', { name: '第二条会话', exact: true })).toHaveClass(/session-active/)
    await page.reload()
    await expect(deleteButton(page, '第一条会话')).toHaveCount(0)
    await expect(page.locator('.message-bubble')).toContainText('会话2的历史回答')
  })
}

test('删除非当前会话不切换当前会话或清空消息', async ({ page }) => {
  await fixture(page)
  await page.goto('/chat')
  await expect(page.locator('.message-bubble')).toContainText('会话1的历史回答')
  await deleteButton(page, '第二条会话').click()
  await confirmDelete(page).click()
  await expect(deleteButton(page, '第二条会话')).toHaveCount(0)
  await expect(page.locator('.message-bubble')).toContainText('会话1的历史回答')
  await expect(page.getByRole('button', { name: '第一条会话', exact: true })).toHaveClass(/session-active/)
})

test('删除失败保留列表和消息并允许重新确认重试', async ({ page }) => {
  const state = await fixture(page)
  state.failDelete = true
  await page.goto('/chat')
  await expect(page.locator('.message-bubble')).toContainText('会话1的历史回答')
  await deleteButton(page, '第一条会话').click()
  await confirmDelete(page).click()
  await expect(page.getByRole('alert')).toContainText('删除服务暂不可用')
  await expect(page.getByRole('tooltip')).not.toBeVisible()
  await expect(deleteButton(page, '第一条会话')).toBeEnabled()
  await expect(page.locator('.message-bubble')).toContainText('会话1的历史回答')
  state.failDelete = false
  await deleteButton(page, '第一条会话').click()
  await confirmDelete(page).click()
  await expect(deleteButton(page, '第一条会话')).toHaveCount(0)
  await expect(page.locator('.message-bubble')).toContainText('会话2的历史回答')
})

test('等待删除完成期间禁止切换、新建、发送和重复删除', async ({ page }) => {
  const state = await fixture(page)
  let finish!: () => void
  state.waitForDelete = new Promise((resolve) => { finish = resolve })
  await page.goto('/chat')
  await page.getByLabel('聊天消息').fill('待发送内容')
  await deleteButton(page, '第一条会话').click()
  await confirmDelete(page).click()
  await expect(deleteButton(page, '第二条会话')).toBeDisabled()
  await expect(page.getByRole('button', { name: '第二条会话', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: /新对话/ })).toBeDisabled()
  await expect(page.getByRole('button', { name: /发送/ })).toBeDisabled()
  await expect(page.getByLabel('聊天消息')).toBeDisabled()
  finish()
  await expect(deleteButton(page, '第一条会话')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /新对话/ })).toBeEnabled()
})

test('删除最后一条会话后清空历史并可发送新会话', async ({ page }) => {
  await fixture(page, 'hr', 1)
  await page.goto('/chat')
  await expect(page.locator('.message-bubble')).toContainText('会话1的历史回答')
  await deleteButton(page, '第一条会话').click()
  await confirmDelete(page).click()
  await expect(page.locator('.message-bubble')).toHaveCount(0)
  await expect(page.getByText('输入岗位需求或员工姓名，开始分析')).toBeVisible()
  await page.getByLabel('聊天消息').fill('新会话提问')
  await page.getByRole('button', { name: /发送/ }).click()
  await expect.poll(() => page.evaluate(() => Boolean((window as any).__streamController))).toBe(true)
  await page.evaluate(() => (window as any).__finish())
  await expect(page.locator('.message-row.assistant')).toContainText('新会话回答')
  await expect(deleteButton(page, '新会话提问')).toBeEnabled()
})

test('回答生成期间所有会话删除按钮禁用，完成后恢复', async ({ page }) => {
  await fixture(page)
  await page.goto('/chat')
  await page.getByLabel('聊天消息').fill('生成回答')
  await expect(page.getByRole('button', { name: /发送/ })).toBeEnabled()
  await page.getByRole('button', { name: /发送/ }).click()
  await expect(deleteButton(page, '第一条会话')).toBeDisabled()
  await expect(deleteButton(page, '第二条会话')).toBeDisabled()
  await expect.poll(() => page.evaluate(() => Boolean((window as any).__streamController))).toBe(true)
  await page.evaluate(() => (window as any).__finish())
  await expect(deleteButton(page, '第一条会话')).toBeEnabled()
})

test('删除当前会话后迟到的历史请求不会覆盖剩余会话消息', async ({ page }) => {
  const state = await fixture(page)
  let finish!: () => void
  state.waitForHistory = new Promise((resolve) => { finish = resolve })
  await page.goto('/chat')
  await expect(deleteButton(page, '第一条会话')).toBeVisible()
  await deleteButton(page, '第一条会话').click()
  state.waitForHistory = Promise.resolve()
  await confirmDelete(page).click()
  await expect(page.locator('.message-bubble')).toContainText('会话2的历史回答')
  finish()
  await expect(page.locator('.message-bubble')).toHaveCount(1)
  await expect(page.locator('.message-bubble')).toContainText('会话2的历史回答')
})
