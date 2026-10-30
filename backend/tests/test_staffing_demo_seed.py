"""Explicit demo initialization only; all fixtures are synthetic."""
import importlib.util
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Candidate, ResumeChunk, User
from app.db.session import Base
from app.services.staffing.common import ensure_state
from app.models.staffing import CompanyWorkDay, Employee, EmployeeCapacityDay, EmployeeDocument, ResourceAllocation, StaffingProject, StaffingResourceState


def script():
    path = Path(__file__).resolve().parents[2] / 'scripts/seed_staffing_demo.py'
    spec = importlib.util.spec_from_file_location('seed_staffing_demo', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def people(db):
    roles = ['后端开发工程师', '后端开发工程师', '前端开发工程师', '测试工程师', '产品经理', '采购经理']
    for index, role in enumerate(roles):
        candidate = Candidate(name=f'合成员工{index}', job_category=role, status='ready', created_by=2, raw_text='明确的合成履历')
        employee = Employee(display_name=candidate.name)
        db.add_all([candidate, employee]); db.flush()
        db.add(EmployeeDocument(employee_id=employee.id, candidate_id=candidate.id, candidate_created_at=candidate.created_at, is_primary=True, linked_by=2))
        parent = ResumeChunk(candidate_id=candidate.id, chunk_type='parent', content='合成父段')
        db.add(parent); db.flush()
        db.add(ResumeChunk(candidate_id=candidate.id, parent_id=parent.id, chunk_type='child', content='本人具备明确的合成项目经历'))
    ensure_state(db)
    db.commit()
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)


def test_demo_is_atomic_explicit_labeled_idempotent_and_keeps_unknown_days(db):
    factory = people(db)
    result = script().seed_demo(factory, 2)
    db.expire_all()
    assert result['initialized_employee_count'] == 6
    assert db.query(CompanyWorkDay).count() == 15
    assert db.query(EmployeeCapacityDay).count() == 60
    assert db.query(EmployeeCapacityDay).filter_by(source_kind='demo_staffing_202611', capacity_units=100).count() == 60
    assert db.query(ResourceAllocation).count() == 6
    assert all(row.title.startswith('演示_') for row in db.query(StaffingProject))
    assert all('演示' in row.basis for row in db.query(ResourceAllocation))
    assert not db.get(StaffingResourceState, 1).bootstrap_closed_at
    assert db.query(EmployeeCapacityDay).filter_by(work_date=date(2026, 11, 16)).count() == 0
    again = script().seed_demo(factory, 2)
    assert again['already_initialized']
    assert db.query(ResourceAllocation).count() == 6


@pytest.mark.parametrize('reason', ['role', 'closed', 'capacity'])
def test_demo_refuses_unapproved_or_conflicting_inputs_without_partial_writes(db, reason):
    factory = people(db)
    if reason == 'closed':
        db.get(StaffingResourceState, 1).bootstrap_closed_at = __import__('datetime').datetime.now()
    if reason == 'capacity':
        db.add(EmployeeCapacityDay(employee_id=db.query(Employee).first().id, work_date=date(2026, 11, 2), capacity_units=50))
    db.commit()
    before = db.query(EmployeeCapacityDay).count()
    with pytest.raises(HTTPException):
        script().seed_demo(factory, 1 if reason == 'role' else 2)
    db.expire_all()
    assert db.query(CompanyWorkDay).count() == 0
    assert db.query(EmployeeCapacityDay).count() == before
    assert db.query(StaffingProject).count() == db.query(ResourceAllocation).count() == 0


def test_cli_repeated_apply_preserves_backup_and_original_manifest(tmp_path, monkeypatch):
    import sys
    from app.db import session as database
    engine = create_engine(f'sqlite:///{(tmp_path / "business.db").as_posix()}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([User(id=1, username='synthetic-admin', role='admin', password_hash='disabled'),
                         User(id=2, username='synthetic-hr', role='hr', password_hash='disabled')])
        session.commit()
        people(session)
    module = script()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setattr(database, 'SessionLocal', factory)
    monkeypatch.setattr(sys, 'argv', ['seed_staffing_demo.py', '--apply'])
    module.main()
    manifest = tmp_path / 'data/demos/2026-11-staffing/manifest.json'
    first = manifest.read_bytes()
    module.main()
    assert manifest.read_bytes() == first
    backups = list((tmp_path / 'data/backups/2026-10-27-demo-initialization').glob('*.db'))
    assert len(backups) == 2
    with factory() as session:
        assert session.query(ResourceAllocation).count() == 6
    engine.dispose()
