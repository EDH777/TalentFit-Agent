from datetime import date, timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.models.staffing import StaffingProject, StaffingRun
from app.models.entities import utcnow


@pytest.fixture
def runtime(db):
    from app.services.staffing.common import ensure_state
    ensure_state(db)
    project = StaffingProject(title='合成项目', created_by=2, start_date=date(2026, 11, 2),
                              end_date=date(2026, 11, 4), requirements_revision=1,
                              requirements_json='{"employee_ids":[],"slots":[]}')
    db.add(project)
    db.commit()
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False), project


def test_claim_once_events_are_ordered_and_old_fence_is_rejected(runtime):
    from app.services.staffing import runs
    factory, project = runtime
    run = runs.create_run(factory, 2, project.id, {'expected_version': 1, 'requirements_revision': 1})
    with pytest.raises(HTTPException) as exc:
        runs.create_run(factory, 2, project.id, {'expected_version': 1, 'requirements_revision': 1})
    assert exc.value.status_code == 409
    lease = runs.claim_run(factory, 'worker-a')
    assert lease['id'] == run['id'] and not runs.claim_run(factory, 'worker-b')
    runs.append_event(factory, lease['id'], lease['fence_token'], 'node_started',
                      {'node': {'id': 'test-node', 'node_id': 'test', 'title': '测试', 'status': 'running',
                                'started_at': utcnow().isoformat(), 'prompt': 'SECRET'}})
    events = runs.list_events(factory, run['id'], 0)
    assert [e['seq'] for e in events] == list(range(1, len(events)+1))
    assert 'SECRET' not in str(events)
    current = runs.get_run(factory, run['id'], 2)
    runs.cancel_run(factory, 2, run['id'], {'expected_version': current['version'], 'requirements_revision': 1})
    assert not runs.is_authorized(factory, run['id'], lease['fence_token'])
    with pytest.raises(runs.LeaseLost):
        runs.append_event(factory, run['id'], lease['fence_token'], 'plan_saved', {'plan_ids': ['late']})
    assert runs.cancel_run(factory, 2, run['id'], {'expected_version': 1, 'requirements_revision': 1})['status'] == 'cancelled'


def test_expired_run_gets_new_uuid_thread_and_only_one_automatic_replacement(runtime):
    from app.services.staffing import runs
    factory, project = runtime
    run = runs.create_run(factory, 2, project.id, {'expected_version': 1, 'requirements_revision': 1})
    lease = runs.claim_run(factory, 'old')
    with factory.begin() as db:
        db.get(StaffingRun, run['id']).lease_until = utcnow() - timedelta(seconds=1)
    replacements = runs.replace_expired(factory)
    assert len(replacements) == 1 and replacements[0]['id'] != run['id']
    assert replacements[0]['replaces_run_id'] == run['id']
    assert not runs.is_authorized(factory, run['id'], lease['fence_token'])
    next_lease = runs.claim_run(factory, 'new')
    assert next_lease['checkpoint_thread_id'] != lease['checkpoint_thread_id']
    with factory.begin() as db:
        db.get(StaffingRun, next_lease['id']).lease_until = utcnow() - timedelta(seconds=1)
    assert runs.replace_expired(factory) == []
    assert runs.get_run(factory, next_lease['id'], 2)['status'] == 'failed'


def test_only_safely_persisted_waiting_run_can_resume(runtime):
    from app.services.staffing import runs
    factory, project = runtime
    run = runs.create_run(factory, 2, project.id, {'expected_version': 1, 'requirements_revision': 1})
    lease = runs.claim_run(factory, 'worker')
    with pytest.raises(HTTPException):
        runs.resume_run(factory, 2, run['id'], {'expected_version': lease['version'], 'requirements_revision': 1})
    waiting = runs.mark_waiting(factory, run['id'], lease['fence_token'], [{'code':'CAPACITY_UNKNOWN','message':'请维护工时'}])
    assert waiting['status'] == 'waiting_input'
    resumed = runs.resume_run(factory, 2, run['id'], {'expected_version': waiting['version'], 'requirements_revision': 1})
    assert resumed['status'] == 'queued'
    active = runs.claim_run(factory, 'worker-2')
    assert active['checkpoint_thread_id'] == lease['checkpoint_thread_id']
    assert active['fence_token'] > lease['fence_token'] and active['attempt'] == 2


def test_changed_requirements_or_disabled_actor_prevents_new_claim(runtime):
    from app.services.staffing import runs
    factory, project = runtime
    run = runs.create_run(factory, 2, project.id, {'expected_version': 1, 'requirements_revision': 1})
    with factory.begin() as db:
        db.get(StaffingProject, project.id).requirements_revision = 2
    assert runs.claim_run(factory, 'worker') is None
    assert runs.get_run(factory, run['id'], 2)['status'] == 'failed'


def test_idle_task_polling_does_not_change_business_resource_revision(runtime):
    from app.models.staffing import StaffingResourceState
    from app.services.staffing import runs
    factory, _ = runtime
    with factory() as db:
        before = db.get(StaffingResourceState,1).revision
    assert runs.claim_run(factory,'idle') is None
    assert runs.replace_expired(factory)==[]
    with factory() as db:
        assert db.get(StaffingResourceState,1).revision==before
