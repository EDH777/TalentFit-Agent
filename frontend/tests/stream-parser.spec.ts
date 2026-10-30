import { test, expect } from '@playwright/test'
import { consumeSse } from '../src/services/stream'

type Event = { type: string; content?: string; detail?: string }

function bytesStream(bytes: Uint8Array, cuts: number[]) {
  let offset = 0
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (offset >= bytes.length) { controller.close(); return }
      const end = Math.min(offset + (cuts.shift() || bytes.length), bytes.length)
      controller.enqueue(bytes.slice(offset, end)); offset = end
    },
  })
}

test('中文按单字节分片仍保留事件顺序与完整正文', async () => {
  const text = ': heartbeat\r\n\r\ndata: {"type":"delta","content":"中文简历"}\r\n\r\ndata: {"type":"done"}\n\n'
  const bytes = new TextEncoder().encode(text)
  const events: Event[] = []
  await consumeSse<Event>(bytesStream(bytes, Array(bytes.length).fill(1)), (event) => events.push(event), 'done')
  expect(events).toEqual([{ type: 'delta', content: '中文简历' }, { type: 'done' }])
})

test('在最终帧到达前立即交付增量', async () => {
  const encoder = new TextEncoder()
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value } })
  const events: Event[] = []
  const reading = consumeSse<Event>(stream, (event) => events.push(event), 'done')
  controller.enqueue(encoder.encode('data: {"type":"delta","content":"已到达"}\n\n'))
  await expect.poll(() => events).toEqual([{ type: 'delta', content: '已到达' }])
  controller.enqueue(encoder.encode('data: {"type":"done"}\n\n')); controller.close()
  await reading
})

test('服务错误不能被当作完成', async () => {
  const bytes = new TextEncoder().encode('data: {"type":"error","detail":"嵌入服务失败"}\n\n')
  await expect(consumeSse<Event>(bytesStream(bytes, []), () => {}, 'batch_done')).rejects.toThrow('嵌入服务失败')
})

test('正文后断流且没有完成帧时报告中断', async () => {
  const bytes = new TextEncoder().encode('data: {"type":"delta","content":"部分答案"}\n\n')
  await expect(consumeSse<Event>(bytesStream(bytes, []), () => {}, 'done')).rejects.toThrow('中断')
})

test('网络读取异常在已有增量后仍明确报告中断', async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value } })
  const events: Event[] = []
  const reading = consumeSse<Event>(stream, (event) => events.push(event), 'done')
  controller.enqueue(new TextEncoder().encode('data: {"type":"delta","content":"已显示"}\n\n'))
  await expect.poll(() => events.length).toBe(1)
  controller.error(new TypeError('network failure'))
  await expect(reading).rejects.toThrow('中断')
})

test('批次完成帧是上传唯一完成条件', async () => {
  const bytes = new TextEncoder().encode('data: {"type":"file_result"}\n\ndata: {"type":"batch_done"}\n\n')
  const events: Event[] = []
  await consumeSse<Event>(bytesStream(bytes, [7, 11]), (event) => events.push(event), 'batch_done')
  expect(events.map((event) => event.type)).toEqual(['file_result', 'batch_done'])
})

test('取消信号终止尚未结束的读取', async () => {
  const abort = new AbortController()
  const stream = new ReadableStream<Uint8Array>()
  const reading = consumeSse<Event>(stream, () => {}, 'done', abort.signal)
  abort.abort()
  await expect(reading).rejects.toMatchObject({ name: 'AbortError' })
})
