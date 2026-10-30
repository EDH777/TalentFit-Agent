import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, Button, Card, Checkbox, Descriptions, Empty, Form, Input, Modal, Pagination, Select, Space, Spin, Switch, Tag, Typography, message } from 'antd'
import { ArrowLeftOutlined, DownOutlined, RightOutlined, DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import api from '../services/api'
import { formatDateTime as date } from '../services/datetime'
import type { ChunkPage, KnowledgeChunk, KnowledgeDocument } from '../types'

const failure = (error: any, fallback: string) => typeof error.response?.data?.detail === 'string' ? error.response.data.detail : fallback
const filters = [{ value: 'all', label: '全部开关状态' }, { value: 'true', label: '自身已启用' }, { value: 'false', label: '自身已停用' }]

export default function DocumentKnowledgePage() {
  const { id } = useParams()
  return <KnowledgeDocumentView key={id} id={Number(id)} />
}

function KnowledgeDocumentView({ id }: { id: number }) {
  const navigate = useNavigate()
  const [document, setDocument] = useState<KnowledgeDocument | null>(null)
  const [documentError, setDocumentError] = useState('')
  const [parents, setParents] = useState<ChunkPage | null>(null)
  const [listError, setListError] = useState('')
  const [listLoading, setListLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [enabled, setEnabled] = useState('all')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<number[]>([])
  const [parent, setParent] = useState<KnowledgeChunk | null>(null)
  const [children, setChildren] = useState<ChunkPage | null>(null)
  const [childLoading, setChildLoading] = useState(false)
  const [childError, setChildError] = useState('')
  const [childQuery, setChildQuery] = useState('')
  const [childSearch, setChildSearch] = useState('')
  const [childEnabled, setChildEnabled] = useState('all')
  const [childPage, setChildPage] = useState(1)
  const [detail, setDetail] = useState<KnowledgeChunk | null>(null)
  const [editor, setEditor] = useState<{ chunk?: KnowledgeChunk; parentId?: number } | null>(null)
  const [editText, setEditText] = useState('')
  const [editError, setEditError] = useState('')
  const [deleting, setDeleting] = useState<number[] | null>(null)
  const [mutationError, setMutationError] = useState('')
  const [busy, setBusy] = useState(false)
  const alive = useRef(true)
  const lock = useRef(false)
  const request = useRef({ document: 0, parents: 0, children: 0 })
  const base = `/knowledge/documents/${id}`

  const loadDocument = useCallback(async () => {
    const sequence = ++request.current.document
    try {
      const { data } = await api.get<KnowledgeDocument>(base)
      if (!alive.current || sequence !== request.current.document) return
      setDocument(data); setDocumentError('')
    } catch (error) { if (alive.current && sequence === request.current.document) setDocumentError(failure(error, '文档信息加载失败')) }
  }, [base])
  const loadParents = useCallback(async () => {
    const sequence = ++request.current.parents
    setListLoading(true); setListError('')
    try {
      const { data } = await api.get<ChunkPage>(`${base}/chunks`, { params: { page, page_size: 10, q: search, enabled } })
      if (!alive.current || sequence !== request.current.parents) return
      if (page > 1 && !data.items.length) { setPage(Math.max(1, Math.ceil(data.total / 10))); return }
      setParents(data); setSelected([])
      setParent(current => current ? data.items.find(item => item.id === current.id) || null : null)
    } catch (error) { if (alive.current && sequence === request.current.parents) setListError(failure(error, '父分段加载失败')) }
    finally { if (alive.current && sequence === request.current.parents) setListLoading(false) }
  }, [base, page, search, enabled])
  const parentId = parent?.id
  const loadChildren = useCallback(async () => {
    if (!parentId) return
    const sequence = ++request.current.children
    setChildLoading(true); setChildError('')
    try {
      const { data } = await api.get<ChunkPage>(`${base}/chunks`, { params: { parent_id: parentId, page: childPage, page_size: 10, q: childSearch, enabled: childEnabled } })
      if (!alive.current || sequence !== request.current.children) return
      if (childPage > 1 && !data.items.length) { setChildPage(Math.max(1, Math.ceil(data.total / 10))); return }
      setChildren(data)
    } catch (error) { if (alive.current && sequence === request.current.children) setChildError(failure(error, '子分段加载失败')) }
    finally { if (alive.current && sequence === request.current.children) setChildLoading(false) }
  }, [base, parentId, childPage, childSearch, childEnabled])

  useEffect(() => { alive.current = true; void loadDocument(); return () => { alive.current = false } }, [loadDocument])
  useEffect(() => { void loadParents() }, [loadParents])
  useEffect(() => { void loadChildren(); return () => { ++request.current.children } }, [loadChildren])

  const refresh = async () => { await Promise.all([loadDocument(), loadParents(), loadChildren()]) }
  const mutate = async (action: () => Promise<unknown>, complete?: () => void) => {
    if (lock.current) return
    lock.current = true; setBusy(true); setMutationError(''); setEditError('')
    try {
      await action()
      if (!alive.current) return
      complete?.(); await refresh()
    } catch (error) {
      if (alive.current) {
        const text = failure(error, '操作失败，请重试')
        if (editor) setEditError(text)
        else setMutationError(text)
      }
    } finally { lock.current = false; if (alive.current) setBusy(false) }
  }
  const toggleChunk = (chunk: KnowledgeChunk, value: boolean) => void mutate(async () => {
    const { data } = await api.patch<KnowledgeChunk>(`${base}/chunks/${chunk.id}`, { enabled: value })
    if (alive.current && parent?.id === chunk.id) setParent(data)
  })
  const openEditor = (chunk?: KnowledgeChunk, parentId?: number) => { setEditor({ chunk, parentId }); setEditText(chunk?.content || ''); setEditError('') }
  const editLimit = editor?.chunk?.chunk_type === 'child' || editor?.parentId ? 2048 : 4000
  const saveChunk = () => {
    if (!editText.trim() || !editor || editText.length > editLimit) return
    void mutate(async () => {
      if (editor.chunk) await api.patch(`${base}/chunks/${editor.chunk.id}`, { content: editText })
      else await api.post(`${base}/chunks`, { content: editText, ...(editor.parentId ? { parent_id: editor.parentId } : {}) })
    }, () => { setEditor(null); message.success('分段已保存') })
  }
  const batch = (action: 'enable' | 'disable' | 'delete', ids = selected) => void mutate(async () => {
    if (ids.length === 1 && action === 'delete') await api.delete(`${base}/chunks/${ids[0]}`)
    else await api.post(`${base}/chunks/batch`, { ids, action })
  }, () => { if (action === 'delete' && parent && ids.includes(parent.id)) setParent(null); setDeleting(null); setSelected([]) })
  const showChildren = (chunk: KnowledgeChunk) => {
    ++request.current.children
    if (parent?.id === chunk.id) { setParent(null); setChildren(null); return }
    setParent(chunk); setChildren(null); setChildPage(1); setChildQuery(''); setChildSearch(''); setChildEnabled('all'); setChildError('')
  }
  const chunkBadges = (chunk: KnowledgeChunk) => <Space wrap size={4}><Tag color={chunk.effective_enabled ? 'green' : 'default'}>{chunk.effective_enabled ? '参与检索' : '不参与检索'}</Tag>{chunk.chunk_type === 'child' && <Tag color={chunk.indexed ? 'blue' : 'gold'}>{chunk.indexed ? '已建立向量索引' : '未建立向量索引'}</Tag>}<Typography.Text type="secondary">{chunk.char_count} 字符 · 召回 {chunk.hit_count} 次</Typography.Text></Space>
  const chunkActions = (chunk: KnowledgeChunk) => {
    const type = chunk.chunk_type === 'parent' ? '父分段' : '子分段'
    return <Space size={4}><Switch size="small" aria-label={`启用${type} ${chunk.id}`} checked={chunk.enabled} disabled={busy} onChange={value => toggleChunk(chunk, value)} />{chunk.chunk_type === 'child' && <Button type="text" disabled={busy} aria-label={`查看${type} ${chunk.id}详情`} onClick={() => setDetail(chunk)}>详情</Button>}<Button type="text" disabled={busy} aria-label={`编辑${type} ${chunk.id}`} icon={<EditOutlined />} onClick={() => openEditor(chunk)} /><Button type="text" disabled={busy} danger aria-label={`删除${type} ${chunk.id}`} icon={<DeleteOutlined />} onClick={() => { setMutationError(''); setDeleting([chunk.id]) }} /></Space>
  }

  return <div className="knowledge-document">
    <div className="page-heading"><div><Button type="link" disabled={busy} icon={<ArrowLeftOutlined />} onClick={() => navigate('/candidates')}>人才知识库</Button><Typography.Title level={3}>{document?.candidate.name || '文档详情'}</Typography.Title></div><Space><Button disabled={busy} icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新</Button><Button disabled={busy || !document} onClick={() => navigate(`/knowledge/retrieval-test?candidate_id=${id}`)}>召回测试</Button></Space></div>
    {documentError && <Alert className="form-alert" type="error" showIcon message={documentError} />}
    {mutationError && !deleting && <Alert className="form-alert" type="error" showIcon message={mutationError} />}
    {!document && !documentError && <Spin />}
    {document && <div className="knowledge-document-layout"><main className="knowledge-main">        {document.candidate.enabled === false && <Alert className="form-alert" type="warning" showIcon message="文档已停用，所有分段当前不参与检索；仍可查看和维护，重新启用将保留各分段开关。" />}
        <div className="knowledge-toolbar"><Space wrap><Input.Search aria-label="搜索父分段正文" placeholder="搜索父分段正文" value={query} disabled={busy} allowClear onChange={e => setQuery(e.target.value)} onSearch={value => { setSearch(value); setPage(1) }} style={{ width: 250 }} /><Select aria-label="父分段开关状态" value={enabled} disabled={busy} options={filters} onChange={value => { setEnabled(value); setPage(1) }} style={{ width: 160 }} /></Space><Button type="primary" disabled={busy} icon={<PlusOutlined />} onClick={() => openEditor()}>新增父分段</Button></div>
        <div className="knowledge-batch"><Checkbox disabled={busy || !parents?.items.length} checked={Boolean(parents?.items.length && selected.length === parents.items.length)} indeterminate={selected.length > 0 && selected.length < (parents?.items.length || 0)} onChange={e => setSelected(e.target.checked ? parents?.items.map(item => item.id) || [] : [])}>选择本页</Checkbox><Typography.Text type="secondary">已选 {selected.length} 段</Typography.Text><Button size="small" disabled={busy || !selected.length} onClick={() => batch('enable')}>批量启用</Button><Button size="small" disabled={busy || !selected.length} onClick={() => batch('disable')}>批量停用</Button><Button size="small" danger disabled={busy || !selected.length} onClick={() => { setMutationError(''); setDeleting(selected) }}>批量删除</Button></div>
        {listError && <Alert className="form-alert" type="error" showIcon message={listError} action={<Button size="small" disabled={busy} onClick={() => void loadParents()}>重试</Button>} />}
        <Spin spinning={listLoading}><div className="knowledge-chunks">{parents?.items.map(chunk => <Card className="knowledge-parent-card" size="small" key={chunk.id}>
          <div className="knowledge-chunk-heading"><Space><Checkbox aria-label={`选择父分段 ${chunk.id}`} disabled={busy} checked={selected.includes(chunk.id)} onChange={e => setSelected(current => e.target.checked ? [...current, chunk.id] : current.filter(value => value !== chunk.id))} /><Typography.Text strong>父分段 #{chunk.position + 1}</Typography.Text></Space>{chunkActions(chunk)}</div>
          <div className="knowledge-content knowledge-parent-preview">{chunk.content.split(/\r?\n/).find(line => line.trim()) || '空分段'}</div>
          <div className="knowledge-chunk-footer"><Button type="link" className="knowledge-expand" disabled={busy} aria-label={`${parent?.id === chunk.id ? '收起' : '展开'}父分段 ${chunk.id}详情`} aria-expanded={parent?.id === chunk.id} aria-controls={`children-${chunk.id}`} icon={parent?.id === chunk.id ? <DownOutlined /> : <RightOutlined />} onClick={() => showChildren(chunk)}>{parent?.id === chunk.id ? '收起详情' : '查看详情'}<span className="knowledge-child-count">{chunk.child_count} 个子段</span></Button><Space wrap size={6}>{chunkBadges(chunk)}</Space></div>
          {parent?.id === chunk.id && <section id={`children-${chunk.id}`} className="knowledge-inline-children" aria-label={`父分段 ${chunk.id}的子分段`}>
            {!chunk.enabled && <Alert className="form-alert" type="warning" showIcon message="父分段已停用，子分段当前不参与检索" />}
            {document.candidate.enabled === false && <Alert className="form-alert" type="warning" showIcon message="文档已停用，子分段当前不参与检索" />}
            <div className="knowledge-toolbar knowledge-child-toolbar"><Input.Search aria-label="搜索子分段正文" placeholder="搜索子分段" value={childQuery} disabled={busy} allowClear onChange={e => setChildQuery(e.target.value)} onSearch={value => { setChildSearch(value); setChildPage(1) }} /><Select aria-label="子分段开关状态" value={childEnabled} disabled={busy} options={filters} onChange={value => { setChildEnabled(value); setChildPage(1) }} /><Button disabled={busy} onClick={() => openEditor(undefined, chunk.id)} icon={<PlusOutlined />}>新增子分段</Button></div>
            {childError && <Alert className="form-alert" type="error" showIcon message={childError} action={<Button size="small" onClick={() => void loadChildren()}>重试</Button>} />}
            <Spin spinning={childLoading}><div className="knowledge-chunks">{children?.items.map(child => <Card size="small" key={child.id} className="knowledge-child-card"><div className="knowledge-chunk-heading"><Typography.Text strong>子分段 #{child.position + 1}</Typography.Text>{chunkActions(child)}</div><Typography.Paragraph className="knowledge-content" ellipsis={{ rows: 3 }}>{child.content}</Typography.Paragraph>{chunkBadges(child)}</Card>)}{children && !children.items.length && !childError && <Empty description="暂无符合条件的子分段" />}</div></Spin>
            {children && <Pagination size="small" className="knowledge-pagination" current={childPage} total={children.total} pageSize={10} showSizeChanger={false} disabled={busy} onChange={setChildPage} showTotal={total => `共 ${total} 个子分段`} />}
          </section>}

        </Card>)}{parents && !parents.items.length && !listError && <Empty description="暂无符合条件的父分段" />}</div></Spin>
        {parents && <Pagination size="small" className="knowledge-pagination" current={page} total={parents.total} pageSize={10} showSizeChanger={false} disabled={busy} onChange={setPage} showTotal={total => `共 ${total} 个父分段`} />}
</main><aside className="knowledge-sidebar"><Card size="small" title="文档信息"><Space direction="vertical" size={8} style={{ width: '100%' }}><div className="knowledge-chunk-heading"><Typography.Text>参与检索</Typography.Text><Switch aria-label="启用当前文档" checked={document.candidate.enabled !== false} disabled={busy} onChange={value => void mutate(() => api.patch(`${base}/enabled`, { enabled: value }))} /></div><Tag color={document.candidate.status === 'ready' ? 'green' : 'gold'}>{document.candidate.status === 'ready' ? '已入库' : document.candidate.status === 'failed' ? '入库失败' : '处理中'}</Tag><Descriptions column={1} size="small" colon={false} items={[
      { key: 'category', label: '岗位分类', children: document.candidate.job_category || '待确认' },
      { key: 'file', label: '原文件', children: document.candidate.source_filename || '无上传文件' },
      { key: 'size', label: '文件大小', children: document.source_file_size === null ? '—' : `${(document.source_file_size / 1024).toFixed(1)} KB` },
      { key: 'parents', label: '父分段', children: document.stats.parent_chunks },
      { key: 'children', label: '子分段', children: document.stats.child_chunks },
      { key: 'active', label: '可检索子分段', children: document.stats.enabled_child_chunks },
      { key: 'indexed', label: '已索引子分段', children: document.stats.indexed_child_chunks },
      { key: 'hits', label: '累计召回', children: `${document.stats.hit_count} 次` },
      { key: 'created', label: '入库时间', children: date(document.candidate.created_at) },
      { key: 'updated', label: '更新时间', children: date(document.candidate.updated_at) },
    ]} /></Space></Card></aside></div>}

    <Modal title={editor?.chunk ? `编辑${editor.chunk.chunk_type === 'parent' ? '父' : '子'}分段` : `新增${editor?.parentId ? '子' : '父'}分段`} open={Boolean(editor)} onCancel={() => { if (!busy) setEditor(null) }} onOk={saveChunk} okText="保存分段" okButtonProps={{ disabled: !editText.trim() || editText.length > editLimit }} confirmLoading={busy} cancelButtonProps={{ disabled: busy }} maskClosable={!busy} closable={!busy} width={720}>
      {editor?.chunk?.chunk_type === 'parent' && <Alert className="form-alert" type="warning" showIcon message="修改父分段正文会重新生成该父分段的全部子分段，并替换这些子分段的手工修改和开关。" />}
      {editError && <Alert className="form-alert" type="error" showIcon message={editError} />}
      <Form layout="vertical"><Form.Item label="分段正文" required><Input.TextArea aria-label="分段正文" rows={12} maxLength={editLimit} value={editText} disabled={busy} onChange={event => setEditText(event.target.value)} /></Form.Item></Form><Typography.Text type="secondary">{editText.length} / {editLimit} 字符</Typography.Text>
    </Modal>
    <Modal title="删除分段" open={Boolean(deleting)} okText="确认删除" okButtonProps={{ danger: true }} confirmLoading={busy} onOk={() => deleting && batch('delete', deleting)} onCancel={() => { if (!busy) setDeleting(null) }} cancelButtonProps={{ disabled: busy }} maskClosable={!busy} closable={!busy}>
      <Typography.Paragraph>确定删除选中的 {deleting?.length} 个分段吗？删除父分段时会同时删除其全部子分段和索引，此操作无法撤销。</Typography.Paragraph>{mutationError && <Alert type="error" showIcon message={mutationError} />}
    </Modal>
    <Modal title={detail ? `${detail.chunk_type === 'parent' ? '父' : '子'}分段 #${detail.position + 1} 详情` : '分段详情'} open={Boolean(detail)} onCancel={() => setDetail(null)} footer={null} width={720}>{detail && <>{chunkBadges(detail)}<Descriptions className="knowledge-detail-meta" size="small" column={2} items={[{ key: 'id', label: '分段编号', children: detail.id }, { key: 'parent', label: '父分段编号', children: detail.parent_id || '—' }, { key: 'own', label: '自身开关', children: detail.enabled ? '已启用' : '已停用' }, { key: 'updated', label: '更新时间', children: date(detail.updated_at) }]} /><Typography.Paragraph className="knowledge-raw-text">{detail.content}</Typography.Paragraph></>}</Modal>
  </div>
}
