"""Deterministic staffing constraints. Never infer skills or missing capacity."""
from __future__ import annotations

from datetime import date
import json
from time import monotonic

from ortools.sat.python import cp_model


def _day(value):
    return value.isoformat() if isinstance(value, date) else str(value)


def _keys(values):
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _issue(code, **values):
    return {'code': code, **values}


def _requirements(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    req = dict(value)
    start, end = date.fromisoformat(_day(req['start_date'])), date.fromisoformat(_day(req['end_date']))
    if not 0 < (end-start).days <= 90:
        raise ValueError('项目日期范围须为1至90日，结束日不计入')
    scope = req.get('employee_ids', [])
    if not 1 <= len(scope) <= 100 or len(set(scope)) != len(scope):
        raise ValueError('必须明确选择1至100名员工且不能重复')
    slots = req.get('slots', [])
    if not 1 <= len(slots) <= 10:
        raise ValueError('角色名额须为1至10个')
    dates = {_day(d) for d in req.get('work_dates', [])}
    if not dates or any(not start <= date.fromisoformat(d) < end for d in dates):
        raise ValueError('工作日须在项目日期范围内')
    seen = set()
    for slot in slots:
        sid = slot.get('slot_id')
        if not sid or sid in seen:
            raise ValueError('角色名额ID缺失或重复')
        seen.add(sid)
        units = slot.get('units_per_day')
        if isinstance(units, bool) or not isinstance(units, int) or not 1 <= units <= 100:
            raise ValueError('每日投入比例须为1%至100%的整数百分比')
        work = {_day(d) for d in slot.get('work_dates', [])}
        if not work or not work <= dates:
            raise ValueError('名额工作日须属于已确认的项目工作日')
    required, excluded = set(req.get('required_employee_ids', [])), set(req.get('excluded_employee_ids', []))
    if required & excluded or not (required | excluded) <= set(scope):
        raise ValueError('指定和排除人员须在项目范围内且不能冲突')
    req['start_date'], req['end_date'] = start.isoformat(), end.isoformat()
    return req


def _capacity_map(capacities, allocations):
    if isinstance(capacities, dict):
        capacities = capacities.get('data', [])
    result = {(row['employee_id'], _day(row.get('date', row.get('work_date')))): dict(row) for row in capacities}
    # Snapshot allocated_units is authoritative: never subtract the same ledger twice.
    for allocation in allocations or []:
        if allocation.get('status', 'active') not in ('active', 'applied'):
            continue
        work = allocation.get('work_dates', allocation.get('work_dates_json', []))
        if isinstance(work, str):
            work = json.loads(work)
        for d in work:
            key = (allocation['employee_id'], _day(d))
            row = result.setdefault(key, {'capacity_units': None})
            if 'allocated_units' not in row:
                row['ledger_units'] = row.get('ledger_units', 0) + allocation['units_per_day']
    for row in result.values():
        row.setdefault('allocated_units', row.pop('ledger_units', 0))
    return result


def _context(req, employees, capacities, allocations):
    people = {e.get('employee_id', e.get('id')): e for e in employees}
    capacity = _capacity_map(capacities, allocations)
    diagnostics, pools = [], {}
    incomplete_facts = False
    unknown_capacity = False
    for eid in req['employee_ids']:
        person = people.get(eid)
        if person is None:
            incomplete_facts = True
            diagnostics.append(_issue('EMPLOYEE_UNAVAILABLE', employee_id=eid))
        elif person.get('employment_status') == 'active' and person.get('facts', {}).get('status') != 'ready':
            incomplete_facts = True
            diagnostics.append(_issue('FACTS_NOT_READY', employee_id=eid, status=person.get('facts', {}).get('status', 'unavailable')))
    for slot in req['slots']:
        pool = []
        sid = slot['slot_id']
        for eid in req['employee_ids']:
            person = people.get(eid)
            if not person or person.get('employment_status') != 'active' or eid in req.get('excluded_employee_ids', []):
                continue
            facts = person.get('facts', {})
            if facts.get('status') != 'ready':
                continue
            missing = _keys(slot.get('required_skills', [])) - _keys(facts.get('skills', []))
            if missing:
                diagnostics.append(_issue('SKILL_NOT_RECORDED', employee_id=eid, slot_id=sid, skills=sorted(missing)))
                continue
            blocked = False
            for d in slot['work_dates']:
                row = capacity.get((eid, _day(d)), {})
                cap, used = row.get('capacity_units'), row.get('allocated_units', 0)
                if cap is None:
                    blocked = unknown_capacity = True
                    diagnostics.append(_issue('UNKNOWN_CAPACITY', employee_id=eid, slot_id=sid, date=_day(d)))
                elif not isinstance(cap, int) or isinstance(cap, bool) or not 0 <= cap <= 100 or not isinstance(used, int) or used < 0:
                    raise ValueError('可投入比例或占用比例数据非法')
                elif used + slot['units_per_day'] > cap:
                    blocked = True
                    diagnostics.append(_issue('CAPACITY_CONFLICT', employee_id=eid, slot_id=sid, date=_day(d), capacity_units=cap, allocated_units=used, requested_units=slot['units_per_day']))
            if not blocked:
                pool.append(eid)
        pools[sid] = pool
        if not pool:
            diagnostics.append(_issue('EMPTY_SLOT_POOL', slot_id=sid))
    return people, capacity, pools, diagnostics, incomplete_facts, unknown_capacity


def validate_assignments(requirements, employees, capacities, allocations=None, assignments=None):
    """Independent selected-team check; no solver or stored validation is trusted."""
    blockers = []
    try:
        req = _requirements(requirements)
        people, capacity, _, _, _, _ = _context(req, employees, capacities, allocations)
    except (ValueError, KeyError, TypeError) as exc:
        return {'schema_version': '1', 'valid': False, 'blockers': [_issue('INVALID_REQUIREMENTS', message=str(exc))], 'warnings': []}
    slots = {s['slot_id']: s for s in req['slots']}
    seen_slots, selected = set(), set()
    for item in assignments or []:
        sid, eid = item.get('slot_id'), item.get('employee_id')
        if sid not in slots or sid in seen_slots:
            blockers.append(_issue('INVALID_SLOT', slot_id=sid))
            continue
        seen_slots.add(sid)
        slot = slots[sid]
        if eid in selected:
            blockers.append(_issue('DUPLICATE_EMPLOYEE', employee_id=eid))
        selected.add(eid)
        if eid not in req['employee_ids'] or eid in req.get('excluded_employee_ids', []):
            blockers.append(_issue('SCOPE_DENIED', employee_id=eid))
        person = people.get(eid)
        if not person or person.get('employment_status') != 'active':
            blockers.append(_issue('EMPLOYEE_NOT_ACTIVE', employee_id=eid))
            continue
        facts = person.get('facts', {})
        if facts.get('status') != 'ready':
            blockers.append(_issue('FACTS_NOT_READY', employee_id=eid))
        missing = _keys(slot.get('required_skills', [])) - _keys(facts.get('skills', []))
        if missing:
            blockers.append(_issue('SKILL_NOT_RECORDED', employee_id=eid, slot_id=sid, skills=sorted(missing)))
        for field, expected in [('units_per_day', slot['units_per_day']), ('start_date', req['start_date']), ('end_date', req['end_date'])]:
            if item.get(field) is not None and _day(item[field]) != _day(expected):
                blockers.append(_issue('ASSIGNMENT_MISMATCH', slot_id=sid, field=field))
        if item.get('work_dates') and {_day(d) for d in item['work_dates']} != {_day(d) for d in slot['work_dates']}:
            blockers.append(_issue('ASSIGNMENT_MISMATCH', slot_id=sid, field='work_dates'))
        for d in slot['work_dates']:
            row = capacity.get((eid, _day(d)), {})
            cap, used = row.get('capacity_units'), row.get('allocated_units', 0)
            if cap is None:
                blockers.append(_issue('UNKNOWN_CAPACITY', employee_id=eid, date=_day(d)))
            elif used + slot['units_per_day'] > cap:
                blockers.append(_issue('CAPACITY_CONFLICT', employee_id=eid, date=_day(d), capacity_units=cap, allocated_units=used, requested_units=slot['units_per_day']))
    if seen_slots != set(slots):
        blockers.append(_issue('MISSING_SLOTS', slot_ids=sorted(set(slots)-seen_slots)))
    if not set(req.get('required_employee_ids', [])) <= selected:
        blockers.append(_issue('REQUIRED_EMPLOYEE_MISSING', employee_ids=sorted(set(req.get('required_employee_ids', []))-selected)))
    return {'schema_version': '1', 'valid': not blockers, 'blockers': blockers, 'warnings': []}


def complete_assignments(requirements, assignments):
    req = _requirements(requirements)
    slots = {s['slot_id']: s for s in req['slots']}
    return [{**a, 'role_name': slots[a['slot_id']]['role_name'], 'units_per_day': slots[a['slot_id']]['units_per_day'], 'start_date': req['start_date'], 'end_date': req['end_date'], 'work_dates': [_day(d) for d in slots[a['slot_id']]['work_dates']]} for a in assignments]


def solve_teams(requirements, employees, capacities, allocations=None, budget_seconds=5):
    start = monotonic()
    deadline = start + min(max(float(budget_seconds), 0), 5)
    base = {'schema_version': '1', 'teams': [], 'diagnostics': [], 'scope_employee_ids': list(requirements.get('employee_ids', [])) if isinstance(requirements, dict) else []}
    try:
        req = _requirements(requirements)
        people, capacity, pools, diagnostics, incomplete_facts, unknown_capacity = _context(req, employees, capacities, allocations)
    except (ValueError, KeyError, TypeError) as exc:
        return {**base, 'status': 'MODEL_INVALID', 'diagnostics': [_issue('INVALID_REQUIREMENTS', message=str(exc))], 'elapsed_seconds': monotonic()-start}
    base['diagnostics'] = diagnostics
    if incomplete_facts:
        return {**base, 'status': 'DATA_INCOMPLETE', 'elapsed_seconds': monotonic()-start}
    first_status = 'UNKNOWN'
    excluded_sets = []
    for _ in range(2):
        model = cp_model.CpModel()
        variables = {(eid, slot['slot_id']): model.new_bool_var(f'x_{eid}_{slot["slot_id"]}') for slot in req['slots'] for eid in pools[slot['slot_id']]}
        for slot in req['slots']:
            model.add(sum(variables[eid, slot['slot_id']] for eid in pools[slot['slot_id']]) == 1)
        for eid in req['employee_ids']:
            member = sum(v for (e, _), v in variables.items() if e == eid)
            model.add(member <= 1)
            if eid in req.get('required_employee_ids', []):
                model.add(member == 1)
        for previous in excluded_sets:
            model.add(sum(v for (eid, _), v in variables.items() if eid in previous) <= len(req['slots'])-1)
        preference = sum(len(_keys(slot.get('preferred_skills', [])) & _keys(people[eid]['facts'].get('skills', []))) * variables[eid, slot['slot_id']] for slot in req['slots'] for eid in pools[slot['slot_id']])
        peak = model.new_int_var(0, 100, 'peak_daily_load')
        burden_terms = []
        for slot in req['slots']:
            for eid in pools[slot['slot_id']]:
                var = variables[eid, slot['slot_id']]
                for d in slot['work_dates']:
                    used = capacity[eid, _day(d)]['allocated_units']
                    model.add(peak >= (used+slot['units_per_day'])*var)
                    burden_terms.append(used*slot['units_per_day']*var)
        burden = sum(burden_terms)
        last_assignments, objective_values, all_optimal = None, [], True
        status_name = 'UNKNOWN'
        for expression, maximize in [(preference, True), (peak, False), (burden, False)]:
            remaining = deadline-monotonic()
            if remaining <= 0:
                status_name, all_optimal = ('FEASIBLE' if last_assignments else 'UNKNOWN'), False
                break
            if maximize:
                model.maximize(expression)
            else:
                model.minimize(expression)
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = remaining
            solver.parameters.num_search_workers = 1
            solver.parameters.random_seed = 0
            status = solver.solve(model)
            status_name = solver.status_name(status)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                if last_assignments:
                    status_name = 'FEASIBLE'
                all_optimal = False
                break
            last_assignments = [{'employee_id': eid, 'slot_id': sid} for (eid, sid), var in variables.items() if solver.value(var)]
            value = int(round(solver.objective_value))
            objective_values.append(value)
            if status != cp_model.OPTIMAL:
                all_optimal = False
                break
            model.add(expression == value)
        if last_assignments:
            assignments = complete_assignments(req, last_assignments)
            validation = validate_assignments(req, employees, capacities, allocations, assignments)
            if not validation['valid']:
                return {**base, 'status': 'MODEL_INVALID', 'diagnostics': validation['blockers'], 'elapsed_seconds': monotonic()-start}
            team_status = 'OPTIMAL' if all_optimal and len(objective_values) == 3 else 'FEASIBLE'
            base['teams'].append({'assignments': assignments, 'solver_status': team_status, 'objectives': objective_values, 'optimal': team_status == 'OPTIMAL'})
            excluded_sets.append({a['employee_id'] for a in assignments})
            if len(base['teams']) == 1:
                first_status = team_status
        else:
            if not base['teams']:
                first_status = 'DATA_INCOMPLETE' if status_name == 'INFEASIBLE' and unknown_capacity else status_name
            break
    return {**base, 'status': first_status, 'elapsed_seconds': monotonic()-start}
