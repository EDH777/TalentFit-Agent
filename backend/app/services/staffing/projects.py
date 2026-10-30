"""Shared staffing projects and explicit requirement confirmation."""
from datetime import date, timedelta
import re

from pydantic import ValidationError

from app.models.entities import utcnow
from app.models.staffing import CompanyWorkDay, Employee, StaffingMessage, StaffingPlan, StaffingProject, StaffingResourceState, StaffingRun
from app.schemas.staffing import ConfirmedRequirements, RequirementDraft, assign_slot_ids
from app.services.staffing.capacity import default_workday
from app.services.staffing.common import fail, json_dump, json_load, require_actor, require_project, resource_write


def _redact(value):
    from app.services.agent import redact_contacts
    return redact_contacts(str(value))


def _date(value):
    try:
        return value if type(value) is date else date.fromisoformat(value)
    except (ValueError, TypeError):
        fail(422, 'INVALID_DATE', '请输入有效业务日期')


def _version(row, payload):
    if payload.get('expected_version') != row.version:
        fail(409, 'STALE_VERSION', '记录版本已变化，请刷新后重试', current_version=row.version)


def _editable(db, project):
    if project.origin_kind != 'managed' or project.business_status != 'draft' or db.query(StaffingPlan.id).filter_by(project_id=project.id, state='applied').first():
        fail(409, 'PROJECT_NOT_EDITABLE', '只有尚未执行的组队项目可以修改需求或方案')


REQUIREMENT_FIELD_LABELS = {
    'schema_version': '需求版本', 'start_date': '开始日期', 'end_date': '结束日期（不含）',
    'work_dates': '工作日', 'slots': '岗位', 'role_name': '岗位名称', 'units_per_day': '每日投入比例',
    'required_skills': '必备技能', 'preferred_skills': '偏好技能', 'employee_ids': '员工范围',
    'required_employee_ids': '指定员工', 'excluded_employee_ids': '排除员工', 'revision': '需求版本号',
    'slot_id': '岗位编号',
}


def _requirement_errors(exc):
    """Turn pydantic locations into the exact field the form should fix."""
    items = []
    for error in exc.errors():
        parts = [str(part) for part in error['loc'] if part not in ('body', 'requirements')]
        labels = []
        for part in parts:
            if part.isdigit():
                # "slots.0.units_per_day" reads as "岗位 1 · 每日投入比例" to the user.
                if labels and labels[-1] == '岗位':
                    labels[-1] = f'岗位 {int(part) + 1}'
                continue
            labels.append(REQUIREMENT_FIELD_LABELS.get(part, part))
        field = ' · '.join(label for label in labels if label) or '需求'
        kind, ctx = error.get('type'), error.get('ctx') or {}
        message = (error.get('msg') or '不合法').removeprefix('Value error, ')
        if kind == 'missing':
            message = '不能为空'
        elif kind in ('int_type', 'int_parsing', 'string_type', 'list_type', 'dict_type'):
            message = '格式不正确'
        elif kind in ('greater_than_equal', 'greater_than'):
            message = f'不能小于 {ctx.get("ge", ctx.get("gt"))}'
        elif kind in ('less_than_equal', 'less_than'):
            message = f'不能大于 {ctx.get("le", ctx.get("lt"))}'
        elif kind in ('too_short', 'string_too_short'):
            message = f'至少需要 {ctx.get("min_length", 1)} 项' if 'min_length' in ctx else '数量不足'
        elif kind in ('too_long', 'string_too_long'):
            message = f'最多 {ctx.get("max_length")} 项' if 'max_length' in ctx else '数量超出限制'
        elif kind == 'literal_error':
            message = '取值不受支持'
        items.append({'field': field, 'message': message})
    return items


def _confirmed(db, payload, old=None):
    if payload.get('confirmed') is not True:
        fail(422, 'CONFIRMATION_REQUIRED', '请先明确确认结构化需求')
    try:
        req = ConfirmedRequirements.model_validate(payload['requirements'])
    except (ValidationError, KeyError) as exc:
        errors = _requirement_errors(exc) if isinstance(exc, ValidationError) else []
        summary = '；'.join(f'{item["field"]}：{item["message"]}' for item in errors[:3]) or '需求字段不完整或约束不合法'
        fail(422, 'INVALID_REQUIREMENTS', f'需求未通过校验：{summary}', errors=errors)
    existing = {s['slot_id'] for s in old.get('slots', [])} if old is not None else None
    req = assign_slot_ids(req, existing)
    employees = db.query(Employee).filter(Employee.id.in_(req.employee_ids)).all()
    if len(employees) != len(req.employee_ids) or any(e.employment_status != 'active' for e in employees):
        fail(422, 'INVALID_EMPLOYEE_SCOPE', '员工范围包含已离职或不可用的员工，请刷新后重试')
    employee_names = {str(employee.display_name).strip() for employee in employees if str(employee.display_name).strip()}
    mistaken_names = [slot.role_name.strip() for slot in req.slots if slot.role_name.strip() in employee_names]
    if mistaken_names:
        fail(422, 'ROLE_NAME_IS_EMPLOYEE', f'岗位名称不能填写员工姓名（{"、".join(mistaken_names)}），请改成岗位或职责名称', names=mistaken_names)
    overrides = {r.work_date: r.is_workday for r in db.query(CompanyWorkDay).filter(CompanyWorkDay.work_date.in_(req.work_dates)).all()}
    dates = {day for day in req.work_dates if overrides.get(day, default_workday(day))}
    if dates != set(req.work_dates):
        invalid = sorted(set(req.work_dates) - dates)
        fail(422, 'UNKNOWN_WORK_CALENDAR', f'这些日期不是公司工作日：{"、".join(day.isoformat() for day in invalid[:5])}', dates=[day.isoformat() for day in invalid])
    for field in ('start_date', 'end_date'):
        if field in payload and _date(payload[field]) != getattr(req, field):
            fail(422, 'INVALID_REQUIREMENTS', '项目日期须与已确认需求一致')
    # Match the evidence extractor's small explicit alias table only.
    from app.services.staffing.evidence import normalize_skill
    for slot in req.slots:
        slot.required_skills = list(dict.fromkeys(normalize_skill(s) for s in slot.required_skills if str(s).strip()))
        slot.preferred_skills = list(dict.fromkeys(normalize_skill(s) for s in slot.preferred_skills if str(s).strip()))
    return req


def message_dict(message):
    return {'id': message.id, 'project_id': message.project_id, 'author_id': message.author_id, 'role': message.role,
        'content': message.content, 'requirements_revision': message.requirements_revision, 'run_id': message.run_id,
        'status': message.status, 'draft': json_load(message.draft_json, {}) or None,
        'created_at': message.created_at.isoformat() if message.created_at else None}


def project_dict(db, project, include_messages=True):
    latest = db.query(StaffingRun).filter_by(project_id=project.id).order_by(StaffingRun.created_at.desc(), StaffingRun.id).first()
    result = {field: getattr(project, field) for field in ('id', 'title', 'description', 'origin_kind', 'requirements_revision', 'business_status', 'created_by', 'version')}
    result.update(start_date=project.start_date.isoformat(), end_date=project.end_date.isoformat(), requirements=json_load(project.requirements_json, {}),
        employee_ids=json_load(project.scope_json, []), created_at=project.created_at.isoformat() if project.created_at else None,
        updated_at=project.updated_at.isoformat() if project.updated_at else None, latest_run=None)
    latest_plan = db.query(StaffingPlan).filter_by(project_id=project.id).order_by(
        (StaffingPlan.state=='applied').desc(),StaffingPlan.revision_no.desc()).first()
    result['latest_plan_state'] = latest_plan.state if latest_plan else None
    if latest:
        result['latest_run'] = {'id': latest.id, 'status': latest.status, 'version': latest.version, 'requirements_revision': latest.requirements_revision,
            'event_seq': latest.event_seq, 'questions': json_load(latest.questions_json, []), 'error_code': latest.error_code, 'mode': latest.mode,
            'result': json_load(latest.result_json, {})}
    if include_messages:
        result['messages'] = [message_dict(m) for m in db.query(StaffingMessage).filter_by(project_id=project.id).order_by(StaffingMessage.created_at, StaffingMessage.id).all()]
    return result


def create_project(factory, actor_id, payload):
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        title = str(payload.get('title', '')).strip()
        if not title or len(title) > 160:
            fail(422, 'INVALID_PROJECT', '项目名称须为1至160个字符')
        origin = payload.get('origin_kind', 'managed')
        if origin not in ('managed', 'baseline'):
            fail(422, 'INVALID_PROJECT', '项目来源无效')
        if origin == 'baseline' and db.get(StaffingResourceState, 1).bootstrap_closed_at:
            fail(409, 'BOOTSTRAP_CLOSED', '基础台账初始化已永久关闭')
        if origin == 'baseline' and payload.get('requirements'):
            fail(422, 'INVALID_PROJECT', '已有项目不需要编造组队需求')
        req = _confirmed(db, payload) if payload.get('requirements') else None
        start, end = (req.start_date, req.end_date) if req else (_date(payload.get('start_date')), _date(payload.get('end_date')))
        if not 0 < (end-start).days <= 90:
            fail(422, 'SCOPE_LIMIT', '项目日期范围须为1至90日，结束日不计入')
        if req:
            req.revision = 1
        project = StaffingProject(title=_redact(title), description=_redact(payload.get('description', ''))[:20000], origin_kind=origin,
            start_date=start, end_date=end, requirements_json=json_dump(req.model_dump(mode='json') if req else {}),
            requirements_revision=1 if req else 0, scope_json=json_dump(req.employee_ids if req else []), created_by=actor_id)
        db.add(project)
        db.flush()
        return project_dict(db, project)


def update_project(factory, actor_id, project_id, payload):
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        project = require_project(db, project_id)
        _version(project, payload)
        _editable(db, project)
        if 'requirements' in payload:
            if payload.get('expected_requirements_revision') != project.requirements_revision:
                fail(409, 'STALE_REQUIREMENTS', '确认需求版本已变化')
            if db.query(StaffingRun.id).filter(StaffingRun.project_id == project.id, StaffingRun.status.in_(['queued', 'running', 'waiting_input'])).first():
                fail(409, 'ACTIVE_RUN', '请先显式取消当前生成任务，再确认需求修改')
            old = json_load(project.requirements_json, {})
            req = _confirmed(db, payload, old if old else None)
            project.requirements_revision += 1
            req.revision = project.requirements_revision
            project.requirements_json = json_dump(req.model_dump(mode='json'))
            project.scope_json = json_dump(req.employee_ids)
            project.start_date, project.end_date = req.start_date, req.end_date
            db.query(StaffingPlan).filter(StaffingPlan.project_id == project.id, StaffingPlan.state != 'applied').update(
                {'state': 'superseded', 'version': StaffingPlan.version + 1}, synchronize_session=False)
        elif 'start_date' in payload or 'end_date' in payload:
            fail(422, 'CONFIRMATION_REQUIRED', '请通过确认结构化需求修改日期')
        if 'title' in payload:
            title = str(payload['title']).strip()
            if not title or len(title) > 160:
                fail(422, 'INVALID_PROJECT', '项目名称须为1至160个字符')
            project.title = _redact(title)
        if 'description' in payload:
            project.description = _redact(payload['description'])[:20000]
        project.version += 1
        project.updated_at = utcnow()
        db.flush()
        return project_dict(db, project)


CHANGE_PATTERN = re.compile(
    r'改|调整|新增|增加|删|去掉|换成|延后|提前|延长|缩短|各投|投入比例|工作日|开始日期|结束日期|排期|比例|岗位|技能')
QUESTION_PATTERN = re.compile(r'为什么|为何|怎么|如何|是否|能否|依据|原因|区别|风险|[？?]')


def detect_intent(content, requirements):
    """Plain-language change requests must reach the draft path, questions the explain path."""
    if not requirements:
        return 'requirements'
    has_change = bool(CHANGE_PATTERN.search(content))
    has_question = bool(QUESTION_PATTERN.search(content))
    if has_change and not has_question:
        return 'requirements'
    if has_change and has_question and re.search(r'改|调整|新增|增加|删|去掉|换成|延长|缩短|各投', content):
        return 'requirements'
    return 'explain'


def _business_reply(text):
    """Models occasionally wrap internal names in backticks; business replies carry plain text."""
    return re.sub(r'`+([^`\n]{1,120})`+', r'\1', text).replace('```', '').strip()


PLAN_STATE_PRIORITY = {'applied': 5, 'submitted': 4, 'validated': 3, 'invalidated': 2, 'draft': 1}
NOT_SELECTED_REASONS = {
    'SKILL_NOT_RECORDED': '简历未记载必备技能：{skills}',
    'UNKNOWN_CAPACITY': '部分日期的可投入比例未知',
    'CAPACITY_CONFLICT': '排期冲突，剩余可投入不足',
    'EMPLOYEE_UNAVAILABLE': '员工不可用',
    'INVALID_EMPLOYEE_SCOPE': '员工已不在项目范围',
    'DUPLICATE_EMPLOYEE': '同一员工不能同时承担两个岗位',
    'EMPTY_SLOT_POOL': '该岗位没有已确认合格的员工',
}


def _not_selected(db, project, requirements, plan_row):
    """Who was in scope but not chosen, and the recorded reason — this answers "为什么不是某人"."""
    if plan_row is None:
        return []
    run = db.query(StaffingRun).filter_by(project_id=project.id, requirements_revision=project.requirements_revision).order_by(
        StaffingRun.created_at.desc()).first()
    result = json_load(run.result_json, {}) if run else {}
    selected = {item['employee_id'] for item in json_load(plan_row.assignments_json, [])}
    scope_ids = requirements.get('employee_ids', [])
    names = {row.id: row.display_name for row in db.query(Employee).filter(Employee.id.in_(scope_ids)).all()}
    reasons = {}
    for item in result.get('diagnostics') or []:
        identity = item.get('employee_id')
        if not identity or identity not in names:
            continue
        template = NOT_SELECTED_REASONS.get(str(item.get('code')))
        if not template:
            continue
        reasons.setdefault(identity, []).append(template.format(skills='、'.join(item.get('skills') or [])) if '{skills}' in template else template)
    rows = []
    for identity, name in names.items():
        if identity in selected:
            continue
        recorded = list(dict.fromkeys(reasons.get(identity, [])))[:3]
        rows.append({'name': name, 'reasons': recorded or ['没有被求解器选中，也没有记录具体原因']})
    return rows[:20]


def _explanation_context(db, project):
    """Business-language facts only: no internal ids, arrays or fingerprints reach the prompt."""
    from app.services.staffing.proposals import plan_dict
    requirements = json_load(project.requirements_json, {})
    # Explain the same plan the page shows: state priority first, then newest revision.
    plans = [row for row in db.query(StaffingPlan).filter_by(project_id=project.id).all() if row.state not in ('superseded', 'rejected')]
    plans.sort(key=lambda row: (PLAN_STATE_PRIORITY.get(row.state, 0), row.revision_no), reverse=True)
    scope_ids = requirements.get('employee_ids', [])
    scope_names = [row.display_name for row in db.query(Employee).filter(Employee.id.in_(scope_ids)).order_by(Employee.display_name).all()]
    context = {
        'title': project.title,
        'start_date': project.start_date.isoformat(),
        'end_date_inclusive': (project.end_date - timedelta(days=1)).isoformat(),
        'work_day_count': len(requirements.get('work_dates', [])),
        'work_days': requirements.get('work_dates', []),
        'scope_employee_count': len(scope_ids),
        'scope_names': scope_names[:60],
        'slots': [{'role': slot.get('role_name'), 'units_per_100': slot.get('units_per_day'),
                   'required_skills': slot.get('required_skills', []),
                   'preferred_skills': slot.get('preferred_skills', [])}
                  for slot in requirements.get('slots', [])],
        'current_plan': None,
        'not_selected': [],
    }
    if plans:
        plan = plan_dict(db, plans[0])
        context['current_plan'] = {
            'state': plan['state'],
            'solver_status': plan['solver_status'],
            'members': [{'name': item.get('display_name'), 'role': item.get('role_name'),
                         'units_per_100': item.get('units_per_day'), 'work_days': len(item.get('work_dates', []))}
                        for item in plan['assignments']],
            'evidence': [{'name': item.get('display_name') or item.get('employee_id'), 'status': item.get('status'),
                          'facts': [{'fact': fact.get('normalized_key'),
                                     'quote': (fact.get('source_refs') or [{}])[0].get('quote', '')}
                                    for fact in (item.get('facts') or [])[:4]]}
                         for item in plan.get('evidence', [])[:6]],
            'blockers': [item.get('message') or item.get('code') for item in (plan.get('validation') or {}).get('blockers', [])],
            'valid': (plan.get('validation') or {}).get('valid'),
        }
        context['not_selected'] = _not_selected(db, project, requirements, plans[0])
    return context


def add_message(factory, actor_id, project_id, payload, draft_provider=None, explain_provider=None):
    content = _redact(payload.get('content', '')).strip()
    if not content or len(content) > 10000:
        fail(422, 'INVALID_MESSAGE', '消息须为1至10000个字符')
    # Read only before optional model work; close the Session before acquiring a write lock.
    with factory() as db:
        user = require_actor(db, actor_id)
        project = require_project(db, project_id)
        revision = project.requirements_revision
        if payload.get('expected_requirements_revision') != revision:
            fail(409, 'STALE_REQUIREMENTS', '需求版本已变化，请刷新后重试')
        requirements = json_load(project.requirements_json, {})
        intent = payload.get('intent') or detect_intent(content, requirements)
        if intent not in ('requirements', 'explain'):
            fail(422, 'INVALID_MESSAGE', '消息意图无效')
        if intent == 'requirements':
            if user.role != 'hr':
                fail(403, 'ROLE_DENIED', '只有人事可以提出需求变更')
            _editable(db, project)
        context = _explanation_context(db, project) if intent == 'explain' else None
    draft = None
    if intent == 'requirements':
        if 'requirements' in payload:
            proposed = {'requirements': payload['requirements'], 'missing_fields': [], 'changes': {}}
        else:
            if draft_provider is None:
                from app.services.staffing.model import requirement_draft
                draft_provider = requirement_draft
            from app.services.llm import ModelUnavailableError
            try:
                proposed = draft_provider(content, requirements)
            except ModelUnavailableError:
                fail(503, 'MODEL_UNAVAILABLE', '需求整理服务暂不可用，请重试或使用表单')
        try:
            draft = RequirementDraft.model_validate(proposed).model_dump(mode='json')
        except (ValidationError, TypeError):
            fail(503, 'MODEL_INVALID_OUTPUT', '需求草稿未通过结构校验，请重试或使用表单')
        response = '已整理需求草稿，请核对角色、工作日、投入比例与员工范围后明确确认。当前正式需求和排期尚未更新。'
    elif '批准' in content:
        response = '批准须通过方案上的正式批准按钮，核对具体员工、角色、日期和每日投入比例后执行。项目对话不会占用排期。'
    elif not context.get('current_plan'):
        response = '当前项目还没有推荐方案。请先确认需求并生成推荐，之后我才能解释推荐人员的简历依据、工作日安排和排期冲突。'
    else:
        if explain_provider is None:
            from app.services.staffing.model import explain_project
            explain_provider = explain_project
        from app.services.llm import ModelUnavailableError
        try:
            response = explain_provider(content, context)
        except ModelUnavailableError:
            fail(503, 'MODEL_UNAVAILABLE', '方案解释服务暂不可用，请重试')
        if not isinstance(response, str) or not response.strip() or len(response) > 10000:
            fail(503, 'MODEL_INVALID_OUTPUT', '方案解释未通过校验，请重试')
        response = _business_reply(_redact(response))
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr' if intent == 'requirements' else None)
        project = require_project(db, project_id)
        if project.requirements_revision != revision:
            fail(409, 'STALE_REQUIREMENTS', '模型处理期间需求已变化，请重新发送')
        if intent == 'requirements':
            _editable(db, project)
        elif '批准' not in content and _explanation_context(db, project) != context:
            fail(409, 'STALE_INPUT', '解释期间方案依据或资源已变化，请重新提问')
        user_message = StaffingMessage(project_id=project.id, author_id=actor_id, role='user', content=content, requirements_revision=revision)
        assistant = StaffingMessage(project_id=project.id, role='assistant', content=response, requirements_revision=revision, draft_json=json_dump(draft or {}))
        db.add_all([user_message, assistant]); db.flush()
        return {'user_message': message_dict(user_message), 'assistant_message': message_dict(assistant), 'draft': draft, 'requirements_revision': revision}
