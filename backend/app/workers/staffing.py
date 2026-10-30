"""Independent lease-based staffing worker; one persistent local MCP child per worker."""
import argparse
import logging
import asyncio
from contextlib import suppress
import os
from pathlib import Path
import signal
import socket
import json
import sys
from uuid import uuid4

from fastapi import HTTPException
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.llm import ModelUnavailableError
from app.services.staffing import model, runs
from app.services.staffing.graph import StaffingExecution
from app.services.staffing.mcp_client import StaffingMcpClient, ToolFailure
from app.services.tracing import astream_graph


logger = logging.getLogger("talentfit.staffing.worker")


async def execute_claim(factory, lease, client, checkpoint_path=None):
    previous = runs.get_run(factory,lease['id'],lease['actor_id'])['result'].get('metrics',{})
    budget = model.RunBudget(previous=previous)
    execution = StaffingExecution(factory,lease,client,budget)
    path = Path(checkpoint_path or settings.staffing_checkpoint_path)
    path.parent.mkdir(parents=True,exist_ok=True)
    config = {'configurable':{'thread_id':lease['checkpoint_thread_id']}, 'recursion_limit':60}

    async def pulse():
        while True:
            await asyncio.sleep(max(.2,min(5,settings.staffing_lease_seconds/3)))
            try:
                await asyncio.to_thread(runs.heartbeat,factory,lease['id'],lease['fence_token'],lease.get('lease_owner') or owner)
            except HTTPException as exc:
                if not isinstance(exc.detail,dict) or exc.detail.get('code')!='RESOURCE_BUSY':
                    raise

    # Owner is server-known claim data, never a browser/model parameter.
    with factory() as db:
        from app.models.staffing import StaffingRun
        owner = db.get(StaffingRun,lease['id']).lease_owner
    heart = asyncio.create_task(pulse())
    waiting, questions = False, []
    try:
        async with asyncio.timeout(max(.01,budget.remaining())):
            async with AsyncSqliteSaver.from_conn_string(str(path)) as checkpointer:
                graph = execution.compile(checkpointer)
                value = Command(resume={'acknowledged':True}) if lease.get('resume') else {
                    'run_id':lease['id'],'project_id':lease['project_id'],'requirements_revision':lease['requirements_revision'],
                    'candidate_ids':[],'budget':budget.metrics()}
                async for event in astream_graph(graph,value,config=config,run_id=lease['id'],subgraphs=True):
                    await execution.guard()
                    await asyncio.to_thread(runs.append_event,factory,lease['id'],lease['fence_token'],event['type'],
                                            {k:v for k,v in event.items() if k!='type'})
                snapshot = await graph.aget_state(config)
                waiting = bool(snapshot.next and any(task.interrupts for task in snapshot.tasks))
                if waiting:
                    questions = snapshot.values.get('questions',[])
            # Checkpointer and stream have closed before business waiting status releases the lease.
            if waiting:
                return await asyncio.to_thread(runs.mark_waiting,factory,lease['id'],lease['fence_token'],questions,budget.metrics())
            if execution.solver_result is None or execution.input_data is None:
                raise RuntimeError('图未产生完整求解结果')
            return await asyncio.to_thread(runs.finish_run,factory,lease['id'],lease['fence_token'],execution.solver_result,
                                          execution.input_data,budget.metrics(),budget.check)
    except runs.LeaseLost:
        return None
    except model.BudgetExceeded as exc:
        code, detail = 'BUDGET_EXCEEDED', str(exc)
    except TimeoutError:
        code, detail = 'BUDGET_EXCEEDED', '本次运行达到预算时间上限，请重试或缩小范围'
    except ModelUnavailableError:
        code, detail = 'MODEL_UNAVAILABLE', '组队模型调用失败或输出未通过校验，请重试或检查模型配置'
    except ToolFailure as exc:
        code, detail = exc.code, str(exc)
    except HTTPException as exc:
        value = exc.detail if isinstance(exc.detail,dict) else {}
        code, detail = value.get('code','STALE_INPUT'), value.get('message','业务依据已变化，请刷新后重新生成')
    except Exception as exc:
        logger.error("run_failed run_id=%s exception=%s", lease["id"], type(exc).__name__)
        code, detail = 'RUN_FAILED', '组队运行未完成，请重试；持续失败时检查本地服务'
    finally:
        heart.cancel()
        with suppress(asyncio.CancelledError, runs.LeaseLost):
            await heart
    try:
        logger.warning("run_failed run_id=%s code=%s", lease["id"], code)
        return runs.fail_run(factory,lease['id'],lease['fence_token'],code,detail,budget.metrics())
    except runs.LeaseLost:
        return None
    except HTTPException as exc:
        if isinstance(exc.detail,dict) and exc.detail.get('code')=='RESOURCE_BUSY':
            return None  # expired lease recovery owns any subsequent retry
        raise


async def worker(once=False):
    configure_logging()
    owner = f'{socket.gethostname()}:{os.getpid()}:{uuid4()}'
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    def stop(*_):
        loop.call_soon_threadsafe(stopped.set)
    for signum in (signal.SIGINT,signal.SIGTERM):
        signal.signal(signum,stop)
    record = Path(settings.staffing_runtime_dir) / f'staffing-mcp-{os.getpid()}.json'
    worker_record = record.with_name('staffing-worker.json')
    worker_record.parent.mkdir(parents=True,exist_ok=True)
    worker_record.write_text(json.dumps({'pid':os.getpid(),'parent_pid':os.getppid(),'executable':sys.executable}),encoding='utf-8')
    async with StaffingMcpClient(record_path=record) as client:
        while not stopped.is_set():
            try:
                await asyncio.to_thread(runs.replace_expired,SessionLocal)
                lease = await asyncio.to_thread(runs.claim_run,SessionLocal,owner)
            except HTTPException as exc:
                if not isinstance(exc.detail,dict) or exc.detail.get('code')!='RESOURCE_BUSY':
                    raise
                await asyncio.sleep(settings.staffing_poll_seconds)
                continue
            if lease:
                try:
                    await execute_claim(SessionLocal,lease,client)
                except Exception:
                    # One failed claim must never stop the worker loop for later runs.
                    logger.exception("claim_failed run_id=%s", lease.get('id'))
            if once:
                break
            try:
                await asyncio.wait_for(stopped.wait(),timeout=settings.staffing_poll_seconds)
            except TimeoutError:
                pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true',help='领取至多一个排队任务后退出（隔离验证用）')
    args = parser.parse_args()
    asyncio.run(worker(args.once))
