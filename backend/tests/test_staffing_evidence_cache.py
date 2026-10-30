"""Derived-fact reads are cached per source snapshot and must never hide a source change."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models import Candidate, ResumeChunk, User
from app.models.staffing import Employee, EmployeeDocument
from app.services.staffing import employees, evidence
from app.services.staffing.common import ensure_state

CONTENT = '熟悉 Python SpringBoot'


@pytest.fixture
def seeded(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'facts-cache.db'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        ensure_state(db)
        user = User(username='cache-hr', password_hash='unused', role='hr')
        db.add(user); db.flush()
        candidate = Candidate(name='缓存员工', created_by=user.id, job_category='后端开发工程师')
        employee = Employee(display_name='缓存员工', facts_status='partial')
        db.add_all([candidate, employee]); db.flush()
        parent = ResumeChunk(candidate_id=candidate.id, chunk_type='parent', content=CONTENT)
        db.add(parent); db.flush()
        child = ResumeChunk(candidate_id=candidate.id, parent_id=parent.id, chunk_type='child', content=CONTENT)
        db.add(child); db.flush()
        db.add(EmployeeDocument(employee_id=employee.id, candidate_id=candidate.id,
                                candidate_created_at=candidate.created_at, is_primary=True, linked_by=user.id))
        employee_id, child_id, actor_id = employee.id, child.id, user.id
    yield factory, employee_id, child_id, actor_id
    engine.dispose()


def _prepare(factory, actor, employee_id, child_id, content=CONTENT):
    return employees.retry_facts(factory, actor, employee_id, extractor=lambda source: {
        'complete': True,
        'facts': [{'fact_type': 'skill', 'normalized_key': 'python', 'fact': {'name': 'Python'},
                   'source_refs': [{'chunk_id': child_id, 'quote': content}]}]})


def test_repeated_reads_return_equal_results_and_do_not_share_state(seeded):
    factory, employee_id, child_id, actor = seeded
    _prepare(factory, actor, employee_id, child_id)
    with factory() as db:
        first = evidence.current_facts(db, employee_id)
        second = evidence.current_facts(db, employee_id)
        assert first == second
        first['skills'].append('tampered')
    with factory() as db:
        assert 'tampered' not in evidence.current_facts(db, employee_id)['skills']


def test_source_change_is_not_masked_by_the_cache(seeded):
    factory, employee_id, child_id, actor = seeded
    _prepare(factory, actor, employee_id, child_id)
    with factory() as db:
        assert evidence.current_facts(db, employee_id)['status'] == 'ready'
    with factory.begin() as db:
        db.query(ResumeChunk).filter_by(id=child_id).update({'content': '熟悉 Python 与 Redis'})
    with factory() as db:
        stale = evidence.current_facts(db, employee_id)
    assert stale['status'] == 'stale' and stale['skills'] == []
    _prepare(factory, actor, employee_id, child_id, content='熟悉 Python 与 Redis')
    with factory() as db:
        refreshed = evidence.current_facts(db, employee_id)
    assert refreshed['status'] == 'ready' and 'redis' in refreshed['skills']


def test_disabling_a_chunk_changes_the_snapshot(seeded):
    factory, employee_id, child_id, actor = seeded
    _prepare(factory, actor, employee_id, child_id)
    with factory.begin() as db:
        db.query(ResumeChunk).filter_by(id=child_id).update({'enabled': False})
    with factory() as db:
        assert evidence.current_facts(db, employee_id)['status'] in {'stale', 'unavailable'}
