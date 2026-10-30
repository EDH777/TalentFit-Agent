import { test, expect, type Page } from '@playwright/test'

const employee = { id: 'synthetic-employee', display_name: '合成说明员工', employee_no: 'HELP001', employment_status: 'active', version: 1, facts_status: 'pending', documents: [] }
async function fixture(page: Page, role = 'hr') {
  let employeeStatus = 'active'
  await page.addInitScript((value) => { localStorage.setItem('talentfit_token', 'synthetic'); localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: value, role: value })) }, role)
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    let body: unknown = []
    if (path.endsWith('/auth/me')) body = { id: 1, username: role, role }
    else if (path.endsWith('/employees') && request.method() === 'GET') body = [{ ...employee, employment_status: employeeStatus, version: employeeStatus === 'active' ? 1 : 2 }]
    else if (path.endsWith('/employees/synthetic-employee') && request.method() === 'PATCH') { employeeStatus = request.postDataJSON().employment_status; body = { ...employee, employment_status: employeeStatus, version: 2 } }
    else if (path.endsWith('/setup')) body = { revision: 1, bootstrap_closed: false }
    else if (path.endsWith('/resume-facts')) body = { status: 'pending', skills: [], facts: [], warnings: [] }
    else if (path.endsWith('/capacity-days')) body = { data: [{ employee_id: employee.id, date: '2026-10-02', capacity_units: 100, allocated_units: 30, remaining_units: 70, version: 1, source_kind: 'demo_staffing_202611', is_workday: true, allocations: [{ allocation_id: 'allocation-a', project_id: 'project-a', project_title: '日历演示项目', status: 'active', units_per_day: 30, basis: '演示占用' }] }] }
    await route.fulfill({ json: body })
  })
}

test('人员详情把未知和每日可投入比例解释在同一抽屉', async ({ page }) => {
  await fixture(page)
  await page.goto('/staffing/employees')
  await page.getByRole('button', { name: '查看员工' }).click()
  await page.getByRole('tab', { name: '可投入时间', exact: true }).click()
  await expect(page.getByText('按日期查看可投入、已安排和剩余比例', { exact: false })).toBeVisible()
  await page.getByLabel('台账开始日期').fill('2026-10-02')
  await page.getByLabel('台账结束日期').fill('2026-10-03')
  await page.getByRole('button', { name: '刷新日历' }).click()
  await page.locator('.capacity-calendar-cell').filter({ hasText: '100%' }).click()
  await expect(page.getByText('剩余 70%', { exact: true })).toBeVisible()
  await expect(page.getByText('日历演示项目', { exact: true })).toBeVisible()
  const overlaps = await page.locator('.capacity-calendar-cell').evaluateAll(cells => cells.some(cell => {
    const available = cell.querySelector('span')?.getBoundingClientRect()
    const allocated = cell.querySelector('small')?.getBoundingClientRect()
    return available && allocated && available.bottom > allocated.top && allocated.bottom > available.top
  }))
  expect(overlaps).toBe(false)
  await expect(page.locator('.capacity-calendar-cell strong')).toHaveCount(0)
})

test('系统准备页面承接初始化关闭，人员准备不显示初始化表单', async ({ page }) => {
  await fixture(page, 'admin')
  await page.goto('/staffing/employees')
  await expect(page.getByRole('button', { name: '从知识库建立档案' })).toHaveCount(0)
  await page.goto('/staffing/system')
  await expect(page.getByRole('heading', { name: '系统准备', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '核对并关闭初始化' })).toBeVisible()
})

test('人事可以将员工移出备选并恢复，历史资料不随操作删除', async ({ page }) => {
  await fixture(page)
  await page.goto('/staffing/employees')
  await page.getByRole('button', { name: '移出备选' }).click()
  await page.getByRole('button', { name: '移 出', exact: true }).click()
  await expect(page.getByText('还没有备选员工')).toBeVisible()
  await page.locator('.staffing-table-toolbar .ant-select').click()
  await page.getByTitle('已移出').click()
  await expect(page.getByText('合成说明员工')).toBeVisible()
  await page.getByRole('button', { name: '恢复备选' }).click()
  await page.locator('.staffing-table-toolbar .ant-select').click()
  await page.getByTitle('备选人员').click()
  await expect(page.getByText('合成说明员工')).toBeVisible()
})
