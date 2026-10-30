import { useEffect, useState } from 'react'
import { Alert, Button, Form, Input, InputNumber, Modal, Select, Space, Table, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { useUser } from '../AppLayout'
import { staffingApi, staffingError } from '../services/staffing'
import { exclusiveEnd, formatRange } from '../services/datetime'
import type { Employee, SetupState, StaffingAllocation, StaffingProject } from '../types/staffing'
import '../styles/staffing.css'

export default function StaffingSystemPage() {
  const hr = useUser()?.role === 'hr'
  const [setup, setSetup] = useState<SetupState | null>(null)
  const [employees, setEmployees] = useState<Employee[]>([])
  const [projects, setProjects] = useState<StaffingProject[]>([])
  const [allocations, setAllocations] = useState<StaffingAllocation[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [projectOpen, setProjectOpen] = useState(false)
  const [allocationOpen, setAllocationOpen] = useState(false)
  const [finalizeOpen, setFinalizeOpen] = useState(false)
  const [projectForm] = Form.useForm()
  const [allocationForm] = Form.useForm()
  const load = async () => {
    try { const [state, people, allProjects, ledger] = await Promise.all([staffingApi.get<SetupState>('/setup'), staffingApi.get<Employee[]>('/employees'), staffingApi.get<StaffingProject[]>('/projects'), staffingApi.get<StaffingAllocation[]>('/allocations')]); setSetup(state); setEmployees(people); setProjects(allProjects.filter(project => project.origin_kind === 'baseline')); setAllocations(ledger); setError('') } catch (e) { setError(staffingError(e)) }
  }
  useEffect(() => { void load() }, [])
  const run = async (action: () => Promise<unknown>) => { setBusy(true); setError(''); try { await action(); await load() } catch (e) { setError(staffingError(e)) } finally { setBusy(false) } }
  const activeEmployees = employees.filter(employee => employee.employment_status === 'active')
  return <div className="staffing-page">
    <div className="staffing-page-heading"><div><Link to="/staffing">返回项目组队</Link><Typography.Title level={2}>系统准备</Typography.Title></div><Button onClick={() => void load()}>刷新</Button></div>
    {error && <Alert type="error" showIcon message={error} />}
    <Alert type={setup?.bootstrap_closed ? 'success' : 'warning'} showIcon message={setup?.bootstrap_closed ? '初始化已关闭' : '初始化仍开放'} description={setup?.bootstrap_closed ? '正式排期只能通过推荐提交和管理员批准产生。' : '人事可以补录真实的上线前项目占用；管理员核对后关闭初始化。关闭后不能重新开启。'} />
    <section className="staffing-region staffing-system-prep">
      <Typography.Title level={3}>上线前已有占用</Typography.Title>
      <p>只录入已经存在的项目安排，写入后计入员工的已安排比例。</p>
      {hr && setup && !setup.bootstrap_closed && <Space wrap><Button type="primary" onClick={() => { projectForm.resetFields(); setProjectOpen(true) }}>登记已有项目</Button>{projects.length > 0 && <Button onClick={() => { allocationForm.resetFields(); setAllocationOpen(true) }}>登记已有占用</Button>}</Space>}
      <Table className="staffing-spaced" rowKey="id" dataSource={allocations} scroll={{ x: 700 }} pagination={{ pageSize: 8 }} locale={{ emptyText: '暂无已有占用；没有历史项目时可以保持为空。' }} columns={[{ title: '项目', dataIndex: 'project_title' }, { title: '员工', dataIndex: 'display_name' }, { title: '日期', render: (_, row) => formatRange(row.start_date, row.end_date) }, { title: '每日已安排', dataIndex: 'units_per_day', align: 'right' as const, render: value => `${value}%` }, { title: '依据', dataIndex: 'basis', render: value => value || '未提供' }]} />
    </section>
    {!setup?.bootstrap_closed && !hr && <section className="staffing-region staffing-system-prep"><Typography.Title level={3}>管理员核对</Typography.Title><p>确认员工身份、工作日、可投入比例和已有占用后，关闭初始化。之后新项目只能走正式审批。</p><Button type="primary" disabled={!setup || !allocations.every(row => row.status !== 'error')} onClick={() => setFinalizeOpen(true)}>核对并关闭初始化</Button></section>}
    <Modal title="登记上线前已有项目" open={projectOpen} onCancel={() => setProjectOpen(false)} okText="保存已有项目" confirmLoading={busy} onOk={() => void run(async () => { const values = await projectForm.validateFields(); await staffingApi.post('/projects', { ...values, end_date: exclusiveEnd(values.end_date), origin_kind: 'baseline' }); setProjectOpen(false) })}><Form layout="vertical" form={projectForm}><Form.Item label="已有项目名称" name="title" rules={[{ required: true }]}><Input /></Form.Item><Form.Item label="实际依据" name="description" rules={[{ required: true }]}><Input.TextArea rows={2} /></Form.Item><div className="staffing-date-pair"><Form.Item label="开始日期" name="start_date" rules={[{ required: true }]}><Input type="date" /></Form.Item><Form.Item label="结束日期（含）" name="end_date" rules={[{ required: true }]}><Input type="date" /></Form.Item></div></Form></Modal>
    <Modal title="登记已有占用" open={allocationOpen} onCancel={() => setAllocationOpen(false)} okText="保存占用" confirmLoading={busy} onOk={() => void run(async () => { const values = await allocationForm.validateFields(); await staffingApi.post('/allocations/baseline', { project_id: values.project_id, expected_project_version: projects.find(project => project.id === values.project_id)?.version, allocations: [{ employee_id: values.employee_id, start_date: values.start_date, end_date: exclusiveEnd(values.end_date), work_dates: values.work_dates.split(/[,，\s]+/).filter(Boolean), units_per_day: values.units_per_day, basis: values.basis }] }, true); setAllocationOpen(false) })}><Form layout="vertical" form={allocationForm}><Form.Item label="已有项目" name="project_id" rules={[{ required: true }]}><Select options={projects.map(project => ({ label: project.title, value: project.id }))} /></Form.Item><Form.Item label="员工" name="employee_id" rules={[{ required: true }]}><Select options={activeEmployees.map(employee => ({ label: employee.display_name, value: employee.id }))} /></Form.Item><div className="staffing-date-pair"><Form.Item label="开始日期" name="start_date" rules={[{ required: true }]}><Input type="date" /></Form.Item><Form.Item label="结束日期（含）" name="end_date" rules={[{ required: true }]}><Input type="date" /></Form.Item></div><Form.Item label="实际工作日" name="work_dates" extra="用逗号或空格分隔，例如 2026-11-02,2026-11-03" rules={[{ required: true }]}><Input /></Form.Item><Form.Item label="每日已安排比例（%）" name="units_per_day" initialValue={40} rules={[{ required: true }]}><InputNumber min={1} max={100} precision={0} /></Form.Item><Form.Item label="实际依据" name="basis" rules={[{ required: true }]}><Input.TextArea rows={2} /></Form.Item></Form></Modal>
    <Modal title="确认关闭初始化" open={finalizeOpen} onCancel={() => setFinalizeOpen(false)} okText="确认永久关闭" confirmLoading={busy} onOk={() => void run(async () => { await staffingApi.post('/setup/finalize', { expected_revision: setup?.revision }, true); setFinalizeOpen(false) })}><p>关闭后不能重新开启，后续正式排期只能由管理员批准推荐方案产生。</p></Modal>
  </div>
}
