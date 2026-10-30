import { useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Modal, Select, Space, Table, Typography } from 'antd'
import { Link, useNavigate } from 'react-router-dom'
import { useUser } from '../AppLayout'
import { staffingApi, staffingError } from '../services/staffing'
import { formatRange } from '../services/datetime'
import type { StaffingProject } from '../types/staffing'
import StaffingStatus from '../components/staffing/StaffingStatus'
import '../styles/staffing.css'

function nextAction(project: StaffingProject): string {
  if (project.business_status === 'archived') return '已归档'
  if (project.business_status === 'completed') return '已结束'
  if (project.business_status === 'cancelled') return '已取消'
  if (project.latest_plan_state === 'applied') return '排期已生效'
  if (project.latest_plan_state === 'submitted') return '等待管理员审批'
  if (project.latest_run?.status && ['queued', 'running', 'waiting_input'].includes(project.latest_run.status)) return '正在生成推荐'
  if (project.requirements_revision > 0) return '生成推荐'
  return '填写项目需求'
}

export default function StaffingProjectsPage() {
  const hr = useUser()?.role === 'hr'
  const navigate = useNavigate()
  const [projects, setProjects] = useState<StaffingProject[]>([])
  const [filter, setFilter] = useState('')
  const [projectQuery, setProjectQuery] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [archive, setArchive] = useState<StaffingProject | null>(null)
  const [closing, setClosing] = useState<{ project: StaffingProject; action: 'complete' | 'cancel' } | null>(null)
  const [form] = Form.useForm()
  const load = async () => {
    setLoading(true)
    try { setProjects(await staffingApi.get<StaffingProject[]>('/projects')); setError('') }
    catch (e) { setError(staffingError(e)) }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])
  const normalizedProjectQuery = projectQuery.trim().toLocaleLowerCase()
  const visibleProjects = projects.filter(project => {
    const matchesStatus = filter ? project.business_status === filter : project.business_status !== 'archived'
    const matchesQuery = !normalizedProjectQuery || [project.title, project.description, project.start_date, project.end_date].some(value => String(value || '').toLocaleLowerCase().includes(normalizedProjectQuery))
    return matchesStatus && matchesQuery
  })
  return <div className="staffing-page staffing-list-page">
    <div className="staffing-page-heading"><div><Typography.Title level={2}>项目组队</Typography.Title></div><Space wrap><Button onClick={() => void load()}>刷新</Button>{hr && <Button type="primary" onClick={() => { form.resetFields(); setCreating(true) }}>新建组队项目</Button>}</Space></div>
    {error && <Alert type="error" showIcon message={error} />}
    <Card className="staffing-list-card"><div className="staffing-toolbar"><Input.Search allowClear aria-label="搜索项目" placeholder="搜索项目名称、目标或日期" value={projectQuery} onChange={event => setProjectQuery(event.target.value)} className="staffing-project-search" /><Select aria-label="项目状态筛选" value={filter} onChange={setFilter} options={[{ value: '', label: '未归档项目' }, { value: 'draft', label: '待组队' }, { value: 'active', label: '排期已生效' }, { value: 'completed', label: '已结束' }, { value: 'cancelled', label: '已取消' }, { value: 'archived', label: '已归档' }]} /></div>
    <Table rowKey="id" size="small" loading={loading} dataSource={visibleProjects} scroll={{ x: 860 }} locale={{ emptyText: projectQuery ? '没有匹配的项目。' : '暂无项目。人事可以新建一个组队项目。' }} columns={[
      { title: '项目名称', dataIndex: 'title', render: (title, p) => <Link to={`/staffing/projects/${p.id}`}>{title}</Link> },
      { title: '日期', render: (_, p) => formatRange(p.start_date, p.end_date) },
      { title: '当前状态', dataIndex: 'business_status', align: 'center' as const, render: value => <StaffingStatus value={value} /> },
      { title: '下一步', render: (_, p) => nextAction(p) },
      { title: '操作', align: 'center' as const, render: (_, p) => <Space size={2} wrap><Button type="link" onClick={() => navigate(`/staffing/projects/${p.id}`)}>进入</Button>{hr && p.business_status === 'active' && <Button type="link" onClick={() => setClosing({ project: p, action: 'complete' })}>结束项目</Button>}{hr && (p.business_status === 'active' || p.business_status === 'draft') && <Button type="link" danger onClick={() => setClosing({ project: p, action: 'cancel' })}>取消项目</Button>}{hr && p.business_status !== 'archived' && <Button type="link" onClick={() => setArchive(p)}>归档</Button>}</Space> },
    ]} /></Card>
    <Modal title="新建组队项目" open={creating} onCancel={() => setCreating(false)} confirmLoading={saving} okText="创建项目" onOk={async () => {
      try { const values = await form.validateFields(); setSaving(true); const p = await staffingApi.post<StaffingProject>('/projects', { ...values, origin_kind: 'managed' }); setCreating(false); navigate(`/staffing/projects/${p.id}`) }
      catch (e) { setError(staffingError(e)) } finally { setSaving(false) }
    }}><Form layout="vertical" form={form}><Form.Item label="项目名称" name="title" rules={[{ required: true, message: '请输入项目名称' }]}><Input maxLength={160} /></Form.Item><Form.Item label="项目目标" name="description" rules={[{ required: true, message: '请说明项目要完成什么' }]}><Input.TextArea rows={3} maxLength={20000} /></Form.Item><div className="staffing-date-pair"><Form.Item label="开始日期（含）" name="start_date" rules={[{ required: true, message: '请选择开始日期' }]}><Input type="date" /></Form.Item><Form.Item label="结束日期（不含）" name="end_date" rules={[{ required: true, message: '请选择结束日期' }]}><Input type="date" /></Form.Item></div></Form></Modal>
    <Modal title="确认归档项目" open={!!archive} onCancel={() => setArchive(null)} okText="确认归档" confirmLoading={saving} onOk={async () => { if (!archive) return; setSaving(true); try { await staffingApi.post(`/projects/${archive.id}/archive`, { expected_version: archive.version }, true); setArchive(null); await load() } catch (e) { setError(staffingError(e)) } finally { setSaving(false) } }}><p>归档“{archive?.title}”会保留业务记录；如果仍有运行或当前/未来占用，请先结束或取消项目释放员工容量。</p></Modal>
    <Modal title={closing?.action === 'complete' ? '提前结束项目' : '取消项目'} open={!!closing} onCancel={() => setClosing(null)} okText={closing?.action === 'complete' ? '确认结束并释放占用' : '确认取消并释放占用'} okButtonProps={{ danger: closing?.action === 'cancel' }} confirmLoading={saving} onOk={async () => { if (!closing) return; setSaving(true); try { await staffingApi.post(`/projects/${closing.project.id}/${closing.action}`, { expected_version: closing.project.version }, true); setClosing(null); await load() } catch (e) { setError(staffingError(e)) } finally { setSaving(false) } }}><p>{closing?.action === 'complete' ? '项目结束后会释放该项目当前及未来的全部员工占用，并保留历史记录。' : '项目取消后会释放该项目当前及未来的全部员工占用，并保留取消记录。'}</p>{closing?.project && <p className="staffing-muted">项目：{closing.project.title}</p>}</Modal>
  </div>
}
