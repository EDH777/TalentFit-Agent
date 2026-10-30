import { useEffect, useState } from 'react'
import { Alert, Button, Card, Empty, Space, Tag, Typography } from 'antd'
import { AlertOutlined, ArrowRightOutlined, CheckCircleOutlined, ClockCircleOutlined, DatabaseOutlined, FileSearchOutlined, MessageOutlined, ProjectOutlined, ReloadOutlined, TeamOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useUser } from '../AppLayout'
import api from '../services/api'
import { staffingApi } from '../services/staffing'
import type { KnowledgeSummary, SearchStatus } from '../types'
import type { Employee, StaffingProject } from '../types/staffing'
import '../styles/dashboard.css'

interface Summary { total: number; ready: number; processing: number; failed: number }
interface DashboardData { summary: Summary; knowledge: KnowledgeSummary | null; search: SearchStatus | null; employees: Employee[]; projects: StaffingProject[] }

const emptyData: DashboardData = { summary: { total: 0, ready: 0, processing: 0, failed: 0 }, knowledge: null, search: null, employees: [], projects: [] }

function resultValue<T>(result: PromiseSettledResult<T>, fallback: T): T {
  return result.status === 'fulfilled' ? result.value : fallback
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const user = useUser()
  const [data, setData] = useState(emptyData)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    const results = await Promise.allSettled([
      api.get<Summary>('/dashboard/summary').then(response => response.data),
      api.get<KnowledgeSummary>('/knowledge/summary').then(response => response.data),
      api.get<SearchStatus>('/search/status').then(response => response.data),
      staffingApi.get<Employee[]>('/employees'),
      staffingApi.get<StaffingProject[]>('/projects'),
    ])
    const [summary, knowledge, search, employees, projects] = results
    const failed = results.filter(item => item.status === 'rejected').length
    setData({ summary: resultValue(summary, emptyData.summary), knowledge: resultValue(knowledge, null), search: resultValue(search, null), employees: resultValue(employees, []), projects: resultValue(projects, []) })
    setError(failed ? '部分工作台数据暂时无法读取，可点击刷新重试。' : '')
    setLoading(false)
  }

  useEffect(() => { void load() }, [])

  const activeEmployees = data.employees.filter(employee => employee.employment_status === 'active')
  const pendingFacts = activeEmployees.filter(employee => employee.facts_status && employee.facts_status !== 'ready').length
  const activeProjects = data.projects.filter(project => project.business_status === 'active').length
  const pendingApproval = data.projects.filter(project => project.latest_plan_state === 'submitted').length
  const pendingChunks = data.search?.pending_chunks || 0
  const attentionCount = data.summary.failed + pendingFacts + pendingApproval + pendingChunks
  const roleLabel = user?.role === 'hr' ? '人事' : '管理员'

  const quickActions = [
    { label: '人才助手', description: '用简历证据分析员工能力', icon: <MessageOutlined />, action: () => navigate('/chat') },
    { label: '人才知识库', description: '维护员工资料与分段状态', icon: <DatabaseOutlined />, action: () => navigate('/candidates') },
    { label: '人员准备', description: '查看员工池与每日可投入比例', icon: <TeamOutlined />, action: () => navigate('/staffing/employees') },
    { label: '项目列表', description: '查看组队项目和审批进展', icon: <ProjectOutlined />, action: () => navigate('/staffing') },
  ]

  return <div className="dashboard-page internal-dashboard">
    <div className="page-heading dashboard-heading"><div><Typography.Title level={2}>工作台</Typography.Title><Typography.Text type="secondary">内部人才运营与提效</Typography.Text></div><Space><Tag color="blue">{roleLabel}</Tag><Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button></Space></div>
    {error && <Alert className="dashboard-alert" type="warning" showIcon message={error} />}
    <section className="dashboard-metric-grid" aria-label="运营概览">
      <Card className="dashboard-metric"><TeamOutlined className="dashboard-metric-icon blue" /><div><div className="dashboard-metric-value">{activeEmployees.length}</div><div className="dashboard-metric-label">备选员工</div><div className="dashboard-metric-note">共 {data.employees.length} 份员工档案</div></div></Card>
      <Card className="dashboard-metric"><DatabaseOutlined className="dashboard-metric-icon purple" /><div><div className="dashboard-metric-value">{data.knowledge?.documents ?? data.summary.total}</div><div className="dashboard-metric-label">知识库文档</div><div className="dashboard-metric-note">已入库 {data.summary.ready} 份</div></div></Card>
      <Card className="dashboard-metric"><ProjectOutlined className="dashboard-metric-icon green" /><div><div className="dashboard-metric-value">{activeProjects}</div><div className="dashboard-metric-label">进行中项目</div><div className="dashboard-metric-note">{pendingApproval ? `${pendingApproval} 个待审批` : '暂无待审批项目'}</div></div></Card>
      <Card className={`dashboard-metric ${attentionCount ? 'has-attention' : ''}`}><AlertOutlined className="dashboard-metric-icon orange" /><div><div className="dashboard-metric-value">{attentionCount}</div><div className="dashboard-metric-label">待处理事项</div><div className="dashboard-metric-note">能力、索引或审批状态</div></div></Card>
    </section>

    <section className="dashboard-content-grid">
      <Card className="dashboard-panel dashboard-actions" title="常用工作" extra={<Typography.Text type="secondary">按业务进入</Typography.Text>}>
        <div className="dashboard-action-list">{quickActions.map(action => <button className="dashboard-action" key={action.label} onClick={action.action}><span className="dashboard-action-icon">{action.icon}</span><span className="dashboard-action-copy"><strong>{action.label}</strong><small>{action.description}</small></span><ArrowRightOutlined className="dashboard-action-arrow" /></button>)}</div>
      </Card>

      <Card className="dashboard-panel dashboard-attention" title="需要处理" extra={attentionCount ? <Tag color="orange">{attentionCount} 项</Tag> : <Tag color="green">已清空</Tag>}>
        <div className="dashboard-attention-list">
          {pendingFacts > 0 && <button onClick={() => navigate('/staffing/employees')}><ClockCircleOutlined /><span><strong>{pendingFacts} 名员工能力资料待准备</strong><small>打开人员准备查看或重试抽取</small></span><ArrowRightOutlined /></button>}
          {pendingChunks > 0 && <button onClick={() => navigate('/candidates')}><FileSearchOutlined /><span><strong>{pendingChunks} 个子分段待建立向量索引</strong><small>知识库仍可使用关键词检索</small></span><ArrowRightOutlined /></button>}
          {pendingApproval > 0 && <button onClick={() => navigate('/staffing')}><CheckCircleOutlined /><span><strong>{pendingApproval} 个项目等待管理员审批</strong><small>审批后才会写入正式排期</small></span><ArrowRightOutlined /></button>}
          {data.summary.failed > 0 && <button onClick={() => navigate('/candidates')}><AlertOutlined /><span><strong>{data.summary.failed} 份资料入库失败</strong><small>打开人才知识库查看失败原因</small></span><ArrowRightOutlined /></button>}
          {!attentionCount && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有需要立即处理的事项" />}
        </div>
      </Card>

    </section>

  </div>
}
