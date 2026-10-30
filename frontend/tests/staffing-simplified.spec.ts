import { test, expect } from '@playwright/test'

const project = { id: 'project-simple', title: '简化项目', description: '快速找到后端人员', origin_kind: 'managed', start_date: '2027-01-04', end_date: '2027-01-05', requirements: null, requirements_revision: 0, business_status: 'draft', version: 1, messages: [], latest_run: null, latest_plan_state: null }

async function fixture(page: any, role = 'hr') {
  await page.addInitScript((value: string) => {
    localStorage.setItem('talentfit_token', 'synthetic')
    localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: value, role: value }))
  }, role)
  await page.route('**/api/**', async (route: any) => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = []
    if (path.endsWith('/auth/me')) body = { id: 1, username: role, role }
    else if (path.endsWith('/projects/project-simple')) body = project
    else if (path.endsWith('/projects')) body = [project]
    else if (path.endsWith('/employees')) body = []
    else if (path.endsWith('/setup')) body = { revision: 1, bootstrap_closed: false }
    else if (path.endsWith('/plans')) body = []
    else if (path.endsWith('/work-calendar')) body = { days: [] }
    await route.fulfill({ json: body })
  })
}

test('项目组队默认入口只展示人员准备和系统准备', async ({ page }) => {
  await fixture(page)
  await page.goto('/staffing')
  const menu = page.getByRole('menu')
  await expect(menu.getByRole('menuitem', { name: '项目列表', exact: true })).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: '人员准备', exact: true })).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: '系统准备', exact: true })).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: '员工与排期', exact: true })).toHaveCount(0)
})

test('项目详情默认使用三步业务流程并隐藏底层版本字段', async ({ page }) => {
  await fixture(page)
  await page.goto('/staffing/projects/project-simple')
  await expect(page.locator('.ant-steps-item-title', { hasText: '项目需求' })).toBeVisible()
  await expect(page.locator('.ant-steps-item-title', { hasText: '推荐方案' })).toBeVisible()
  await expect(page.locator('.ant-steps-item-title', { hasText: '正式排期' })).toBeVisible()
  await expect(page.getByText('需求版本', { exact: false })).toHaveCount(0)
  await expect(page.getByText('方案版本', { exact: false })).toHaveCount(0)
  await expect(page.getByText('run_id', { exact: false })).toHaveCount(0)
})
