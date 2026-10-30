import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Alert, Button, Card, Empty, Form, Input, InputNumber, Space, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, SearchOutlined } from '@ant-design/icons'
import api from '../services/api'
import type { RetrievalTestResult } from '../types'

export default function RetrievalTestPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const candidateId = Number(params.get('candidate_id')) || undefined
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState<number | null>(5)
  const [result, setResult] = useState<RetrievalTestResult | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const sequence = useRef(0)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => { ++sequence.current; controller.current?.abort(); setResult(null); setError(''); setLoading(false); return () => { ++sequence.current; controller.current?.abort() } }, [candidateId])
  const run = async () => {
    if (loading || !query.trim() || limit === null || !Number.isInteger(limit) || limit < 1 || limit > 20) return
    const request = ++sequence.current
    controller.current?.abort(); controller.current = new AbortController()
    setLoading(true); setError(''); setResult(null)
    try {
      const { data } = await api.post<RetrievalTestResult>('/knowledge/retrieval-test', { query: query.trim(), limit, ...(candidateId ? { candidate_id: candidateId } : {}) }, { signal: controller.current.signal })
      if (request === sequence.current) setResult(data)
    } catch (error: any) { if (request === sequence.current) setError(typeof error.response?.data?.detail === 'string' ? error.response.data.detail : '召回测试失败，请稍后重试') }
    finally { if (request === sequence.current) setLoading(false) }
  }
  return <div className="knowledge-retrieval"><div className="page-heading"><div><Button type="link" icon={<ArrowLeftOutlined />} onClick={() => navigate(candidateId ? `/candidates/${candidateId}` : '/candidates')}>返回知识库</Button><Typography.Title level={2}>召回测试</Typography.Title><Typography.Text type="secondary">按真实检索链路返回命中分段，不生成助手回答，也不计入正式召回次数。</Typography.Text></div></div>
    <Card><Form layout="vertical" onFinish={() => void run()}><Tag color="blue">{candidateId ? `仅测试文档 #${candidateId}` : '测试全部已启用文档'}</Tag><Form.Item label="测试问题" required><Input.TextArea aria-label="测试问题" rows={4} placeholder="输入岗位要求、技能或具体问题" value={query} disabled={loading} onChange={event => setQuery(event.target.value)} /></Form.Item><Space align="end"><Form.Item label="返回条数" style={{ marginBottom: 0 }}><InputNumber aria-label="返回条数" min={1} max={20} precision={0} value={limit} disabled={loading} onChange={setLimit} /></Form.Item><Button type="primary" htmlType="submit" icon={<SearchOutlined />} loading={loading} disabled={!query.trim() || limit === null || !Number.isInteger(limit) || limit < 1 || limit > 20}>开始测试</Button></Space></Form></Card>
    {error && <Alert className="knowledge-result" type="error" showIcon message={error} />}
    {result && <div className="knowledge-result"><Space wrap className="form-alert"><Tag color={result.mode === 'hybrid' ? 'blue' : 'gold'}>{result.mode === 'hybrid' ? '混合检索：关键词 + 语义向量' : '关键词检索'}</Tag>{result.embedding_model && <Typography.Text>{result.embedding_model}</Typography.Text>}<Typography.Text>命中 {result.hits.length} 个子分段 · 耗时 {result.elapsed_ms.toFixed(1)} 毫秒</Typography.Text></Space>{result.warning && <Alert className="form-alert" showIcon type="warning" message={result.warning} />}
      {result.hits.length ? result.hits.map((hit, index) => <Card className="knowledge-recall-hit" key={`${hit.candidate_id}-${hit.chunk_id}`} title={<Space wrap><Tag color="blue">#{index + 1}</Tag><Button type="link" onClick={() => navigate(`/candidates/${hit.candidate_id}`)}>{hit.name}</Button><Typography.Text type="secondary">子分段 #{hit.position + 1} · 编号 {hit.chunk_id}</Typography.Text></Space>} extra={<Typography.Text>得分 {hit.score.toFixed(6)}</Typography.Text>}>
        <Space wrap><Tag>父分段编号 #{hit.parent_id ?? '—'}</Tag><Typography.Text type="secondary">关键词排名：{hit.keyword_rank ?? '未命中'} · 向量排名：{hit.vector_rank ?? '未命中'}</Typography.Text></Space><Typography.Paragraph className="knowledge-content">{hit.content}</Typography.Paragraph><details className="knowledge-parent-context"><summary>查看父分段上下文</summary><Typography.Paragraph className="knowledge-content">{hit.parent_content}</Typography.Paragraph></details>
      </Card>) : <Card><Empty description="没有命中当前启用的分段，可调整问题或检查文档与分段开关" /></Card>}
    </div>}
  </div>
}
