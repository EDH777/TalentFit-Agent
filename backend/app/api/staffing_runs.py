"""Background runs and authenticated, replayable SSE. Disconnect never cancels work."""
import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.api.deps import current_user
from app.api.employees import require_staffing_editor, staffing_session_factory
from app.models import User
from app.services.staffing import runs

router = APIRouter(prefix='/api/staffing', tags=['staffing-runs'])


@router.post('/projects/{project_id}/runs', status_code=202)
def create_run(project_id: str, payload: dict, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return runs.create_run(factory, user.id, project_id, payload)


@router.get('/projects/{project_id}/runs')
def project_runs(project_id: str, user: User = Depends(current_user), factory=Depends(staffing_session_factory)):
    return runs.project_runs(factory, project_id, user.id)


@router.get('/runs/{run_id}')
def get_run(run_id: str, user: User = Depends(current_user), factory=Depends(staffing_session_factory)):
    return runs.get_run(factory, run_id, user.id)


@router.post('/runs/{run_id}/cancel')
def cancel_run(run_id: str, payload: dict, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return runs.cancel_run(factory, user.id, run_id, payload)


@router.post('/runs/{run_id}/resume')
def resume_run(run_id: str, payload: dict, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return runs.resume_run(factory, user.id, run_id, payload)


@router.get('/runs/{run_id}/events')
def events(run_id: str, request: Request, after_seq: int = Query(default=0, ge=0),
           user: User = Depends(current_user), factory=Depends(staffing_session_factory)):
    runs.get_run(factory, run_id, user.id)  # authenticate/authorize before sending streaming headers

    async def stream():
        last, idle = after_seq, 0
        while not await request.is_disconnected():
            batch = await asyncio.to_thread(runs.list_events, factory, run_id, last, user.id)
            for event in batch:
                last = event['seq']
                yield f"id: {last}\ndata: {json.dumps(jsonable_encoder(event), ensure_ascii=False)}\n\n"
            state = await asyncio.to_thread(runs.get_run, factory, run_id, user.id)
            if state['status'] in (*runs.TERMINAL, 'waiting_input') and last >= state['last_event_seq']:
                return
            if batch:
                idle = 0
                continue
            idle += 1
            if idle % 20 == 0:
                yield ': keep-alive\n\n'
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
