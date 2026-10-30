import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.candidates import router
from app.api.deps import current_user
from app.db.session import get_db
from app.models import Candidate, ResumeChunk, User
from app.models.staffing import Employee
from app.services import embeddings, knowledge
from app.services.staffing import employees
from test_staffing_foundation import staffing, document


@pytest.fixture
def linked_api(staffing):
    factory, actor, _ = staffing
    source, child, _ = document(factory, actor)
    employee_id = employees.from_documents(factory, actor, [source])['results'][0]['employee_id']
    application = FastAPI()
    application.include_router(router)
    def read_db():
        with factory() as db:
            yield db
    with factory() as db:
        user = db.get(User, actor)
    application.dependency_overrides[current_user] = lambda: user
    application.dependency_overrides[get_db] = read_db
    with TestClient(application) as client:
        yield client, factory, actor, employee_id, source['candidate_id'], child


def employee_position(factory, actor, employee_id, category):
    with factory() as db:
        employee = employees.employee_dict(db, db.get(Employee, employee_id))
    return employees.update_employee(factory, actor, employee_id, {'expected_version': employee['version'],
        'expected_primary_document': employee['primary_document'], 'current_position': category})


def test_stale_cv_form_cannot_restore_previous_shared_category_or_change_metadata(linked_api):
    client, factory, actor, employee_id, candidate_id, child = linked_api
    route = f'/api/candidates/{candidate_id}'
    opened = client.get(route).json()
    employee_position(factory, actor, employee_id, '产品经理')
    stale = client.put(route, json={'job_category': opened['job_category'], 'years_experience': '8年', 'expected_updated_at': opened['updated_at']})
    assert stale.status_code == 409
    current = client.get(route).json()
    assert current['job_category'] == '产品经理' and current['years_experience'] == opened['years_experience']
    with factory() as db:
        assert db.get(ResumeChunk, child).embedding_json is None


def test_linked_category_update_requires_version_but_unlinked_compatibility_remains(linked_api):
    client, factory, actor, _, candidate_id, _ = linked_api
    route = f'/api/candidates/{candidate_id}'
    assert client.put(route, json={'job_category': '采购经理'}).status_code == 409
    opened = client.get(route).json()
    assert client.put(route, json={'job_category': '采购经理', 'expected_updated_at': opened['updated_at']}).status_code == 200
    with factory.begin() as db:
        candidate = Candidate(name='未关联员工', raw_text='', created_by=actor)
        db.add(candidate)
        db.flush()
        unlinked_id = candidate.id
    assert client.put(f'/api/candidates/{unlinked_id}', json={'job_category': '采购经理'}).status_code == 200


@pytest.mark.parametrize('index_change', [False, True])
def test_category_cas_rejects_employee_change_after_precheck_before_commit(linked_api, monkeypatch, index_change):
    client, factory, actor, employee_id, candidate_id, child = linked_api
    route = f'/api/candidates/{candidate_id}'
    opened = client.get(route).json()
    if index_change:
        monkeypatch.setattr(embeddings, 'enabled', lambda: True)
        def vectors(texts):
            employee_position(factory, actor, employee_id, '产品经理')
            return [[0.1, 0.2] for _ in texts]
        monkeypatch.setattr(embeddings, 'embed_texts', vectors)
    else:
        original = knowledge.touch_document
        def interleaved(db, candidate):
            employee_position(factory, actor, employee_id, '产品经理')
            original(db, candidate)
        monkeypatch.setattr('app.api.candidates.touch_document', interleaved, raising=False)
    payload = {'job_category': '采购经理', 'expected_updated_at': opened['updated_at']}
    if index_change:
        payload['years_experience'] = '8年'
    response = client.put(route, json=payload)
    assert response.status_code == 409
    current = client.get(route).json()
    assert current['job_category'] == '产品经理' and current['years_experience'] == opened['years_experience']
    with factory() as db:
        assert db.get(ResumeChunk, child).embedding_json is None


def test_fresh_cv_update_invalidates_previous_employee_snapshot(linked_api):
    client, factory, actor, employee_id, candidate_id, _ = linked_api
    with factory() as db:
        opened_employee = employees.employee_dict(db, db.get(Employee, employee_id))
    route = f'/api/candidates/{candidate_id}'
    opened = client.get(route).json()
    assert client.put(route, json={'job_category': '采购经理', 'expected_updated_at': opened['updated_at']}).status_code == 200
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        employees.update_employee(factory, actor, employee_id, {'expected_version': opened_employee['version'],
            'expected_primary_document': opened_employee['primary_document'], 'current_position': '产品经理'})
    assert exc.value.status_code == 409
    assert client.get(route).json()['job_category'] == '采购经理'
