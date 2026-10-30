"""Versioned employee identities and explicit resume associations."""
from datetime import date, datetime

from app.core.job_categories import JOB_CATEGORIES
from app.models import Candidate
from app.models.staffing import Employee, EmployeeDocument, ResourceAllocation
from app.services.staffing.common import business_today, fail, json_load, require_actor, resource_write


def require_employee(db, employee_id):
    employee = db.get(Employee, employee_id)
    if not employee:
        fail(404, 'NOT_FOUND', '员工不存在')
    return employee


def check_version(employee, payload):
    if payload.get('expected_version') != employee.version:
        fail(409, 'STALE_INPUT', '员工资料版本已更新，请刷新')


def identity_time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            fail(422, 'INVALID_DOCUMENT', '资料身份时间格式无效')
    if not isinstance(value, datetime):
        fail(422, 'INVALID_DOCUMENT', '必须提供资料创建时间快照')
    return value.replace(tzinfo=None)


def require_document(db, item):
    candidate = db.get(Candidate, item.get('candidate_id'))
    stamp = identity_time(item.get('candidate_created_at'))
    if not candidate or candidate.created_at.replace(tzinfo=None) != stamp or candidate.deleted_at or candidate.status != 'ready' or not candidate.enabled:
        fail(409, 'STALE_DOCUMENT', '资料已变化、停用或尚未成功入库')
    return candidate


def primary_document(db, employee_id):
    return db.query(EmployeeDocument).filter_by(employee_id=employee_id, link_status='active', is_primary=True).first()


def primary_candidate(db, link):
    if not link:
        return None
    candidate = db.get(Candidate, link.candidate_id)
    if (not candidate or candidate.created_at.replace(tzinfo=None) != link.candidate_created_at.replace(tzinfo=None)
            or candidate.deleted_at or not candidate.enabled or candidate.status != 'ready'):
        return None
    return candidate


def primary_snapshot(link, candidate):
    if not link:
        return None
    return {'id': link.id, 'candidate_id': link.candidate_id,
        'candidate_created_at': link.candidate_created_at.replace(tzinfo=None).isoformat(), 'version': link.version,
        'job_category': candidate.job_category if candidate else None,
        'candidate_updated_at': candidate.updated_at.replace(tzinfo=None).isoformat() if candidate else None}


def employee_dict(db, employee, include_facts=True):
    link = primary_document(db, employee.id)
    candidate = primary_candidate(db, link)
    result = {key: getattr(employee, key) for key in ('id', 'employee_no', 'display_name', 'department_name', 'current_position', 'position_origin', 'employment_status', 'version', 'created_at', 'updated_at', 'facts_status')} | {
        'primary_document': primary_snapshot(link, candidate)}
    result['documents'] = [{'id':item.id,'candidate_id':item.candidate_id,'candidate_created_at':item.candidate_created_at.isoformat(),
                            'is_primary':item.is_primary,'version':item.version} for item in db.query(EmployeeDocument).filter_by(
                                employee_id=employee.id,link_status='active').order_by(EmployeeDocument.linked_at,EmployeeDocument.id)]
    if include_facts:
        from app.services.staffing.evidence import current_facts
        facts = current_facts(db, employee.id)
        result['facts_status'] = facts['status']
    else:
        # The table only needs the persisted status. Full source/fact validation
        # is loaded when the user opens one employee, avoiding one chunk scan per row.
        result['facts_status'] = employee.facts_status or 'pending'
    if link:
        result['current_position'] = (candidate.job_category or '') if candidate else ''
        result['position_origin'] = 'knowledge'
    elif employee.position_origin != 'hr':
        result['current_position'] = ''
    return result


def apply_fields(employee, payload):
    for key in ('display_name', 'employee_no', 'department_name', 'current_position', 'employment_status'):
        if key not in payload:
            continue
        value = payload[key]
        if key == 'current_position' and value is not None and value not in JOB_CATEGORIES:
            fail(422, 'INVALID_EMPLOYEE', '当前岗位请选择固定岗位类别，或清空为待确认')
        if key == 'employment_status' and value not in ('active', 'inactive', 'departed'):
            fail(422, 'INVALID_EMPLOYEE', '员工状态无效')
        if key != 'employment_status':
            if value is not None and not isinstance(value, str):
                fail(422, 'INVALID_EMPLOYEE', '员工字段必须为文本')
            value = (value or '').strip()
            if key == 'display_name' and not value:
                fail(422, 'INVALID_EMPLOYEE', '员工姓名不能为空')
            if len(value) > (120 if key == 'display_name' else 160):
                fail(422, 'INVALID_EMPLOYEE', '员工字段过长')
            if key == 'employee_no':
                value = value or None
        setattr(employee, key, value)
    if 'current_position' in payload:
        employee.position_origin = 'hr'


def create_employee(factory, actor_id, payload):
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        if not payload.get('display_name'):
            fail(422, 'INVALID_EMPLOYEE', '员工姓名不能为空')
        employee = Employee()
        apply_fields(employee, payload)
        db.add(employee)
        db.flush()
        return employee_dict(db, employee)


def update_employee(factory, actor_id, employee_id, payload):
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        employee = require_employee(db, employee_id)
        check_version(employee, payload)
        if 'current_position' in payload:
            link = primary_document(db, employee_id)
            if link:
                candidate = primary_candidate(db, link)
                if not candidate or payload.get('expected_primary_document') != primary_snapshot(link, candidate):
                    fail(409, 'STALE_DOCUMENT', '主简历或岗位分类已变化，请刷新后重试')
                value = payload['current_position']
                if value is not None and value not in JOB_CATEGORIES:
                    fail(422, 'INVALID_EMPLOYEE', '当前岗位请选择固定岗位类别，或清空为待确认')
                candidate.job_category = value
        if payload.get('employment_status', 'active') != 'active':
            allocations = db.query(ResourceAllocation).filter(ResourceAllocation.employee_id == employee_id,
                ResourceAllocation.status.in_(['active', 'applied']), ResourceAllocation.end_date > business_today()).all()
            allocations = [row for row in allocations if any(max(business_today(),row.start_date) <= date.fromisoformat(day) < row.end_date
                           for day in json_load(row.work_dates_json, []))]
            if allocations:
                fail(409, 'CAPACITY_CONFLICT', '员工仍有当前或未来项目占用，不能停用或离职', allocation_ids=[a.id for a in allocations])
        apply_fields(employee, payload)
        if 'current_position' in payload and primary_document(db, employee_id):
            employee.position_origin = 'knowledge'
        employee.version += 1
        db.flush()
        return employee_dict(db, employee)


def from_documents(factory, actor_id, documents):
    if not isinstance(documents, list) or not 1 <= len(documents) <= 100 or any(not isinstance(item, dict) for item in documents):
        fail(422, 'INVALID_BATCH', '每批请选择1至100份资料')
    results = []
    for item in documents:
        try:
            with resource_write(factory) as db:
                require_actor(db, actor_id, 'hr')
                candidate = require_document(db, item)
                existing = db.query(EmployeeDocument).filter_by(candidate_id=candidate.id, candidate_created_at=candidate.created_at, link_status='active').first()
                if existing:
                    results.append({'candidate_id': candidate.id, 'status': 'already_linked', 'employee_id': existing.employee_id})
                    continue
                requested_id = item.get('employee_id')
                if requested_id:
                    employee = require_employee(db, requested_id)
                    check_version(employee, item)
                    status = 'linked'
                elif not item.get('create_new') and db.query(Employee.id).filter_by(display_name=candidate.name).first():
                    results.append({'candidate_id': candidate.id, 'status': 'needs_identity_choice', 'message': '存在同名员工，请选择已有身份或明确新建'})
                    continue
                else:
                    employee = Employee(display_name=candidate.name, employment_status='active')
                    db.add(employee)
                    db.flush()
                    status = 'created'
                primary = primary_document(db, employee.id) is None
                db.add(EmployeeDocument(employee_id=employee.id, candidate_id=candidate.id, candidate_created_at=candidate.created_at,
                    is_primary=primary, linked_by=actor_id))
                if status == 'linked':
                    employee.version += 1
                if primary:
                    employee.facts_status = 'pending'
                    employee.facts_fingerprint = ''
                db.flush()
                results.append({'candidate_id': candidate.id, 'status': status, 'employee_id': employee.id, 'version': employee.version})
        except Exception as exc:
            from fastapi import HTTPException
            if not isinstance(exc, HTTPException) or exc.status_code in (401, 403):
                raise
            detail = exc.detail if isinstance(exc.detail, dict) else {'message': str(exc.detail)}
            results.append({'candidate_id': item.get('candidate_id'), 'status': 'failed', **detail})
    return {'results': results}


def set_documents(factory, actor_id, employee_id, payload):
    items = payload.get('documents', [])
    if not isinstance(items, list) or len(items) > 100:
        fail(422, 'INVALID_BATCH', '最多关联100份资料')
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        employee = require_employee(db, employee_id)
        check_version(employee, payload)
        candidates = [require_document(db, item) for item in items]
        if len({candidate.id for candidate in candidates}) != len(candidates) or sum(bool(i.get('is_primary')) for i in items) > 1:
            fail(422, 'INVALID_DOCUMENT', '资料重复或指定了多份主简历')
        old = db.query(EmployeeDocument).filter_by(employee_id=employee_id, link_status='active').all()
        old_primary = next((link for link in old if link.is_primary), None)
        for link in old:
            link.link_status = 'inactive'
            link.is_primary = False
            link.version += 1
        db.flush()
        explicit_primary = next((c.id for c, item in zip(candidates, items) if item.get('is_primary')), None)
        retained_primary = old_primary.candidate_id if old_primary and any(c.id == old_primary.candidate_id and c.created_at.replace(tzinfo=None) == old_primary.candidate_created_at.replace(tzinfo=None) for c in candidates) else None
        selected_primary = explicit_primary or retained_primary or (candidates[0].id if candidates and old_primary is None else None)
        for candidate in candidates:
            other = db.query(EmployeeDocument).filter_by(candidate_id=candidate.id, candidate_created_at=candidate.created_at, link_status='active').first()
            if other:
                fail(409, 'IDENTITY_CONFLICT', '资料已关联其他员工')
            db.add(EmployeeDocument(employee_id=employee_id, candidate_id=candidate.id, candidate_created_at=candidate.created_at,
                is_primary=candidate.id == selected_primary, linked_by=actor_id))
        employee.version += 1
        employee.facts_status = 'pending'
        employee.facts_fingerprint = ''
        if employee.position_origin != 'hr':
            employee.current_position = ''
        db.flush()
        return employee_dict(db, employee)


def retry_facts(factory, actor_id, employee_id, extractor=None):
    """Rebuild facts from the current primary resume without changing resume chunks."""
    from app.services.staffing import evidence
    with factory() as db:
        require_actor(db, actor_id, 'hr')
        require_employee(db, employee_id)
        if extractor is None:
            from app.services.staffing import model
            from app.services.staffing.model import RunBudget
            budget = RunBudget()
            extractor = lambda source: model.extract_resume(source, budget)
        prepared = evidence.prepare(db, employee_id, extractor)
    evidence.refresh(factory, employee_id, prepared)
    with factory() as db:
        return evidence.current_facts(db, employee_id)
