"""Read-only business tool implementation; each call authenticates its own context."""
from datetime import date

import jwt
from sqlalchemy import text

from app.core.config import settings
from app.models.staffing import Employee, StaffingResourceState, ResourceAllocation
from app.models.entities import utcnow
from app.services.agent import redact_contacts
from app.services import embeddings
from app.services.staffing import evidence, capacity, runs
from app.services.staffing.common import require_actor, require_project, json_load, fail


def authorize(db, project_id, meta):
    try:
        token = (meta or {}).get('talentfit/authorization')
        claims = jwt.decode(token, settings.jwt_secret, algorithms=['HS256'], audience='talentfit-resources',
                            issuer='talentfit-worker', options={'require': ['exp', 'iat', 'sub', 'run_id', 'project_id', 'fence']})
        if claims['project_id'] != project_id:
            raise ValueError('scope')
        actor = require_actor(db, int(claims['sub']), 'hr')
    except Exception:
        fail(403, 'SCOPE_DENIED', '工具调用授权缺失、失效或不属于此项目')
    try:
        run = runs._authorized(db, claims['run_id'], claims['fence'])
        if run.actor_id != actor.id or run.project_id != project_id:
            raise runs.LeaseLost()
    except runs.LeaseLost:
        fail(409, 'STALE_INPUT', '运行已取消、过期或需求已变更')
    project = require_project(db, project_id)
    requirements = json_load(project.requirements_json, {})
    return project, requirements


def _scope(db, requirements, employee_ids):
    if not isinstance(employee_ids, list) or not 1 <= len(employee_ids) <= 20 or len(employee_ids) != len(set(employee_ids)):
        fail(422, 'INVALID_ARGUMENT', '每次请选择1至20名不同员工')
    allowed = set(requirements.get('employee_ids', []))
    if not set(employee_ids).issubset(allowed):
        fail(403, 'SCOPE_DENIED', '员工不属于已确认项目范围')
    active = {row.id for row in db.query(Employee).filter(Employee.id.in_(employee_ids), Employee.employment_status == 'active')}
    if active != set(employee_ids):
        fail(409, 'STALE_INPUT', '员工状态已变更')


def _envelope(db, data, kind, *, warnings=None, cursor=None, retrieval_mode=None):
    state = db.get(StaffingResourceState, 1)
    result = {'schema_version': '1', 'data': data, 'source_kind': kind, 'observed_at': utcnow().isoformat(),
              'data_revision': state.revision if state else 0, 'warnings': warnings or [], 'next_cursor': cursor}
    if retrieval_mode is not None:
        result['retrieval_mode'] = retrieval_mode
    return result


def execute_tool(factory, name, args, meta):
    with factory() as db:
        # This process never writes; even a mistaken future ORM flush is rejected by SQLite.
        db.execute(text('PRAGMA query_only=ON'))
        project, req = authorize(db, args['project_id'], meta)
        if name == 'search_employees':
            if args['role_slot_id'] not in {slot['slot_id'] for slot in req.get('slots', [])}:
                fail(403, 'SCOPE_DENIED', '名额不属于本项目需求')
            limit = args.get('limit', 20)
            try:
                offset = int(args.get('cursor') or 0)
            except (TypeError, ValueError):
                fail(422, 'INVALID_ARGUMENT', '无效分页标识')
            if not 1 <= limit <= 20 or not 0 <= offset <= 100:
                fail(422, 'INVALID_ARGUMENT', '每页最多20名员工')
            query = args.get('query', '').casefold().strip()[:200]
            people = db.query(Employee).filter(Employee.id.in_(req.get('employee_ids', [])),
                Employee.employment_status == 'active').order_by(Employee.id).all()
            rows = []
            for person in people:
                facts = evidence.current_facts(db, person.id)
                if query and query not in (' '.join(facts['skills']) + person.display_name).casefold():
                    continue
                rows.append({'employee_id': person.id, 'display_name': redact_contacts(person.display_name),
                             'skills': facts['skills'], 'document_link_id': facts['document_link_id'],
                             'facts_status': facts['status'], 'source_fingerprint': facts['source_fingerprint']})
            return _envelope(db, rows[offset:offset + limit], 'effective_primary_resume',
                             cursor=str(offset + limit) if len(rows) > offset + limit else None)
        if name == 'get_employee_evidence':
            _scope(db, req, args['employee_ids'])
            skills = args.get('skill_names', [])
            if len(skills) > 100 or any(len(s) > 160 for s in skills):
                fail(422, 'INVALID_ARGUMENT', '技能查询范围过大')
            facts = [evidence.current_facts(db, identity) for identity in args['employee_ids']]
            snapshots = [evidence.source_snapshot(db, identity) for identity in args['employee_ids']]
            candidate_ids = [item['candidate_id'] for item in snapshots if item]
            warnings, mode = [], 'not_requested'
            if skills and candidate_ids:
                status = embeddings.index_status(db, candidate_ids=candidate_ids)
                mode = status['mode']
                if status['pending_chunks']:
                    warnings.append('部分启用分段尚未建立向量索引，请由人事补建。')
            # current_facts returns only verified short refs; it never returns whole source chunks.
            for item in facts:
                item.pop('raw_text', None)
                for fact in item.get('facts', []):
                    for ref in fact.get('source_refs', []):
                        if 'quote' in ref:
                            ref['quote'] = redact_contacts(ref['quote'])[:320]
            return _envelope(db, facts, 'effective_primary_resume', warnings=warnings, retrieval_mode=mode)
        if name == 'get_capacity':
            _scope(db, req, args['employee_ids'])
            try:
                start, end = date.fromisoformat(args['start_date']), date.fromisoformat(args['end_date'])
                if not date.fromisoformat(req['start_date']) <= start < end <= date.fromisoformat(req['end_date']) or (end-start).days > 90:
                    raise ValueError()
            except (TypeError, KeyError, ValueError):
                fail(403, 'SCOPE_DENIED', '日期必须属于项目范围且不超过90天')
            result = capacity.snapshot(db, args['employee_ids'], start, end,
                work_dates=[date.fromisoformat(d) for d in req.get('work_dates', []) if start <= date.fromisoformat(d) < end])
            return result
        if name == 'get_project_context':
            related = args.get('related_project_ids', [])
            if len(related) > 20 or len(set(related)) != len(related):
                fail(422, 'INVALID_ARGUMENT', '关联项目范围过大')
            allocations = db.query(ResourceAllocation).filter(
                ResourceAllocation.employee_id.in_(req.get('employee_ids', [])),
                ResourceAllocation.status.in_(('active','applied')),
                ResourceAllocation.start_date < project.end_date, ResourceAllocation.end_date > project.start_date).all()
            visible = {row.project_id for row in allocations} | {project.id}
            if not set(related).issubset(visible):
                fail(403, 'SCOPE_DENIED', '仅可读取与此项目资源冲突有关的项目')
            rows = [{'project_id': project.id, 'title': redact_contacts(project.title), 'start_date': project.start_date.isoformat(),
                     'end_date': project.end_date.isoformat(), 'requirements_revision': project.requirements_revision}]
            for identity in related:
                if identity == project.id:
                    continue
                other = require_project(db, identity)
                rows.append({'project_id': other.id, 'title': redact_contacts(other.title),
                    'start_date': other.start_date.isoformat(), 'end_date': other.end_date.isoformat(),
                    'allocations': [{'employee_id': row.employee_id, 'units_per_day': row.units_per_day,
                                     'work_dates': json_load(row.work_dates_json, [])}
                                    for row in allocations if row.project_id == identity]})
            return _envelope(db, rows, 'project_resource_ledger')
        fail(403, 'SCOPE_DENIED', '工具不在允许的只读目录中')
