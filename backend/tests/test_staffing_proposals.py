from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models import Candidate, ResumeChunk, User
from app.models.staffing import CompanyWorkDay, Employee, EmployeeCapacityDay, ResourceAllocation, StaffingPlan, StaffingProject, StaffingResourceState, StaffingRun
from app.services.staffing.common import ensure_state, json_dump, json_load
from app.services.staffing import projects, proposals
from app.services.staffing.employees import from_documents
from app.services.staffing.evidence import prepare, refresh
from app.services.staffing.solver import solve_teams


@pytest.fixture
def setup(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'proposals.db'),connect_args={'timeout':10,'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    with factory.begin() as db:
        ensure_state(db)
        hr=User(username='synthetic-hr',password_hash='unused',role='hr')
        admin=User(username='synthetic-admin',password_hash='unused',role='admin')
        db.add_all([hr,admin]);db.flush()
        ids=hr.id,admin.id
        db.add(CompanyWorkDay(work_date=date(2027,1,4),is_workday=True,updated_by=hr.id))
    yield factory,*ids,engine
    engine.dispose()


def make_employee(factory,hr,name='合成开发'):
    with factory.begin() as db:
        candidate=Candidate(name=name,created_by=hr)
        db.add(candidate);db.flush()
        parent=ResumeChunk(candidate_id=candidate.id,chunk_type='parent',content='熟悉 Python')
        db.add(parent);db.flush()
        child=ResumeChunk(candidate_id=candidate.id,parent_id=parent.id,content='熟悉 Python')
        db.add(child);db.flush()
        document={'candidate_id':candidate.id,'candidate_created_at':candidate.created_at.isoformat(),'create_new':True}
        child_id=child.id
    eid=from_documents(factory,hr,[document])['results'][0]['employee_id']
    with factory() as db:
        prepared=prepare(db,eid,lambda source:{'facts':[{'fact_type':'skill','normalized_key':'python','fact':{'name':'python'},'source_refs':[{'chunk_id':child_id,'quote':'熟悉 Python'}]}]})
    refresh(factory,eid,prepared)
    with factory.begin() as db:
        db.add(EmployeeCapacityDay(employee_id=eid,work_date=date(2027,1,4),capacity_units=100))
    return eid,child_id


def make_project(factory,hr,eids,title='合成项目',units=60):
    req={'start_date':'2027-01-04','end_date':'2027-01-05','work_dates':['2027-01-04'],'employee_ids':eids,
         'slots':[{'role_name':'开发','units_per_day':units,'work_dates':['2027-01-04'],'required_skills':['Python']}]}
    return projects.create_project(factory,hr,{'title':title,'start_date':req['start_date'],'end_date':req['end_date'],'confirmed':True,'requirements':req})


def generate(factory,project_id):
    with factory.begin() as db:
        project=db.get(projects.StaffingProject,project_id)
        inp=proposals.build_input(db,project)
        result=solve_teams(inp['requirements'],inp['employees'],inp['capacities'],inp['allocations'])
        plans=proposals.save_generated_plans(db,project,None,result,inp)
        db.flush()
        return [proposals.plan_dict(db,p) for p in plans]


def submit(factory,hr,plan):
    checked=proposals.validate_plan(factory,hr,plan['id'],{'expected_version':plan['version']})
    return proposals.submit(factory,hr,plan['id'],{'expected_version':checked['version'],'input_fingerprint':checked['input_fingerprint']},'submit-'+plan['id'])


def close_setup(factory):
    with factory.begin() as db:
        db.get(StaffingResourceState,1).bootstrap_closed_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc)


def test_requirements_explicit_confirmation_server_slots_and_active_run_conflict(setup):
    factory,hr,admin,_=setup
    eid,_=make_employee(factory,hr)
    p=make_project(factory,hr,[eid])
    assert p['requirements_revision']==1
    assert p['requirements']['slots'][0]['slot_id']
    req=p['requirements']
    with pytest.raises(HTTPException) as exc:
        projects.update_project(factory,hr,p['id'],{'expected_version':p['version'],'expected_requirements_revision':1,'requirements':req})
    assert exc.value.status_code==422
    with pytest.raises(HTTPException) as exc:
        projects.create_project(factory,admin,{'title':'拒绝','start_date':'2027-01-04','end_date':'2027-01-05'})
    assert exc.value.status_code==403


def test_requirements_accept_default_weekday_without_explicit_calendar_row(setup):
    factory, hr, _, _ = setup
    eid, _ = make_employee(factory, hr)
    req = {'start_date': '2027-01-05', 'end_date': '2027-01-06', 'work_dates': ['2027-01-05'],
           'employee_ids': [eid], 'slots': [{'role_name': '开发', 'units_per_day': 60,
                                              'work_dates': ['2027-01-05'], 'required_skills': ['Python']}]}
    project = projects.create_project(factory, hr, {'title': '默认工作日', 'start_date': req['start_date'],
                                                     'end_date': req['end_date'], 'confirmed': True,
                                                     'requirements': req})
    assert project['requirements_revision'] == 1


def test_requirements_still_reject_default_weekend_without_override(setup):
    factory, hr, _, _ = setup
    eid, _ = make_employee(factory, hr)
    req = {'start_date': '2027-01-09', 'end_date': '2027-01-10', 'work_dates': ['2027-01-09'],
           'employee_ids': [eid], 'slots': [{'role_name': '开发', 'units_per_day': 60,
                                              'work_dates': ['2027-01-09'], 'required_skills': ['Python']}]}
    with pytest.raises(HTTPException) as exc:
        projects.create_project(factory, hr, {'title': '默认周末', 'start_date': req['start_date'],
                                               'end_date': req['end_date'], 'confirmed': True,
                                               'requirements': req})
    assert exc.value.detail['code'] == 'UNKNOWN_WORK_CALENDAR'


def test_requirement_role_name_cannot_be_employee_name(setup):
    factory, hr, _, _ = setup
    employee_id, _ = make_employee(factory, hr, name='李民')
    with pytest.raises(HTTPException) as exc:
        projects.create_project(factory, hr, {
            'title': '岗位名校验', 'start_date': '2027-01-04', 'end_date': '2027-01-05',
            'confirmed': True, 'requirements': {
                'start_date': '2027-01-04', 'end_date': '2027-01-05', 'work_dates': ['2027-01-04'],
                'employee_ids': [employee_id], 'slots': [{'role_name': '李民', 'units_per_day': 40, 'work_dates': ['2027-01-04']}]
            }
        })
    assert exc.value.status_code == 422
    assert '岗位名称' in str(exc.value.detail)


def test_approve_idempotency_permissions_and_second_plan_rejected(setup):
    factory,hr,admin,engine=setup
    a,_=make_employee(factory,hr); b,_=make_employee(factory,hr,'合成替代')
    p=make_project(factory,hr,[a,b]); plans=generate(factory,p['id'])
    submitted=submit(factory,hr,plans[0]);close_setup(factory)
    payload={'expected_version':submitted['version'],'input_fingerprint':submitted['input_fingerprint']}
    with pytest.raises(HTTPException) as exc:
        proposals.approve(factory,hr,submitted['id'],payload,'approval')
    assert exc.value.status_code==403
    statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
    event.listen(engine,'before_cursor_execute',capture)
    applied=proposals.approve(factory,admin,submitted['id'],payload,'approval')
    event.remove(engine,'before_cursor_execute',capture)
    assert statements[0].lstrip().upper().startswith('UPDATE STAFFING_RESOURCE_STATE')
    assert applied['state']=='applied'
    assert proposals.approve(factory,admin,submitted['id'],payload,'approval')==applied
    with pytest.raises(HTTPException) as exc:
        proposals.approve(factory,admin,submitted['id'],{**payload,'expected_version':999},'approval')
    assert exc.value.status_code==409
    with factory() as db:assert db.query(ResourceAllocation).count()==1
    with pytest.raises(HTTPException):submit(factory,hr,plans[1])


def test_stale_source_hides_quote_and_blocks_approval(setup):
    factory,hr,admin,_=setup
    eid,child_id=make_employee(factory,hr)
    p=make_project(factory,hr,[eid]);plan=submit(factory,hr,generate(factory,p['id'])[0]);close_setup(factory)
    with factory.begin() as db:db.get(ResumeChunk,child_id).enabled=False
    with factory() as db:
        shown=proposals.plan_dict(db,db.get(StaffingPlan,plan['id']))
    assert '熟悉 Python' not in str(shown['evidence'])
    with pytest.raises(HTTPException) as exc:
        proposals.approve(factory,admin,plan['id'],{'expected_version':plan['version'],'input_fingerprint':plan['input_fingerprint']},'stale')
    assert exc.value.status_code==409
    with factory() as db:
        assert db.query(ResourceAllocation).count()==0
        assert db.get(StaffingPlan,plan['id']).state=='invalidated'


def test_unrelated_employee_change_does_not_stale_selected_plan(setup):
    factory,hr,admin,_=setup
    a,_=make_employee(factory,hr);b,_=make_employee(factory,hr,'合成其他')
    p=make_project(factory,hr,[a,b]);plan=submit(factory,hr,generate(factory,p['id'])[0]);close_setup(factory)
    other=({a,b}-{x['employee_id'] for x in plan['assignments']}).pop()
    with factory.begin() as db:db.get(Employee,other).employment_status='inactive'
    result=proposals.approve(factory,admin,plan['id'],{'expected_version':plan['version'],'input_fingerprint':plan['input_fingerprint']},'unrelated')
    assert result['state']=='applied'


def test_two_projects_cannot_concurrently_overbook(setup):
    factory,hr,admin,_=setup
    a,_=make_employee(factory,hr)
    p1=make_project(factory,hr,[a],title='合成甲');p2=make_project(factory,hr,[a],title='合成乙')
    plans=[submit(factory,hr,generate(factory,p['id'])[0]) for p in [p1,p2]];close_setup(factory)
    def action(plan):
        try:return proposals.approve(factory,admin,plan['id'],{'expected_version':plan['version'],'input_fingerprint':plan['input_fingerprint']},'approve-'+plan['id'])['state']
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(action,plans))
    assert sorted(map(str,results))==['409','applied']
    with factory() as db:assert db.query(ResourceAllocation).count()==1


def test_requirement_draft_shared_messages_never_auto_confirms_and_variant_is_new(setup):
    factory,hr,admin,_=setup
    a,_=make_employee(factory,hr);b,_=make_employee(factory,hr,'合成替代')
    p=make_project(factory,hr,[a,b]);plan=generate(factory,p['id'])[0]
    result=projects.add_message(factory,hr,p['id'],{'content':'把投入改为40%','intent':'requirements','expected_requirements_revision':1},draft_provider=lambda content,req:{'requirements':{**req,'slots':[{**req['slots'][0],'units_per_day':40}]},'missing_fields':[],'changes':{'units_per_day':40}})
    assert result['draft']['requirements']['slots'][0]['units_per_day']==40
    with factory() as db:
        detail=projects.project_dict(db,db.get(projects.StaffingProject,p['id']))
    assert detail['requirements']['slots'][0]['units_per_day']==60
    assert len(detail['messages'])==2
    old_eid=plan['assignments'][0]['employee_id'];other=({a,b}-{old_eid}).pop()
    variant=proposals.variant(factory,hr,plan['id'],{'expected_version':plan['version'],'assignments':[{'employee_id':other,'slot_id':plan['assignments'][0]['slot_id']}]})
    assert variant['id']!=plan['id'] and variant['state']=='draft'
    with factory() as db:assert json_load(db.get(StaffingPlan,plan['id']).assignments_json)[0]['employee_id']==old_eid


def test_baseline_finalization_permanent_and_archive_does_not_release(setup):
    factory,hr,admin,_=setup
    a,_=make_employee(factory,hr)
    project=projects.create_project(factory,hr,{'title':'合成已有项目','origin_kind':'baseline','start_date':'2027-01-04','end_date':'2027-01-05'})
    result=proposals.add_baseline(factory,hr,{'project_id':project['id'],'expected_project_version':project['version'],'allocations':[{'employee_id':a,'start_date':'2027-01-04','end_date':'2027-01-05','work_dates':['2027-01-04'],'units_per_day':40,'basis':'合成确认台账'}]},'baseline')
    assert len(result['allocation_ids'])==1
    with factory() as db:revision=db.get(StaffingResourceState,1).revision
    proposals.finalize_setup(factory,admin,{'expected_revision':revision},'finalize')
    with pytest.raises(HTTPException):
        projects.create_project(factory,hr,{'title':'初始化已关闭','origin_kind':'baseline','start_date':'2027-01-04','end_date':'2027-01-05'})
    with pytest.raises(HTTPException):proposals.archive(factory,hr,project['id'],{'expected_version':result['project']['version']},'archive')
    with factory() as db:assert db.query(ResourceAllocation).count()==1

def test_baseline_accepts_default_weekday_without_calendar_row(setup):
    factory,hr,_,_=setup
    a,_=make_employee(factory,hr)
    project=projects.create_project(factory,hr,{'title':'默认工作日已有项目','origin_kind':'baseline','start_date':'2027-01-05','end_date':'2027-01-06'})
    result=proposals.add_baseline(factory,hr,{'project_id':project['id'],'expected_project_version':project['version'],'allocations':[{'employee_id':a,'start_date':'2027-01-05','end_date':'2027-01-06','work_dates':['2027-01-05'],'units_per_day':40,'basis':'合成确认台账'}]},'baseline-default-weekday')
    assert len(result['allocation_ids'])==1

def test_staffing_project_api_uses_shared_read_and_role_gated_writes(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import staffing as api
    from app.api.deps import current_user
    from app.api.employees import staffing_session_factory
    from app.db.session import get_db
    factory,hr,admin,_=setup
    a,_=make_employee(factory,hr)
    local=FastAPI();local.include_router(api.router)
    chosen=[hr]
    def actor():
        with factory() as db:return db.get(User,chosen[0])
    def reader():
        with factory() as db:yield db
    local.dependency_overrides[current_user]=actor
    local.dependency_overrides[get_db]=reader
    local.dependency_overrides[staffing_session_factory]=lambda:factory
    with TestClient(local) as client:
        response=client.post('/api/staffing/projects',json={'title':'API合成项目','start_date':'2027-01-04','end_date':'2027-01-05'})
        assert response.status_code==200
        project=response.json()
        assert isinstance(client.get('/api/staffing/projects').json(),list)
        assert client.get('/api/staffing/projects/'+project['id']).json()['messages']==[]
        chosen[0]=admin
        assert client.get('/api/staffing/projects/'+project['id']).status_code==200
        assert client.post('/api/staffing/projects',json={'title':'管理员不可创建','start_date':'2027-01-04','end_date':'2027-01-05'}).status_code==403
        assert client.post('/api/staffing/setup/finalize',json={'expected_revision':0}).status_code==422

def test_idempotent_replay_never_replays_stale_evidence_text(setup):
    from app.models.staffing import StaffingAction
    factory,hr,admin,_=setup
    eid,child=make_employee(factory,hr)
    p=make_project(factory,hr,[eid]);plan=submit(factory,hr,generate(factory,p['id'])[0]);close_setup(factory)
    payload={'expected_version':plan['version'],'input_fingerprint':plan['input_fingerprint']}
    proposals.approve(factory,admin,plan['id'],payload,'dynamic-evidence')
    with factory.begin() as db:db.get(ResumeChunk,child).enabled=False
    replay=proposals.approve(factory,admin,plan['id'],payload,'dynamic-evidence')
    assert '熟悉 Python' not in str(replay['evidence'])
    with factory() as db:
        assert all('熟悉 Python' not in row.result_json for row in db.query(StaffingAction).all())


def test_old_plan_preserves_its_role_name_after_requirements_change(setup):
    factory,hr,_,_=setup
    eid,_=make_employee(factory,hr)
    p=make_project(factory,hr,[eid]);plan=generate(factory,p['id'])[0]
    req=p['requirements'];req['slots'][0]['role_name']='新角色'
    projects.update_project(factory,hr,p['id'],{'expected_version':p['version'],'expected_requirements_revision':1,'requirements':req,'confirmed':True})
    with factory() as db:shown=proposals.plan_dict(db,db.get(StaffingPlan,plan['id']))
    assert shown['assignments'][0]['role_name']=='开发'

def test_explanation_reads_current_plan_context_outside_lock_and_cannot_change_requirements(setup):
    factory,hr,admin,engine=setup
    a,_=make_employee(factory,hr)
    p=make_project(factory,hr,[a]);generate(factory,p['id'])
    captured=[]
    def explain(content,context):
        captured.append(context)
        assert context['current_plan']['members'][0]['name']=='合成开发'
        assert context['current_plan']['evidence'][0]['facts'][0]['fact']=='python'
        assert 'employee_id' not in str(context) and 'plans' not in str(context)
        return '依据当前简历记载的 Python 与已知逐日容量，选择该员工。'
    result=projects.add_message(factory,admin,p['id'],{'content':'为什么选择这位员工？','intent':'explain','expected_requirements_revision':1},explain_provider=explain)
    assert 'Python' in result['assistant_message']['content']
    assert captured and result['draft'] is None
    with factory() as db:
        assert db.get(projects.StaffingProject,p['id']).requirements_revision==1
        assert db.query(ResourceAllocation).count()==0


def test_plain_language_change_request_reaches_draft_not_explanation(setup):
    factory,hr,_,_=setup
    a,_=make_employee(factory,hr)
    p=make_project(factory,hr,[a])
    asked=[]
    result=projects.add_message(factory,hr,p['id'],{
        'content':'把 12 月 1 号到 4 号，要一个后端一个前端，各投一半时间','expected_requirements_revision':1},
        draft_provider=lambda content,req: asked.append(content) or {'requirements':{**req,'slots':[{**req['slots'][0],'units_per_day':50}]},'missing_fields':[],'changes':{}},
        explain_provider=lambda content,ctx: pytest.fail('需求变更不能走解释路径'))
    assert asked and result['draft']['requirements']['slots'][0]['units_per_day']==50
    with factory() as db:
        assert db.get(projects.StaffingProject,p['id']).requirements_revision==1


def test_explanation_without_plan_answers_without_calling_the_model(setup):
    factory,hr,admin,_=setup
    a,_=make_employee(factory,hr)
    p=make_project(factory,hr,[a])
    result=projects.add_message(factory,admin,p['id'],{'content':'为什么选择这位员工？','intent':'explain','expected_requirements_revision':1},
        explain_provider=lambda content,ctx: pytest.fail('没有方案时不应调用模型'))
    assert '还没有推荐方案' in result['assistant_message']['content']
    assert result['draft'] is None


def test_explanation_context_is_business_language_only(setup):
    factory,hr,_,_=setup
    a,_=make_employee(factory,hr)
    p=make_project(factory,hr,[a])
    with factory() as db:
        context=projects._explanation_context(db,db.get(projects.StaffingProject,p['id']))
    assert set(context)=={'title','start_date','end_date_inclusive','work_day_count','work_days','scope_employee_count','scope_names','slots','current_plan','not_selected'}
    assert context['scope_employee_count']==1 and context['work_day_count']==1
    assert context['current_plan'] is None
    assert 'employee_ids' not in context


def test_explanation_explains_why_a_candidate_was_not_selected(setup):
    factory,hr,_,_=setup
    a,_=make_employee(factory,hr,'合成甲'); b,_=make_employee(factory,hr,'合成乙')
    p=make_project(factory,hr,[a,b])
    slot_id=p['requirements']['slots'][0]['slot_id']
    with factory.begin() as db:
        db.add(StaffingPlan(project_id=p['id'], requirements_revision=1, revision_no=1, state='validated', solver_status='OPTIMAL',
            assignments_json=json_dump([{'employee_id':a,'slot_id':slot_id,'role_name':'开发','units_per_day':60,
                'start_date':'2027-01-04','end_date':'2027-01-05','work_dates':['2027-01-04']}]),
            evidence_refs_json='[]', validation_json=json_dump({'valid':True,'blockers':[],'warnings':[]}), input_fingerprint='a'*64))
        db.add(StaffingRun(project_id=p['id'], actor_id=hr, requirements_revision=1, status='succeeded',
            result_json=json_dump({'solver_status':'OPTIMAL','diagnostics':[
                {'code':'SKILL_NOT_RECORDED','employee_id':b,'skills':['spring'],'slot_id':slot_id}]})))
    with factory() as db:
        context=projects._explanation_context(db,db.get(projects.StaffingProject,p['id']))
    assert [member['name'] for member in context['current_plan']['members']]==['合成甲']
    assert len(context['not_selected'])==1, context['not_selected']
    assert context['not_selected'][0]['name']=='合成乙'
    reasons=context['not_selected'][0]['reasons']
    assert '简历未记载必备技能' in reasons[0] and 'spring' in reasons[0]
    assert set(context['scope_names'])=={'合成甲','合成乙'}



def test_requirement_error_does_not_echo_rejected_input(setup):
    factory,hr,_,_=setup
    a,_=make_employee(factory,hr)
    with pytest.raises(HTTPException) as exc:
        projects.create_project(factory,hr,{'title':'合成错误','confirmed':True,'requirements':{'start_date':'private@example.test','employee_ids':[a]}})
    assert 'private@example.test' not in str(exc.value.detail)


def test_requirement_validation_names_the_exact_field(setup):
    factory,hr,_,_=setup
    a,_=make_employee(factory,hr)
    base={'start_date':'2027-01-04','end_date':'2027-01-05','work_dates':['2027-01-04'],'employee_ids':[a]}
    with pytest.raises(HTTPException) as exc:
        projects.create_project(factory,hr,{'title':'合成校验','confirmed':True,'requirements':{
            **base,'slots':[{'role_name':'开发','units_per_day':120,'work_dates':['2027-01-04']}]}})
    detail=exc.value.detail
    assert detail['code']=='INVALID_REQUIREMENTS'
    assert any(item['field']=='岗位 1 · 每日投入比例' and '100' in item['message'] for item in detail['errors']), detail
    assert '岗位 1 · 每日投入比例' in detail['message']
    with pytest.raises(HTTPException) as exc:
        projects.create_project(factory,hr,{'title':'合成校验2','confirmed':True,'requirements':{
            **base,'slots':[{'units_per_day':40,'work_dates':['2027-01-04']}]}})
    errors=exc.value.detail['errors']
    assert any(item['field']=='岗位 1 · 岗位名称' and item['message']=='不能为空' for item in errors), errors
    with pytest.raises(HTTPException) as exc:
        projects.create_project(factory,hr,{'title':'合成校验3','confirmed':True,'requirements':{
            **base,'slots':[{'role_name':'开发','units_per_day':40,'work_dates':[]}]}})
    errors=exc.value.detail['errors']
    assert any(item['field'].endswith('工作日') and '至少' in item['message'] for item in errors), errors


def test_cancel_allocation_releases_capacity_and_allows_archive(setup):
    factory, hr, _, _ = setup
    employee_id, _ = make_employee(factory, hr)
    project = make_project(factory, hr, [employee_id])
    with factory.begin() as db:
        row = ResourceAllocation(employee_id=employee_id, project_id=project['id'], role_slot_id='slot-a',
            start_date=date(2027, 1, 4), end_date=date(2027, 1, 5), work_dates_json='["2027-01-04"]',
            units_per_day=60, status='active', basis='测试占用')
        db.add(row); db.flush(); allocation_id, project_version = row.id, db.get(StaffingProject, project['id']).version
        db.get(StaffingProject, project['id']).business_status = 'active'
    result = proposals.cancel_allocation(factory, hr, allocation_id, {'expected_version': 1}, 'cancel-allocation')
    assert result['status'] == 'cancelled'
    with factory() as db:
        snapshot = __import__('app.services.staffing.capacity', fromlist=['snapshot']).snapshot(db, [employee_id], date(2027, 1, 4), date(2027, 1, 5), [date(2027, 1, 4)])
        assert snapshot['data'][0]['allocated_units'] == 0
        current_project = db.get(StaffingProject, project['id'])
        current_project_version = current_project.version
    archived = proposals.archive(factory, hr, project['id'], {'expected_version': current_project_version}, 'archive-after-cancel')
    assert archived['business_status'] == 'archived'


def test_adjust_allocation_creates_new_future_record_and_releases_old_units(setup):
    factory, hr, _, _ = setup
    employee_id, _ = make_employee(factory, hr, '调整员工')
    project = make_project(factory, hr, [employee_id])
    with factory.begin() as db:
        row = ResourceAllocation(employee_id=employee_id, project_id=project['id'], role_slot_id='slot-a',
            start_date=date(2027, 1, 4), end_date=date(2027, 1, 6), work_dates_json='["2027-01-04","2027-01-05"]',
            units_per_day=60, status='active', basis='测试占用')
        db.add(row); db.flush(); allocation_id = row.id
    result = proposals.adjust_allocation(factory, hr, allocation_id, {'expected_version': 1, 'units_per_day': 40,
        'work_dates': ['2027-01-04', '2027-01-05'], 'basis': '调整后的投入'}, 'adjust-allocation')
    assert result['status'] == 'active' and result['units_per_day'] == 40
    with factory() as db:
        rows = db.query(ResourceAllocation).filter_by(project_id=project['id']).order_by(ResourceAllocation.created_at).all()
        assert rows[0].status == 'superseded'
        assert rows[1].status == 'active' and rows[1].units_per_day == 40


def test_complete_project_releases_all_allocations_and_marks_project_completed(setup):
    factory, hr, _, _ = setup
    employee_id, _ = make_employee(factory, hr, '提前结束员工')
    project = make_project(factory, hr, [employee_id])
    with factory.begin() as db:
        row = ResourceAllocation(employee_id=employee_id, project_id=project['id'], role_slot_id='slot-a',
            start_date=date(2027, 1, 4), end_date=date(2027, 1, 8), work_dates_json='["2027-01-04","2027-01-05"]',
            units_per_day=100, status='active', basis='测试占用')
        db.add(row); db.flush()
        project_version = db.get(StaffingProject, project['id']).version
    result = proposals.complete_project(factory, hr, project['id'], {'expected_version': project_version}, 'complete-project')
    assert result['business_status'] == 'completed'
    with factory() as db:
        allocation = db.query(ResourceAllocation).filter_by(project_id=project['id']).one()
        assert allocation.status == 'completed'
        snapshot = __import__('app.services.staffing.capacity', fromlist=['snapshot']).snapshot(
            db, [employee_id], date(2027, 1, 4), date(2027, 1, 8), [date(2027, 1, 4), date(2027, 1, 5)])
        assert all(item['allocated_units'] == 0 for item in snapshot['data'])


def test_cancel_project_releases_all_allocations_and_marks_project_cancelled(setup):
    factory, hr, _, _ = setup
    employee_id, _ = make_employee(factory, hr, '取消项目员工')
    project = make_project(factory, hr, [employee_id])
    with factory.begin() as db:
        row = ResourceAllocation(employee_id=employee_id, project_id=project['id'], role_slot_id='slot-a',
            start_date=date(2027, 1, 4), end_date=date(2027, 1, 8), work_dates_json='["2027-01-04"]',
            units_per_day=50, status='applied', basis='测试占用')
        db.add(row); db.flush()
        project_version = db.get(StaffingProject, project['id']).version
        db.get(StaffingProject, project['id']).business_status = 'active'
    result = proposals.cancel_project(factory, hr, project['id'], {'expected_version': project_version}, 'cancel-project')
    assert result['business_status'] == 'cancelled'
    with factory() as db:
        assert db.query(ResourceAllocation).filter_by(project_id=project['id'], status='cancelled').count() == 1
