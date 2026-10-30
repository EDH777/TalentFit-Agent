"""Versioned proposals, current evidence display and atomic allocation actions."""
from datetime import date
from hashlib import sha256

from pydantic import ValidationError

from app.models.entities import utcnow
from app.models.staffing import Employee, ResourceAllocation, StaffingAction, StaffingPlan, StaffingResourceState, StaffingRun, new_id
from app.schemas.staffing import Assignment
from app.services.staffing import capacity, evidence, projects, solver
from app.services.staffing.common import business_today, fail, json_dump, json_load, require_actor, require_project, resource_write

RULE_VERSION = 'staffing-1'


def _hash(value):
    return sha256(json_dump(value).encode()).hexdigest()


def _facts_digest(facts):
    return _hash({'status': facts.get('status'), 'source_fingerprint': facts.get('source_fingerprint'), 'document_link_id': facts.get('document_link_id'),
        'skills': sorted(facts.get('skills', [])), 'facts': sorted([
            {k: v for k, v in item.items() if k != 'id'} for item in facts.get('facts', [])], key=json_dump)})


def input_fingerprint(input_data, assignments=None):
    """Only selected people/days matter to approval; unrelated edits stay harmless."""
    req = input_data['requirements']
    days = {}
    if assignments is None:
        days = {eid: set(req['work_dates']) for eid in req['employee_ids']}
    else:
        slots = {s['slot_id']: s for s in req['slots']}
        for item in assignments:
            days.setdefault(item['employee_id'], set()).update(slots.get(item['slot_id'], {}).get('work_dates', []))
    employees = [{ 'employee_id': e['employee_id'], 'employment_status': e['employment_status'], 'facts_digest': _facts_digest(e['facts'])}
        for e in input_data['employees'] if e['employee_id'] in days]
    capacities = [{k: row[k] for k in ('employee_id', 'date', 'capacity_units', 'allocated_units', 'version')}
        for row in input_data['capacities'] if row['employee_id'] in days and row['date'] in days[row['employee_id']]]
    allocations = [{**row, 'work_dates': sorted(set(row['work_dates']) & days[row['employee_id']])}
        for row in input_data.get('allocation_sources', []) if row['employee_id'] in days and set(row['work_dates']) & days[row['employee_id']]]
    calendar = [row for row in input_data.get('calendar', []) if row['work_date'] in set().union(*days.values())] if days else []
    return _hash({'rule_version': RULE_VERSION, 'project_id': input_data['project_id'], 'requirements_revision': input_data['requirements_revision'], 'requirements': req,
        'employees': sorted(employees, key=lambda x: x['employee_id']), 'capacities': sorted(capacities, key=lambda x: (x['employee_id'], x['date'])),
        'allocations': sorted(allocations, key=lambda x: x['id']), 'calendar': sorted(calendar, key=lambda x: x['work_date'])})


def build_input(db, project):
    req = json_load(project.requirements_json, {})
    if project.origin_kind != 'managed' or not req or project.requirements_revision < 1:
        fail(422, 'REQUIREMENTS_UNCONFIRMED', '请先确认该组队项目的结构化需求')
    people, diagnostics = [], []
    for eid in req['employee_ids']:
        employee = db.get(Employee, eid, populate_existing=True)
        if employee:
            facts = evidence.current_facts(db, eid)
            people.append({'employee_id': eid, 'display_name': employee.display_name, 'employee_no': employee.employee_no,
                'employment_status': employee.employment_status, 'version': employee.version, 'facts': facts})
        else:
            people.append({'employee_id': eid, 'display_name': '已移除员工', 'employee_no': None, 'employment_status': 'unavailable', 'version': 0,
                'facts': {'status': 'unavailable', 'skills': [], 'facts': [], 'source_fingerprint': '', 'document_link_id': None}})
            diagnostics.append({'code': 'EMPLOYEE_UNAVAILABLE', 'employee_id': eid,
                'message': '项目范围内的员工已不可用，请重新确认项目员工范围后再次生成'})
    snapshot = capacity.snapshot(db, req['employee_ids'], req['start_date'], req['end_date'], req['work_dates'])
    allocation_sources = []
    for row in db.query(ResourceAllocation).filter(ResourceAllocation.employee_id.in_(req['employee_ids']), ResourceAllocation.status.in_(['active', 'applied']),
            ResourceAllocation.start_date < project.end_date, ResourceAllocation.end_date > project.start_date).all():
        allocation_sources.append({'id': row.id, 'employee_id': row.employee_id, 'project_id': row.project_id, 'units_per_day': row.units_per_day,
            'version': row.version, 'status': row.status, 'work_dates': json_load(row.work_dates_json, [])})
    calendar = capacity.work_calendar(db, req['start_date'], req['end_date'])
    valid_days = {row['work_date'] for row in calendar if row['is_workday']}
    if not set(req['work_dates']) <= valid_days:
        diagnostics.append({'code': 'UNKNOWN_WORK_CALENDAR', 'dates': sorted(set(req['work_dates'])-valid_days),
            'message': '项目工作日已不在公司工作日历中，请先维护公司工作日'})
    data = {'schema_version': '1', 'project_id': project.id, 'requirements_revision': project.requirements_revision,
        'requirements': req, 'employees': people, 'capacities': snapshot['data'], 'allocations': [], 'allocation_sources': allocation_sources,
        'calendar': calendar, 'observed_at': snapshot['observed_at'], 'data_revision': snapshot['data_revision'], 'diagnostics': diagnostics}
    data['fingerprint'] = input_fingerprint(data)
    return data


def _validate(input_data, assignments):
    validation = solver.validate_assignments(input_data['requirements'], input_data['employees'], input_data['capacities'], input_data['allocations'], assignments)
    validation['blockers'].extend(input_data['diagnostics'])
    validation['valid'] = not validation['blockers']
    validation['input_fingerprint'] = input_fingerprint(input_data, assignments)
    return validation


def _evidence_refs(input_data, assignments):
    selected = {a['employee_id'] for a in assignments}
    return [{'employee_id': e['employee_id'], 'source_fingerprint': e['facts'].get('source_fingerprint'),
        'document_link_id': e['facts'].get('document_link_id'), 'facts_digest': _facts_digest(e['facts']),
        'refs': [{k: v for k, v in ref.items() if k != 'quote'} for fact in e['facts'].get('facts', []) for ref in fact.get('source_refs', [])]}
        for e in input_data['employees'] if e['employee_id'] in selected]


def _next_revision(db, project_id):
    previous = db.query(StaffingPlan).filter_by(project_id=project_id).order_by(StaffingPlan.revision_no.desc()).first()
    return previous.revision_no+1 if previous else 1


def save_generated_plans(db, project, run_id, result, input_data):
    projects._editable(db, project)
    current = build_input(db, project)
    if input_data['requirements_revision'] != project.requirements_revision or current['fingerprint'] != input_data['fingerprint']:
        fail(409, 'STALE_INPUT', '生成期间项目范围、简历依据或资源台账已变化，请重新生成')
    teams = result.get('teams', [])
    if len(teams) > 2:
        fail(422, 'INVALID_PROPOSAL', '每次最多保存两套不同团队')
    seen, plans = set(), []
    revision = _next_revision(db, project.id)
    for team in teams:
        raw = team['assignments']
        validation = _validate(current, raw)
        if not validation['valid'] or team['solver_status'] not in ('OPTIMAL', 'FEASIBLE'):
            fail(422, 'INVALID_PROPOSAL', '方案未通过独立硬约束校验', blockers=validation['blockers'])
        members = frozenset(a['employee_id'] for a in raw)
        if members in seen:
            fail(422, 'DUPLICATE_TEAM', '成员相同的岗位排列不能算作第二套团队')
        seen.add(members)
        assignments = solver.complete_assignments(current['requirements'], raw)
        plan = StaffingPlan(project_id=project.id, run_id=run_id, requirements_revision=project.requirements_revision, revision_no=revision,
            assignments_json=json_dump(assignments), evidence_refs_json=json_dump(_evidence_refs(current, assignments)), solver_status=team['solver_status'],
            validation_json=json_dump({**validation, 'diagnostics': result.get('diagnostics', []), 'objectives': team.get('objectives', []), 'optimal': team.get('optimal', False)}),
            input_fingerprint=validation['input_fingerprint'], state='draft')
        db.add(plan); plans.append(plan); revision += 1
    db.flush()
    return plans


def plan_dict(db, plan):
    project = require_project(db, plan.project_id)
    req = json_load(project.requirements_json, {})
    slots = {s['slot_id']: s for s in req.get('slots', [])}
    assignments = json_load(plan.assignments_json, [])
    refs = {r['employee_id']: r for r in json_load(plan.evidence_refs_json, [])}
    shown_evidence = []
    shown_assignments = []
    for item in assignments:
        eid = item['employee_id']
        employee = db.get(Employee, eid)
        current = evidence.current_facts(db, eid) if employee else {'status': 'unavailable', 'facts': [], 'skills': []}
        recorded = refs.get(eid, {})
        effective = current.get('status') == 'ready' and recorded.get('source_fingerprint') == current.get('source_fingerprint') and recorded.get('facts_digest') == _facts_digest(current)
        shown_evidence.append({'employee_id': eid, 'status': 'current' if effective else 'stale', 'source_status': current.get('status'),
            'message': '' if effective else '简历依据已变更，需重新生成/校验', 'document_link_id': current.get('document_link_id'),
            'source_fingerprint': current.get('source_fingerprint'), 'skills': current.get('skills', []) if effective else [],
            'facts': current.get('facts', []) if effective else [], 'source_times': sorted({str(ref['created_at']) for fact in current.get('facts', []) for ref in fact.get('source_refs', []) if ref.get('created_at')}) if effective else []})
        shown_assignments.append({**item, 'display_name': employee.display_name if employee else '已移除员工',
            'employee_no': employee.employee_no if employee else None, 'role_name': item.get('role_name', slots.get(item['slot_id'], {}).get('role_name', '历史角色'))})
    rows = []
    if assignments:
        all_days = sorted({d for item in assignments for d in item['work_dates']})
        if all_days:
            snap = capacity.snapshot(db, sorted({a['employee_id'] for a in assignments}), min(a['start_date'] for a in assignments), max(a['end_date'] for a in assignments), all_days)
            by_employee = {a['employee_id']: a for a in assignments}
            for row in snap['data']:
                item = by_employee[row['employee_id']]
                if row['date'] in item['work_dates']:
                    requested = 0 if plan.state == 'applied' else item['units_per_day']
                    rows.append({**row, 'requested_units': requested, 'after_remaining_units': None if row['remaining_units'] is None else row['remaining_units']-requested})
    result = {field: getattr(plan, field) for field in ('id', 'project_id', 'run_id', 'requirements_revision', 'revision_no', 'solver_status', 'input_fingerprint', 'state', 'version')}
    validation = json_load(plan.validation_json, {})
    result.update(assignments=shown_assignments, evidence=shown_evidence, validation=validation, diagnostics=validation.get('diagnostics', []), capacity=rows,
        created_at=plan.created_at.isoformat() if plan.created_at else None, updated_at=plan.updated_at.isoformat() if plan.updated_at else None)
    return result


def list_plans(db, project_id):
    require_project(db, project_id)
    return [plan_dict(db, p) for p in db.query(StaffingPlan).filter_by(project_id=project_id).order_by(StaffingPlan.revision_no).all()]


def _plan(db, plan_id):
    plan = db.get(StaffingPlan, plan_id)
    if not plan:
        fail(404, 'NOT_FOUND', '方案不存在')
    return plan, require_project(db, plan.project_id)


def _current_plan(db, plan, project):
    projects._editable(db, project)
    if plan.requirements_revision != project.requirements_revision or plan.state == 'superseded':
        fail(409, 'STALE_REQUIREMENTS', '该方案属于旧需求，请重新生成')


def validate_plan(factory, actor_id, plan_id, payload):
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        plan, project = _plan(db, plan_id)
        projects._version(plan, payload)
        _current_plan(db, plan, project)
        if plan.state not in ('draft', 'validated', 'invalidated'):
            fail(409, 'INVALID_PLAN_STATE', '只有未提交的方案可以重新校验')
        inp = build_input(db, project)
        assignments = json_load(plan.assignments_json, [])
        validation = _validate(inp, assignments)
        plan.validation_json = json_dump(validation)
        plan.input_fingerprint = validation['input_fingerprint']
        plan.evidence_refs_json = json_dump(_evidence_refs(inp, assignments))
        plan.state = 'validated' if validation['valid'] else 'invalidated'
        plan.version += 1; plan.updated_at = utcnow()
        db.flush()
        return plan_dict(db, plan)


def variant(factory, actor_id, plan_id, payload):
    try:
        assignments = [Assignment.model_validate(item).model_dump(mode='json', exclude_none=True) for item in payload.get('assignments', [])]
    except (ValidationError, TypeError):
        fail(422, 'INVALID_ASSIGNMENTS', '替换方案字段非法')
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        original, project = _plan(db, plan_id)
        projects._version(original, payload)
        _current_plan(db, original, project)
        inp = build_input(db, project)
        validation = _validate(inp, assignments)
        # Ineligible replacements may be saved as draft, but shape/timing tampering is rejected.
        if any(b['code'] in ('INVALID_SLOT', 'MISSING_SLOTS', 'ASSIGNMENT_MISMATCH', 'INVALID_REQUIREMENTS') for b in validation['blockers']):
            fail(422, 'INVALID_ASSIGNMENTS', '请为所有名额提交明确人员，日期和投入必须保持已确认需求', blockers=validation['blockers'])
        assignments = solver.complete_assignments(inp['requirements'], assignments)
        plan = StaffingPlan(project_id=project.id, requirements_revision=project.requirements_revision, revision_no=_next_revision(db, project.id),
            assignments_json=json_dump(assignments), evidence_refs_json=json_dump(_evidence_refs(inp, assignments)), solver_status='FEASIBLE' if validation['valid'] else 'UNKNOWN',
            validation_json=json_dump(validation), input_fingerprint=validation['input_fingerprint'], state='draft')
        db.add(plan); db.flush()
        return plan_dict(db, plan)


def _replay(db, actor_id, action, key, payload):
    if not key or len(key) > 160:
        fail(422, 'IDEMPOTENCY_KEY_REQUIRED', '正式操作须提供1至160字符的幂等键')
    digest = _hash(payload)
    previous = db.query(StaffingAction).filter_by(actor_id=actor_id, action_type=action, request_key=key).first()
    if previous:
        if previous.payload_hash != digest:
            fail(409, 'IDEMPOTENCY_CONFLICT', '同一幂等键不能用于不同请求内容')
        result = json_load(previous.result_json, {})
        dynamic_plan_id = result.pop('_dynamic_plan_id', None)
        if dynamic_plan_id:
            current_plan, _ = _plan(db, dynamic_plan_id)
            current = plan_dict(db, current_plan)
            # Preserve the action result while resolving evidence only from live sources.
            result.update(evidence=current['evidence'], capacity=current['capacity'])
        return digest, result
    return digest, None


def _record(db, actor_id, action, key, digest, result, project_id=None, plan_id=None):
    stored = {k: v for k, v in result.items() if k not in ('evidence', 'capacity')} if plan_id else result
    if plan_id:
        stored = {**stored, '_dynamic_plan_id': plan_id}
    db.add(StaffingAction(actor_id=actor_id, action_type=action, request_key=key, payload_hash=digest,
        project_id=project_id, plan_id=plan_id, result_json=json_dump(stored)))


def _invalidate(plan, validation):
    plan.state = 'invalidated'; plan.version += 1; plan.updated_at = utcnow()
    plan.validation_json = json_dump(validation)


def _action(factory, actor_id, plan_id, payload, request_key, action):
    stale_error, result = None, None
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr' if action == 'submit' else 'admin')
        digest, prior = _replay(db, actor_id, action, request_key, {'plan_id': plan_id, **payload})
        if prior is not None:
            return prior
        plan, project = _plan(db, plan_id)
        projects._version(plan, payload)
        _current_plan(db, plan, project)
        expected_state = 'validated' if action == 'submit' else 'submitted'
        if plan.state != expected_state:
            fail(409, 'INVALID_PLAN_STATE', '方案状态已变化，请刷新后操作', current_state=plan.state)
        if payload.get('input_fingerprint') != plan.input_fingerprint:
            fail(409, 'STALE_INPUT', '请求指纹与待操作方案不一致')
        if action == 'approve' and not db.get(StaffingResourceState, 1).bootstrap_closed_at:
            fail(409, 'BOOTSTRAP_OPEN', '请管理员先核对并关闭基础台账初始化')
        if action == 'reject':
            plan.state = 'rejected'; plan.version += 1; plan.updated_at = utcnow()
        else:
            inp = build_input(db, project)
            assignments = json_load(plan.assignments_json, [])
            validation = _validate(inp, assignments)
            if not validation['valid'] or validation['input_fingerprint'] != plan.input_fingerprint:
                _invalidate(plan, validation)
                stale_error = {'code': 'STALE_INPUT', 'message': '相关简历依据或资源已变化，请重新校验并提交', 'blockers': validation['blockers']}
            elif action == 'submit':
                plan.state = 'submitted'; plan.version += 1; plan.updated_at = utcnow()
            else:
                # Resource lock was the very first SQL; this check and all writes share that transaction.
                if db.query(ResourceAllocation.id).filter(ResourceAllocation.project_id == project.id, ResourceAllocation.origin_plan_id.is_not(None)).first():
                    fail(409, 'PROJECT_ALREADY_APPLIED', '该项目已执行初始组队方案，不能再次占用')
                for item in assignments:
                    db.add(ResourceAllocation(employee_id=item['employee_id'], project_id=project.id, role_slot_id=item['slot_id'],
                        start_date=date.fromisoformat(item['start_date']), end_date=date.fromisoformat(item['end_date']), work_dates_json=json_dump(item['work_dates']),
                        units_per_day=item['units_per_day'], status='active', origin_plan_id=plan.id, basis='管理员批准的初始组队方案'))
                plan.state = 'applied'; plan.version += 1; plan.updated_at = utcnow()
                project.business_status = 'active'; project.version += 1; project.updated_at = utcnow()
        db.flush()
        if stale_error is None:
            result = plan_dict(db, plan)
            _record(db, actor_id, action, request_key, digest, result, project.id, plan.id)
    if stale_error:
        fail(409, **stale_error)
    return result


def submit(factory, actor_id, plan_id, payload, request_key):
    return _action(factory, actor_id, plan_id, payload, request_key, 'submit')


def approve(factory, actor_id, plan_id, payload, request_key):
    return _action(factory, actor_id, plan_id, payload, request_key, 'approve')


def reject(factory, actor_id, plan_id, payload, request_key):
    return _action(factory, actor_id, plan_id, payload, request_key, 'reject')


def _allocation(db, allocation_id):
    row = db.get(ResourceAllocation, allocation_id)
    if not row:
        fail(404, 'NOT_FOUND', '占用记录不存在')
    project = require_project(db, row.project_id)
    return row, project


def allocation_dict(db, row):
    employee = db.get(Employee, row.employee_id)
    project = require_project(db, row.project_id)
    return {**{key: getattr(row, key) for key in ('id', 'employee_id', 'project_id', 'units_per_day', 'basis', 'status', 'origin_plan_id', 'version')},
        'start_date': row.start_date.isoformat(), 'end_date': row.end_date.isoformat(),
        'work_dates': json_load(row.work_dates_json, []), 'display_name': employee.display_name if employee else '已移除员工',
        'project_title': project.title}


def _finish_allocation(factory, actor_id, allocation_id, payload, request_key, status):
    action = f'allocation_{status}'
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        digest, prior = _replay(db, actor_id, action, request_key, {'allocation_id': allocation_id, **payload})
        if prior is not None:
            return prior
        row, project = _allocation(db, allocation_id)
        projects._version(row, payload)
        if row.status not in ('active', 'applied'):
            fail(409, 'INVALID_ALLOCATION_STATE', '该占用已经结束、取消或被调整，请刷新后重试', current_state=row.status)
        row.status = status
        row.version += 1
        db.flush()
        result = allocation_dict(db, row)
        _record(db, actor_id, action, request_key, digest, result, project.id)
        return result


def complete_allocation(factory, actor_id, allocation_id, payload, request_key):
    return _finish_allocation(factory, actor_id, allocation_id, payload, request_key, 'completed')


def cancel_allocation(factory, actor_id, allocation_id, payload, request_key):
    return _finish_allocation(factory, actor_id, allocation_id, payload, request_key, 'cancelled')


def adjust_allocation(factory, actor_id, allocation_id, payload, request_key):
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        digest, prior = _replay(db, actor_id, 'allocation_adjust', request_key, {'allocation_id': allocation_id, **payload})
        if prior is not None:
            return prior
        row, project = _allocation(db, allocation_id)
        projects._version(row, payload)
        if row.status not in ('active', 'applied'):
            fail(409, 'INVALID_ALLOCATION_STATE', '只有当前或未来占用可以调整，请刷新后重试', current_state=row.status)
        units = payload.get('units_per_day')
        if type(units) is not int or not 1 <= units <= 100:
            fail(422, 'INVALID_ALLOCATION', '每日投入比例必须为1%至100%的整数')
        try:
            new_days = sorted({capacity.as_date(value) for value in payload.get('work_dates', [])})
        except Exception:
            fail(422, 'INVALID_ALLOCATION', '调整后的工作日格式无效')
        if not new_days or any(day < business_today() for day in new_days):
            fail(422, 'INVALID_ALLOCATION', '调整只能修改今天及未来的工作日')
        if any(day < row.start_date or day >= row.end_date for day in new_days):
            fail(422, 'INVALID_ALLOCATION', '调整后的工作日必须位于原项目日期范围内')
        calendar = {item['work_date']: item for item in capacity.work_calendar(db, row.start_date, row.end_date)}
        if any(not calendar[day.isoformat()]['is_workday'] for day in new_days):
            fail(422, 'UNKNOWN_WORK_CALENDAR', '调整后的日期包含非公司工作日')
        old_days = set(json_load(row.work_dates_json, []))
        snap = capacity.snapshot(db, [row.employee_id], row.start_date, row.end_date, new_days)
        for item in snap['data']:
            available = item['remaining_units']
            if item['date'] in old_days and available is not None:
                available += row.units_per_day
            if available is None:
                fail(409, 'UNKNOWN_CAPACITY', '调整日期的可投入比例未知', date=item['date'])
            if available < units:
                fail(409, 'CAPACITY_CONFLICT', '调整后的投入比例超过员工剩余可安排比例', date=item['date'], remaining_units=available)
        row.status = 'superseded'
        row.version += 1
        replacement = ResourceAllocation(employee_id=row.employee_id, project_id=row.project_id, role_slot_id=row.role_slot_id,
            start_date=row.start_date, end_date=row.end_date, work_dates_json=json_dump([day.isoformat() for day in new_days]),
            units_per_day=units, status='active', origin_plan_id=row.origin_plan_id, basis=projects._redact(payload.get('basis') or row.basis)[:2000])
        db.add(replacement); db.flush()
        result = allocation_dict(db, replacement)
        _record(db, actor_id, 'allocation_adjust', request_key, digest, result, project.id)
        return result


def _close_project(factory, actor_id, project_id, payload, request_key, status):
    """Close a project and release every current/future allocation in one transaction."""
    action = f'project_{status}'
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        digest, prior = _replay(db, actor_id, action, request_key, {'project_id': project_id, **payload})
        if prior is not None:
            return prior
        project = require_project(db, project_id)
        projects._version(project, payload)
        if project.business_status not in ('draft', 'active'):
            fail(409, 'INVALID_PROJECT_STATE', '项目已经结束、取消或归档，请刷新后重试', current_state=project.business_status)
        if db.query(StaffingRun.id).filter(
            StaffingRun.project_id == project.id,
            StaffingRun.status.in_(['queued', 'running', 'waiting_input']),
        ).first():
            fail(409, 'ACTIVE_RUN', '推荐任务仍在执行，请先取消当前生成任务')
        allocation_status = 'completed' if status == 'completed' else 'cancelled'
        for row in db.query(ResourceAllocation).filter(
            ResourceAllocation.project_id == project.id,
            ResourceAllocation.status.in_(['active', 'applied']),
        ).with_for_update().all():
            row.status = allocation_status
            row.version += 1
        project.business_status = status
        project.version += 1
        project.updated_at = utcnow()
        db.flush()
        result = projects.project_dict(db, project)
        _record(db, actor_id, action, request_key, digest, result, project.id)
        return result


def complete_project(factory, actor_id, project_id, payload, request_key):
    return _close_project(factory, actor_id, project_id, payload, request_key, 'completed')


def cancel_project(factory, actor_id, project_id, payload, request_key):
    return _close_project(factory, actor_id, project_id, payload, request_key, 'cancelled')


def archive(factory, actor_id, project_id, payload, request_key):
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        digest, prior = _replay(db, actor_id, 'archive', request_key, {'project_id': project_id, **payload})
        if prior is not None:
            return prior
        project = require_project(db, project_id)
        projects._version(project, payload)
        if db.query(StaffingRun.id).filter(StaffingRun.project_id == project.id, StaffingRun.status.in_(['queued', 'running', 'waiting_input'])).first():
            fail(409, 'ACTIVE_RUN', '生成任务未结束，不能归档')
        for row in db.query(ResourceAllocation).filter(ResourceAllocation.project_id == project.id, ResourceAllocation.status.in_(['active', 'applied'])).all():
            if any(date.fromisoformat(d) >= business_today() for d in json_load(row.work_dates_json, [])):
                fail(409, 'ACTIVE_ALLOCATIONS', '项目仍有当前或未来正式占用，不能归档')
        project.business_status = 'archived'; project.version += 1; project.updated_at = utcnow()
        db.flush(); result = projects.project_dict(db, project)
        _record(db, actor_id, 'archive', request_key, digest, result, project.id)
        return result


def add_baseline(factory, actor_id, payload, request_key):
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        digest, prior = _replay(db, actor_id, 'baseline', request_key, payload)
        if prior is not None:
            return prior
        state = db.get(StaffingResourceState, 1)
        if state.bootstrap_closed_at:
            fail(409, 'BOOTSTRAP_CLOSED', '基础台账初始化已永久关闭')
        project = require_project(db, payload.get('project_id'))
        if project.origin_kind != 'baseline' or project.business_status == 'archived':
            fail(422, 'INVALID_BASELINE_PROJECT', '只有初始化登记的已有项目可直接录入占用')
        if payload.get('expected_project_version') != project.version:
            fail(409, 'STALE_VERSION', '项目版本已变化')
        items = payload.get('allocations', [])
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            fail(422, 'SCOPE_LIMIT', '每次登记1至100条已有占用')
        identifiers = []
        for item in items:
            employee = db.get(Employee, item.get('employee_id'))
            if not employee or employee.employment_status != 'active':
                fail(422, 'INVALID_EMPLOYEE_SCOPE', '已有占用须关联当前在职员工')
            start, end = capacity.as_date(item.get('start_date')), capacity.as_date(item.get('end_date'))
            if start < project.start_date or end > project.end_date:
                fail(422, 'INVALID_BASELINE', '占用日期须在已有项目范围内')
            all_days = capacity.dates_between(start, end)
            days = [capacity.as_date(d) for d in item.get('work_dates', [])]
            units = item.get('units_per_day')
            if not days or len(set(days)) != len(days) or not set(days) <= set(all_days) or type(units) is not int or not 1 <= units <= 100 or not str(item.get('basis', '')).strip():
                fail(422, 'INVALID_BASELINE', '已有占用须包含明确工作日、1%至100%的整数投入比例与依据')
            # The company calendar defaults to Monday-Friday and persists only
            # exceptions.  Use the shared effective calendar so a normal
            # weekday does not require a redundant CompanyWorkDay row.
            work = {date.fromisoformat(row['work_date']) for row in capacity.work_calendar(db, start, end)
                    if row['is_workday']}
            if work != set(days):
                fail(422, 'UNKNOWN_WORK_CALENDAR', '请先维护公司工作日')
            snap = capacity.snapshot(db, [employee.id], start, end, days)
            blockers = [row for row in snap['data'] if row['capacity_units'] is None or row['remaining_units'] < units]
            if blockers:
                fail(409, 'CAPACITY_CONFLICT', '已有占用存在未知的可投入比例，或超出每日可投入比例', blockers=blockers)
            # Flush each row: a later row in the same request must see this new occupancy.
            identifier = new_id(); identifiers.append(identifier)
            db.add(ResourceAllocation(id=identifier, employee_id=employee.id, project_id=project.id, role_slot_id=new_id(), start_date=start, end_date=end,
                work_dates_json=json_dump([d.isoformat() for d in days]), units_per_day=units, status='active', basis=projects._redact(item['basis'])[:2000]))
            db.flush()
        project.business_status = 'active'; project.version += 1; project.updated_at = utcnow()
        db.flush(); result = {'allocation_ids': identifiers, 'project': projects.project_dict(db, project)}
        _record(db, actor_id, 'baseline', request_key, digest, result, project.id)
        return result


def finalize_setup(factory, actor_id, payload, request_key):
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'admin')
        digest, prior = _replay(db, actor_id, 'finalize', request_key, payload)
        if prior is not None:
            return prior
        state = db.get(StaffingResourceState, 1)
        if state.bootstrap_closed_at:
            fail(409, 'BOOTSTRAP_CLOSED', '基础台账初始化已永久关闭')
        if payload.get('expected_revision') != state.revision-1:
            fail(409, 'STALE_INPUT', '资源台账已变化，请刷新核对后再确认')
        state.bootstrap_closed_at = utcnow(); state.bootstrap_closed_by = actor_id
        result = {'revision': state.revision, 'bootstrap_closed': True, 'bootstrap_closed_at': state.bootstrap_closed_at.isoformat(), 'bootstrap_closed_by': actor_id}
        _record(db, actor_id, 'finalize', request_key, digest, result)
        return result
