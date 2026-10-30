import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Calendar, Card, Checkbox, Drawer, Form, Input, InputNumber, message, Modal, Popconfirm, Select, Space, Table, Tag, Tabs, Typography } from 'antd'
import dayjs from 'dayjs'
import { Link } from 'react-router-dom'
import { useUser } from '../AppLayout'
import api from '../services/api'
import { JOB_CATEGORY_OPTIONS } from '../services/jobCategories'
import { safeSummary, staffingApi, staffingError } from '../services/staffing'
import { exclusiveEnd, inclusiveEnd } from '../services/datetime'
import type { Candidate } from '../types'
import type { CapacityAllocation, CapacityDay, Employee, ResumeFacts, SetupState, WorkDay } from '../types/staffing'
import StaffingStatus from '../components/staffing/StaffingStatus'
import '../styles/staffing.css'

const factLabels: Record<string, string> = { name: '名称', time: '时间', role: '职责角色', technologies: '使用技术', responsibilities: '职责', results: '成果' }

export default function StaffingEmployeesPage() {
  const hr = useUser()?.role === 'hr'
  const [employees, setEmployees] = useState<Employee[]>([])
  const [setup, setSetup] = useState<SetupState | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [employeeQuery, setEmployeeQuery] = useState('')
  const [employeeStatus, setEmployeeStatus] = useState<'active' | 'inactive'>('active')
  const [busy, setBusy] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [documents, setDocuments] = useState<Candidate[]>([])
  const [selected, setSelected] = useState<React.Key[]>([])
  const [identities, setIdentities] = useState<Record<number, string>>({})
  const [importResults, setImportResults] = useState<Record<string, any>[]>([])
  const [editing, setEditing] = useState<Employee | null>(null)
  const [editForm] = Form.useForm()
  const [allocationForm] = Form.useForm()
  const [resourceEmployee, setResourceEmployee] = useState<Employee | null>(null)
  const [facts, setFacts] = useState<ResumeFacts | null>(null)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [days, setDays] = useState<CapacityDay[]>([])
  const [selectedCapacityDate, setSelectedCapacityDate] = useState('')
  const [adjustingAllocation, setAdjustingAllocation] = useState<CapacityAllocation | null>(null)
  const [calendar, setCalendar] = useState<WorkDay[]>([])
  const [calendarDates, setCalendarDates] = useState<string[]>([])
  const [calendarText, setCalendarText] = useState('')
  const resourceRequest = useRef(0)
  const capacityRequest = useRef(0)
  const capacityView = useRef('')
  capacityView.current = `${resourceEmployee?.id || ''}/${startDate}/${endDate}`
  const load = async () => {
    setLoading(true)
    try { const [e, state] = await Promise.all([staffingApi.get<Employee[]>('/employees'), staffingApi.get<SetupState>('/setup')]); setEmployees(e); setSetup(state); setError('') }
    catch (e) { setError(staffingError(e)) }
    finally { setLoading(false) }
  }
  useEffect(() => { void load(); return () => { ++resourceRequest.current; ++capacityRequest.current } }, [])
  useEffect(() => { ++capacityRequest.current; setDays([]); setSelectedCapacityDate('') }, [startDate, endDate])
  const perform = async (action: () => Promise<unknown>) => { setBusy(true); setError(''); try { await action(); await load() } catch (e) { setError(staffingError(e)) } finally { setBusy(false) } }
  const loadDocuments = async () => { const result = await api.get<Candidate[]>('/candidates'); setDocuments(result.data.filter(item => item.status === 'ready')) }
  const loadCalendar = async () => {
    if (!startDate || !endDate || startDate > endDate) { setError('请填写有效日期区间'); return }
    try { const data = await staffingApi.get<{ days: WorkDay[] }>('/work-calendar', { start_date: startDate, end_date: exclusiveEnd(endDate) }); setCalendar(data.days); setCalendarDates(data.days.filter(day => day.is_workday).map(day => day.work_date)) } catch (e) { setError(staffingError(e)) }
  }
  const openEmployee = async (employee: Employee) => {
    const request = ++resourceRequest.current; ++capacityRequest.current; const today = dayjs(); setError(''); setResourceEmployee(employee); setFacts(null); setDays([]); setStartDate(today.startOf('month').format('YYYY-MM-DD')); setEndDate(inclusiveEnd(today.add(1, 'month').startOf('month').format('YYYY-MM-DD'))); setSelectedCapacityDate('')
    try { const data = await staffingApi.get<ResumeFacts>(`/employees/${employee.id}/resume-facts`); if (request === resourceRequest.current) setFacts(data) } catch (e) { if (request === resourceRequest.current) setError(staffingError(e)) }
  }
  const loadCapacity = async () => {
    if (!resourceEmployee || !startDate || !endDate || startDate > endDate) { setError('请先填写日期区间'); return }
    const view = `${resourceEmployee.id}/${startDate}/${endDate}`; if (view !== capacityView.current) return
    const request = ++capacityRequest.current
    try { const data = await staffingApi.get<{ data: CapacityDay[] }>(`/employees/${resourceEmployee.id}/capacity-days`, { start_date: startDate, end_date: exclusiveEnd(endDate) }); if (request === capacityRequest.current && view === capacityView.current) setDays(data.data) } catch (e) { if (request === capacityRequest.current) setError(staffingError(e)) }
  }
  useEffect(() => { if (resourceEmployee && startDate && endDate && startDate <= endDate) void loadCapacity() }, [resourceEmployee?.id, startDate, endDate])
  const allocationAction = async (allocation: CapacityAllocation, action: 'complete' | 'cancel') => {
    setBusy(true); setError('')
    try { await staffingApi.post(`/allocations/${allocation.allocation_id}/${action}`, { expected_version: allocation.version || 1 }, true); await loadCapacity() }
    catch (e) { setError(staffingError(e)) } finally { setBusy(false) }
  }
  const adjustAllocation = async () => {
    if (!adjustingAllocation) return
    setBusy(true); setError('')
    try { const values = await allocationForm.validateFields(); await staffingApi.patch(`/allocations/${adjustingAllocation.allocation_id}`, { ...values, expected_version: adjustingAllocation.version || 1, work_dates: values.work_dates.split(/[,，\s]+/).filter(Boolean) }, true); setAdjustingAllocation(null); allocationForm.resetFields(); await loadCapacity() }
    catch (e) { setError(staffingError(e)) } finally { setBusy(false) }
  }
  const retryFacts = async () => {
    if (!resourceEmployee) return
    setBusy(true); setError('')
    try { const result = await staffingApi.post<ResumeFacts>(`/employees/${resourceEmployee.id}/resume-facts/retry`, {}); setFacts(result); await load() }
    catch (e) { setError(staffingError(e)) }
    finally { setBusy(false) }
  }
  const setEmployeePoolStatus = async (employee: Employee, status: 'active' | 'inactive') => {
    setBusy(true); setError('')
    try {
      await staffingApi.patch(`/employees/${employee.id}`, { employment_status: status, expected_version: employee.version })
      message.success(status === 'active' ? '员工已恢复到备选人员' : '员工已移出备选人员')
      await load()
    } catch (e) { setError(staffingError(e)) } finally { setBusy(false) }
  }
  const employeeOptions = employees.filter(item => item.employment_status === 'active').map(item => ({ value: item.id, label: `${item.display_name}（${item.employee_no || item.id.slice(0, 8)}）` }))
  const dateFields = <div className="staffing-date-pair"><label>开始日期（含）<Input aria-label="台账开始日期" type="date" value={startDate} onChange={e => setStartDate(e.target.value)} /></label><label>结束日期（含）<Input aria-label="台账结束日期" type="date" value={endDate} onChange={e => setEndDate(e.target.value)} /></label></div>
  const capacityByDate = new Map(days.map(day => [day.date, day]))
  const selectedCapacity = capacityByDate.get(selectedCapacityDate)
  const capacityCalendar = startDate && endDate ? <Calendar fullscreen={false} value={selectedCapacityDate ? dayjs(selectedCapacityDate) : undefined} validRange={[dayjs(startDate), dayjs(endDate)]} onSelect={value => setSelectedCapacityDate(value.format('YYYY-MM-DD'))} cellRender={value => {
    const row = capacityByDate.get(value.format('YYYY-MM-DD'))
    if (!row) return <span className="capacity-calendar-empty">—</span>
    return <div className={`capacity-calendar-cell ${row.is_workday === false ? 'is-rest' : row.capacity_units == null ? 'is-unknown' : row.remaining_units != null && row.remaining_units < 0 ? 'is-over' : ''}`} onClick={() => setSelectedCapacityDate(value.format('YYYY-MM-DD'))}><span>{row.is_workday === false ? '休息' : row.capacity_units == null ? '未知' : `${row.capacity_units}%`}</span><small>{row.is_workday === false ? '' : `已排 ${row.allocated_units}%`}</small></div>
  }} /> : null
  const capacityDetails = selectedCapacity && <Card size="small" className="capacity-day-detail" title={`${selectedCapacity.date} · ${selectedCapacity.is_workday === false ? '非工作日' : '工作日'}`}><Space wrap><Tag color={selectedCapacity.is_workday === false ? 'default' : 'blue'}>{selectedCapacity.is_workday === false ? '休息日' : `可投入 ${selectedCapacity.capacity_units == null ? '未知' : `${selectedCapacity.capacity_units}%`}`}</Tag>{selectedCapacity.is_workday !== false && <Tag color={selectedCapacity.remaining_units != null && selectedCapacity.remaining_units < 0 ? 'red' : 'green'}>剩余 {selectedCapacity.remaining_units == null ? '未知' : `${selectedCapacity.remaining_units}%`}</Tag>}</Space>{selectedCapacity.allocations?.length ? <div className="capacity-allocation-list">{selectedCapacity.allocations.map(allocation => <div className="capacity-allocation-item" key={allocation.allocation_id}><div><Typography.Text strong>{allocation.project_title}</Typography.Text><Typography.Text type="secondary"> · {allocation.units_per_day}% · {allocation.status === 'active' || allocation.status === 'applied' ? '生效中' : allocation.status === 'completed' ? '已结束' : allocation.status === 'cancelled' ? '已取消' : '已调整'}</Typography.Text>{allocation.basis && <div className="staffing-muted">{allocation.basis}</div>}</div>{hr && (allocation.status === 'active' || allocation.status === 'applied') && <Space size={4}><Button size="small" onClick={() => { setAdjustingAllocation(allocation); allocationForm.setFieldsValue({ units_per_day: allocation.units_per_day, work_dates: selectedCapacity.date, basis: allocation.basis || '' }) }}>调整</Button><Popconfirm title="结束后将释放今天及未来占用" onConfirm={() => void allocationAction(allocation, 'complete')}><Button size="small">结束</Button></Popconfirm><Popconfirm title="取消后将释放今天及未来占用" onConfirm={() => void allocationAction(allocation, 'cancel')}><Button size="small" danger>取消</Button></Popconfirm></Space>}</div>)}</div> : <div className="staffing-muted capacity-no-allocation">当天没有项目占用</div>}</Card>
  const normalizedEmployeeQuery = employeeQuery.trim().toLocaleLowerCase()
  const visibleEmployees = employees.filter(item => item.employment_status === employeeStatus && (!normalizedEmployeeQuery || [item.display_name, item.employee_no, item.department_name, item.current_position].filter(Boolean).some(value => String(value).toLocaleLowerCase().includes(normalizedEmployeeQuery))))
  const peopleTable = <><div className="staffing-table-toolbar"><Input.Search allowClear aria-label="搜索员工" placeholder="搜索姓名、工号、部门或岗位" value={employeeQuery} onChange={event => setEmployeeQuery(event.target.value)} className="staffing-employee-search" /><Select aria-label="员工状态筛选" value={employeeStatus} onChange={setEmployeeStatus} options={[{ value: 'active', label: '备选人员' }, { value: 'inactive', label: '已移出' }]} /></div><Table rowKey="id" size="small" loading={loading} dataSource={visibleEmployees} scroll={{ x: 860 }} locale={{ emptyText: employeeQuery ? '没有匹配的员工。' : employeeStatus === 'active' ? '还没有备选员工，人事可以从知识库建立档案。' : '没有已移出的员工。' }} columns={[
    { title: '员工', dataIndex: 'display_name' }, { title: '工号', dataIndex: 'employee_no', render: (value, item) => <span title={item.id}>{value || item.id.slice(0, 8)}</span> }, { title: '当前岗位', render: (_, item) => <>{item.current_position || '待确认'} <span className="staffing-muted">{item.position_origin === 'knowledge' ? '知识库同步' : item.position_origin === 'hr' ? '人事维护' : ''}</span></> }, { title: '能力状态', dataIndex: 'facts_status', align: 'center' as const, render: value => <StaffingStatus value={value} /> }, { title: '操作', align: 'center' as const, render: (_, item) => <Space wrap><Button type="link" onClick={() => void openEmployee(item)}>查看员工</Button>{hr && <Button type="link" onClick={() => { setEditing(item); editForm.setFieldsValue({ ...item, current_position: item.current_position || null }) }}>维护档案</Button>}{hr && item.employment_status === 'active' && <Popconfirm title="移出备选人员？" description="会保留员工、简历和历史排期；如果仍有当前或未来占用，系统会拒绝本次操作。" okText="移出" cancelText="取消" onConfirm={() => void setEmployeePoolStatus(item, 'inactive')}><Button type="link" danger disabled={busy}>移出备选</Button></Popconfirm>}{hr && item.employment_status !== 'active' && <Button type="link" disabled={busy} onClick={() => void setEmployeePoolStatus(item, 'active')}>恢复备选</Button>}</Space> },
  ]} /></>
  return <div className="staffing-page staffing-list-page">
    <div className="staffing-page-heading"><div><Link to="/staffing">返回项目组队</Link><Typography.Title level={2}>人员准备</Typography.Title></div><Space wrap><Button onClick={() => void load()}>刷新</Button>{hr && <Button type="primary" onClick={() => void perform(async () => { await loadDocuments(); setImportResults([]); setSelected([]); setIdentities({}); setImportOpen(true) })}>从知识库建立档案</Button>}</Space></div>
    {error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} />}
    <Card className="staffing-list-card staffing-employee-card"><Tabs items={[
      { key: 'employees', label: '员工档案', children: peopleTable },
      { key: 'calendar', label: '公司工作日', children: <section className="staffing-ledger-section"><Typography.Title level={3}>公司工作日</Typography.Title><p>默认周一至周五为工作日、周六周日休息。只需维护节假日、调休等特殊日期。</p>{dateFields}<Button onClick={() => void loadCalendar()}>读取公司日历</Button><div className="staffing-spaced"><Checkbox.Group disabled={!hr} value={calendarDates} options={calendar.map(day => ({ label: `${day.work_date}${day.is_default ? '（默认）' : '（特殊）'}`, value: day.work_date }))} onChange={values => setCalendarDates(values as string[])} /></div>{hr && <><label>新增特殊日期（逗号或换行分隔，YYYY-MM-DD）<Input.TextArea aria-label="新增公司工作日" value={calendarText} onChange={e => setCalendarText(e.target.value)} rows={3} placeholder="例如节假日调休日期" /></label><Button className="staffing-spaced" type="primary" loading={busy} onClick={() => void perform(async () => { const extra = calendarText.split(/[\s,，]+/).filter(Boolean); if (extra.some(value => !/^\d{4}-\d{2}-\d{2}$/.test(value))) throw new Error('日期格式需为 YYYY-MM-DD'); const all = [...new Set([...calendar.map(day => day.work_date), ...extra])]; if (!all.length) throw new Error('请填写至少一个明确日期'); await staffingApi.put('/work-calendar', { expected_revision: setup?.revision, days: all.map(work_date => ({ work_date, is_workday: extra.includes(work_date) || calendarDates.includes(work_date) })) }); setCalendarText(''); await loadCalendar() })}>保存特殊日期</Button></>}</section> },
    ]} /></Card>
    <Modal title="从知识库建立员工档案" open={importOpen} width={820} onCancel={() => setImportOpen(false)} okText="建立或关联" confirmLoading={busy} onOk={() => void perform(async () => { if (!selected.length) throw new Error('请至少选择一份简历'); const result = await staffingApi.post<{ results: Record<string, any>[] }>('/employees/from-documents', { documents: documents.filter(item => selected.includes(item.id)).map(item => ({ candidate_id: item.id, candidate_created_at: item.created_at, ...(identities[item.id] === 'new' ? { create_new: true } : identities[item.id] ? { employee_id: identities[item.id], expected_version: employees.find(employee => employee.id === identities[item.id])?.version } : {}) })) }); setImportResults(result.results) })}>
      <Alert type="info" message="这里只建立员工身份，不重新上传简历。没有同名冲突时系统自动建立；有歧义时请选择已有员工或新建独立员工。" /><Table rowKey="id" rowSelection={{ selectedRowKeys: selected, onChange: keys => setSelected(keys.slice(0, 100)) }} dataSource={documents} pagination={{ pageSize: 6 }} scroll={{ x: 620 }} columns={[{ title: '简历姓名', dataIndex: 'name' }, { title: '资料时间', dataIndex: 'created_at' }, { title: '身份选择', render: (_, item) => <Select aria-label={`简历${item.id}身份选择`} className="staffing-full" value={identities[item.id] || 'auto'} onChange={value => setIdentities({ ...identities, [item.id]: value === 'auto' ? '' : value })} options={[{ value: 'auto', label: '无歧义时自动建立' }, { value: 'new', label: '新建独立员工' }, ...employeeOptions]} /> }]} />
      {importResults.map((result, index) => <Alert key={index} className="staffing-spaced" type={['created', 'linked', 'already_linked'].includes(result.status) ? 'success' : 'warning'} message={`资料 #${result.candidate_id ?? ''}：${({ created: '已建档', linked: '已关联', already_linked: '已有关联', needs_identity_choice: '需要选择身份', failed: '失败' } as Record<string, string>)[result.status] || result.status}`} description={result.message || result.employee_id} />)}
    </Modal>
    <Modal title="维护员工档案" open={!!editing} onCancel={() => setEditing(null)} confirmLoading={busy} okText="保存档案" onOk={() => void perform(async () => { if (!editing) return; const values = await editForm.validateFields(); await staffingApi.patch(`/employees/${editing.id}`, { ...values, current_position: values.current_position || null, expected_version: editing.version, ...(editing.primary_document ? { expected_primary_document: editing.primary_document } : {}) }); setEditing(null) })}>{error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} />}<Form form={editForm} layout="vertical"><Form.Item label="姓名" name="display_name" rules={[{ required: true }]}><Input /></Form.Item><Form.Item label="工号（可空）" name="employee_no"><Input /></Form.Item><Form.Item label="部门" name="department_name"><Input /></Form.Item><Form.Item label="当前岗位" name="current_position" extra="人事修改后会同步知识库主简历；清空表示待确认"><Select aria-label="当前岗位" allowClear placeholder="待确认" options={JOB_CATEGORY_OPTIONS} /></Form.Item><Form.Item label="在职状态" name="employment_status"><Select options={[{ label: '在职', value: 'active' }, { label: '停用', value: 'inactive' }, { label: '离职', value: 'departed' }]} /></Form.Item></Form></Modal>
    <Modal title="调整未来占用" open={!!adjustingAllocation} confirmLoading={busy} okText="保存调整" onCancel={() => { if (!busy) setAdjustingAllocation(null) }} onOk={() => void adjustAllocation()}>
      <Form form={allocationForm} layout="vertical"><Form.Item label="调整后每日投入比例（%）" name="units_per_day" rules={[{ required: true }]}><InputNumber min={1} max={100} precision={0} /></Form.Item><Form.Item label="调整后工作日" name="work_dates" extra="只能填写今天及未来日期，用逗号或空格分隔" rules={[{ required: true }]}><Input /></Form.Item><Form.Item label="调整依据" name="basis"><Input.TextArea rows={2} /></Form.Item></Form>
    </Modal>
    <Drawer title={`${resourceEmployee?.display_name || ''} · 员工详情`} open={!!resourceEmployee} width={860} onClose={() => { ++resourceRequest.current; ++capacityRequest.current; setResourceEmployee(null) }} className="staffing-drawer">
      <Tabs items={[
        { key: 'facts', label: '能力依据', children: <><StaffingStatus value={facts?.status} />{hr && facts && facts.status !== 'ready' && <Button loading={busy} onClick={() => void retryFacts()}>重新抽取能力事实</Button>}<Space wrap>{facts?.skills?.map(skill => <Tag key={skill}>{skill}</Tag>)}</Space>{facts?.facts?.slice(0, 8).map((fact, index) => <section className="staffing-fact" key={index}><Typography.Text strong>{fact.normalized_key || fact.fact_type}</Typography.Text>{Object.entries(fact.fact || {}).filter(([key, value]) => key !== 'conflicted' && value !== null && value !== '').map(([key, value]) => <p key={key}>{factLabels[key] || '简历记载'}：{Array.isArray(value) ? value.join('、') : safeSummary(value)}</p>)}{fact.source_refs?.slice(0, 2).map((ref, refIndex) => <blockquote key={refIndex}>{ref.quote || '引用已不可用'}</blockquote>)}</section>)}{!facts?.facts?.length && <div className="staffing-empty">{facts?.status === 'pending' ? '能力事实尚未准备；生成推荐时会按当前主简历读取。' : facts?.status === 'partial' ? '本次抽取只覆盖了部分有效分段；可点击“重新抽取能力事实”。' : '暂无可用能力事实。'}</div>}</> },
        { key: 'capacity', label: '可投入时间', children: <><p>按日期查看可投入、已安排和剩余比例；点选某天可查看该项目占用并结束或调整。</p>{dateFields}<Space wrap><Button onClick={() => void loadCapacity()}>刷新日历</Button><Typography.Text type="secondary">已读取 {days.length ? `${days[0]?.date} 至 ${days[days.length - 1]?.date}` : '请先读取日期区间'}</Typography.Text></Space><div className="capacity-calendar-shell">{capacityCalendar}{capacityDetails}</div></> },
      ]} />
    </Drawer>
  </div>
}
