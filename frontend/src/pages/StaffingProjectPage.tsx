import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Modal, Space, Spin, Steps, Tag, Typography } from 'antd'
import { Link, useParams } from 'react-router-dom'
import { useUser } from '../AppLayout'
import ExecutionTrace from '../components/ExecutionTrace'
import ProjectAssistant from '../components/staffing/ProjectAssistant'
import RequirementsEditor from '../components/staffing/RequirementsEditor'
import PlanComparison from '../components/staffing/PlanComparison'
import StaffingStatus from '../components/staffing/StaffingStatus'
import { formatRange } from '../services/datetime'
import { safeSummary, staffingApi, staffingError, subscribeStaffingEvents } from '../services/staffing'
import type { TraceNode } from '../types'
import type { Employee, Requirements, SetupState, StaffingPlan, StaffingProject, StaffingRun } from '../types/staffing'
import '../styles/staffing.css'

const REJECTED_STATES = ['superseded', 'rejected']
const PLAN_PRIORITY: Record<string, number> = { applied: 5, submitted: 4, validated: 3, invalidated: 2, draft: 1 }

/** Where the project currently sits in 需求 → 推荐 → 排期, derived from data rather than local clicks. */
function projectStage(project: StaffingProject, plans: StaffingPlan[], run: StaffingRun | null, editing: boolean): number {
  if (editing || !project.requirements_revision || !project.requirements) return 0
  if (plans.some(plan => plan.state === 'applied')) return 2
  const relevant = plans.filter(plan => !REJECTED_STATES.includes(plan.state))
  const runMatches = run?.requirements_revision === project.requirements_revision
  if (relevant.length || (runMatches && ['queued', 'running', 'waiting_input', 'failed'].includes(run!.status))) return 1
  if (runMatches && run!.status === 'succeeded') return 1
  return 0
}

/** A finished run without plans still has a reason; it must not read as "nothing generated yet". */
function solverOutcome(result: StaffingRun['result']): { message: string; diagnostics: unknown[] } {
  const status = result?.solver_status
  if (status === 'INFEASIBLE') return { message: '当前条件下没有满足全部要求的团队', diagnostics: result?.diagnostics || [] }
  if (status === 'UNKNOWN') return { message: '在限定时间内无法确认可行方案', diagnostics: result?.diagnostics || [] }
  if (status === 'DATA_INCOMPLETE') return { message: '资料或排期不完整，无法完成求解', diagnostics: result?.diagnostics || [] }
  return { message: '本次没有生成推荐方案', diagnostics: result?.diagnostics || [] }
}

export default function StaffingProjectPage() {
  const { projectId = '' } = useParams()
  const hr = useUser()?.role === 'hr'
  const [project, setProject] = useState<StaffingProject | null>(null)
  const [employees, setEmployees] = useState<Employee[]>([])
  const [plans, setPlans] = useState<StaffingPlan[]>([])
  const [setup, setSetup] = useState<SetupState | null>(null)
  const [run, setRun] = useState<StaffingRun | null>(null)
  const [nodes, setNodes] = useState<TraceNode[]>([])
  const [connection, setConnection] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [closeAction, setCloseAction] = useState<'complete' | 'cancel' | null>(null)
  const [editingRequirements, setEditingRequirements] = useState(false)
  const [requirementDraft, setRequirementDraft] = useState<Requirements | null>(null)
  const activeProjectId = useRef(projectId)
  const loadRevision = useRef(0)
  activeProjectId.current = projectId
  const load = useCallback(async () => {
    const revision = ++loadRevision.current
    try {
      const [nextProject, nextEmployees, nextPlans, nextSetup] = await Promise.all([staffingApi.get<StaffingProject>(`/projects/${projectId}`), staffingApi.get<Employee[]>('/employees'), staffingApi.get<StaffingPlan[]>(`/projects/${projectId}/plans`), staffingApi.get<SetupState>('/setup')])
      if (activeProjectId.current !== projectId || revision !== loadRevision.current) return
      setProject(nextProject); setEmployees(nextEmployees); setPlans(nextPlans); setSetup(nextSetup); setRun(nextProject.latest_run || null); setError('')
    } catch (e) { if (activeProjectId.current === projectId && revision === loadRevision.current) setError(staffingError(e)) }
  }, [projectId])
  useEffect(() => { setProject(null); setEmployees([]); setPlans([]); setRun(null); setNodes([]); setError(''); void load(); return () => { loadRevision.current += 1 } }, [load])
  const runId = run?.id
  useEffect(() => {
    if (!runId) return
    const controller = new AbortController()
    setNodes([])
    subscribeStaffingEvents(runId, event => {
      if (controller.signal.aborted || activeProjectId.current !== projectId) return
      if (event.node) setNodes(current => { const index = current.findIndex(node => node.id === event.node!.id); return index < 0 ? [...current, event.node!] : current.map((node, i) => i === index ? event.node! : node) })
    }, value => { if (!controller.signal.aborted && activeProjectId.current === projectId) { setRun(value); if (['succeeded', 'failed', 'cancelled', 'waiting_input'].includes(value.status)) void load() } }, value => { if (!controller.signal.aborted && activeProjectId.current === projectId) setConnection(value) }, controller.signal).catch(e => { if (!controller.signal.aborted && activeProjectId.current === projectId) setError(staffingError(e)) })
    return () => controller.abort()
  }, [runId, projectId, load])
  const active = !!run && ['queued', 'running', 'waiting_input'].includes(run.status)
  const generate = async () => {
    if (!project) return
    setBusy(true); setError('')
    try { const next = await staffingApi.post<StaffingRun>(`/projects/${project.id}/recommendation`, { expected_version: project.version, requirements_revision: project.requirements_revision, mode: 'multi' }); setRun(next); await load() }
    catch (e) { setError(staffingError(e)); await load() } finally { setBusy(false) }
  }
  const cancel = async () => {
    if (!run || !project) return
    setBusy(true); setError('')
    try { const next = await staffingApi.post<StaffingRun>(`/runs/${run.id}/cancel`, { expected_version: run.version, requirements_revision: run.requirements_revision }); setRun(next) }
    catch (e) { setError(staffingError(e)) } finally { setBusy(false) }
  }
  const resume = async () => {
    if (!run || !project) return
    setBusy(true); setError('')
    try { const next = await staffingApi.post<StaffingRun>(`/runs/${run.id}/resume`, { expected_version: run.version, requirements_revision: run.requirements_revision }); setRun(next); await load() }
    catch (e) { setError(staffingError(e)) }
    finally { setBusy(false) }
  }
  const closeProject = async () => {
    if (!project || !closeAction) return
    setBusy(true); setError('')
    try { await staffingApi.post(`/projects/${project.id}/${closeAction}`, { expected_version: project.version }, true); setCloseAction(null); await load() }
    catch (e) { setError(staffingError(e)) } finally { setBusy(false) }
  }
  const applyDraft = (draft: Requirements) => {
    // Slot ids are backend-owned; work days stay as proposed, date range decides the rest.
    const slots = (draft.slots || []).map(({ slot_id: _ignored, ...slot }) => ({
      ...slot, required_skills: slot.required_skills || [], preferred_skills: slot.preferred_skills || [],
    }))
    setRequirementDraft({ ...draft, slots, employee_ids: [], required_employee_ids: [], excluded_employee_ids: [] })
    setEditingRequirements(true)
    window.requestAnimationFrame(() => document.querySelector('.staffing-requirements')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }
  const closeRequirementEditor = () => { setEditingRequirements(false); setRequirementDraft(null) }
  if (!project) return <div className="staffing-page">{error ? <Alert type="error" message={error} action={<Button onClick={() => void load()}>重试</Button>} /> : <Spin />}</div>
  const stage = projectStage(project, plans, run, editingRequirements)
  const currentPlan = plans.filter(plan => !REJECTED_STATES.includes(plan.state))
    .sort((a, b) => (PLAN_PRIORITY[b.state] || 0) - (PLAN_PRIORITY[a.state] || 0) || b.revision_no - a.revision_no)[0]
  const trace = run ? <ExecutionTrace nodes={nodes} /> : undefined
  const requirements = project.requirements
  const workDays = requirements?.work_dates || []
  const activeEmployees = employees.filter(employee => employee.employment_status === 'active')
  const solver = stage === 1 && run?.status === 'succeeded' && !plans.filter(plan => !REJECTED_STATES.includes(plan.state)).length ? solverOutcome(run.result) : null
  const canGenerate = hr && project.business_status === 'draft' && !active && stage === 0 && Boolean(requirements)
    && !plans.filter(plan => !REJECTED_STATES.includes(plan.state)).length
  const canStepBack = hr && project.business_status === 'draft' && !active && stage === 1
  const assistantModes: ('requirements' | 'explain')[] = stage === 0
    ? (hr && project.business_status === 'draft' ? ['requirements'] : [])
    : ['explain']
  return <div className="staffing-page">
    <div className="staffing-page-heading"><div><Link to="/staffing">返回项目列表</Link><Typography.Title level={2}>{project.title}</Typography.Title></div><Space wrap><StaffingStatus value={project.business_status} />{currentPlan && <StaffingStatus value={currentPlan.state} kind="plan" />}{hr && project.business_status === 'active' && <Button onClick={() => setCloseAction('complete')}>提前结束项目</Button>}{hr && (project.business_status === 'active' || project.business_status === 'draft') && <Button danger onClick={() => setCloseAction('cancel')}>取消项目</Button>}<Button onClick={() => void load()}>刷新</Button></Space></div>
    {error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} />}
    <Card size="small" className="staffing-project-status-card"><div className="staffing-project-status-head"><div><Typography.Text type="secondary">项目状态</Typography.Text><div><StaffingStatus value={project.business_status} /></div></div><Steps size="small" current={stage} items={[{ title: '项目需求' }, { title: '推荐方案' }, { title: '正式排期' }]} /></div></Card>

    {stage === 0 && <Card size="small" className="staffing-region staffing-section-card" title="项目需求"
      extra={requirements && !editingRequirements && hr && project.business_status === 'draft' && !active ? <Button onClick={() => setEditingRequirements(true)}>修改需求</Button> : undefined}>
      {editingRequirements || !requirements
        ? <>
          <RequirementsEditor project={project} employees={employees} disabled={!hr || active || project.business_status !== 'draft'} draft={requirementDraft}
            onSaved={() => { closeRequirementEditor(); void load() }} />
          {editingRequirements && requirements && <Button className="staffing-spaced" onClick={closeRequirementEditor}>取消修改</Button>}
        </>
        : <>
          <div className="staffing-summary-grid">
            <div><Typography.Text type="secondary">项目日期</Typography.Text><div>{formatRange(requirements.start_date, requirements.end_date)}</div></div>
            <div><Typography.Text type="secondary">员工范围</Typography.Text><div>{activeEmployees.length} 名在职员工</div></div>
          </div>
          <div className="staffing-summary-block">
            <Typography.Text type="secondary">项目工作日（{workDays.length} 天，按公司日历自动确定）</Typography.Text>
            <Space wrap size={4} className="staffing-spaced-sm">{workDays.map(day => <Tag key={day}>{day}</Tag>)}</Space>
          </div>
          <div className="staffing-summary-block">
            <Typography.Text type="secondary">岗位与参与日期</Typography.Text>
            {requirements.slots.map(slot => {
              const days = slot.work_dates?.length || workDays.length
              const partial = slot.work_dates?.length > 0 && slot.work_dates.length < workDays.length
              return <div className="staffing-slot-summary" key={slot.slot_id || slot.role_name}>
                <Typography.Text strong>{slot.role_name}</Typography.Text>
                <Tag>{slot.units_per_day}%</Tag>
                <Tag color={partial ? 'gold' : 'default'}>{partial ? `参与 ${days}/${workDays.length} 天` : '全程参与'}</Tag>
                {slot.required_skills.length > 0 ? slot.required_skills.map(skill => <Tag key={skill} color="blue">{skill}</Tag>) : <Typography.Text type="secondary">未填写必备技能</Typography.Text>}
              </div>
            })}
          </div>
          <Space wrap className="staffing-actions">
            {canGenerate && <Button type="primary" loading={busy} onClick={() => void generate()}>生成推荐</Button>}
            <span className="staffing-muted">确认需求后生成一套可审批的方案；生成不会占用排期。</span>
          </Space>
        </>}
    </Card>}

    {stage === 1 && <Card size="small" className="staffing-region staffing-section-card" title="推荐方案"
      extra={<Space wrap>{canStepBack && <Button onClick={() => setEditingRequirements(true)}>上一步：修改需求</Button>}<StaffingStatus value={active ? run?.status : undefined} /></Space>}>
      {!hr && plans.some(plan => plan.state === 'submitted') && setup && !setup.bootstrap_closed && <Alert className="staffing-bottom" type="warning" showIcon message="暂不能正式批准" description="请先由管理员在“系统准备”完成核对并关闭初始化。" />}
      {active && <Space wrap className="staffing-spaced"><Button danger loading={busy} onClick={() => void cancel()}>取消当前生成</Button>{connection && <span className="staffing-muted" role="status">{connection}</span>}<span className="staffing-muted">关闭页面不会取消后台任务</span></Space>}
      {run?.status === 'waiting_input' && <Alert type="warning" className="staffing-spaced" message="需要补充信息" description={<>{run.questions?.map((question, index) => <p key={index}>{safeSummary(question)}</p>)}{hr && <Button loading={busy} onClick={() => void resume()}>已补充，重新检查</Button>}</>} />}
      {run?.status === 'failed' && <Alert type="error" className="staffing-spaced" message="推荐生成失败" description="请检查缺失信息后重新生成，系统没有写入正式排期。" />}
      {solver && <Alert className="staffing-spaced" type="warning" showIcon message={solver.message} description={solver.diagnostics.length ? solver.diagnostics.map((item, index) => <p key={index}>{safeSummary(item)}</p>) : <p>可调整岗位技能或日期范围后重新生成。</p>} />}
      {!solver && (plans.filter(plan => !REJECTED_STATES.includes(plan.state)).length
        ? <PlanComparison plans={plans} project={project} employees={employees} hr={hr} onChanged={() => void load()} trace={trace} />
        : !active && <div className="staffing-empty">正在准备推荐，请稍候…</div>)}
    </Card>}

    {stage === 2 && <Card size="small" className="staffing-region staffing-section-card" title="正式排期">
      <Alert className="staffing-bottom" type="success" showIcon message="该方案已批准并写入正式员工排期"
        description={`项目日期 ${formatRange(project.start_date, project.end_date)}。如需调整，可提前结束或取消项目，系统会释放当前及未来的占用并保留历史记录。`} />
      <PlanComparison plans={plans} project={project} employees={employees} hr={hr} onChanged={() => void load()} trace={trace} />
    </Card>}

    {assistantModes.length > 0 && <Card size="small" className="staffing-region staffing-section-card" title="组队助手">
      <ProjectAssistant key={`${project.id}:${stage}`} project={project} hr={hr} modes={assistantModes} onDraft={applyDraft} />
    </Card>}
    <Modal title={closeAction === 'complete' ? '提前结束项目' : '取消项目'} open={!!closeAction} onCancel={() => setCloseAction(null)} okText={closeAction === 'complete' ? '确认结束并释放占用' : '确认取消并释放占用'} okButtonProps={{ danger: closeAction === 'cancel' }} confirmLoading={busy} onOk={() => void closeProject()}><p>{closeAction === 'complete' ? '项目结束后会释放当前及未来的全部员工占用，并保留历史记录。' : '项目取消后会释放当前及未来的全部员工占用，并保留取消记录。'}</p></Modal>
  </div>
}
