"""Company-calendar defaults plus explicit daily capacity overrides."""
from datetime import date, datetime, timedelta, timezone

from app.models.staffing import CompanyWorkDay, EmployeeCapacityDay, ResourceAllocation, StaffingProject, StaffingResourceState
from app.services.staffing.common import fail, json_load, require_actor, resource_write
from app.services.staffing.employees import check_version, require_employee


def as_date(value):
    try:
        return value if type(value) is date else date.fromisoformat(value)
    except (ValueError, TypeError):
        fail(422, 'INVALID_DATE', '日期格式无效')


def dates_between(start_date, end_date):
    start, end = as_date(start_date), as_date(end_date)
    if not 0 < (end - start).days <= 90:
        fail(422, 'SCOPE_LIMIT', '日期范围须为1至90天')
    return [start + timedelta(days=i) for i in range((end-start).days)]


def default_workday(work_date):
    """Normal company calendar: Monday-Friday work, Saturday-Sunday off."""
    return as_date(work_date).weekday() < 5


def allocation_days(db, employee_ids, start_date, end_date):
    rows = db.query(ResourceAllocation).filter(ResourceAllocation.employee_id.in_(employee_ids), ResourceAllocation.status.in_(['active', 'applied']),
        ResourceAllocation.start_date < end_date, ResourceAllocation.end_date > start_date).all()
    totals, allocations = {}, {}
    for row in rows:
        for raw in set(json_load(row.work_dates_json, [])):
            day = as_date(raw)
            if max(start_date, row.start_date) <= day < min(end_date, row.end_date):
                key = (row.employee_id, day)
                totals[key] = totals.get(key, 0) + row.units_per_day
                allocations.setdefault(key, []).append(row.id)
    return totals, allocations


def snapshot(db, employee_ids, start_date, end_date, work_dates=None):
    if len(employee_ids) > 100:
        fail(422, 'SCOPE_LIMIT', '员工范围最多100名')
    all_dates = dates_between(start_date, end_date)
    days = all_dates if work_dates is None else [as_date(day) for day in work_dates]
    if not set(days).issubset(all_dates):
        fail(422, 'SCOPE_DENIED', '工作日超出查询范围')
    totals, _ = allocation_days(db, employee_ids, all_dates[0], all_dates[-1] + timedelta(days=1))
    calendar = {row['work_date']: row for row in work_calendar(db, all_dates[0], all_dates[-1] + timedelta(days=1))}
    rows = db.query(EmployeeCapacityDay).filter(EmployeeCapacityDay.employee_id.in_(employee_ids), EmployeeCapacityDay.work_date.in_(days)).all()
    indexed = {(row.employee_id, row.work_date): row for row in rows}
    details = {}
    allocation_rows = db.query(ResourceAllocation).filter(ResourceAllocation.employee_id.in_(employee_ids),
        ResourceAllocation.start_date < all_dates[-1] + timedelta(days=1), ResourceAllocation.end_date > all_dates[0]).all()
    projects = {project.id: project.title for project in db.query(StaffingProject).filter(StaffingProject.id.in_({row.project_id for row in allocation_rows})).all()}
    for allocation in allocation_rows:
        for raw in set(json_load(allocation.work_dates_json, [])):
            day = as_date(raw)
            if day in days and allocation.employee_id in employee_ids:
                details.setdefault((allocation.employee_id, day), []).append({
                    'allocation_id': allocation.id, 'project_id': allocation.project_id,
                    'project_title': projects.get(allocation.project_id, '已删除项目'),
                    'status': allocation.status, 'units_per_day': allocation.units_per_day, 'version': allocation.version,
                    'basis': allocation.basis,
                })
    data, unknown = [], []
    for employee_id in employee_ids:
        for day in days:
            row = indexed.get((employee_id, day))
            calendar_row = calendar[day.isoformat()]
            is_workday = calendar_row['is_workday']
            capacity = row.capacity_units if row else (100 if is_workday else 0)
            allocated = totals.get((employee_id, day), 0)
            data.append({'employee_id': employee_id, 'date': day.isoformat(), 'capacity_units': capacity, 'allocated_units': allocated,
                'remaining_units': None if capacity is None else capacity - allocated, 'version': row.version if row else 0,
                'source_kind': row.source_kind if row else ('company_calendar_default' if is_workday else 'company_calendar_weekend'),
                'observed_at': row.observed_at.isoformat() if row else None, 'is_workday': is_workday,
                'is_default': row is None, 'allocations': details.get((employee_id, day), [])})
            if capacity is None and is_workday:
                unknown.append({'employee_id': employee_id, 'date': day.isoformat()})
    state = db.get(StaffingResourceState, 1)
    return {'schema_version': '1', 'data': data, 'unknown_dates': unknown, 'source_kind': 'manual_capacity_ledger',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'data_revision': state.revision if state else 0,
        'warnings': ['演示排期，仅用于功能测试，不代表实际承诺'] if any((row['source_kind'] or '').startswith('demo_') for row in data) else [], 'next_cursor': None}


def set_capacity_days(factory, actor_id, employee_id, payload):
    days = payload.get('days', [])
    if not isinstance(days, list) or not 1 <= len(days) <= 90:
        fail(422, 'SCOPE_LIMIT', '每次维护1至90条日期的可投入比例')
    parsed = [(as_date(item.get('work_date')), item) for item in days]
    if len({d for d, _ in parsed}) != len(parsed):
        fail(422, 'INVALID_CAPACITY', '可投入比例的日期不能重复')
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        employee = require_employee(db, employee_id)
        check_version(employee, payload)
        for day, item in parsed:
            units = item.get('capacity_units')
            if units is not None and (type(units) is not int or not 0 <= units <= 100):
                fail(422, 'INVALID_CAPACITY', '可投入比例必须为0%至100%的整数百分比或未知')
            used, refs = allocation_days(db, [employee_id], day, day + timedelta(days=1))
            if used.get((employee_id, day), 0) > (units or 0):
                fail(409, 'CAPACITY_CONFLICT', '可投入比例不能低于已生效的占用比例', allocation_ids=refs[(employee_id, day)])
            row = db.query(EmployeeCapacityDay).filter_by(employee_id=employee_id, work_date=day).first()
            if 'expected_version' in item and item['expected_version'] != (row.version if row else 0):
                fail(409, 'STALE_INPUT', '人员排期版本已变化')
            if row:
                row.capacity_units = units
                row.source_kind = 'manual_capacity_ledger'
                row.version += 1
                row.observed_at = datetime.now(timezone.utc)
            else:
                db.add(EmployeeCapacityDay(employee_id=employee_id, work_date=day, capacity_units=units))
        employee.version += 1
        db.flush()
        return {'employee_id': employee_id, 'version': employee.version}


def work_calendar(db, start_date, end_date):
    start, end = as_date(start_date), as_date(end_date)
    if not 0 < (end - start).days <= 366:
        fail(422, 'SCOPE_LIMIT', '日历查询范围最多366天')
    overrides = {row.work_date: row for row in db.query(CompanyWorkDay).filter(CompanyWorkDay.work_date >= start, CompanyWorkDay.work_date < end).all()}
    return [{'work_date': day.isoformat(), 'is_workday': overrides[day].is_workday if day in overrides else default_workday(day),
        'version': overrides[day].version if day in overrides else 0, 'is_default': day not in overrides}
        for day in (start + timedelta(days=i) for i in range((end - start).days))]


def set_work_calendar(factory, actor_id, payload):
    days = payload.get('days', [])
    if not 1 <= len(days) <= 366:
        fail(422, 'SCOPE_LIMIT', '每次维护1至366个日期')
    parsed = [(as_date(item.get('work_date')), item) for item in days]
    if len({d for d, _ in parsed}) != len(parsed) or any(type(item.get('is_workday')) is not bool for _, item in parsed):
        fail(422, 'INVALID_CALENDAR', '日历日期重复或工作日标记无效')
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        state = db.get(StaffingResourceState, 1)
        if payload.get('expected_revision') != state.revision - 1:
            fail(409, 'STALE_INPUT', '台账版本已更新')
        for day, item in parsed:
            row = db.get(CompanyWorkDay, day)
            if item['is_workday'] == default_workday(day):
                if row:
                    db.delete(row)
                continue
            if row:
                row.is_workday = item['is_workday']
                row.version += 1
                row.updated_at = datetime.now(timezone.utc)
                row.updated_by = actor_id
            else:
                db.add(CompanyWorkDay(work_date=day, is_workday=item['is_workday'], updated_by=actor_id))
        return {'revision': state.revision}
