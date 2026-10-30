import { useState } from 'react'
import { Alert, Button, Collapse, Modal, Space, Table, Typography } from 'antd'
import type { Employee, StaffingPlan, StaffingProject } from '../../types/staffing'
import { safeSummary, staffingApi, staffingError } from '../../services/staffing'

function diagnosticSummary(value: unknown, people: Map<string, string>): string {
  if (value && typeof value === 'object' && 'employee_id' in value) {
    const item = value as Record<string, unknown>
    const employeeId = String(item.employee_id)
    return safeSummary({ ...item, employee_id: people.get(employeeId) || '已移除员工' })
  }
  return safeSummary(value)
}

function Evidence({ plan, employees }: { plan: StaffingPlan; employees: Employee[] }) {
  const people = new Map(employees.map(employee => [employee.id, employee.display_name]))
  const evidence = (plan.evidence || []).slice(0, 3) as Record<string, any>[]
  return <div className="staffing-evidence">
    {!evidence.length && <p className="staffing-muted">没有可用的简历依据；请检查员工主简历和有效子段。</p>}
    {evidence.map((entry, index) => <section key={index}><Typography.Text strong>{people.get(entry.employee_id) || entry.display_name || '推荐人员'}</Typography.Text>{entry.status === 'stale' && <Alert type="warning" message="简历依据已变化，请重新生成推荐" />}{(entry.facts || []).slice(0, 3).map((fact: any, factIndex: number) => <div className="staffing-fact" key={factIndex}><p>{fact.normalized_key || fact.fact_type || '简历记载'}</p>{(fact.source_refs || []).slice(0, 2).map((ref: any, refIndex: number) => <blockquote key={refIndex}>{ref.quote || '引用内容已不可用'}<footer>来自当前有效简历分段</footer></blockquote>)}</div>)}</section>)}
  </div>
}

export default function PlanComparison({ plans, project, employees, hr, onChanged, trace }: { plans: StaffingPlan[]; project: StaffingProject; employees: Employee[]; hr: boolean; onChanged: () => void; trace?: React.ReactNode }) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [approval, setApproval] = useState<{ plan: StaffingPlan; action: 'approve' | 'reject' } | null>(null)
  const statePriority: Record<string, number> = { applied: 5, submitted: 4, validated: 3, invalidated: 2, draft: 1 }
  const plan = [...plans].filter(item => !['superseded', 'rejected'].includes(item.state)).sort((a, b) => (statePriority[b.state] || 0) - (statePriority[a.state] || 0) || b.revision_no - a.revision_no)[0]
  const person = (id: string) => employees.find(employee => employee.id === id)?.display_name || '已移除员工'
  const people = new Map(employees.map(employee => [employee.id, employee.display_name]))
  const role = (id: string) => project.requirements?.slots?.find(slot => slot.slot_id === id)
  const rows = plan?.assignments.map(item => ({ ...item, name: person(item.employee_id), role_name: item.role_name || role(item.slot_id)?.role_name || '岗位', units: item.units_per_day ?? role(item.slot_id)?.units_per_day, dates: item.work_dates || role(item.slot_id)?.work_dates || [] })) || []
  const action = async (kind: 'submit' | 'approve' | 'reject') => {
    if (!plan) return
    setBusy(kind); setError('')
    try {
      if (kind === 'submit') await staffingApi.submitRecommendation(project.id, plan)
      else if (kind === 'approve') await staffingApi.approveRecommendation(project.id, plan)
      else await staffingApi.post(`/plans/${plan.id}/reject`, { expected_version: plan.version, input_fingerprint: plan.input_fingerprint }, true)
      setApproval(null); onChanged()
    } catch (e) { setError(staffingError(e)); onChanged() } finally { setBusy('') }
  }
  if (!plan) return <div className="staffing-empty">还没有推荐。确认项目需求后，点击“生成推荐”。</div>
  return <article className="staffing-plan">
    {error && <Alert type="error" showIcon message={error} />}
    <Typography.Title level={4}>当前推荐</Typography.Title>
    <Table rowKey="slot_id" size="small" pagination={false} scroll={{ x: 520 }} dataSource={rows} columns={[
      { title: '推荐人员', dataIndex: 'name' }, { title: '项目岗位', dataIndex: 'role_name' }, { title: '每日投入比例', dataIndex: 'units', align: 'right' as const, render: value => value == null ? '未知' : `${value}%` }, { title: '工作日', render: (_, row) => row.dates.join('、') || '未知' },
    ]} />
    {plan.validation && <Alert className="staffing-spaced" type={plan.validation.valid ? 'success' : 'warning'} message={plan.validation.valid ? '硬约束检查通过' : '需要补充或调整信息'} description={<>{plan.validation.blockers?.map((blocker, index) => <p key={index}>{diagnosticSummary(blocker, people)}</p>)}{plan.validation.warnings?.map((warning, index) => <p key={index}>{warning}</p>)}</>} />}
    {plan.diagnostics?.map((item, index) => <p className="staffing-muted" key={index}>{diagnosticSummary(item, people)}</p>)}
    <Collapse ghost items={[
      { key: 'evidence', label: '查看详细能力依据', children: <Evidence plan={plan} employees={employees} /> },
      { key: 'capacity', label: '查看逐日排期检查', children: !plan.capacity?.length ? <p>当前没有可展示的逐日排期数据，未知时间不会被当作空闲。</p> : <Table rowKey={row => `${row.employee_id}-${row.date}`} size="small" pagination={{ pageSize: 7 }} scroll={{ x: 560 }} dataSource={plan.capacity} columns={[{ title: '员工', dataIndex: 'employee_id', render: person }, { title: '日期', dataIndex: 'date' }, { title: '可投入比例', dataIndex: 'capacity_units', align: 'right' as const, render: value => value == null ? '未知' : `${value}%` }, { title: '已安排', dataIndex: 'allocated_units', align: 'right' as const, render: value => `${value}%` }, { title: '方案后剩余', dataIndex: 'after_remaining_units', align: 'right' as const, render: value => value == null ? '未知' : `${value}%` }]} /> },
      ...(trace ? [{ key: 'trace', label: '查看处理详情', children: trace }] : []),
    ]} />
    {(hr && ['draft', 'validated', 'invalidated'].includes(plan.state)) || (!hr && plan.state === 'submitted')
      ? <Space wrap className="staffing-actions">
        {hr && ['draft', 'validated', 'invalidated'].includes(plan.state) && <Button type="primary" loading={busy === 'submit'} onClick={() => void action('submit')}>提交审批</Button>}
        {!hr && plan.state === 'submitted' && <><Button type="primary" loading={busy === 'approve'} onClick={() => setApproval({ plan, action: 'approve' })}>批准并占用排期</Button><Button danger onClick={() => setApproval({ plan, action: 'reject' })}>驳回</Button></>}
      </Space>
      : null}
    <Modal title={approval?.action === 'approve' ? '确认正式调配' : '确认驳回推荐'} open={!!approval} onCancel={() => setApproval(null)} confirmLoading={!!busy} okText={approval?.action === 'approve' ? '确认批准并占用' : '确认驳回'} onOk={() => approval && void action(approval.action)}>
      {approval?.action === 'approve' ? <><p>管理员批准后，以下人员和每日投入比例才会写入正式排期。</p>{rows.map(row => <p key={row.slot_id}><strong>{row.name}</strong> · {row.role_name} · {row.units == null ? '未知' : `${row.units}%`} · {row.dates.join('、')}</p>)}<Alert type="warning" message="后端会在事务内重新核对简历依据、工作日和每日可投入比例。" /></> : <p>驳回会保留审计记录，不会占用排期。</p>}
    </Modal>
  </article>
}
