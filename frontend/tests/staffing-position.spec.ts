import { expect, test } from '@playwright/test'

test('未确认岗位返回空字符串时，只修改部门也提交null并保留主简历快照', async ({ page }) => {
  const primary = { id: 'link-pending', candidate_id: 13, candidate_created_at: '2026-10-26T08:00:00Z', candidate_updated_at: '2026-10-27T08:00:00Z', job_category: null, version: 1 }
  const employee = { id: 'synthetic-pending', display_name: '合成待确认员工', employee_no: 'ROLE002', department_name: '', current_position: '', position_origin: 'knowledge', employment_status: 'active', version: 1, facts_status: 'pending', documents: [], primary_document: primary }
  const writes: Record<string, unknown>[] = []
  await page.addInitScript(() => {
    localStorage.setItem('talentfit_token', 'synthetic')
    localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: 'hr', role: 'hr' }))
  })
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = []
    if (path.endsWith('/auth/me')) body = { id: 1, username: 'hr', role: 'hr' }
    else if (path.endsWith('/employees')) body = [employee]
    else if (path.endsWith('/setup')) body = { revision: 1, bootstrap_closed: false }
    else if (path.endsWith('/synthetic-pending') && route.request().method() === 'PATCH') {
      const values = route.request().postDataJSON(); writes.push(values)
      if (values.current_position === '') return route.fulfill({ status: 422, json: { detail: '岗位分类必须为允许值或null' } })
      Object.assign(employee, values, { version: 2 }); body = employee
    }
    await route.fulfill({ json: body })
  })
  await page.goto('/staffing/employees')
  const row = page.getByRole('row').filter({ hasText: '合成待确认员工' })
  await expect(row).toContainText('待确认')
  await row.getByRole('button', { name: '维护档案' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('部门', { exact: true }).fill('合成采购部')
  await dialog.getByRole('button', { name: '保存档案' }).click()
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0]).toMatchObject({ current_position: null, department_name: '合成采购部', expected_version: 1, expected_primary_document: { id: 'link-pending', candidate_id: 13, candidate_created_at: '2026-10-26T08:00:00Z', candidate_updated_at: '2026-10-27T08:00:00Z', job_category: null, version: 1 } })
  await expect(dialog).toBeHidden()
})

test('能力尚未派生也显示知识库岗位，人事选择采购经理或清空并发送分类', async ({ page }) => {
  const employee = { id: 'synthetic-role', display_name: '合成岗位员工', employee_no: 'ROLE001', department_name: '', current_position: '前端开发工程师', position_origin: 'knowledge', employment_status: 'active', version: 1, facts_status: 'pending', documents: [], primary_document: { id: 'link-role', candidate_id: 12, candidate_created_at: '2026-10-26T08:00:00Z', candidate_updated_at: '2026-10-27T08:00:00Z', job_category: '前端开发工程师', version: 1 } }
  const writes: Record<string, unknown>[] = []
  await page.addInitScript(() => {
    localStorage.setItem('talentfit_token', 'synthetic')
    localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: 'hr', role: 'hr' }))
  })
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = []
    if (path.endsWith('/auth/me')) body = { id: 1, username: 'hr', role: 'hr' }
    else if (path.endsWith('/employees')) body = [employee]
    else if (path.endsWith('/setup')) body = { revision: 1, bootstrap_closed: false }
    else if (path.endsWith('/synthetic-role') && route.request().method() === 'PATCH') {
      const values = route.request().postDataJSON(); writes.push(values)
      Object.assign(employee, values, { position_origin: 'knowledge', version: employee.version + 1 }); body = employee
    }
    await route.fulfill({ json: body })
  })
  await page.goto('/staffing/employees')
  const row = page.getByRole('row').filter({ hasText: '合成岗位员工' })
  await expect(row).toContainText('前端开发工程师')
  await expect(row).toContainText('知识库同步')
  await expect(row).toContainText('尚未准备')
  await row.getByRole('button', { name: '维护档案' }).click()
  const dialog = page.getByRole('dialog')
  const position = dialog.getByRole('combobox', { name: '当前岗位', exact: true })
  await position.focus(); await position.press('ArrowDown')
  await expect(page.locator('.ant-select-dropdown:visible .ant-select-item-option')).toHaveCount(8)
  await page.locator('.ant-select-dropdown:visible').getByTitle('采购经理', { exact: true }).click()
  await dialog.getByRole('button', { name: '保存档案' }).click()
  await expect.poll(() => writes[0]?.current_position).toBe('采购经理')
  expect(writes[0].expected_primary_document).toEqual({ id: 'link-role', candidate_id: 12, candidate_created_at: '2026-10-26T08:00:00Z', candidate_updated_at: '2026-10-27T08:00:00Z', job_category: '前端开发工程师', version: 1 })
  await expect(row).toContainText('采购经理')
  await row.getByRole('button', { name: '维护档案' }).click()
  await dialog.locator('.ant-form-item').filter({ hasText: '当前岗位' }).locator('.ant-select-clear').click()
  await dialog.getByRole('button', { name: '保存档案' }).click()
  await expect.poll(() => writes.length).toBe(2)
  expect(writes[1]).toMatchObject({ current_position: null, expected_version: 2 })
  await expect(row).toContainText('待确认')
  await page.route('**/api/staffing/employees/synthetic-role', route => route.fulfill({ status: 409, json: { detail: '主简历岗位已变化，请刷新后重试' } }))
  await row.getByRole('button', { name: '维护档案' }).click()
  await dialog.getByRole('button', { name: '保存档案' }).click()
  await expect(dialog).toContainText('主简历岗位已变化，请刷新后重试')
  await expect(dialog).toBeVisible()
})
