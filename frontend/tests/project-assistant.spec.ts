import { test, expect, type Page } from '@playwright/test'

const dates = ['2026-11-02', '2026-11-03']
const slot = { slot_id: 'slot-a', role_name: '后端', units_per_day: 40, work_dates: dates, required_skills: ['Python'], preferred_skills: [] }
const requirements = { schema_version: '1', revision: 1, start_date: dates[0], end_date: '2026-11-04', work_dates: dates, employee_ids: ['employee-a'], required_employee_ids: [], excluded_employee_ids: [], slots: [slot] }
const project = { id: 'project-a', title: '合成设备项目', description: '分析设备数据', business_status: 'draft', origin_kind: 'managed', start_date: dates[0], end_date: '2026-11-04', requirements_revision: 1, version: 3, requirements, messages: [], latest_run: null, latest_plan_state: null }
const history = [
  { id: 'm1', role: 'user', content: '这套推荐的依据是什么？', requirements_revision: 1 },
  { id: 'm2', role: 'assistant', content: '推荐来自当前有效主简历中的 Python 项目经历。', requirements_revision: 1 },
]
const plan = { id: 'plan-a', project_id: 'project-a', run_id: 'run-a', requirements_revision: 1, revision_no: 1, solver_status: 'OPTIMAL', state: 'validated', version: 2, input_fingerprint: 'f'.repeat(64), assignments: [{ employee_id: 'employee-a', slot_id: 'slot-a', role_name: '后端', units_per_day: 40, start_date: dates[0], end_date: '2026-11-04', work_dates: dates }], evidence: [], validation: { valid: true, blockers: [], warnings: [] }, diagnostics: [], capacity: [] }
const draft = {
  requirements: { ...requirements, slots: [{ role_name: '前端', units_per_day: 60, work_dates: dates, required_skills: ['React'], preferred_skills: [] }] },
  missing_fields: ['员工范围'],
  changes: { units_per_day: 60 },
}

async function fixture(page: Page, role: 'hr' | 'admin', replyFor: (body: any) => unknown, options: { withPlan?: boolean } = {}) {
  const sent: { path: string; body: any }[] = []
  await page.addInitScript((value) => {
    localStorage.setItem('talentfit_token', 'synthetic')
    localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: value, role: value }))
  }, role)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    const body = method === 'POST' ? route.request().postDataJSON() : null
    if (method === 'POST' && path.endsWith('/messages')) sent.push({ path, body })
    let payload: unknown = []
    if (path.endsWith('/auth/me')) payload = { id: 1, username: role, role }
    else if (path.endsWith('/employees')) payload = [{ id: 'employee-a', display_name: '合成员工甲', employment_status: 'active', version: 1, facts_status: 'ready' }]
    else if (path.endsWith('/setup')) payload = { revision: 1, bootstrap_closed: true }
    else if (path.endsWith('/work-calendar')) payload = { days: dates.map(work_date => ({ work_date, is_workday: true })) }
    else if (path.endsWith('/plans')) payload = options.withPlan ? [plan] : []
    else if (path.endsWith('/projects/project-a/messages')) payload = method === 'POST' ? replyFor(body) : history
    else if (path.endsWith('/projects/project-a')) payload = project
    else if (path.endsWith('/requirement')) payload = { ...project, requirements_revision: 2 }
    await route.fulfill({ json: payload })
  })
  return sent
}

test('人事可以让组队助手整理需求草稿，并显式填入表单', async ({ page }) => {
  const sent = await fixture(page, 'hr', body => ({
    user_message: { id: 'm3', role: 'user', content: body.content, requirements_revision: 1 },
    assistant_message: { id: 'm4', role: 'assistant', content: '已整理需求草稿，请核对后确认。', requirements_revision: 1, draft },
    draft,
    requirements_revision: 1,
  }))
  await page.goto('/staffing/projects/project-a')
  await expect(page.getByText('推荐来自当前有效主简历中的 Python 项目经历。')).toBeVisible()
  await expect(page.getByText('用一句话描述需求变化', { exact: false })).toBeVisible()
  await page.getByLabel('组队助手消息').fill('把前端岗位改成 60%，技能要 React')
  await page.getByRole('button', { name: /发\s*送/ }).click()
  await expect.poll(() => sent.at(-1)?.body).toMatchObject({ content: '把前端岗位改成 60%，技能要 React', expected_requirements_revision: 1, intent: 'requirements' })
  await expect(page.getByText('需求草稿（未生效）')).toBeVisible()
  await expect(page.getByText('待确认：员工范围')).toBeVisible()

  await page.getByRole('button', { name: '填入需求表单' }).click()
  await expect(page.getByRole('button', { name: '保存并确认需求' })).toBeVisible()
  await expect(page.getByLabel('岗位名称')).toHaveValue('前端')
  await expect(page.getByLabel('每日投入比例（%）')).toHaveValue('60')
  // 草稿只是表单预填，尚未提交正式需求
  await expect.poll(() => sent.filter(item => item.path.endsWith('/requirement')).length).toBe(0)
})

test('管理员只能询问解释，不出现修改需求入口', async ({ page }) => {
  const sent = await fixture(page, 'admin', body => ({
    user_message: { id: 'm3', role: 'user', content: body.content, requirements_revision: 1 },
    assistant_message: { id: 'm4', role: 'assistant', content: '因为张伟的简历记载了工单接口开发经历。', requirements_revision: 1 },
    draft: null,
    requirements_revision: 1,
  }), { withPlan: true })
  await page.goto('/staffing/projects/project-a')
  await expect(page.getByRole('button', { name: '修改需求', exact: true })).toHaveCount(0)
  await expect(page.getByText('针对当前方案询问推荐理由', { exact: false })).toBeVisible()
  await page.getByLabel('组队助手消息').fill('为什么选择这位员工？')
  await page.getByRole('button', { name: /发\s*送/ }).click()
  await expect.poll(() => sent.at(-1)?.body).toMatchObject({ intent: 'explain', expected_requirements_revision: 1 })
  await expect(page.getByText('因为张伟的简历记载了工单接口开发经历。')).toBeVisible()
  await expect(page.getByText('需求草稿（未生效）')).toHaveCount(0)
})

test('人事可以重新打开需求表单修改已确认需求', async ({ page }) => {
  await fixture(page, 'hr', body => ({ user_message: { id: 'm3', role: 'user', content: body.content }, assistant_message: { id: 'm4', role: 'assistant', content: '好的。' }, draft: null, requirements_revision: 1 }))
  await page.goto('/staffing/projects/project-a')
  await page.getByRole('button', { name: '修改需求', exact: true }).click()
  await expect(page.getByRole('button', { name: '保存并确认需求' })).toBeVisible()
  await expect(page.getByLabel('岗位名称')).toHaveValue('后端')
  await page.getByRole('button', { name: '取消修改', exact: true }).click()
  await expect(page.getByRole('button', { name: '保存并确认需求' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '生成推荐', exact: true })).toBeVisible()
})
