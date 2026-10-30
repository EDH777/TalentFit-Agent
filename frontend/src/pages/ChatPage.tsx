import { useEffect, useRef, useState } from 'react'
import { Alert, Avatar, Button, Card, Empty, Input, List, Popconfirm, Space, Spin, Tag, Typography } from 'antd'
import { DeleteOutlined, PlusOutlined, RobotOutlined, SendOutlined, UserOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import api from '../services/api'
import { formatTime } from '../services/datetime'
import { streamRequest } from '../services/stream'
import ExecutionTrace from '../components/ExecutionTrace'
import type { ChatEvent, Message, RetrievalInfo, SearchStatus, Session } from '../types'

const intentNames: Record<string, string> = { talent_match: '人才匹配', person_profile: '单人画像', candidate_compare: '多人对比', general: '普通问答' }

export default function ChatPage() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [messages, setMessages] = useState<(Message & { incomplete?: boolean })[]>([])
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [retrieval, setRetrieval] = useState<RetrievalInfo | null>(null)
  const [searchStatus, setSearchStatus] = useState<SearchStatus | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const requestId = useRef(0)
  const sessionsVersion = useRef(0)
  const nextMessageId = useRef(0)
  const skipHistory = useRef<number | null>(null)
  const streamController = useRef<AbortController | null>(null)

  useEffect(() => {
    api.get<Session[]>('/chat/sessions').then(({ data }) => {
      setSessions(data); setSessionId(data[0]?.id || null)
    }).catch(() => setError('会话列表加载失败，请刷新页面重试')).finally(() => setSessionsLoading(false))
    api.get<SearchStatus>('/search/status').then(({ data }) => setSearchStatus(data)).catch(() => setSearchStatus(null))
    return () => streamController.current?.abort()
  }, [])
  useEffect(() => {
    const current = ++requestId.current
    if (!sessionId) { setMessages([]); setHistoryLoading(false); return }
    if (skipHistory.current === sessionId) { skipHistory.current = null; return }
    setHistoryLoading(true)
    api.get<Message[]>(`/chat/sessions/${sessionId}/messages`).then(({ data }) => {
      if (current === requestId.current) setMessages(data)
    }).catch(() => { if (current === requestId.current) setError('历史消息加载失败') }).finally(() => {
      if (current === requestId.current) setHistoryLoading(false)
    })
  }, [sessionId])
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, loading])

  const removeSession = async (id: number) => {
    if (loading || deletingId !== null) return
    setDeletingId(id); setError('')
    try {
      await api.delete(`/chat/sessions/${id}`)
      sessionsVersion.current += 1
      setSessions((items) => items.filter((item) => item.id !== id))
      if (sessionId === id) {
        requestId.current += 1
        setSessionId(sessions.find((item) => item.id !== id)?.id ?? null)
        setMessages([]); setHistoryLoading(false); setRetrieval(null)
      }
    } catch (e: any) {
      setError(typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '会话删除失败，请重试')
    } finally { setDeletingId(null) }
  }

  const send = async () => {
    const text = content.trim(); if (!text || loading || historyLoading || sessionsLoading || deletingId !== null) return
    setError(''); setLoading(true); setRetrieval(null)
    const controller = new AbortController(); streamController.current = controller
    const userId = --nextMessageId.current
    const assistantId = --nextMessageId.current
    let active = sessionId
    try {
      if (!active) {
        const { data } = await api.post<Session>('/chat/sessions', { title: text.slice(0, 40) })
        active = data.id
        skipHistory.current = active
        setSessionId(active)
        setSessions((items) => [data, ...items])
      }
      setContent('')
      const optimistic: Message = { id: userId, role: 'user', content: text, created_at: new Date().toISOString(), sources: [] }
      setMessages((items) => [...items, optimistic, { id: assistantId, role: 'assistant', content: '', created_at: new Date().toISOString(), sources: [] }])
      await streamRequest<ChatEvent>(`/chat/sessions/${active}/messages/stream`, { content: text }, (event) => {
        if (event.type === 'node_started' || event.type === 'node_finished') setMessages((items) => items.map((item) => {
          if (item.id !== assistantId) return item
          const trace = item.trace || []
          return { ...item, trace: trace.some((node) => node.id === event.node.id) ? trace.map((node) => node.id === event.node.id ? event.node : node) : [...trace, event.node] }
        }))
        if (event.type === 'retrieval') setRetrieval(event)
        if (event.type === 'delta') setMessages((items) => items.map((item) => item.id === assistantId ? { ...item, content: item.content + event.content } : item))
        if (event.type === 'done') setMessages((items) => items.map((item) => item.id === assistantId ? event.message : item))
      }, 'done', controller.signal)
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setError(e.response?.data?.detail || e.message || 'Agent 暂时不可用，请稍后重试')
      }
      setMessages((items) => items.map((item) => item.id === assistantId ? { ...item, incomplete: true, trace: item.trace?.map((node) => node.status === 'running' ? { ...node, status: 'interrupted', elapsed_ms: Math.max(0, Date.now() - Date.parse(node.started_at)) } : node) } : item))
    } finally {
      setLoading(false); streamController.current = null
      const version = sessionsVersion.current
      if (!controller.signal.aborted) api.get<Session[]>('/chat/sessions').then(({ data }) => { if (version === sessionsVersion.current) setSessions(data) }).catch(() => {})
    }
  }

  const mode = retrieval || (searchStatus && searchStatus.total_chunks > 0 ? searchStatus : null)

  return <div className="chat-page">
    <div className="page-heading"><div><Typography.Title level={2}>人才助手</Typography.Title><Typography.Text type="secondary">基于知识库简历证据回答岗位匹配、能力画像和人员对比。</Typography.Text></div>
      <Space wrap>{mode ? <><Tag color={mode.mode === 'hybrid' ? 'blue' : 'gold'}>{mode.mode === 'hybrid' ? '混合检索' : '关键词检索'}</Tag>{mode.embedding_model && <Typography.Text type="secondary">{mode.embedding_model}</Typography.Text>}</> : <Typography.Text type="secondary">{searchStatus?.total_chunks === 0 ? '暂无可检索内容' : '检索状态暂不可用'}</Typography.Text>}</Space>
    </div>
    {retrieval?.warning && <Typography.Paragraph type="warning">{retrieval.warning}</Typography.Paragraph>}
    <div className="chat-workspace">
      <Card className="session-panel"><Button block icon={<PlusOutlined />} disabled={loading || sessionsLoading || historyLoading || deletingId !== null} onClick={() => { setSessionId(null); setMessages([]); setError(''); setRetrieval(null) }}>新对话</Button>
        <List dataSource={sessions} locale={{ emptyText: '暂无历史会话' }} renderItem={(item) => <List.Item className="session-row"><Button className={`session-select ${sessionId === item.id ? 'session-active' : ''}`} type="text" disabled={loading || deletingId !== null} title={item.title} onClick={() => { setSessionId(item.id); setError(''); setRetrieval(null) }}>{item.title}</Button>
          <Popconfirm title="删除该会话？" description="该会话及全部消息将永久删除。" okText="删除" cancelText="取消" disabled={loading || deletingId !== null} onConfirm={() => { void removeSession(item.id) }}>
            <Button className="session-delete" type="text" danger icon={<DeleteOutlined />} aria-label={`删除会话：${item.title}`} title="删除会话" disabled={loading || deletingId !== null} loading={deletingId === item.id} />
          </Popconfirm>
        </List.Item>} />
      </Card>
      <Card className="chat-card"><div className="message-list">
        {historyLoading && <Spin />}
        {!historyLoading && messages.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="输入岗位需求或员工姓名，开始分析" />}
        {messages.length > 0 && <List dataSource={messages} split={false} renderItem={(item) => <List.Item className={`message-row ${item.role}`}>
          <Avatar icon={item.role === 'assistant' ? <RobotOutlined /> : <UserOutlined />} className="message-avatar" />
          <div className="message-bubble"><div className="message-meta"><Space>{item.role === 'assistant' ? '人才助手' : '你'}{item.intent && <Tag>{intentNames[item.intent] || item.intent}</Tag>}{item.incomplete && <Tag color="red">未完成</Tag>}<span>{formatTime(item.created_at)}</span></Space></div>
            {item.role === 'assistant' && Boolean(item.trace?.length) && <ExecutionTrace nodes={item.trace!} />}
            <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content || (item.incomplete ? '本次回复未完成，请重试。' : '正在准备回复…')}</ReactMarkdown></div>
            {Boolean(item.sources?.length) && <div className="sources">参考简历：{item.sources.map((source) => <Tag key={source.candidate_id}>{source.name}</Tag>)}</div>}
          </div>
        </List.Item>} />}
        <div ref={endRef} />
      </div>
      {error && <Alert className="chat-alert" type="error" showIcon message={error} />}
      <div className="chat-input"><Input.TextArea aria-label="聊天消息" maxLength={8000} disabled={deletingId !== null} value={content} onChange={(e) => setContent(e.target.value)} onPressEnter={(e) => { if (!e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send() } }} placeholder="例如：推荐熟悉 Java 和 Spring 的后端工程师" autoSize={{ minRows: 2, maxRows: 5 }} /><Button type="primary" icon={<SendOutlined />} disabled={!content.trim() || historyLoading || sessionsLoading || deletingId !== null} onClick={() => void send()} loading={loading}>发送</Button></div>
      </Card>
    </div>
  </div>
}
