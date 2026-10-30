"""Bounded SSE worker bridge; the worker owns its database/model resources."""
import asyncio
import json
import logging
from queue import Empty, Full, Queue
from threading import Event, Thread

from fastapi.responses import StreamingResponse


logger = logging.getLogger("talentfit.stream")


class StreamCancelled(BaseException):
    pass


def event_response(worker):
    async def body():
        queue = Queue(maxsize=128)
        cancelled = Event()
        finished = Event()

        def emit(event):
            while not cancelled.is_set():
                try:
                    queue.put(event, timeout=0.2)
                    return
                except Full:
                    continue
            raise StreamCancelled()

        def run():
            try:
                worker(emit, cancelled.is_set)
            except StreamCancelled:
                pass
            except Exception as exc:
                logger.error("stream_failed exception=%s", type(exc).__name__)
                try:
                    emit({"type": "error", "detail": "处理过程出现异常，请刷新查看已保存的结果后重试。"})
                except StreamCancelled:
                    pass
            finally:
                finished.set()

        Thread(target=run, daemon=True, name="talentfit-stream").start()
        try:
            while not finished.is_set() or not queue.empty():
                try:
                    event = await asyncio.to_thread(queue.get, True, 0.25)
                except Empty:
                    continue
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        finally:
            cancelled.set()

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
