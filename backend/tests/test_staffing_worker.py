"""Real graph/checkpointer/MCP with only the external language model replaced."""
import asyncio
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models import Candidate, ResumeChunk, User
from app.models.staffing import Employee, EmployeeDocument, EmployeeCapacityDay, CompanyWorkDay, StaffingProject
from app.services.staffing import evidence, runs
from app.services.staffing.common import ensure_state, json_dump


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'openai_api_key', '')
    path = tmp_path / 'worker.db'
    url = 'sqlite:///' + path.as_posix()
    engine = create_engine(url, connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        ensure_state(db)
        db.execute(text('CREATE VIRTUAL TABLE resume_chunks_fts USING fts5(chunk_id UNINDEXED,candidate_id UNINDEXED,content)'))
        db.add(User(id=2, username='synthetic', password_hash='unused', role='hr'))
        person = Candidate(name='合成甲', created_by=2)
        employee = Employee(display_name='合成甲')
        db.add_all([person, employee]); db.flush()
        parent = ResumeChunk(candidate_id=person.id, chunk_type='parent', content='Python开发')
        db.add(parent); db.flush()
        child = ResumeChunk(candidate_id=person.id,parent_id=parent.id,chunk_type='child',content='熟悉Python开发。')
        db.add(child); db.flush()
        db.add(EmployeeDocument(employee_id=employee.id,candidate_id=person.id,candidate_created_at=person.created_at,is_primary=True,linked_by=2))
        dates = ['2026-11-02','2026-11-03']
        for day in dates:
            db.add(CompanyWorkDay(work_date=date.fromisoformat(day), is_workday=True, updated_by=2))
        employee_id, child_id = employee.id, child.id
        req = {'schema_version':'1','start_date':dates[0],'end_date':'2026-11-04','work_dates':dates,
            'slots':[{'slot_id':'slot-a','role_name':'后端','units_per_day':50,'work_dates':dates,'required_skills':['python'],'preferred_skills':[]}],
            'employee_ids':[employee_id],'required_employee_ids':[],'excluded_employee_ids':[],'revision':1}
        project = StaffingProject(title='合成项目',created_by=2,start_date=date(2026,11,2),end_date=date(2026,11,4),requirements_revision=1,
                                  requirements_json=json_dump(req),scope_json=json_dump([employee_id]))
        db.add(project); db.flush()
        project_id = project.id
    with factory() as db:
        prepared = evidence.prepare(db, employee_id, lambda source:{'facts':[{'fact_type':'skill','normalized_key':'python','fact':{'name':'Python'},
            'source_refs':[{'chunk_id':child_id,'quote':'熟悉Python开发。'}]}]})
    evidence.refresh(factory,employee_id,prepared)
    from app.services.staffing import model
    monkeypatch.setattr(model,'coordinate',lambda context,budget:{'action':'collect','summary':'查询能力与资源'})
    def decision(messages, tools, budget):
        if any(m['role']=='tool' for m in messages):
            return {'calls':[],'summary':'已核对当前来源，未知工时仍标记未知。'}
        names = {t['function']['name'] for t in tools}
        calls = []
        if 'get_employee_evidence' in names:
            calls.append({'id':'evidence-call','name':'get_employee_evidence','arguments':{'project_id':project_id,'employee_ids':[employee_id],'skill_names':[]}})
        if 'get_capacity' in names:
            calls.append({'id':'capacity-call','name':'get_capacity','arguments':{'project_id':project_id,'employee_ids':[employee_id], 'start_date':'2026-11-02','end_date':'2026-11-04'}})
        return {'calls':calls, 'summary':''}
    monkeypatch.setattr(model,'tool_decision',decision)
    monkeypatch.setattr(model,'summarize',lambda context,budget:'已按已确认范围和逐日容量生成方案，尚未占用排期。')
    yield factory, project_id, employee_id, url, tmp_path / 'checkpoints.sqlite'
    engine.dispose()


def execute(factory, lease, url, checkpoints):
    from app.services.staffing.mcp_client import StaffingMcpClient
    from app.workers.staffing import execute_claim
    async def main():
        async with StaffingMcpClient(database_url=url) as client:
            return await execute_claim(factory, lease, client, checkpoints)
    return asyncio.run(main())


def test_waiting_checkpoint_resumes_after_new_connection_and_ledger_input(workbench):
    factory, project_id, employee_id, url, checkpoints = workbench
    with factory.begin() as db:
        for day in ['2026-11-02', '2026-11-03']:
            db.add(EmployeeCapacityDay(employee_id=employee_id, work_date=date.fromisoformat(day), capacity_units=None))
    run = runs.create_run(factory,2,project_id,{'expected_version':1,'requirements_revision':1})
    lease = runs.claim_run(factory,'first-process')
    execute(factory,lease,url,checkpoints)
    waiting = runs.get_run(factory,run['id'],2)
    assert waiting['status']=='waiting_input' and waiting['questions']
    assert checkpoints.exists()
    with factory.begin() as db:
        for day in ['2026-11-02','2026-11-03']:
            row = db.query(EmployeeCapacityDay).filter_by(employee_id=employee_id, work_date=date.fromisoformat(day)).one()
            row.capacity_units = 100
    runs.resume_run(factory,2,run['id'],{'expected_version':waiting['version'],'requirements_revision':1})
    resumed = runs.claim_run(factory,'restarted-process')
    execute(factory,resumed,url,checkpoints)
    finished = runs.get_run(factory,run['id'],2)
    assert finished['status']=='succeeded', finished
    assert finished['result']['solver_status']=='OPTIMAL' and len(finished['result']['plan_ids'])==1
    events = runs.list_events(factory,run['id'],0)
    assert any(e['type']=='input_required' for e in events)
    assert any(e['type']=='plan_saved' for e in events)
    assert any(e['type']=='tool_finished' for e in events)
    assert any(e['type']=='agent_reconciled' for e in events)
    assert len({e['node']['id'] for e in events if e['type']=='node_started'}) > 8
    assert [e['seq'] for e in events]==list(range(1,len(events)+1))
    assert finished['result']['metrics']['tool_calls']==4


def test_single_coordinator_and_multi_graph_produce_valid_team(workbench):
    factory, project_id, employee_id, url, checkpoints = workbench
    with factory.begin() as db:
        for day in ['2026-11-02','2026-11-03']:
            db.add(EmployeeCapacityDay(employee_id=employee_id,work_date=date.fromisoformat(day),capacity_units=100))
    for mode in ['single','multi']:
        run = runs.create_run(factory,2,project_id,{'expected_version':1,'requirements_revision':1,'mode':mode})
        lease = runs.claim_run(factory,'worker-'+mode)
        execute(factory,lease,url,checkpoints)
        result = runs.get_run(factory,run['id'],2)
        assert result['status']=='succeeded' and result['result']['plan_ids']
        assert result['result']['metrics']['tool_calls']==2


def test_cancel_while_read_tool_is_in_flight_never_publishes_plan(workbench):
    from app.services.staffing.mcp_client import StaffingMcpClient
    from app.workers.staffing import execute_claim
    from app.models.staffing import StaffingPlan
    factory,project_id,_,url,checkpoints = workbench
    run = runs.create_run(factory,2,project_id,{'expected_version':1,'requirements_revision':1})
    lease = runs.claim_run(factory,'cancel-worker')
    async def main():
        async with StaffingMcpClient(database_url=url) as client:
            started, release = asyncio.Event(),asyncio.Event()
            original = client.call
            async def delayed(*args,**kwargs):
                result = await original(*args,**kwargs)
                started.set()
                await release.wait()
                return result
            client.call = delayed
            task = asyncio.create_task(execute_claim(factory,lease,client,checkpoints))
            await asyncio.wait_for(started.wait(),timeout=20)
            current = runs.get_run(factory,run['id'],2)
            runs.cancel_run(factory,2,run['id'],{'expected_version':current['version'],'requirements_revision':1})
            release.set()
            await task
    asyncio.run(main())
    with factory() as db:
        assert db.query(StaffingPlan).count()==0
    assert runs.get_run(factory,run['id'],2)['status']=='cancelled'


def test_checkpoints_do_not_store_resumes_or_authorization_credentials(workbench):
    import sqlite3
    factory,project_id,_,url,checkpoints = workbench
    with factory.begin() as db:
        employee_id = db.query(Employee).first().id
        for day in ['2026-11-02', '2026-11-03']:
            db.add(EmployeeCapacityDay(employee_id=employee_id, work_date=date.fromisoformat(day), capacity_units=None))
    run = runs.create_run(factory,2,project_id,{'expected_version':1,'requirements_revision':1})
    execute(factory,runs.claim_run(factory,'privacy-worker'),url,checkpoints)
    assert runs.get_run(factory,run['id'],2)['status']=='waiting_input'
    with sqlite3.connect(checkpoints) as db:
        rows = [row for table in ['checkpoints','writes'] for row in db.execute(f'SELECT * FROM {table}').fetchall()]
    serialized = b' '.join(value if isinstance(value,bytes) else str(value).encode() for row in rows for value in row)
    for private in ['熟悉Python开发。','talentfit/authorization','raw_text','processed_text','api_key','Bearer ']:
        assert private.encode() not in serialized


def test_cancellation_during_failure_finalization_does_not_kill_worker(workbench, monkeypatch):
    from app.services.staffing import model
    from app.services.llm import ModelUnavailableError
    factory,project_id,_,url,checkpoints = workbench
    run = runs.create_run(factory,2,project_id,{'expected_version':1,'requirements_revision':1})
    lease = runs.claim_run(factory,'racing-failure-worker')
    def unavailable(*_):
        raise ModelUnavailableError('合成服务失败')
    monkeypatch.setattr(model,'coordinate',unavailable)
    original = runs.fail_run
    def cancel_before_final_write(*args,**kwargs):
        current = runs.get_run(factory,run['id'],2)
        runs.cancel_run(factory,2,run['id'],{'expected_version':current['version'],'requirements_revision':1})
        return original(*args,**kwargs)
    monkeypatch.setattr(runs,'fail_run',cancel_before_final_write)
    execute(factory,lease,url,checkpoints)
    assert runs.get_run(factory,run['id'],2)['status']=='cancelled'


def test_idle_worker_retries_transient_sqlite_write_contention(tmp_path,monkeypatch):
    from fastapi import HTTPException
    from app.workers import staffing as worker_module
    from app.core.config import settings
    calls={'polls':0,'claims':0}
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    def recover(_):
        calls['polls']+=1
        if calls['polls']==1:raise HTTPException(409,detail={'code':'RESOURCE_BUSY','message':'synthetic lock'})
        return []
    def claim(*_):calls['claims']+=1;return None
    monkeypatch.setattr(worker_module,'StaffingMcpClient',Client)
    monkeypatch.setattr(runs,'replace_expired',recover)
    monkeypatch.setattr(runs,'claim_run',claim)
    monkeypatch.setattr(settings,'staffing_runtime_dir',str(tmp_path))
    monkeypatch.setattr(settings,'staffing_poll_seconds',.001)
    asyncio.run(worker_module.worker(once=True))
    assert calls=={'polls':2,'claims':1}


def test_busy_failure_transition_defers_to_lease_recovery(workbench,monkeypatch):
    from fastapi import HTTPException
    from app.services.staffing import model
    from app.services.llm import ModelUnavailableError
    factory,project_id,_,url,checkpoints=workbench
    run=runs.create_run(factory,2,project_id,{'expected_version':1,'requirements_revision':1})
    def fail(*_):raise ModelUnavailableError('合成模型失败')
    def busy(*_):raise HTTPException(409,detail={'code':'RESOURCE_BUSY','message':'synthetic lock'})
    monkeypatch.setattr(model,'coordinate',fail)
    monkeypatch.setattr(runs,'fail_run',busy)
    execute(factory,runs.claim_run(factory,'busy-worker'),url,checkpoints)
    assert runs.get_run(factory,run['id'],2)['status']=='running'
