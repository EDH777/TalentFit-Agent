import api from './api'
import { consumeSse } from './stream'
import type { ProjectMessageReply, StaffingEvent, StaffingMessage, StaffingPlan, StaffingRun } from '../types/staffing'

export const staffingApi = {
  get: async <T,>(path: string, params?: object): Promise<T> => (await api.get<T>('/staffing' + path, { params })).data,
  post: async <T,>(path: string, body: object, idempotent = false): Promise<T> => (await api.post<T>('/staffing' + path, body, idempotent ? { headers: { 'Idempotency-Key': crypto.randomUUID() } } : undefined)).data,
  patch: async <T,>(path: string, body: object, idempotent = false): Promise<T> => (await api.patch<T>('/staffing' + path, body, idempotent ? { headers: { 'Idempotency-Key': crypto.randomUUID() } } : undefined)).data,
  put: async <T,>(path: string, body: object): Promise<T> => (await api.put<T>('/staffing' + path, body)).data,
  submitRecommendation: async <T,>(projectId: string, plan: StaffingPlan): Promise<T> => staffingApi.post<T>(`/projects/${projectId}/recommendation/submit`, { plan_id: plan.id, expected_version: plan.version, input_fingerprint: plan.input_fingerprint }, true),
  approveRecommendation: async <T,>(projectId: string, plan: StaffingPlan): Promise<T> => staffingApi.post<T>(`/projects/${projectId}/recommendation/approve`, { plan_id: plan.id, expected_version: plan.version, input_fingerprint: plan.input_fingerprint }, true),
  projectMessages: async (projectId: string): Promise<StaffingMessage[]> => staffingApi.get<StaffingMessage[]>(`/projects/${projectId}/messages`),
  sendProjectMessage: async (projectId: string, content: string, requirementsRevision: number, intent?: 'explain' | 'requirements'): Promise<ProjectMessageReply> =>
    staffingApi.post<ProjectMessageReply>(`/projects/${projectId}/messages`, { content, expected_requirements_revision: requirementsRevision, ...(intent ? { intent } : {}) }),
}
export function staffingError(error: unknown): string {
  const e = error as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = e.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) return String(detail.message)
  if (Array.isArray(detail)) return detail.map(item => item.msg || '输入无效').join('；')
  return e.message || '操作失败，请刷新后重试'
}
export function safeSummary(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const item = value as Record<string, unknown>
    const codes: Record<string, string> = { UNKNOWN_CAPACITY: '日期可投入比例未知', CAPACITY_CONFLICT: '每日排期冲突', FACTS_NOT_READY: '简历能力资料未准备完整', SKILL_NOT_RECORDED: '必备技能未记载', EMPLOYEE_UNAVAILABLE: '员工当前不在可用范围', EMPTY_SLOT_POOL: '该角色暂无已确认合格员工', REQUIRED_EMPLOYEE_MISSING: '缺少指定员工', DUPLICATE_EMPLOYEE: '同一员工不能承担两个角色名额', INVALID_REQUIREMENTS: '需求信息不完整或约束不合法' }
    return [item.message || item.detail || codes[String(item.code)] || item.code, item.date, item.employee_id, Array.isArray(item.skills) ? item.skills.join('、') : null].filter(Boolean).map(String).join(' · ')
  }
  return String(value ?? '')
}
const inactive = new Set(['succeeded', 'failed', 'cancelled', 'waiting_input'])
export async function subscribeStaffingEvents(runId: string, onEvent: (event: StaffingEvent) => void, onStatus: (run: StaffingRun) => void, onConnection: (message: string) => void, signal: AbortSignal): Promise<void> {
  let seq = 0
  const base = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api').replace(/\/$/, '')
  while (!signal.aborted) {
    try {
      const headers = new Headers({ Accept: 'text/event-stream', Authorization: `Bearer ${localStorage.getItem('talentfit_token') || ''}` })
      const response = await fetch(`${base}/staffing/runs/${encodeURIComponent(runId)}/events?after_seq=${seq}`, { headers, signal })
      if (!response.ok) {
        if (response.status === 401) { localStorage.removeItem('talentfit_token'); localStorage.removeItem('talentfit_user'); window.location.href = '/login'; return }
        const payload = await response.json().catch(() => null)
        throw new Error(typeof payload?.detail === 'string' ? payload.detail : payload?.detail?.message || `执行记录连接失败（${response.status}）`)
      }
      if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new Error('执行记录连接未返回有效事件流')
      onConnection('已连接执行记录')
      await consumeSse<StaffingEvent>(response.body, event => {
        if (!Number.isInteger(event.seq) || event.seq <= seq) return
        seq = event.seq; onEvent(event)
      }, 'run_finished', signal)
    } catch (error) {
      if (signal.aborted) return
      onConnection(`执行记录连接中断，正在核对后台状态；${staffingError(error)}`)
    }
    if (signal.aborted) return
    try {
      const run = await staffingApi.get<StaffingRun>(`/runs/${runId}`)
      if (signal.aborted) return
      onStatus(run)
      if (inactive.has(run.status)) { onConnection(run.status === 'waiting_input' ? '等待补充信息，可恢复运行' : '已核对后台运行状态'); return }
    } catch (error) { onConnection(`暂时无法核对后台状态，将重连；${staffingError(error)}`) }
    await new Promise<void>(resolve => { const timer = window.setTimeout(resolve, 1200); signal.addEventListener('abort', () => { clearTimeout(timer); resolve() }, { once: true }) })
  }
}
