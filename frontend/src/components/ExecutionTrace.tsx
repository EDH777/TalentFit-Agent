import { useEffect, useState } from 'react'
import { Tag } from 'antd'
import { ApartmentOutlined, CheckCircleFilled, ClockCircleOutlined, DownOutlined, ExclamationCircleFilled, LoadingOutlined, RobotOutlined, SearchOutlined, ToolOutlined } from '@ant-design/icons'
import type { TraceNode } from '../types'

const statusNames = { running: '运行中', succeeded: '已完成', failed: '失败', interrupted: '已中断' }
const nodeTypes: Record<string, { label: string; icon: React.ReactNode }> = {
  planner: { label: '任务规划', icon: <ApartmentOutlined /> },
  retrieval: { label: '检索', icon: <SearchOutlined /> },
  tool: { label: '工具', icon: <ToolOutlined /> },
  task: { label: '任务', icon: <ApartmentOutlined /> },
  generator: { label: '生成', icon: <RobotOutlined /> },
  llm: { label: '模型', icon: <RobotOutlined /> },
}

function StatusIcon({ status }: { status: TraceNode['status'] }) {
  if (status === 'running') return <LoadingOutlined spin />
  if (status === 'succeeded') return <CheckCircleFilled />
  if (status === 'failed') return <ExclamationCircleFilled />
  return <ClockCircleOutlined />
}

export default function ExecutionTrace({ nodes }: { nodes: TraceNode[] }) {
  const running = nodes.some((node) => node.status === 'running')
  const [expanded, setExpanded] = useState(running)
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  if (!nodes.length) return null

  const status = running ? 'running' : nodes.some((node) => node.status === 'failed') ? 'failed' : nodes.some((node) => node.status === 'interrupted') ? 'interrupted' : 'succeeded'
  const start = Math.min(...nodes.map((node) => Date.parse(node.started_at)))
  const end = Math.max(...nodes.map((node) => node.status === 'running' ? now : Date.parse(node.started_at) + (node.elapsed_ms || 0)))
  const elapsed = Math.max(0, end - start)

  return <section className={`execution-trace trace-${status}`} aria-label="执行记录">
    <button type="button" className="trace-toggle" aria-label="执行过程" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      <span className={`trace-status status-${status}`}><StatusIcon status={status} /></span>
      <span className="trace-heading">执行过程 <span className="trace-result">{status === 'running' ? '执行中' : statusNames[status]}</span></span>
      <span className="trace-total">{nodes.length} 个节点 · {(elapsed / 1000).toFixed(1)}s</span>
      <DownOutlined className={expanded ? 'trace-chevron expanded' : 'trace-chevron'} />
    </button>
    {expanded && <ol className="trace-nodes" aria-label="执行节点">
      {nodes.map((node) => {
        const kind = nodeTypes[node.node_type] || { label: '节点', icon: <ToolOutlined /> }
        return <li key={node.id} className="trace-node" data-execution-id={node.id} data-status={node.status}>
          <div className="trace-node-heading">
            <span className="trace-node-icon" aria-hidden="true">{kind.icon}</span>
            <span className="trace-node-title">{node.title || node.node_id || kind.label}</span>
            <span className={`trace-status status-${node.status}`}><StatusIcon status={node.status} /></span>
          </div>
          <div className="trace-node-meta"><Tag>{kind.label}</Tag><span>{statusNames[node.status]}</span>{node.elapsed_ms !== undefined && <span>{node.elapsed_ms < 1000 ? `${node.elapsed_ms}ms` : `${(node.elapsed_ms / 1000).toFixed(1)}s`}</span>}</div>
          {node.summary && <details className="trace-summary"><summary>查看摘要</summary><p>{node.summary}</p></details>}
        </li>
      })}
    </ol>}
  </section>
}
