"""Fresh-session resource transactions and stable staffing errors."""
from contextlib import contextmanager
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.models import User
from app.models.staffing import StaffingProject, StaffingResourceState


def fail(status, code, message, **extra):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message, **extra})


def json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def json_load(value, default=None):
    return json.loads(value) if value else default


def business_today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def ensure_state(db):
    """Startup/test initialization only; never called by resource_write."""
    if db.get(StaffingResourceState, 1) is None:
        db.add(StaffingResourceState(id=1, revision=0))
        db.flush()


@contextmanager
def resource_write(factory, *, advance_revision=True):
    with factory() as db:
        try:
            with db.begin():
                result = db.execute(update(StaffingResourceState).where(StaffingResourceState.id == 1).values(
                    revision=StaffingResourceState.revision + (1 if advance_revision else 0)))
                if result.rowcount != 1:
                    fail(503, 'SOURCE_UNAVAILABLE', '资源台账尚未初始化')
                yield db
        except OperationalError as exc:
            if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
                fail(409, 'RESOURCE_BUSY', '资源台账正在更新，请重试')
            raise
        except IntegrityError:
            fail(409, 'IDENTITY_CONFLICT', '身份或业务记录已存在，请刷新后重试')


def require_actor(db, actor_id, role=None):
    user = db.get(User, actor_id)
    if not user or not user.is_active:
        fail(401, 'UNAUTHENTICATED', '账号不可用')
    if user.role not in ('hr', 'admin') or role and user.role != role:
        fail(403, 'ROLE_DENIED', '当前账号没有此操作权限')
    return user


def require_project(db, project_id):
    project = db.get(StaffingProject, project_id)
    if not project:
        fail(404, 'NOT_FOUND', '项目不存在')
    return project
