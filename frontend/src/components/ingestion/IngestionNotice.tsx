import { Alert, Button, Progress } from 'antd'
import { useLocation, useNavigate } from 'react-router-dom'
import { useIngestion } from './IngestionContext'

export default function IngestionNotice() {
  const { files, uploading, batchStarted, batchSummary, uploadError, overallProgress } = useIngestion()
  const location = useLocation()
  const navigate = useNavigate()
  if (location.pathname === '/candidates' || !files.length || !batchStarted) return null
  const completed = files.filter(item => item.status === 'ready' || item.status === 'failed').length
  return <Alert
    className="ingestion-global-notice"
    type={uploadError ? 'error' : batchSummary?.failed ? 'warning' : uploading ? 'info' : 'success'}
    showIcon
    style={{ marginBottom: 12 }}
    message={<div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px 12px' }}>
      <span>{uploading ? `简历入库中：已处理${completed}/${files.length}份` : uploadError ? '简历入库连接中断，请查看结果' : `简历入库完成：成功${batchSummary?.succeeded || 0}份，失败${batchSummary?.failed || 0}份`}</span>
      {uploading && <Progress aria-label="跨页入库总进度" percent={overallProgress} size="small" status="active" style={{ width: 160, maxWidth: '100%', marginBottom: 0 }} />}
      <Button onClick={() => navigate('/candidates')}>{uploading ? '查看入库进度' : '查看入库结果'}</Button>
    </div>}
  />
}
