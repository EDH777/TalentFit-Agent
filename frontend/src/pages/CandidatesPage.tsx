import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, Form, Input, Modal, Popconfirm, Progress, Select, Space, Switch, Table, Tag, Typography, Upload, message } from 'antd'
import type { UploadProps } from 'antd'
import { useNavigate } from 'react-router-dom'
import { DeleteOutlined, EditOutlined, InboxOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import api from '../services/api'
import { streamRequest } from '../services/stream'
import { formatDateTime } from '../services/datetime'
import type { Candidate, CandidateDetail, IndexEvent, JobCategory, KnowledgeSummary, SearchStatus } from '../types'
import { useIngestion } from '../components/ingestion/IngestionContext'
import { JOB_CATEGORY_OPTIONS } from '../services/jobCategories'

const { Dragger } = Upload

export default function CandidatesPage() {
  const [items, setItems] = useState<Candidate[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const { uploading, files, batchStarted, batchSummary, uploadError, overallProgress, completionRevision, selectFiles, clearFiles, removeFile, startUpload } = useIngestion()
  const [searchStatus, setSearchStatus] = useState<SearchStatus | null>(null)
  const [statusError, setStatusError] = useState('')
  const [reindexing, setReindexing] = useState(false)
  const [indexProgress, setIndexProgress] = useState<{ completed: number; total: number } | null>(null)
  const [indexError, setIndexError] = useState('')
  const [saving, setSaving] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState<JobCategory | 'unclassified' | ''>('')
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<CandidateDetail | null>(null)
  const [summary, setSummary] = useState<KnowledgeSummary | null>(null)
  const [summaryError, setSummaryError] = useState('')
  const [selected, setSelected] = useState<React.Key[]>([])
  const [mutating, setMutating] = useState(false)
  const [error, setError] = useState('')
  const [form] = Form.useForm()
  const streamController = useRef<AbortController | null>(null)
  const listRequest = useRef(0)
  const summaryRequest = useRef(0)
  const statusRequest = useRef(0)
  const navigate = useNavigate()
  const busy = uploading || reindexing || saving || mutating

  const load = async () => {
    const request = ++listRequest.current
    setLoading(true); setError('')
    try { const { data } = await api.get<Candidate[]>('/candidates', { params: { q: query, status: statusFilter, job_category: categoryFilter || undefined } }); if (request === listRequest.current) { setItems(data); setSelected([]) } }
    catch (e: any) { if (request === listRequest.current) setError(e.response?.data?.detail || '人才列表加载失败') }
    finally { if (request === listRequest.current) setLoading(false) }
  }
  const loadSummary = async () => {
    const request = ++summaryRequest.current
    try { const { data } = await api.get<KnowledgeSummary>('/knowledge/summary'); if (request === summaryRequest.current) { setSummary(data); setSummaryError('') } }
    catch (e: any) { if (request === summaryRequest.current) { setSummary(null); setSummaryError(e.response?.data?.detail || '知识库统计加载失败，请刷新重试') } }
  }
  useEffect(() => { void load() }, [statusFilter, categoryFilter])
  const loadSearchStatus = async () => {
    const request = ++statusRequest.current
    try { const { data } = await api.get<SearchStatus>('/search/status'); if (request === statusRequest.current) { setSearchStatus(data); setStatusError('') } }
    catch { if (request === statusRequest.current) { setSearchStatus(null); setStatusError('检索状态加载失败，请刷新重试') } }
  }
  useEffect(() => { void loadSearchStatus(); void loadSummary(); return () => { streamController.current?.abort(); ++listRequest.current; ++summaryRequest.current; ++statusRequest.current } }, [])
  useEffect(() => { if (completionRevision) { void load(); void loadSearchStatus(); void loadSummary() } }, [completionRevision])

  const setDocumentEnabled = async (ids: number[], enabled: boolean, batch = false) => {
    if (busy) return
    setMutating(true)
    try {
      if (batch) await api.post('/knowledge/documents/batch', { ids, action: enabled ? 'enable' : 'disable' })
      else await api.patch(`/knowledge/documents/${ids[0]}/enabled`, { enabled })
      await Promise.all([load(), loadSummary(), loadSearchStatus()])
      message.success(enabled ? '文档已启用' : '文档已停用，保留全部内容')
    } catch (e: any) { message.error(typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '文档启停失败') }
    finally { setMutating(false) }
  }

  const openCreate = () => { setEditing(null); form.resetFields(); setModalOpen(true) }
  const openEdit = async (candidate: Candidate) => {
    try {
    const { data } = await api.get<CandidateDetail>(`/candidates/${candidate.id}`)
    setEditing(data); form.setFieldsValue({ name: data.name, job_category: data.job_category ?? undefined, years_experience: data.years_experience, tags: data.tags.join(', '), raw_text: data.raw_text }); setModalOpen(true)
    } catch { message.error('人才详情加载失败') }
  }
  const save = async (values: { name: string; job_category?: JobCategory | null; years_experience?: string; tags?: string; raw_text?: string }) => {
    setSaving(true)
    const body = { ...values, job_category: values.job_category ?? null, tags: (values.tags || '').split(/[,，]/).map((item) => item.trim()).filter(Boolean) }
    if (editing && (editing.source_filename || editing.raw_text === null)) delete body.raw_text
    try {
      if (editing) await api.put(`/candidates/${editing.id}`, { ...body, expected_updated_at: editing.updated_at })
      else await api.post('/candidates/manual', body)
      message.success(editing ? '人才资料已更新' : '人才资料已入库'); setModalOpen(false); await Promise.all([load(), loadSearchStatus(), loadSummary()])
    } catch (e: any) { message.error(typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '保存失败，请检查必填字段') }
    finally { setSaving(false) }
  }
  const remove = async (id: number) => { if (busy) return; setMutating(true); try { await api.delete(`/candidates/${id}`); message.success('已删除'); await Promise.all([load(), loadSearchStatus(), loadSummary()]) } catch (e: any) { message.error(e.response?.data?.detail || '删除失败') } finally { setMutating(false) } }
  const uploadProps: UploadProps = {
    multiple: true, showUploadList: false, accept: '.pdf,.docx,.txt,.jpg,.jpeg,.png', disabled: busy,
    beforeUpload: (file, selected) => {
      if (busy || file.uid !== selected[0]?.uid) return Upload.LIST_IGNORE
      const error = selectFiles(selected)
      if (error) message.error(error)
      return Upload.LIST_IGNORE
    },
  }
  const reindex = async () => {
    if (busy) return
    const controller = new AbortController(); streamController.current = controller
    setReindexing(true); setIndexError(''); setIndexProgress(null)
    try {
      await streamRequest<IndexEvent>('/candidates/reindex/stream', {}, (event) => {
        if (event.type === 'index_progress') setIndexProgress({ completed: event.completed, total: event.total })
        if (event.type === 'index_done') {
          setSearchStatus((current) => current ? { ...current, indexed_chunks: event.indexed_chunks, total_chunks: event.total_chunks, pending_chunks: event.total_chunks - event.indexed_chunks } : current)
          message.success('向量索引补建完成')
        }
      }, 'index_done', controller.signal)
    } catch (e: any) { if (e.name !== 'AbortError') setIndexError(e.message || '向量索引补建失败') }
    finally {
      setReindexing(false); streamController.current = null
      if (!controller.signal.aborted) await Promise.all([loadSearchStatus(), loadSummary()])
    }
  }
  const columns = [
    { title: '姓名', dataIndex: 'name', key: 'name', width: 132, render: (value: string, row: Candidate) => <Button className="candidate-name" type="link" title={value} disabled={busy} onClick={() => navigate(`/candidates/${row.id}`)}>{value}</Button> },
    { title: '岗位分类', dataIndex: 'job_category', key: 'job_category', width: 145, render: (value: JobCategory | null | undefined) => value || <Typography.Text type="secondary">待确认</Typography.Text> },
    { title: '工作年限', dataIndex: 'years_experience', key: 'years_experience', render: (value: string) => value || '未明确' },
    { title: '标签', dataIndex: 'tags', key: 'tags', render: (tags: string[]) => <Space wrap>{tags.slice(0, 4).map((tag) => <Tag key={tag} title={tag}>{tag}</Tag>)}</Space> },
    { title: '入库时间', dataIndex: 'created_at', key: 'created_at', width: 145, render: formatDateTime },
    { title: '状态', dataIndex: 'status', key: 'status', align: 'center' as const, render: (value: string, row: Candidate) => <Tag title={row.error_message || undefined} color={value === 'ready' ? 'green' : value === 'failed' ? 'red' : 'gold'}>{value === 'ready' ? '已入库' : value === 'failed' ? `失败${row.error_message ? `：${row.error_message}` : ''}` : '处理中'}</Tag> },
    { title: '参与检索', key: 'enabled', align: 'center' as const, render: (_: unknown, row: Candidate) => <Switch aria-label={`启用文档：${row.name}`} checked={row.enabled !== false} disabled={busy} onChange={(enabled) => void setDocumentEnabled([row.id], enabled)} /> },
    { title: '操作', key: 'action', align: 'center' as const, render: (_: unknown, row: Candidate) => <Space><Button type="text" disabled={busy} icon={<EditOutlined />} onClick={() => void openEdit(row)}>编辑</Button><Popconfirm disabled={busy} title="确定删除这份人才资料吗？" onConfirm={() => void remove(row.id)}><Button type="text" disabled={busy} danger icon={<DeleteOutlined />}>删除</Button></Popconfirm></Space> },
  ]
  return <div className="knowledge-list">
    <div className="page-heading"><div><Typography.Title level={3}>人才知识库</Typography.Title><Typography.Text type="secondary">维护文档、父子分段与检索状态，点击姓名查看分段。</Typography.Text></div><Space><Button disabled={busy} onClick={() => navigate('/knowledge/retrieval-test')}>召回测试</Button><Button disabled={busy} icon={<ReloadOutlined />} onClick={() => { void load(); void loadSearchStatus(); void loadSummary() }}>刷新</Button><Button disabled={busy} type="primary" icon={<PlusOutlined />} onClick={openCreate}>手动入库</Button></Space></div>
    {error && <Alert type="error" showIcon message={error} />}
    <Card className="knowledge-overview"><div className="knowledge-summary">{summaryError ? <Alert type="error" showIcon message={summaryError} /> : summary ? <Space wrap size="large"><Typography.Text>文档 {summary.documents} 份 · 已启用 {summary.enabled_documents} 份</Typography.Text><Typography.Text>累计召回 {summary.hit_count} 次</Typography.Text></Space> : <Typography.Text type="secondary">正在读取知识库统计…</Typography.Text>}</div>
    <div className="search-status"><Space wrap>{summary?.documents === 0 ? <Typography.Text type="secondary">暂无知识文档，入库后可查看检索状态。</Typography.Text> : searchStatus ? searchStatus.total_chunks === 0 ? <Typography.Text type="secondary">暂无可检索内容，请检查文档和分段是否已启用。</Typography.Text> : <><Tag color={searchStatus.mode === 'hybrid' ? 'blue' : 'gold'}>{searchStatus.mode === 'hybrid' ? '混合检索：关键词 + 语义向量' : '关键词检索'}</Tag>{searchStatus.embedding_model && <Typography.Text>{searchStatus.embedding_model} · {searchStatus.dimensions}维</Typography.Text>}{searchStatus.pending_chunks > 0 && <Tag>部分文档待建立向量索引</Tag>}{searchStatus.embedding_model && searchStatus.pending_chunks > 0 && <Button disabled={uploading} loading={reindexing} onClick={() => void reindex()}>补建向量索引</Button>}</> : <Typography.Text type="secondary">{statusError || '正在读取检索状态…'}</Typography.Text>}</Space>
      {reindexing && <div className="index-progress" role="status"><Typography.Text>{indexProgress ? `补建进度：${indexProgress.completed} / ${indexProgress.total}` : '正在连接索引服务…'}</Typography.Text><Progress percent={indexProgress?.total ? Math.round(indexProgress.completed / indexProgress.total * 100) : 0} /></div>}
      {indexError && <Alert type="error" showIcon message={indexError} />}
    </div></Card>
    <Card className="upload-card"><Dragger {...uploadProps}><div className="knowledge-upload-prompt"><InboxOutlined /><p className="ant-upload-text">{uploading ? '正在解析和入库，请稍候…' : '点击或拖拽简历原文件到这里'}</p></div><p className="ant-upload-hint">每批最多3份，选择后点击“开始入库”。支持 PDF、DOCX、TXT、JPG、JPEG、PNG，单文件不超过 15 MB；扫描 PDF 和图片会自动识别文字</p></Dragger>
      {files.length > 0 && <div className="ingestion-progress"><div className="ingestion-toolbar"><Typography.Text strong>本批{files.length}份 · 总进度（处理阶段）</Typography.Text><Space><Button disabled={busy} onClick={clearFiles}>清空选择</Button><Button type="primary" disabled={busy || batchStarted} loading={uploading} onClick={() => void startUpload()}>开始入库</Button></Space></div><Progress aria-label="本批入库总进度" percent={overallProgress} status={uploadError ? 'exception' : uploading ? 'active' : undefined} /><Typography.Paragraph type="secondary">站内切换页面可继续入库；刷新或关闭页面会中断未完成处理。</Typography.Paragraph>
        {files.map((item, index) => <div className="ingestion-file" key={`${item.file.name}-${index}`}><div className="ingestion-file-heading"><Typography.Text strong>{item.file.name}</Typography.Text><Button type="text" disabled={busy} aria-label={`移除 ${item.file.name}`} icon={<DeleteOutlined />} onClick={() => removeFile(index)} /></div><Space wrap><Tag color={item.status === 'ready' ? 'green' : item.status === 'failed' || item.status === 'interrupted' ? 'red' : 'blue'}>{item.label}</Tag>{item.detail && <Typography.Text type="danger">{item.detail}</Typography.Text>}</Space><Progress aria-label={`${item.file.name}入库进度`} percent={item.progress} size="small" status={item.status === 'failed' || item.status === 'interrupted' ? 'exception' : item.status === 'ready' ? 'success' : uploading && item.status === 'processing' ? 'active' : 'normal'} /></div>)}
      </div>}
      {batchSummary && <Alert type={batchSummary.failed ? 'warning' : 'success'} showIcon message={`入库完成：成功${batchSummary.succeeded}份，失败${batchSummary.failed}份`} description="选择新文件可开始下一批。" />}
      {uploadError && <Alert type="error" showIcon message={uploadError} />}
    </Card>
    <Card className="table-card"><Space wrap className="table-toolbar"><Input.Search placeholder="搜索姓名、岗位分类或标签" disabled={busy} allowClear onSearch={() => void load()} onChange={(e) => setQuery(e.target.value)} style={{ width: 280 }} /><Select aria-label="入库状态" disabled={busy} value={statusFilter} style={{ width: 140 }} onChange={setStatusFilter} options={[{ value: '', label: '全部状态' }, { value: 'ready', label: '已入库' }, { value: 'failed', label: '失败' }]} /><Select aria-label="岗位分类筛选" disabled={busy} value={categoryFilter} style={{ width: 175 }} onChange={setCategoryFilter} options={[{ value: '', label: '全部岗位分类' }, ...JOB_CATEGORY_OPTIONS, { value: 'unclassified', label: '待确认' }]} /><Button disabled={busy || !selected.length || selected.length > 100} onClick={() => void setDocumentEnabled(selected as number[], true, true)}>批量启用</Button><Button disabled={busy || !selected.length || selected.length > 100} onClick={() => void setDocumentEnabled(selected as number[], false, true)}>批量停用</Button>{selected.length > 0 && <Typography.Text type="secondary">已选{selected.length}份（每次最多100份）</Typography.Text>}</Space><Table locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={query.trim() || statusFilter || categoryFilter ? '未找到匹配的人才资料，请调整筛选条件' : '暂无人才资料，可上传简历或手动入库'} /> }} rowKey="id" loading={loading} dataSource={items} size="small" columns={columns} rowSelection={{ selectedRowKeys: selected, onChange: setSelected, getCheckboxProps: (row) => ({ disabled: busy, 'aria-label': `选择文档：${row.name}` }) }} scroll={{ x: 1050 }} pagination={{ pageSize: 10, showSizeChanger: false }} /></Card>
    <Modal title={editing ? '编辑人才资料' : '手动入库'} open={modalOpen} onCancel={() => { if (!saving) setModalOpen(false) }} cancelButtonProps={{ disabled: saving }} closable={!saving} maskClosable={!saving} onOk={() => form.submit()} confirmLoading={saving} okText="保存" width={720}>
      {editing && <Alert className="form-alert" type="info" showIcon message="仅修改姓名、岗位分类、年限和标签会保留手工分段；修改简历正文将替换全部分段和分段开关。" />}
      <Form form={form} layout="vertical" disabled={saving} onFinish={save}><Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}><Input /></Form.Item><Form.Item name="years_experience" label="工作年限"><Input placeholder="例如：5年" /></Form.Item><Form.Item name="job_category" label="岗位分类"><Select aria-label="岗位分类" allowClear placeholder="待确认" options={JOB_CATEGORY_OPTIONS} /></Form.Item><Form.Item name="tags" label="技能标签"><Input placeholder="用逗号分隔，例如：Java, Spring, SQL" /></Form.Item>{(!editing || (!editing.source_filename && editing.raw_text !== null)) && <Form.Item name="raw_text" label="简历正文" rules={[{ required: true, message: '请输入简历正文' }]}><Input.TextArea rows={12} /></Form.Item>}</Form>
    </Modal>
  </div>
}
