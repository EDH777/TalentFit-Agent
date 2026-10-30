"""Actual official SDK stdio protocol, independent temp DB and synthetic staff."""
import asyncio
from datetime import date
import json

import jwt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import Base
from app.models import User
from app.models.staffing import Employee, StaffingProject


@pytest.fixture
def mcp_context(tmp_path):
    from app.services.staffing.common import ensure_state
    from app.services.staffing import runs
    path = tmp_path / 'mcp.db'
    engine = create_engine('sqlite:///' + path.as_posix(), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        ensure_state(db)
        db.add(User(id=2, username='synthetic-hr', password_hash='unused', role='hr'))
        person = Employee(display_name='合成员工')
        db.add(person)
        db.flush()
        req = {'employee_ids': [person.id], 'start_date': '2026-11-02', 'end_date': '2026-11-04',
               'work_dates': ['2026-11-02', '2026-11-03'], 'slots': [{'slot_id': 'slot-a'}]}
        project = StaffingProject(title='合成项目', created_by=2, start_date=date(2026,11,2), end_date=date(2026,11,4),
                                  requirements_revision=1, requirements_json=json.dumps(req))
        db.add(project)
        db.flush()
        identity, employee_id = project.id, person.id
    runs.create_run(factory, 2, identity, {'expected_version': 1, 'requirements_revision': 1})
    lease = runs.claim_run(factory, 'integration-test')
    yield factory, lease, employee_id, 'sqlite:///' + path.as_posix()
    engine.dispose()


def test_real_stdio_discovery_authorization_and_unknown_capacity(mcp_context):
    from app.services.staffing.mcp_client import StaffingMcpClient, authorization_meta
    factory, lease, employee_id, url = mcp_context

    async def scenario():
        async with StaffingMcpClient(database_url=url) as client:
            assert set(client.tool_names) == {'search_employees', 'get_employee_evidence', 'get_capacity', 'get_project_context'}
            metadata = authorization_meta(lease)
            result = await client.call('get_capacity', {'project_id': lease['project_id'], 'employee_ids': [employee_id],
                'start_date': '2026-11-02', 'end_date': '2026-11-04'}, metadata)
            assert result['data'][0]['capacity_units'] == 100
            assert result['source_kind'] == 'manual_capacity_ledger'
            assert result['schema_version'] == '1' and result['data_revision'] >= 0
            raw = await client.session.call_tool('get_capacity', {'project_id': lease['project_id'], 'employee_ids': [employee_id],
                'start_date': '2026-11-02', 'end_date': '2026-11-04'})
            assert raw.is_error and 'SCOPE_DENIED' in str(raw.content)
            bad = {'talentfit/authorization': metadata['talentfit/authorization'][:-5] + 'xxxxx'}
            raw = await client.session.call_tool('get_project_context', {'project_id': lease['project_id'], 'related_project_ids': []}, meta=bad)
            assert raw.is_error and 'SCOPE_DENIED' in str(raw.content)
            raw = await client.session.call_tool('get_capacity', {'project_id': lease['project_id'], 'employee_ids': ['outside'],
                'start_date': '2026-11-02', 'end_date': '2026-11-04'}, meta=metadata)
            assert raw.is_error and 'SCOPE_DENIED' in str(raw.content)
            # Per-call metadata does not become a global current user after a previous success.
            raw = await client.session.call_tool('get_project_context', {'project_id': lease['project_id'], 'related_project_ids': []})
            assert raw.is_error
    asyncio.run(scenario())


def test_context_cannot_reauthorize_cancelled_worker(mcp_context):
    from app.services.staffing.mcp_client import StaffingMcpClient, authorization_meta
    from app.services.staffing import runs
    factory, lease, employee_id, url = mcp_context

    async def scenario():
        async with StaffingMcpClient(database_url=url) as client:
            metadata = authorization_meta(lease)
            current = runs.get_run(factory, lease['id'], 2)
            runs.cancel_run(factory, 2, lease['id'], {'expected_version': current['version'], 'requirements_revision': 1})
            raw = await client.session.call_tool('search_employees', {'project_id': lease['project_id'], 'role_slot_id': 'slot-a', 'query':'', 'limit':20}, meta=metadata)
            assert raw.is_error and 'STALE_INPUT' in str(raw.content)
    asyncio.run(scenario())
