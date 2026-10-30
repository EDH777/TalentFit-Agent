const interruptedMessage = '连接已中断，未收到完成确认，请刷新查看已保存结果'

export async function consumeSse<T extends { type: string; detail?: string | null }>(
  stream: ReadableStream<Uint8Array>, onEvent: (event: T) => void, terminalType: string, signal?: AbortSignal,
): Promise<void> {
  const reader = stream.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let buffer = ''
  const abort = () => { void reader.cancel().catch(() => {}) }
  signal?.addEventListener('abort', abort, { once: true })
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException('请求已取消', 'AbortError')
      const { value, done } = await reader.read().catch(() => {
        if (signal?.aborted) throw new DOMException('请求已取消', 'AbortError')
        throw new Error(interruptedMessage)
      })
      if (signal?.aborted) throw new DOMException('请求已取消', 'AbortError')
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let boundary: RegExpExecArray | null
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        const data = frame.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).replace(/^ /, '')).join('\n')
        if (!data) continue
        const event = JSON.parse(data) as T
        if (!event || typeof event.type !== 'string') throw new Error('收到无效的进度事件')
        if (event.type === 'error') throw new Error(event.detail || '服务处理失败')
        onEvent(event)
        if (event.type === terminalType) return
      }
      if (done) throw new Error(interruptedMessage)
    }
  } finally {
    signal?.removeEventListener('abort', abort)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export async function streamRequest<T extends { type: string; detail?: string | null }>(
  path: string, body: FormData | object, onEvent: (event: T) => void, terminalType: string, signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({ Accept: 'text/event-stream' })
  const token = localStorage.getItem('talentfit_token')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const multipart = body instanceof FormData
  if (!multipart) headers.set('Content-Type', 'application/json')
  const base = import.meta.env?.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api'
  const response = await fetch(base.replace(/\/$/, '') + path, { method: 'POST', headers, body: multipart ? body : JSON.stringify(body), signal })
  if (response.status === 401 && window.location.pathname !== '/login') {
    localStorage.removeItem('talentfit_token')
    localStorage.removeItem('talentfit_user')
    window.location.href = '/login'
  }
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    throw new Error(typeof error?.detail === 'string' ? error.detail : `请求失败（${response.status}）`)
  }
  if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new Error('服务未返回有效的进度流')
  await consumeSse(response.body, onEvent, terminalType, signal)
}
