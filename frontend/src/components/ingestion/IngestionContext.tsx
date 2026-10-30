import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { streamRequest } from '../../services/stream'
import type { UploadEvent } from '../../types'

type IngestionFile = { file: File; progress: number; label: string; status: 'queued' | 'processing' | 'ready' | 'failed' | 'interrupted'; detail?: string | null }
type BatchSummary = Extract<UploadEvent, { type: 'batch_done' }>

interface IngestionState {
  files: IngestionFile[]
  uploading: boolean
  batchStarted: boolean
  batchSummary: BatchSummary | null
  uploadError: string
  overallProgress: number
  completionRevision: number
  selectFiles: (selected: File[]) => string | null
  clearFiles: () => void
  removeFile: (index: number) => void
  startUpload: () => Promise<void>
  dispose: () => void
}

const IngestionContext = createContext<IngestionState | null>(null)

export function IngestionProvider({ children, token, canUpload }: { children: ReactNode; token: string | null; canUpload: boolean }) {
  const navigate = useNavigate()
  const [files, setFiles] = useState<IngestionFile[]>([])
  const [uploading, setUploading] = useState(false)
  const [batchStarted, setBatchStarted] = useState(false)
  const [batchSummary, setBatchSummary] = useState<BatchSummary | null>(null)
  const [uploadError, setUploadError] = useState('')
  const [completionRevision, setCompletionRevision] = useState(0)
  const controllerRef = useRef<AbortController | null>(null)
  const accountRef = useRef(localStorage.getItem('talentfit_user'))
  const sameAccount = () => localStorage.getItem('talentfit_token') === token && localStorage.getItem('talentfit_user') === accountRef.current

  const dispose = () => {
    controllerRef.current?.abort()
    controllerRef.current = null
  }
  useEffect(() => dispose, [])
  useEffect(() => {
    const sessionChanged = (event: StorageEvent) => {
      if ((event.key === null || event.key === 'talentfit_token' || event.key === 'talentfit_user') && !sameAccount()) {
        dispose(); setFiles([]); setUploading(false)
        navigate('/login', { replace: true })
      }
    }
    window.addEventListener('storage', sessionChanged)
    return () => window.removeEventListener('storage', sessionChanged)
  }, [navigate, token])
  useEffect(() => {
    if (!uploading) return
    const warn = (event: BeforeUnloadEvent) => { if (controllerRef.current && sameAccount()) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [uploading])

  const clearFiles = () => {
    if (controllerRef.current) return
    setFiles([]); setBatchStarted(false); setBatchSummary(null); setUploadError('')
  }
  const selectFiles = (selected: File[]) => {
    if (!canUpload || controllerRef.current) return null
    const previous = batchStarted ? [] : files
    if (previous.length + selected.length > 3) return '每批最多选择3份简历'
    if (selected.some(file => file.size > 15 * 1024 * 1024)) return '文件不能超过15 MB'
    setFiles([...previous, ...selected.map((file): IngestionFile => ({ file, progress: 0, label: '等待开始', status: 'queued' }))])
    setBatchStarted(false); setBatchSummary(null); setUploadError('')
    return null
  }
  const removeFile = (index: number) => { if (!controllerRef.current) setFiles(current => current.filter((_, position) => position !== index)) }
  const startUpload = async () => {
    if (!canUpload || !files.length || controllerRef.current || batchStarted || !sameAccount()) return
    const controller = new AbortController()
    controllerRef.current = controller
    const current = () => controllerRef.current === controller && !controller.signal.aborted && sameAccount()
    const body = new FormData()
    files.forEach(item => body.append('files', item.file))
    setUploading(true); setBatchStarted(true); setUploadError(''); setBatchSummary(null)
    try {
      await streamRequest<UploadEvent>('/candidates/upload/stream', body, event => {
        if (!current()) return
        if (event.type === 'file_progress') setFiles(items => items.map((item, index) => index === event.file_index && item.status !== 'ready' && item.status !== 'failed' ? { ...item, progress: Math.min(99, Math.max(0, event.progress)), label: event.label, status: 'processing' } : item))
        if (event.type === 'file_result') setFiles(items => items.map((item, index) => index === event.file_index ? { ...item, progress: event.status === 'ready' ? 100 : Math.min(99, item.progress), label: event.status === 'ready' ? '已入库' : '入库失败', status: event.status, detail: event.detail } : item))
        if (event.type === 'batch_done') setBatchSummary(event)
      }, 'batch_done', controller.signal)
    } catch (error: any) {
      if (current() && error.name !== 'AbortError') {
        setUploadError(error.message || '入库连接中断，请刷新确认结果')
        setFiles(items => items.map(item => item.status === 'ready' || item.status === 'failed' ? item : { ...item, status: 'interrupted', label: '处理中断，请刷新确认结果' }))
      }
    } finally {
      if (current()) { setUploading(false); setCompletionRevision(value => value + 1) }
      if (controllerRef.current === controller) controllerRef.current = null
    }
  }
  const overallProgress = files.length ? Math.floor(files.reduce((sum, item) => sum + item.progress, 0) / files.length) : 0
  return <IngestionContext.Provider value={{ files, uploading, batchStarted, batchSummary, uploadError, overallProgress, completionRevision, selectFiles, clearFiles, removeFile, startUpload, dispose }}>{children}</IngestionContext.Provider>
}

export function useIngestion() {
  const state = useContext(IngestionContext)
  if (!state) throw new Error('入库状态需要登录后的共享布局')
  return state
}
