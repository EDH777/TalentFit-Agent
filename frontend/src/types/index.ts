export type Role = 'admin' | 'hr'
export type JobCategory = '产品经理' | '前端开发工程师' | '后端开发工程师' | '测试工程师' | 'AI应用开发工程师' | '现场实施工程师' | '硬件工程师' | '采购经理'

export interface User {
  id: number
  username: string
  role: Role
}

export interface Candidate {
  id: number
  name: string
  job_category?: JobCategory | null
  years_experience: string
  tags: string[]
  source_filename?: string | null
  status: string
  error_message?: string | null
  created_at: string
  updated_at: string
  enabled?: boolean
}

export interface CandidateDetail extends Candidate {
  raw_text: string | null
}

export interface Session {
  id: number
  title: string
  created_at: string
  updated_at: string
}

export interface Message {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  intent?: string | null
  created_at: string
  sources: { candidate_id: number; name: string }[]
  trace?: TraceNode[]
}

export interface TraceNode {
  id: string
  node_id: string
  title: string
  node_type: string
  status: 'running' | 'succeeded' | 'failed' | 'interrupted'
  started_at: string
  elapsed_ms?: number
  summary?: string
}

export interface RetrievalInfo {
  mode: 'hybrid' | 'keyword'
  embedding_model: string | null
  warning?: string | null
}

export interface SearchStatus extends RetrievalInfo {
  dimensions: number
  indexed_chunks: number
  total_chunks: number
  pending_chunks: number
}

export type StreamError = { type: 'error'; detail: string }

export type ChatEvent =
  | { type: 'node_started' | 'node_finished'; node: TraceNode }
  | ({ type: 'retrieval' } & RetrievalInfo)
  | { type: 'delta'; content: string }
  | { type: 'done'; message: Message; intent: string; sources: Message['sources'] }
  | StreamError

export type UploadEvent =
  | { type: 'batch_start'; total: number }
  | { type: 'file_progress'; file_index: number; filename: string; stage: 'received' | 'extract' | 'profile' | 'preprocess' | 'normalize' | 'chunk' | 'keyword' | 'embedding' | 'done'; label: string; progress: number }
  | { type: 'file_result'; file_index: number; filename: string; status: 'ready' | 'failed'; candidate: CandidateDetail | null; detail: string | null }
  | { type: 'batch_done'; total: number; succeeded: number; failed: number }
  | StreamError

export type IndexEvent =
  | { type: 'index_progress'; completed: number; total: number }
  | { type: 'index_done'; indexed_chunks: number; total_chunks: number }
  | StreamError

export interface KnowledgeSummary {
  documents: number
  enabled_documents: number
  parent_chunks: number
  child_chunks: number
  enabled_child_chunks: number
  indexed_child_chunks: number
  hit_count: number
}

export interface KnowledgeDocument {
  candidate: Candidate
  stats: { parent_chunks: number; child_chunks: number; enabled_child_chunks: number; indexed_child_chunks: number; character_count: number; hit_count: number }
  source_file_size: number | null
}

export interface KnowledgeChunk {
  id: number
  candidate_id: number
  parent_id: number | null
  chunk_type: 'parent' | 'child'
  position: number
  content: string
  enabled: boolean
  effective_enabled: boolean
  char_count: number
  hit_count: number
  child_count: number
  enabled_child_count: number
  indexed: boolean
  created_at: string
  updated_at: string
}

export interface ChunkPage { items: KnowledgeChunk[]; total: number; page: number; page_size: number }
export interface RetrievalTestResult extends RetrievalInfo {
  elapsed_ms: number
  hits: { chunk_id: number; parent_id: number | null; candidate_id: number; name: string; position: number; content: string; parent_content: string; score: number; keyword_rank: number | null; vector_rank: number | null }[]
}
