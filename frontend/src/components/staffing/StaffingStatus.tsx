import { Tag } from 'antd'

/** Project, run and resume-facts states. Plan states are separate: both use `draft`. */
export const stateNames: Record<string, string> = {
  draft: '待组队', active: '排期已生效', completed: '已结束', cancelled: '已取消', archived: '已归档',
  queued: '正在准备推荐', running: '正在核对员工与排期', waiting_input: '需要补充信息', succeeded: '推荐已完成', failed: '推荐生成失败',
  OPTIMAL: '已找到方案', FEASIBLE: '已找到可行方案', INFEASIBLE: '当前条件没有满足全部要求的方案', UNKNOWN: '暂时无法确认', DATA_INCOMPLETE: '资料或排期不完整',
  ready: '可用', pending: '尚未准备', partial: '派生未完成', stale: '需要重新生成', unavailable: '资料不可用',
}

const planStateNames: Record<string, string> = {
  draft: '推荐已生成', validated: '推荐已准备好', invalidated: '需要重新核对',
  submitted: '等待管理员审批', applied: '排期已生效', rejected: '已驳回', superseded: '历史推荐',
}

const successStates = ['applied', 'succeeded', 'completed', 'OPTIMAL', 'ready']
const failureStates = ['failed', 'invalidated', 'INFEASIBLE', 'cancelled']
const pendingStates = ['running', 'submitted', 'waiting_input', 'DATA_INCOMPLETE', 'UNKNOWN', 'partial']

export default function StaffingStatus({ value, kind }: { value?: string | null; kind?: 'plan' }) {
  if (!value) return <span className="staffing-muted">—</span>
  const labels = kind === 'plan' ? planStateNames : stateNames
  const color = successStates.includes(value) ? 'green' : failureStates.includes(value) ? 'red' : pendingStates.includes(value) ? 'gold' : undefined
  return <Tag color={color}>{labels[value] || value}</Tag>
}
