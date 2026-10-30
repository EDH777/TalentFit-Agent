import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, Input, Segmented, Space, Tag, Typography } from 'antd'
import type { Requirements, StaffingMessage, StaffingProject } from '../../types/staffing'
import { staffingApi, staffingError } from '../../services/staffing'
import { formatRange } from '../../services/datetime'

const MAX_LENGTH = 10000

function DraftCard({ draft, hr, onDraft }: { draft: NonNullable<StaffingMessage['draft']>; hr: boolean; onDraft: (draft: Requirements) => void }) {
  const requirements = draft.requirements || {}
  const slots = requirements.slots || []
  return <div className="staffing-draft">
    <Space wrap><Tag color="gold">需求草稿（未生效）</Tag>{draft.missing_fields?.length ? <Typography.Text type="warning">待确认：{draft.missing_fields.join('、')}</Typography.Text> : null}</Space>
    {requirements.start_date && requirements.end_date && <div className="staffing-muted">日期：{formatRange(requirements.start_date, requirements.end_date)}</div>}
    {slots.length > 0 && <div className="staffing-muted">岗位：{slots.map(slot => `${slot.role_name || '未命名'} ${slot.units_per_day ?? '?'}%${slot.required_skills?.length ? `（${slot.required_skills.join('、')}）` : ''}`).join('；')}</div>}
    {requirements.work_dates?.length ? <div className="staffing-muted">工作日：{requirements.work_dates.length} 天</div> : null}
    {hr && <Button size="small" onClick={() => onDraft(requirements as Requirements)}>填入需求表单</Button>}
  </div>
}

export default function ProjectAssistant({ project, hr, modes, onDraft }: { project: StaffingProject; hr: boolean; modes: ('requirements' | 'explain')[]; onDraft: (draft: Requirements) => void }) {
  const [messages, setMessages] = useState<StaffingMessage[]>([])
  const [content, setContent] = useState('')
  const [mode, setMode] = useState<'requirements' | 'explain'>(modes[0] || 'explain')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => { setMode(modes[0] || 'explain') }, [modes.join('|')])
  useEffect(() => {
    let ignore = false
    setLoading(true); setError(''); setMessages([])
    staffingApi.projectMessages(project.id)
      .then(items => { if (!ignore) setMessages(items) })
      .catch(e => { if (!ignore) setError(staffingError(e)) })
      .finally(() => { if (!ignore) setLoading(false) })
    return () => { ignore = true }
  }, [project.id])
  useEffect(() => {
    const list = listRef.current
    if (list) list.scrollTop = list.scrollHeight
  }, [messages, sending])

  const send = async () => {
    const text = content.trim()
    if (!text || sending) return
    setSending(true); setError('')
    try {
      // Admin messages are always explanations; requirement drafts are HR-only on the server.
      const intent = hr ? mode : 'explain'
      const reply = await staffingApi.sendProjectMessage(project.id, text, project.requirements_revision, intent)
      setMessages(items => [...items, reply.user_message, reply.assistant_message])
      setContent('')
    } catch (e) { setError(staffingError(e)) } finally { setSending(false) }
  }

  const placeholder = mode === 'requirements'
    ? '例如：12 月 1 号到 4 号，要一个后端一个前端，各投一半时间'
    : '例如：为什么选张伟而不是李娜？这套方案有排期冲突吗？'
  return <Card size="small" bordered={false} className="staffing-assistant">
    <Typography.Paragraph type="secondary" className="staffing-assistant-hint">
      {mode === 'requirements'
        ? '用一句话描述需求变化，助手会整理成未生效草稿；确认后回填表单保存。'
        : '针对当前方案询问推荐理由、简历出处和排期冲突；助手不会修改需求或占用排期。'}
    </Typography.Paragraph>
    {modes.length > 1 && <Segmented className="staffing-assistant-mode" size="small" value={mode} onChange={value => setMode(value as 'requirements' | 'explain')}
      options={[{ label: '改需求', value: 'requirements' }, { label: '问方案', value: 'explain' }]} />}
    {error && <Alert className="staffing-spaced" type="error" showIcon message={error} closable onClose={() => setError('')} />}
    <div className="staffing-messages" role="log" aria-label="组队对话" ref={listRef}>
      {!loading && !messages.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={mode === 'requirements' ? '还没有对话。可以直接描述这次的需求变化。' : '还没有对话。可以问“这套推荐的依据是什么？”'} />}
      {messages.map(message => <div key={message.id} className={`staffing-message ${message.role === 'user' ? 'user' : 'assistant'}`}>
        <Typography.Paragraph>{message.content}</Typography.Paragraph>
        {message.draft && <DraftCard draft={message.draft} hr={hr} onDraft={onDraft} />}
      </div>)}
      {sending && <Typography.Text type="secondary">组队助手正在处理…</Typography.Text>}
    </div>
    <div className="staffing-composer">
      <Input.TextArea aria-label="组队助手消息" rows={2} maxLength={MAX_LENGTH} value={content} disabled={sending}
        placeholder={placeholder}
        onChange={event => setContent(event.target.value)}
        onPressEnter={event => { if (!event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} />
      <Button type="primary" loading={sending} disabled={!content.trim()} onClick={() => void send()}>发送</Button>
    </div>
  </Card>
}
