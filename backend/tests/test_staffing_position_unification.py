from datetime import datetime

import pytest
from fastapi import HTTPException

from app.models import Candidate, User
from app.models.staffing import Employee
from app.services.staffing import employees, evidence
from test_staffing_foundation import staffing, document


def linked(factory, actor):
    source, child, _ = document(factory, actor)
    with factory.begin() as db:
        db.get(Candidate, source['candidate_id']).job_category = '采购经理'
    employee_id = employees.from_documents(factory, actor, [source])['results'][0]['employee_id']
    with factory() as db:
        return source, child, employees.employee_dict(db, db.get(Employee, employee_id))


def test_linked_category_is_immediate_independent_of_facts_or_legacy_override(staffing):
    factory, actor, _ = staffing
    source, _, employee = linked(factory, actor)
    assert employee['current_position'] == '采购经理'
    assert employee['position_origin'] == 'knowledge'
    assert employee['facts_status'] != 'ready'
    with factory.begin() as db:
        db.get(Employee, employee['id']).current_position = '旧的人工岗位'
        db.get(Employee, employee['id']).position_origin = 'hr'
        db.get(Candidate, source['candidate_id']).job_category = '测试工程师'
    with factory() as db:
        current = employees.employee_dict(db, db.get(Employee, employee['id']))
        assert current['current_position'] == '测试工程师'
        assert evidence.current_facts(db, employee['id'])['skills'] == []


def test_employee_edit_synchronizes_only_valid_primary_and_null_clears(staffing):
    factory, actor, _ = staffing
    source, _, employee = linked(factory, actor)
    changed = employees.update_employee(factory, actor, employee['id'], {'expected_version': employee['version'], 'expected_primary_document': employee['primary_document'], 'current_position': '产品经理'})
    assert changed['current_position'] == '产品经理' and changed['position_origin'] == 'knowledge'
    with factory() as db:
        assert db.get(Candidate, source['candidate_id']).job_category == '产品经理'
    cleared = employees.update_employee(factory, actor, employee['id'], {'expected_version': changed['version'], 'expected_primary_document': changed['primary_document'], 'current_position': None})
    assert cleared['current_position'] == ''
    with factory() as db:
        assert db.get(Candidate, source['candidate_id']).job_category is None


@pytest.mark.parametrize('invalid', ['其他', '', 42, True, ['采购经理']])
def test_new_employee_position_edits_require_category_or_null(staffing, invalid):
    factory, actor, _ = staffing
    with pytest.raises(HTTPException) as exc:
        employees.create_employee(factory, actor, {'display_name': '合成人员', 'current_position': invalid})
    assert exc.value.status_code == 422


@pytest.mark.parametrize('invalid_source', ['deleted', 'disabled', 'reused'])
def test_invalid_primary_never_leaks_or_writes_replacement_category(staffing, invalid_source):
    factory, actor, _ = staffing
    source, _, employee = linked(factory, actor)
    with factory.begin() as db:
        row = db.get(Candidate, source['candidate_id'])
        if invalid_source == 'deleted':
            row.deleted_at = datetime(2027, 1, 1)
        elif invalid_source == 'disabled':
            row.enabled = False
        else:
            row.created_at = datetime(2027, 1, 1)
        row.job_category = '硬件工程师'
    with factory() as db:
        assert employees.employee_dict(db, db.get(Employee, employee['id']))['current_position'] == ''
    with pytest.raises(HTTPException) as exc:
        employees.update_employee(factory, actor, employee['id'], {'expected_version': employee['version'], 'expected_primary_document': employee['primary_document'], 'current_position': '产品经理'})
    assert exc.value.status_code == 409
    with factory() as db:
        assert db.get(Candidate, source['candidate_id']).job_category == '硬件工程师'


def test_category_edit_snapshot_and_employee_version_reject_stale_writes_and_admin(staffing):
    factory, actor, _ = staffing
    source, _, employee = linked(factory, actor)
    with factory.begin() as db:
        db.get(Candidate, source['candidate_id']).job_category = '硬件工程师'
        admin = User(username='position-admin', password_hash='unused', role='admin')
        db.add(admin)
        db.flush()
        admin_id = admin.id
    for actor_id, version, snapshot, status in [
        (actor, employee['version'], employee['primary_document'], 409),
        (actor, 0, employee['primary_document'], 409),
        (admin_id, employee['version'], employee['primary_document'], 403),
        (actor, employee['version'], None, 409),
    ]:
        with pytest.raises(HTTPException) as exc:
            employees.update_employee(factory, actor_id, employee['id'], {'expected_version': version, 'expected_primary_document': snapshot, 'current_position': '产品经理'})
        assert exc.value.status_code == status


def test_fact_refresh_never_overwrites_current_category(staffing):
    factory, actor, _ = staffing
    source, child, employee = linked(factory, actor)
    with factory() as db:
        prepared = evidence.prepare(db, employee['id'], lambda _: {'facts': [{'fact_type': 'position', 'normalized_key': '后端工程师', 'fact': {'name': '后端工程师'}, 'source_refs': [{'chunk_id': child, 'quote': '现任后端工程师'}]}]})
    employees.update_employee(factory, actor, employee['id'], {'expected_version': employee['version'], 'expected_primary_document': employee['primary_document'], 'current_position': '测试工程师'})
    evidence.refresh(factory, employee['id'], prepared)
    with factory() as db:
        assert db.get(Candidate, source['candidate_id']).job_category == '测试工程师'
        assert db.get(Employee, employee['id']).current_position == '测试工程师'
        assert employees.employee_dict(db, db.get(Employee, employee['id']))['current_position'] == '测试工程师'
        assert len(evidence.current_facts(db, employee['id'])['positions']) == 1


def test_unlinked_legacy_hr_position_preserved_until_explicit_edit(staffing):
    factory, actor, _ = staffing
    employee = employees.create_employee(factory, actor, {'display_name': '合成人员'})
    with factory.begin() as db:
        row = db.get(Employee, employee['id'])
        row.current_position = '旧岗位名称'
        row.position_origin = 'hr'
    with factory() as db:
        assert employees.employee_dict(db, db.get(Employee, employee['id']))['current_position'] == '旧岗位名称'
    updated = employees.update_employee(factory, actor, employee['id'], {'expected_version': employee['version'], 'current_position': '采购经理'})
    assert updated['current_position'] == '采购经理' and updated['position_origin'] == 'hr'


def test_api_category_and_employee_position_share_value_and_preserve_children(staffing):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.candidates import router as candidate_router
    from app.api.employees import router as employee_router, staffing_session_factory
    from app.api.deps import current_user
    from app.db.session import get_db
    from app.models import ResumeChunk
    factory, actor, _ = staffing
    source, child, employee = linked(factory, actor)
    with factory.begin() as db:
        row = db.get(ResumeChunk, child)
        row.enabled = False
        row.embedding_json = '[0.25,0.75]'
    application = FastAPI()
    application.include_router(candidate_router)
    application.include_router(employee_router)
    def read_db():
        with factory() as db:
            yield db
    with factory() as db:
        user = db.get(User, actor)
    application.dependency_overrides[current_user] = lambda: user
    application.dependency_overrides[get_db] = read_db
    application.dependency_overrides[staffing_session_factory] = lambda: factory
    with TestClient(application) as client:
        candidate_route = f"/api/candidates/{source['candidate_id']}"
        opened = client.get(candidate_route).json()
        assert client.put(candidate_route, json={'job_category': '硬件工程师', 'expected_updated_at': opened['updated_at']}).status_code == 200
        current = client.get('/api/staffing/employees').json()[0]
        assert current['current_position'] == '硬件工程师'
        changed = client.patch(f"/api/staffing/employees/{employee['id']}", json={'expected_version': current['version'], 'expected_primary_document': current['primary_document'], 'current_position': None})
        assert changed.status_code == 200
        assert client.get(candidate_route).json()['job_category'] is None
    with factory() as db:
        row = db.get(ResumeChunk, child)
        assert row.enabled is False and row.embedding_json == '[0.25,0.75]'


def test_demo_capacity_is_labeled_and_manual_overwrite_removes_demo_source(staffing):
    from datetime import date
    from app.models.staffing import EmployeeCapacityDay
    from app.services.staffing import capacity
    factory, actor, _ = staffing
    employee = employees.create_employee(factory, actor, {'display_name': '合成排期员工'})
    with factory.begin() as db:
        db.add(EmployeeCapacityDay(employee_id=employee['id'], work_date=date(2026, 11, 2), capacity_units=100, source_kind='demo_staffing_202611'))
    with factory() as db:
        data = capacity.snapshot(db, [employee['id']], '2026-11-02', '2026-11-04')
        assert data['data'][0]['source_kind'] == 'demo_staffing_202611'
        assert data['data'][1]['source_kind'] == 'company_calendar_default'
        assert data['warnings'] == ['演示排期，仅用于功能测试，不代表实际承诺']
    capacity.set_capacity_days(factory, actor, employee['id'], {'expected_version': employee['version'], 'days': [{'work_date': '2026-11-02', 'capacity_units': 80}]})
    with factory() as db:
        data = capacity.snapshot(db, [employee['id']], '2026-11-02', '2026-11-03')
        assert data['data'][0]['source_kind'] == 'manual_capacity_ledger'
        assert data['data'][0]['remaining_units'] == 80
        assert data['warnings'] == []
