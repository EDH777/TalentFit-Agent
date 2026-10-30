from datetime import date, datetime
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models import Candidate, ResumeChunk, User


@pytest.fixture
def staffing(tmp_path):
    from app.services.staffing.common import ensure_state
    engine = create_engine('sqlite:///' + str(tmp_path / 'staffing.db'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        ensure_state(db)
        user = User(username='staffing-hr', password_hash='unused', role='hr')
        db.add(user)
        db.flush()
        actor_id = user.id
    yield factory, actor_id, engine
    engine.dispose()


def document(factory, actor_id, name='合成员工', text='熟悉 Python。现任后端工程师。'):
    with factory.begin() as db:
        candidate = Candidate(name=name, created_by=actor_id, raw_text='原文不得用于派生', job_category='后端开发工程师')
        db.add(candidate)
        db.flush()
        parent = ResumeChunk(candidate_id=candidate.id, chunk_type='parent', content=text)
        db.add(parent)
        db.flush()
        child = ResumeChunk(candidate_id=candidate.id, parent_id=parent.id, content=text)
        db.add(child)
        db.flush()
        return {'candidate_id': candidate.id, 'candidate_created_at': candidate.created_at.isoformat()}, child.id, parent.id


def test_document_import_is_uuid_idempotent_and_same_name_needs_choice(staffing):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.capacity import snapshot
    factory, actor, _ = staffing
    first, _, _ = document(factory, actor)
    created = from_documents(factory, actor, [first])['results'][0]
    assert created['status'] == 'created'
    assert str(UUID(created['employee_id'])) == created['employee_id']
    with factory() as db:
        capacity = snapshot(db, [created['employee_id']], date(2027, 1, 4), date(2027, 1, 5))
    assert capacity['data'][0]['capacity_units'] == 100
    assert capacity['unknown_dates'] == []
    assert from_documents(factory, actor, [first])['results'][0]['employee_id'] == created['employee_id']
    second, _, _ = document(factory, actor)
    assert from_documents(factory, actor, [second])['results'][0]['status'] == 'needs_identity_choice'
    chosen = from_documents(factory, actor, [{**second, 'create_new': True}])['results'][0]
    assert chosen['employee_id'] != created['employee_id']


def test_resource_first_sql_and_unknown_capacity_and_conflict(staffing):
    from app.models.staffing import ResourceAllocation, StaffingProject
    from app.services.staffing.common import resource_write
    from app.services.staffing.employees import create_employee, update_employee
    from app.services.staffing.capacity import snapshot, set_capacity_days
    factory, actor, engine = staffing
    employee = create_employee(factory, actor, {'display_name': '容量员工'})
    with factory() as db:
        result = snapshot(db, [employee['id']], date(2027, 1, 4), date(2027, 1, 5))
    assert result['data'][0]['capacity_units'] == 100
    set_capacity_days(factory, actor, employee['id'], {'expected_version': 1, 'days': [{'work_date': '2027-01-04', 'capacity_units': 100}]})
    with resource_write(factory) as db:
        project = StaffingProject(title='合成已占用项目', created_by=actor, start_date=date(2027, 1, 4), end_date=date(2027, 1, 5))
        db.add(project)
        db.flush()
        db.add(ResourceAllocation(employee_id=employee['id'], project_id=project.id, role_slot_id='role', start_date=project.start_date, end_date=project.end_date, work_dates_json='["2027-01-04"]', units_per_day=60))
    with pytest.raises(HTTPException) as exc:
        set_capacity_days(factory, actor, employee['id'], {'expected_version': 2, 'days': [{'work_date': '2027-01-04', 'capacity_units': 50}]})
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        update_employee(factory, actor, employee['id'], {'expected_version': 2, 'employment_status': 'inactive'})
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    with resource_write(factory):
        pass
    event.remove(engine, 'before_cursor_execute', capture)
    assert statements[0].lstrip().upper().startswith('UPDATE STAFFING_RESOURCE_STATE')


def test_evidence_quotes_current_primary_only_and_source_change_rejected(staffing):
    from app.models.staffing import Employee
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    factory, actor, _ = staffing
    source, chunk_id, parent_id = document(factory, actor)
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    def extractor(source):
        return {'facts': [
            {'fact_type': 'skill', 'normalized_key': 'python', 'fact': {'name': 'Python'}, 'source_refs': [{'chunk_id': chunk_id, 'quote': '熟悉 Python'}]},
            {'fact_type': 'skill', 'normalized_key': 'java', 'fact': {'name': 'Java'}, 'source_refs': [{'chunk_id': chunk_id, 'quote': '掌握 Java'}]},
        ]}
    with factory() as db:
        prepared = prepare(db, employee_id, extractor)
    assert refresh(factory, employee_id, prepared)
    with factory() as db:
        assert current_facts(db, employee_id)['skills'] == ['python']
    with factory.begin() as db:
        db.get(ResumeChunk, parent_id).enabled = False
    with factory() as db:
        assert current_facts(db, employee_id)['status'] == 'stale'
        assert current_facts(db, employee_id)['skills'] == []
    with pytest.raises(HTTPException) as exc:
        refresh(factory, employee_id, prepared)
    assert exc.value.status_code == 409


def test_requirement_dates_and_server_owned_slot_ids():
    from app.schemas.staffing import ConfirmedRequirements, assign_slot_ids
    base = {'start_date': '2027-01-04', 'end_date': '2027-01-06', 'work_dates': ['2027-01-04'], 'employee_ids': ['employee'], 'slots': [{'role_name': '后端', 'units_per_day': 50, 'work_dates': ['2027-01-04'], 'required_skills': ['Python']}]}
    requirements = assign_slot_ids(ConfirmedRequirements.model_validate(base))
    assert UUID(requirements.slots[0].slot_id)
    with pytest.raises(ValueError):
        ConfirmedRequirements.model_validate({**base, 'end_date': '2027-01-04'})
    with pytest.raises(HTTPException):
        assign_slot_ids(requirements, existing_ids=set())


def test_evidence_skill_adjacent_chinese_and_negation(staffing):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    factory, actor, _ = staffing
    source, child, _ = document(factory, actor, text='熟悉Python开发；未使用Java。现任后端工程师。')
    employee = from_documents(factory, actor, [source])['results'][0]['employee_id']
    def extract(snapshot):
        return {'facts': [
            {'fact_type': 'skill', 'normalized_key': 'python', 'fact': {'name': 'Python'}, 'source_refs': [{'chunk_id': child, 'quote': '熟悉Python开发'}]},
            {'fact_type': 'skill', 'normalized_key': 'java', 'fact': {'name': 'Java'}, 'source_refs': [{'chunk_id': child, 'quote': 'Java'}]},
            {'fact_type': 'position', 'normalized_key': '后端工程师', 'fact': {'name': '后端工程师'}, 'source_refs': [{'chunk_id': child, 'quote': '现任后端工程师'}]},
        ]}
    with factory() as db:
        prepared = prepare(db, employee, extract)
    refresh(factory, employee, prepared)
    with factory() as db:
        result = current_facts(db, employee)
        assert result['skills'] == ['python']
        assert len(result['positions']) == 1


def test_partial_extractor_cannot_publish_complete_cache(staffing):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    factory, actor, _ = staffing
    source, child, _ = document(factory, actor)
    employee = from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory() as db:
        prepared = prepare(db, employee, lambda _: {'complete': False, 'facts': []})
    refresh(factory, employee, prepared)
    with factory() as db:
        assert current_facts(db, employee)['status'] != 'ready'


def test_document_switch_keeps_uuid_and_reused_candidate_id_never_inherits(staffing):
    from app.models.staffing import EmployeeDocument
    from app.services.staffing.employees import from_documents, set_documents
    from app.services.staffing.evidence import source_snapshot
    factory, actor, _ = staffing
    first, _, _ = document(factory, actor)
    second, _, _ = document(factory, actor, name='另一份简历')
    employee = from_documents(factory, actor, [first])['results'][0]['employee_id']
    result = set_documents(factory, actor, employee, {'expected_version': 1, 'documents': [{**first}, {**second, 'is_primary': True}]})
    assert result['id'] == employee
    assert result['primary_document']['candidate_id'] == second['candidate_id']
    with factory.begin() as db:
        candidate = db.get(Candidate, second['candidate_id'])
        candidate.created_at = datetime(2027, 1, 1)
    with factory() as db:
        assert source_snapshot(db, employee) is None
    stale = from_documents(factory, actor, [second])['results'][0]
    assert stale['status'] == 'failed'


def test_import_concurrent_same_document_is_one_employee(staffing):
    from concurrent.futures import ThreadPoolExecutor
    from app.models.staffing import Employee
    from app.services.staffing.employees import from_documents
    factory, actor, _ = staffing
    source, _, _ = document(factory, actor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(from_documents, factory, actor, [source]) for _ in range(2)]
        results = [future.result()['results'][0] for future in futures]
    assert results[0]['employee_id'] == results[1]['employee_id']
    with factory() as db:
        assert db.query(Employee).count() == 1


def test_calendar_is_explicit_and_capacity_rejects_admin(staffing):
    from app.services.staffing.capacity import set_work_calendar, work_calendar, set_capacity_days
    from app.services.staffing.employees import create_employee
    factory, actor, _ = staffing
    set_work_calendar(factory, actor, {'expected_revision': 0, 'days': [{'work_date': '2027-01-09', 'is_workday': True}]})
    with factory() as db:
        calendar = work_calendar(db, '2027-01-08', '2027-01-11')
        assert [row['work_date'] for row in calendar] == ['2027-01-08', '2027-01-09', '2027-01-10']
        assert calendar[0]['is_workday'] is True and calendar[0]['is_default'] is True
        assert calendar[1]['is_workday'] is True and calendar[1]['is_default'] is False
        assert calendar[2]['is_workday'] is False and calendar[2]['is_default'] is True
    employee = create_employee(factory, actor, {'display_name': '测试权限'})
    with factory.begin() as db:
        admin = User(username='foundation-admin', password_hash='unused', role='admin')
        db.add(admin)
        db.flush()
        admin_id = admin.id
    with pytest.raises(HTTPException) as exc:
        set_capacity_days(factory, admin_id, employee['id'], {'expected_version': 1, 'days': [{'work_date': '2027-01-09', 'capacity_units': 100}]})
    assert exc.value.status_code == 403


def test_employee_category_does_not_depend_on_skill_source(staffing):
    from app.models.staffing import Employee
    from app.services.staffing.employees import from_documents, employee_dict
    factory, actor, _ = staffing
    source, _, parent = document(factory, actor)
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory.begin() as db:
        employee = db.get(Employee, employee_id)
        employee.current_position = '失效岗位'
        employee.position_origin = 'resume'
        db.get(ResumeChunk, parent).enabled = False
    with factory() as db:
        assert employee_dict(db, db.get(Employee, employee_id))['current_position'] == '后端开发工程师'


def test_employee_api_uses_injected_fresh_factory(staffing):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.employees import router, staffing_session_factory
    from app.api.deps import current_user
    from app.db.session import get_db
    from app.models.staffing import Employee
    factory, actor, _ = staffing
    application = FastAPI()
    application.include_router(router)
    def read_db():
        with factory() as db:
            yield db
    with factory() as db:
        user = db.get(User, actor)
        application.dependency_overrides[current_user] = lambda: user
    application.dependency_overrides[get_db] = read_db
    application.dependency_overrides[staffing_session_factory] = lambda: factory
    with TestClient(application) as client:
        response = client.post('/api/staffing/employees', json={'display_name': '合成API员工'})
        assert response.status_code == 200
        employee_id = response.json()['id']
        with factory() as db:
            assert db.get(Employee, employee_id).display_name == '合成API员工'
        assert client.get('/api/staffing/setup').json()['bootstrap_closed'] is False


def test_refresh_guard_rejects_stale_worker_without_changing_cache(staffing):
    from app.models.staffing import Employee, EmployeeResumeFact
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    from app.services.staffing.common import fail
    factory, actor, _ = staffing
    source, child, _ = document(factory, actor)
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    def extractor(_):
        return {'facts': [{'fact_type': 'skill', 'normalized_key': 'python', 'fact': {'name': 'Python'}, 'source_refs': [{'chunk_id': child, 'quote': '熟悉 Python'}]}]}
    with factory() as db:
        prepared = prepare(db, employee_id, extractor)
    refresh(factory, employee_id, prepared)
    with factory() as db:
        previous_version = db.get(Employee, employee_id).version
        previous_fact_id = db.query(EmployeeResumeFact).one().id
    def rejected_guard(db):
        fail(409, 'STALE_FENCE', '运行已失去执行授权')
    with pytest.raises(HTTPException) as exc:
        refresh(factory, employee_id, prepared, guard=rejected_guard)
    assert exc.value.detail['code'] == 'STALE_FENCE'
    with factory() as db:
        assert db.get(Employee, employee_id).version == previous_version
        assert db.query(EmployeeResumeFact).one().id == previous_fact_id
        assert current_facts(db, employee_id)['skills'] == ['python']


def test_malformed_document_batch_returns_business_validation(staffing):
    from app.services.staffing.employees import from_documents
    factory, actor, _ = staffing
    with pytest.raises(HTTPException) as exc:
        from_documents(factory, actor, None)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        from_documents(factory, actor, [None])
    assert exc.value.status_code == 422


@pytest.mark.parametrize('text', [
    'Python 尚未使用。熟悉 SQL。',
    'Python: no experience. Proficient in SQL.',
    'My colleague used Python; I performed manual tests. I use SQL.',
    '同事熟悉Python，本人负责手工测试。本人熟悉SQL。',
    '项目使用Python，本人负责手工测试。本人熟悉SQL。',
    '本人负责Python项目的手工测试。本人熟悉SQL。',
    'I performed manual tests on a Python project. I use SQL.',
    "I don't use Python. I use SQL.",
    '本人没使用Python。本人熟悉SQL。',
])
def test_rejected_skill_never_enters_ready_facts_or_solver(staffing, text):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    from app.services.staffing.solver import solve_teams
    factory, actor, _ = staffing
    source, child, _ = document(factory, actor, text=text)
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    def extractor(_):
        return {'complete': True, 'facts': [
            {'fact_type': 'skill', 'normalized_key': key, 'fact': {'name': key}, 'source_refs': [{'chunk_id': child, 'quote': name}]}
            for key, name in [('python', 'Python'), ('sql', 'SQL')]]}
    with factory() as db:
        prepared = prepare(db, employee_id, extractor)
    refresh(factory, employee_id, prepared)
    with factory() as db:
        facts = current_facts(db, employee_id)
    assert facts['status'] == 'ready'
    assert facts['skills'] == ['sql']
    requirements = {'start_date': '2027-01-04', 'end_date': '2027-01-05', 'work_dates': ['2027-01-04'],
        'employee_ids': [employee_id], 'slots': [{'slot_id': 'backend', 'role_name': '后端', 'units_per_day': 50,
        'work_dates': ['2027-01-04'], 'required_skills': ['python'], 'preferred_skills': []}]}
    result = solve_teams(requirements, [{'id': employee_id, 'employment_status': 'active', 'facts': facts}],
        [{'employee_id': employee_id, 'date': '2027-01-04', 'capacity_units': 100, 'allocated_units': 0}], [])
    assert result['status'] == 'INFEASIBLE'
    assert result['teams'] == []


def test_cross_chunk_self_contradiction_preserves_affected_refs_only(staffing):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    factory, actor, _ = staffing
    source, positive, parent = document(factory, actor, text='I use Python. I use SQL.')
    with factory.begin() as db:
        negative = ResumeChunk(candidate_id=source['candidate_id'], parent_id=parent, content='I have never used Python.')
        db.add(negative)
        db.flush()
        negative_id = negative.id
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory() as db:
        prepared = prepare(db, employee_id, lambda _: {'complete': True, 'facts': [
            {'fact_type': 'skill', 'normalized_key': key, 'fact': {'name': key}, 'source_refs': [{'chunk_id': positive, 'quote': name}]}
            for key, name in [('python', 'Python'), ('sql', 'SQL')]]})
    refresh(factory, employee_id, prepared)
    with factory() as db:
        facts = current_facts(db, employee_id)
    assert facts['status'] == 'ready'
    assert facts['skills'] == ['sql']
    conflict = next(item for item in facts['facts'] if item['normalized_key'] == 'python')
    assert conflict['fact']['conflicted'] is True
    assert {ref['chunk_id'] for ref in conflict['source_refs']} == {positive, negative_id}
    assert facts['warnings']


def test_different_historical_project_technologies_do_not_conflict(staffing):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    factory, actor, _ = staffing
    source, first, parent = document(factory, actor, text='项目甲（2020）：本人使用Python完成后端开发。')
    with factory.begin() as db:
        second = ResumeChunk(candidate_id=source['candidate_id'], parent_id=parent, content='项目乙（2022）：本人使用Java，未使用Python。')
        db.add(second)
        db.flush()
        second_id = second.id
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory() as db:
        prepared = prepare(db, employee_id, lambda _: {'complete': True, 'facts': [
            {'fact_type': 'skill', 'normalized_key': 'py', 'fact': {'name': 'Python'}, 'source_refs': [{'chunk_id': first, 'quote': '本人使用Python'}]},
            {'fact_type': 'skill', 'normalized_key': 'java', 'fact': {'name': 'Java'}, 'source_refs': [{'chunk_id': second_id, 'quote': '本人使用Java'}]},
        ]})
    refresh(factory, employee_id, prepared)
    with factory() as db:
        assert current_facts(db, employee_id)['skills'] == ['java', 'python']


def test_project_technology_facts_are_available_for_skill_matching(staffing):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    factory, actor, _ = staffing
    source, child, _ = document(factory, actor, text='项目甲：本人使用 Spring Boot 和 MySQL。')
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory() as db:
        prepared = prepare(db, employee_id, lambda _: {'complete': True, 'facts': [{
            'fact_type': 'project_experience', 'normalized_key': '项目甲',
            'fact': {'name': '项目甲', 'technologies': ['Spring Boot', 'MySQL']},
            'source_refs': [{'chunk_id': child, 'quote': '项目甲：本人使用 Spring Boot 和 MySQL。'}],
        }]})
    refresh(factory, employee_id, prepared)
    with factory() as db:
        assert current_facts(db, employee_id)['skills'] == ['mysql', 'spring boot']


def test_employee_rename_does_not_change_evidence_identity(staffing):
    from app.services.staffing.employees import from_documents, update_employee
    from app.services.staffing.evidence import source_snapshot
    factory, actor, _ = staffing
    source, _, _ = document(factory, actor)
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory() as db:
        fingerprint = source_snapshot(db, employee_id)['source_fingerprint']
    renamed = update_employee(factory, actor, employee_id, {'expected_version': 1, 'display_name': '合成新姓名'})
    assert renamed['id'] == employee_id
    with factory() as db:
        assert source_snapshot(db, employee_id)['source_fingerprint'] == fingerprint


def test_explicit_conflict_is_retained_with_valid_refs_and_unrelated_skills(staffing):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    factory, actor, _ = staffing
    source, child, _ = document(factory, actor, text='本人熟悉Python。本人熟悉SQL。')
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory() as db:
        prepared = prepare(db, employee_id, lambda _: {'complete': True, 'facts': [
            {'fact_type': 'skill', 'normalized_key': 'python', 'fact': {'name': 'Python', 'conflicted': True}, 'source_refs': [{'chunk_id': child, 'quote': '本人熟悉Python'}]},
            {'fact_type': 'skill', 'normalized_key': 'sql', 'fact': {'name': 'SQL'}, 'source_refs': [{'chunk_id': child, 'quote': '本人熟悉SQL'}]},
            {'fact_type': 'skill', 'normalized_key': 'java', 'fact': {'name': 'Java', 'conflicted': True}, 'source_refs': [{'chunk_id': child, 'quote': '本人熟悉Java'}]},
        ]})
    refresh(factory, employee_id, prepared)
    with factory() as db:
        result = current_facts(db, employee_id)
    assert result['status'] == 'ready'
    assert result['skills'] == ['sql']
    assert len(result['conflicts']) == 1
    assert result['conflicts'][0]['normalized_key'] == 'python'
    assert result['conflicts'][0]['usable'] is False
    assert result['conflicts'][0]['source_refs'][0]['quote'] == '本人熟悉Python'


def test_old_extractor_version_cache_requires_refresh(staffing):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare, refresh, current_facts
    factory, actor, _ = staffing
    source, child, _ = document(factory, actor)
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory() as db:
        prepared = prepare(db, employee_id, lambda _: {'facts': [{'fact_type': 'skill', 'normalized_key': 'python', 'fact': {'name': 'Python'},
            'source_refs': [{'chunk_id': child, 'quote': '熟悉 Python'}]}]}, extractor_version='1')
    refresh(factory, employee_id, prepared)
    with factory() as db:
        result = current_facts(db, employee_id)
    assert result['status'] == 'stale'
    assert result['skills'] == []


def test_skill_quote_must_belong_to_cited_self_claim(staffing):
    from app.services.staffing.employees import from_documents
    from app.services.staffing.evidence import prepare
    factory, actor, _ = staffing
    source, child, _ = document(factory, actor, text='My colleague used Python. I use Python.')
    employee_id = from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory() as db:
        prepared = prepare(db, employee_id, lambda _: {'complete': True, 'facts': [
            {'fact_type': 'skill', 'normalized_key': 'python', 'fact': {'name': 'Python'},
                'source_refs': [{'chunk_id': child, 'quote': 'My colleague used Python'}]},
        ]})
    assert len(prepared['facts']) == 1
    assert prepared['facts'][0]['source_refs'][0]['quote'] == 'I use Python'
