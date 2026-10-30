import { test, expect, type Page } from '@playwright/test'

const dates = ['2026-11-02', '2026-11-03']
const employee = { id: 'employee-a', display_name: '合成员工甲', employee_no: 'S001', employment_status: 'active', version: 1, facts_status: 'ready' }
const requirements = { schema_version: '1', revision: 1, start_date: dates[0], end_date: '2026-11-04', work_dates: dates, employee_ids: ['employee-a'], required_employee_ids: [], excluded_employee_ids: [], slots: [{ slot_id: 'slot-a', role_name: '后端', units_per_day: 40, work_dates: dates, required_skills: ['Python'], preferred_skills: [] }] }
const project = { id: 'project-a', title: '合成设备项目', description: '分析设备数据', business_status: 'draft', origin_kind: 'managed', start_date: dates[0], end_date: '2026-11-04', requirements_revision: 1, version: 1, requirements, messages: [], latest_run: null, latest_plan_state: null }
const plan = { id: 'plan-a', revision_no: 1, state: 'submitted', solver_status: 'OPTIMAL', version: 1, input_fingerprint: 'a'.repeat(64), requirements_revision: 1, assignments: [{ employee_id: 'employee-a', slot_id: 'slot-a', units_per_day: 40, start_date: dates[0], end_date: '2026-11-04', work_dates: dates }], validation: { valid: true, blockers: [], warnings: [] }, evidence: [], capacity: [] }

async function fixture(page: Page, role = 'hr', options: { withPlan?: boolean, emptyRequirements?: boolean, requirementError?: object } = {}) {
  const requests: { path: string; body: any }[] = []
  await page.addInitScript((value) => { localStorage.setItem('talentfit_token', 'synthetic'); localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: value, role: value })) }, role)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() !== 'GET') requests.push({ path, body: route.request().postDataJSON() })
    let body: unknown = []
    if (path.endsWith('/auth/me')) body = { id: 1, username: role, role }
    else if (path.endsWith('/employees')) body = [employee]
    else if (path.endsWith('/setup')) body = { revision: 1, bootstrap_closed: true }
    else if (path.endsWith('/work-calendar')) body = { days: dates.map(work_date => ({ work_date, is_workday: true })) }
    else if (path.endsWith('/plans')) body = options.withPlan ? [plan] : []
    else if (path.endsWith('/projects/project-a')) body = { ...project, requirements: options.emptyRequirements ? null : project.requirements, requirements_revision: options.emptyRequirements ? 0 : project.requirements_revision, latest_plan_state: options.withPlan ? plan.state : null }
    else if (path.endsWith('/projects')) body = [{ ...project, requirements: options.emptyRequirements ? null : project.requirements, requirements_revision: options.emptyRequirements ? 0 : project.requirements_revision }]
    else if (path.endsWith('/recommendation/approve')) body = { ...plan, state: 'applied', version: 2 }
    else if (path.endsWith('/recommendation/submit')) body = { ...plan, state: 'submitted', version: 2 }
    else if (path.endsWith('/recommendation')) body = { id: 'run-a', project_id: project.id, requirements_revision: 1, version: 1, status: 'queued', questions: [] }
    else if (path.endsWith('/project-a/requirement')) {
      if (options.requirementError) { await route.fulfill({ status: 422, json: { detail: options.requirementError } }); return }
      body = { ...project, requirements_revision: 1, requirements }
    }
    await route.fulfill({ json: body })
  })
  return requests
}

test('项目详情使用三步业务流程并隐藏底层版本字段', async ({ page }) => {
  await fixture(page, 'hr', { emptyRequirements: true })
  await page.goto('/staffing/projects/project-a')
  for (const name of ['项目需求', '推荐方案', '正式排期']) await expect(page.locator('.ant-steps-item-title', { hasText: name })).toBeVisible()
  await expect(page.getByText('需求版本', { exact: false })).toHaveCount(0)
  await expect(page.getByText('方案版本', { exact: false })).toHaveCount(0)
})

test('保存需求默认使用全部在职员工且不展示员工范围表单', async ({ page }) => {
  const requests = await fixture(page, 'hr', { emptyRequirements: true })
  await page.goto('/staffing/projects/project-a')
  await expect(page.getByRole('combobox', { name: '组队员工范围（最多100人）' })).toHaveCount(0)
  await expect(page.getByText('必须包含员工', { exact: true })).toHaveCount(0)
  await page.getByLabel('岗位名称').fill('后端开发工程师')
  await page.getByRole('button', { name: '保存并确认需求' }).click()
  await expect.poll(() => requests.find(item => item.path.endsWith('/project-a/requirement'))?.body).toMatchObject({ confirmed: true, requirements: { employee_ids: [employee.id], required_employee_ids: [], excluded_employee_ids: [] } })
})

test('推荐结果只显示一套当前方案，管理员批准使用业务 facade', async ({ page }) => {
  const requests = await fixture(page, 'admin', { withPlan: true })
  await page.goto('/staffing/projects/project-a')
  await expect(page.getByRole('heading', { name: '当前推荐', exact: true })).toBeVisible()
  await expect(page.getByText('需求版本', { exact: false })).toHaveCount(0)
  await page.getByRole('button', { name: '批准并占用排期', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认批准并占用' }).click()
  await expect.poll(() => requests.some(item => item.path.endsWith('/recommendation/approve') && item.body.plan_id === plan.id)).toBe(true)
})

test('岗位可以只参与部分工作日，保存时提交该岗位自己的日期', async ({ page }) => {
  const requests = await fixture(page)
  await page.goto('/staffing/projects/project-a')
  await page.getByRole('button', { name: '修改需求', exact: true }).click()
  await expect(page.getByText('参与工作日（已选 2/2 天）')).toBeVisible()
  await page.getByRole('checkbox', { name: '11-03' }).uncheck()
  await expect(page.getByText('参与工作日（已选 1/2 天）')).toBeVisible()
  await page.getByRole('button', { name: '保存并确认需求' }).click()
  await expect.poll(() => requests.find(item => item.path.endsWith('/project-a/requirement'))?.body).toMatchObject({
    requirements: { work_dates: dates, slots: [{ work_dates: ['2026-11-02'] }] },
  })
})

test('需求校验失败时指出具体字段而不是泛泛提示', async ({ page }) => {
  const message = '需求未通过校验：岗位 · 每日投入比例：超出允许范围'
  const requests = await fixture(page, 'hr', { requirementError: { code: 'INVALID_REQUIREMENTS', message, errors: [{ field: '岗位 · 每日投入比例', message: '超出允许范围' }] } })
  await page.goto('/staffing/projects/project-a')
  await page.getByRole('button', { name: '修改需求', exact: true }).click()
  await page.getByRole('button', { name: '保存并确认需求' }).click()
  await expect(page.getByText('需求未通过校验：岗位 · 每日投入比例：超出允许范围')).toBeVisible()
  expect(requests.some(item => item.path.endsWith('/project-a/requirement'))).toBe(true)
})

test('人员准备只暴露员工与公司工作日，系统准备承接初始化', async ({ page }) => {
  await fixture(page)
  await page.goto('/staffing/employees')
  await expect(page.getByRole('heading', { name: '人员准备', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: '员工档案', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: '公司工作日', exact: true })).toBeVisible()
  await page.getByLabel('搜索员工').fill('不存在的员工')
  await expect(page.getByText('没有匹配的员工。', { exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: '初始化已有占用', exact: true })).toHaveCount(0)
})

