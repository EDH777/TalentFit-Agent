"""Durable execution authorization, fenced leases and ordered safe events."""
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.entities import utcnow
from app.models.staffing import StaffingRun, StaffingEvent, StaffingProject, new_id
from app.services.staffing.common import resource_write, require_actor, require_project, fail, json_dump, json_load

ACTIVE = ('queued', 'running', 'waiting_input')
TERMINAL = ('succeeded', 'failed', 'cancelled')


class LeaseLost(RuntimeError):
    pass


def run_dict(run):
    return {'id': run.id, 'project_id': run.project_id, 'requirements_revision': run.requirements_revision,
            'version': run.version, 'status': run.status, 'mode': run.mode,
            'last_event_seq': run.event_seq, 'event_seq': run.event_seq,
            'questions': json_load(run.questions_json, []), 'result': json_load(run.result_json, {}),
            'error_code': run.error_code, 'replaces_run_id': run.replaces_run_id, 'attempt': run.attempt,
            'created_at': run.created_at, 'updated_at': run.updated_at, 'finished_at': run.finished_at}


def _get(db, run_id):
    run = db.get(StaffingRun, run_id)
    if not run:
        fail(404, 'NOT_FOUND', '运行不存在')
    require_project(db, run.project_id)
    return run


def _version(run, payload):
    if payload.get('expected_version') != run.version or payload.get('requirements_revision') != run.requirements_revision:
        fail(409, 'STALE_INPUT', '运行版本已变化，请刷新后重试')


def _safe(value):
    from app.services.agent import redact_contacts
    if isinstance(value, str):
        return redact_contacts(value)[:1000]
    if isinstance(value, list):
        return [_safe(v) for v in value[:100]]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items() if k not in (
            'prompt', 'raw_text', 'processed_text', 'token', 'authorization', 'content', 'secret', 'tool_output')}
    return value


def _event(db, run, event_type, payload):
    allowed = {'node', 'summary', 'status', 'detail', 'code', 'questions', 'plan_ids', 'solver_status',
               'diagnostics', 'metrics', 'tool_name', 'call_id', 'count', 'elapsed_ms', 'replaces_run_id'}
    payload = _safe({k: v for k, v in payload.items() if k in allowed})
    if 'node' in payload:
        payload['node'] = {k: v for k, v in payload['node'].items() if k in (
            'id', 'node_id', 'title', 'node_type', 'namespace', 'status', 'started_at', 'elapsed_ms', 'summary')}
    run.event_seq += 1
    row = StaffingEvent(run_id=run.id, seq=run.event_seq, type=event_type, safe_payload_json=json_dump(payload))
    db.add(row)
    return {'seq': row.seq, 'run_id': run.id, 'type': event_type, **payload}


def _authorized(db, run_id, fence):
    run = db.get(StaffingRun, run_id)
    now = utcnow().replace(tzinfo=None)
    if not run or run.status != 'running' or run.fence_token != fence or not run.lease_until or run.lease_until.replace(tzinfo=None) <= now:
        raise LeaseLost('运行执行权已失效')
    project = db.get(StaffingProject, run.project_id)
    if not project or project.business_status != 'draft' or project.requirements_revision != run.requirements_revision:
        raise LeaseLost('需求已变更')
    try:
        require_actor(db, run.actor_id, 'hr')
    except Exception as exc:
        raise LeaseLost('执行账号已失效') from exc
    return run


def is_authorized(factory, run_id, fence):
    with factory() as db:
        try:
            _authorized(db, run_id, fence)
            return True
        except LeaseLost:
            return False


def create_run(factory, actor_id, project_id, payload):
    with resource_write(factory, advance_revision=False) as db:
        require_actor(db, actor_id, 'hr')
        project = require_project(db, project_id)
        if project.business_status != 'draft' or project.origin_kind != 'managed':
            fail(409, 'PROJECT_LOCKED', '只有未执行的组队项目可以生成方案')
        if payload.get('expected_version') != project.version or payload.get('requirements_revision') != project.requirements_revision:
            fail(409, 'STALE_INPUT', '需求版本已变化，请刷新后重试')
        if project.requirements_revision < 1:
            fail(422, 'REQUIREMENTS_UNCONFIRMED', '请先确认项目需求')
        if db.query(StaffingRun).filter(StaffingRun.project_id == project_id, StaffingRun.status.in_(ACTIVE)).first():
            fail(409, 'ACTIVE_RUN', '此项目已有活跃运行，请先取消旧运行')
        mode = payload.get('mode', 'multi')
        if mode not in ('multi', 'single'):
            fail(422, 'INVALID_MODE', '无效的组队模式')
        identity = new_id()
        run = StaffingRun(id=identity, checkpoint_thread_id=identity, project_id=project_id, actor_id=actor_id,
                          requirements_revision=project.requirements_revision, mode=mode)
        db.add(run)
        try:
            db.flush()
        except IntegrityError:
            # A concurrent request won the single-active-run index; report the business meaning.
            fail(409, 'ACTIVE_RUN', '此项目已有活跃运行，请先取消旧运行')
        _event(db, run, 'run_queued', {'status': 'queued'})
        db.flush()
        return run_dict(run)


def get_run(factory, run_id, actor_id):
    with factory() as db:
        require_actor(db, actor_id)
        return run_dict(_get(db, run_id))


def project_runs(factory, project_id, actor_id):
    with factory() as db:
        require_actor(db, actor_id)
        require_project(db, project_id)
        return [run_dict(row) for row in db.query(StaffingRun).filter_by(project_id=project_id).order_by(StaffingRun.created_at.desc()).limit(100)]


def claim_run(factory, owner):
    with resource_write(factory, advance_revision=False) as db:
        queued = db.query(StaffingRun).filter_by(status='queued').order_by(StaffingRun.created_at, StaffingRun.id).all()
        for run in queued:
            project = db.get(StaffingProject, run.project_id)
            try:
                require_actor(db, run.actor_id, 'hr')
                valid = project and project.business_status == 'draft' and project.requirements_revision == run.requirements_revision
            except Exception:
                valid = False
            if not valid:
                run.status, run.error_code = 'failed', 'STALE_INPUT'
                run.version += 1
                run.finished_at = utcnow()
                _event(db, run, 'run_finished', {'status': 'failed', 'code': 'STALE_INPUT', 'detail': '需求或账号已变更，请重新发起'})
                continue
            expected = run.fence_token
            result = db.execute(update(StaffingRun).where(StaffingRun.id == run.id, StaffingRun.status == 'queued',
                StaffingRun.fence_token == expected).values(status='running', lease_owner=owner,
                lease_until=utcnow() + timedelta(seconds=settings.staffing_lease_seconds), fence_token=expected + 1,
                version=StaffingRun.version + 1, attempt=StaffingRun.attempt + 1,
                started_at=run.started_at or utcnow()).execution_options(synchronize_session=False))
            if not result.rowcount:
                continue
            db.refresh(run)
            _event(db, run, 'run_started', {'status': 'running'})
            db.flush()
            return {**run_dict(run), 'fence_token': run.fence_token, 'checkpoint_thread_id': run.checkpoint_thread_id,
                    'actor_id': run.actor_id, 'resume': json_load(run.resume_json, {})}
    return None


def heartbeat(factory, run_id, fence, owner):
    with resource_write(factory, advance_revision=False) as db:
        run = _authorized(db, run_id, fence)
        if run.lease_owner != owner:
            raise LeaseLost('执行者已变更')
        run.lease_until = utcnow() + timedelta(seconds=settings.staffing_lease_seconds)


def append_event(factory, run_id, fence, event_type, payload):
    with resource_write(factory, advance_revision=False) as db:
        return _event(db, _authorized(db, run_id, fence), event_type, payload)


def list_events(factory, run_id, after_seq, actor_id=None):
    with factory() as db:
        if actor_id is not None:
            require_actor(db, actor_id)
        _get(db, run_id)
        return [{'seq': row.seq, 'run_id': run_id, 'type': row.type, **json_load(row.safe_payload_json, {})}
                for row in db.query(StaffingEvent).filter(StaffingEvent.run_id == run_id, StaffingEvent.seq > after_seq).order_by(StaffingEvent.seq).limit(500)]


def cancel_run(factory, actor_id, run_id, payload):
    with resource_write(factory, advance_revision=False) as db:
        require_actor(db, actor_id, 'hr')
        run = _get(db, run_id)
        if run.status in TERMINAL:
            return run_dict(run)
        _version(run, payload)
        run.status, run.lease_owner, run.lease_until = 'cancelled', None, None
        run.fence_token += 1
        run.version += 1
        run.finished_at = utcnow()
        _event(db, run, 'run_finished', {'status': 'cancelled', 'detail': '运行已取消，未占用排期'})
        db.flush()
        return run_dict(run)


def mark_waiting(factory, run_id, fence, questions, metrics=None):
    """Call only AFTER graph stream closes and checkpointer has flushed interrupt."""
    with resource_write(factory, advance_revision=False) as db:
        run = _authorized(db, run_id, fence)
        run.questions_json = json_dump(_safe(questions))
        run.result_json = json_dump({'metrics': metrics or {}})
        run.status, run.lease_owner, run.lease_until = 'waiting_input', None, None
        run.version += 1
        _event(db, run, 'input_required', {'status': 'waiting_input', 'questions': questions})
        db.flush()
        return run_dict(run)


def resume_run(factory, actor_id, run_id, payload):
    with resource_write(factory, advance_revision=False) as db:
        require_actor(db, actor_id, 'hr')
        run = _get(db, run_id)
        _version(run, payload)
        project = require_project(db, run.project_id)
        if run.status != 'waiting_input' or run.lease_owner or run.lease_until:
            fail(409, 'RUN_NOT_WAITING', '只有安全暂停、等待补充的运行可以恢复')
        if project.requirements_revision != run.requirements_revision or project.business_status != 'draft':
            fail(409, 'STALE_INPUT', '需求已变更，请取消旧运行并重新生成')
        if set(payload) - {'expected_version', 'requirements_revision', 'answers'}:
            fail(422, 'INVALID_RESUME', '修改需求请取消旧运行后重新确认')
        # Facts are supplied via controlled employee/capacity APIs, never model-written resume values.
        run.resume_json = json_dump({'acknowledged': True})
        run.status = 'queued'
        run.version += 1
        _event(db, run, 'run_queued', {'status': 'queued', 'summary': '已请求重新读取当前资料与人员排期'})
        db.flush()
        return run_dict(run)


def replace_expired(factory):
    replacements = []
    with resource_write(factory, advance_revision=False) as db:
        expired = db.query(StaffingRun).filter(StaffingRun.status == 'running', StaffingRun.lease_until < utcnow()).all()
        for run in expired:
            run.status, run.error_code = 'failed', 'LEASE_EXPIRED'
            run.fence_token += 1
            run.version += 1
            run.lease_owner, run.lease_until, run.finished_at = None, None, utcnow()
            _event(db, run, 'run_finished', {'status': 'failed', 'code': 'LEASE_EXPIRED', 'detail': '执行进程中断，旧执行权已废止'})
            project = db.get(StaffingProject, run.project_id)
            if run.replaces_run_id or not project or project.requirements_revision != run.requirements_revision or project.business_status != 'draft':
                continue
            db.flush()  # release unique active-run slot before creating independent replacement
            identity = new_id()
            replacement = StaffingRun(id=identity, checkpoint_thread_id=identity, project_id=run.project_id,
                actor_id=run.actor_id, requirements_revision=run.requirements_revision, replaces_run_id=run.id, mode=run.mode)
            db.add(replacement)
            db.flush()
            _event(db, replacement, 'run_queued', {'status': 'queued', 'replaces_run_id': run.id})
            db.flush()
            replacements.append(run_dict(replacement))
    return replacements


def fail_run(factory, run_id, fence, code, detail, metrics=None):
    with resource_write(factory, advance_revision=False) as db:
        run = _authorized(db, run_id, fence)
        run.status, run.error_code, run.finished_at = 'failed', code, utcnow()
        run.lease_owner, run.lease_until = None, None
        run.version += 1
        run.result_json = json_dump({'metrics': metrics or {}})
        _event(db, run, 'run_finished', {'status': 'failed', 'code': code, 'detail': detail, 'metrics': metrics or {}})


def finish_run(factory, run_id, fence, solver_result, input_data, metrics=None, budget_guard=None):
    from app.services.staffing.proposals import save_generated_plans
    with resource_write(factory, advance_revision=False) as db:
        run = _authorized(db, run_id, fence)
        if budget_guard:
            budget_guard()
        project = require_project(db, run.project_id)
        plans = save_generated_plans(db, project, run.id, solver_result, input_data)
        if budget_guard:
            budget_guard()
        db.flush()
        result = {'solver_status': solver_result['status'], 'diagnostics': solver_result.get('diagnostics', []),
                  'reliability': solver_result.get('reliability', {}),
                  'plan_ids': [plan.id for plan in plans], 'scope_count': len(input_data.get('employees', [])), 'metrics': metrics or {}}
        run.result_json = json_dump(_safe(result))
        run.status, run.finished_at = 'succeeded', utcnow()
        run.lease_owner, run.lease_until = None, None
        run.version += 1
        if plans:
            _event(db, run, 'plan_saved', {'plan_ids': result['plan_ids']})
        _event(db, run, 'run_finished', {'status': 'succeeded', **result})
        db.flush()
        return run_dict(run)
