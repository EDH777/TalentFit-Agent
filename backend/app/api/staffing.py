"""Company-shared project workbench. Resource execution remains explicit."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.employees import require_staffing_approver, require_staffing_editor, staffing_session_factory
from app.db.session import get_db
from app.models import User
from app.models.staffing import ResourceAllocation, StaffingMessage, StaffingPlan, StaffingProject
from app.schemas.staffing import AllocationAdjustRequest, Assignment
from app.services.staffing import projects, proposals, runs
from app.services.staffing.common import fail, require_actor, require_project

router = APIRouter(prefix='/api/staffing', tags=['staffing-projects'])


class Request(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ProjectCreate(Request):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=20000)
    origin_kind: Literal['managed', 'baseline'] = 'managed'
    start_date: date | None = None
    end_date: date | None = None
    requirements: dict | None = None
    confirmed: bool = False


class ProjectPatch(Request):
    expected_version: int = Field(ge=1, strict=True)
    expected_requirements_revision: int | None = Field(default=None, ge=0, strict=True)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=20000)
    requirements: dict | None = None
    confirmed: bool = False


class VersionRequest(Request):
    expected_version: int = Field(ge=1, strict=True)


class ActionRequest(VersionRequest):
    input_fingerprint: str = Field(min_length=64, max_length=64)


class VariantRequest(VersionRequest):
    assignments: list[Assignment] = Field(min_length=1, max_length=10)


class MessageRequest(Request):
    content: str = Field(min_length=1, max_length=10000)
    expected_requirements_revision: int = Field(ge=0, strict=True)
    intent: Literal['explain', 'requirements'] | None = None
    requirements: dict | None = None


class BaselineAllocation(Request):
    employee_id: str
    start_date: date
    end_date: date
    work_dates: list[date] = Field(min_length=1, max_length=90)
    units_per_day: int = Field(ge=1, le=100, strict=True)
    basis: str = Field(min_length=1, max_length=2000)


class BaselineRequest(Request):
    project_id: str
    expected_project_version: int = Field(ge=1, strict=True)
    allocations: list[BaselineAllocation] = Field(min_length=1, max_length=100)


class FinalizeRequest(Request):
    expected_revision: int = Field(ge=0, strict=True)


class RecommendationRequest(VersionRequest):
    requirements_revision: int = Field(ge=0, strict=True)
    mode: Literal['multi', 'single'] = 'multi'


class RecommendationAction(Request):
    plan_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1, strict=True)
    input_fingerprint: str = Field(min_length=64, max_length=64)


@router.get('/projects')
def list_projects(status: Literal['draft', 'active', 'completed', 'cancelled', 'archived'] | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_actor(db, user.id)
    query = db.query(StaffingProject)
    if status:
        query = query.filter_by(business_status=status)
    return [projects.project_dict(db, p, include_messages=False) for p in query.order_by(StaffingProject.created_at.desc(), StaffingProject.id).all()]


@router.post('/projects')
def create_project(payload: ProjectCreate, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return projects.create_project(factory, user.id, payload.model_dump(mode='json', exclude_none=True))


@router.get('/projects/{project_id}')
def get_project(project_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_actor(db, user.id)
    return projects.project_dict(db, require_project(db, project_id))


@router.patch('/projects/{project_id}/requirement')
def save_project_requirement(project_id: str, payload: ProjectPatch, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    """Saving the requirement form confirms the current requirement in one action."""
    return projects.update_project(factory, user.id, project_id, payload.model_dump(mode='json', exclude_unset=True, exclude_none=True))


@router.post('/projects/{project_id}/recommendation', status_code=202)
def generate_recommendation(project_id: str, payload: RecommendationRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    """Business facade for the full resumable Multi-Agent recommendation run."""
    return runs.create_run(factory, user.id, project_id, payload.model_dump(mode='json'))


def _recommendation_plan(factory, actor_id: int, project_id: str, payload: RecommendationAction):
    with factory() as db:
        require_actor(db, actor_id)
        plan = db.get(StaffingPlan, payload.plan_id)
        if not plan or plan.project_id != project_id:
            fail(404, 'NOT_FOUND', '当前项目没有这套推荐方案')
    return payload.model_dump(mode='json')


@router.post('/projects/{project_id}/recommendation/submit')
def submit_recommendation(project_id: str, payload: RecommendationAction, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    data = _recommendation_plan(factory, user.id, project_id, payload)
    plan_id = data.pop('plan_id')
    with factory() as db:
        plan = db.get(StaffingPlan, plan_id)
        if plan and plan.state in ('draft', 'invalidated'):
            checked = proposals.validate_plan(factory, user.id, plan_id, {'expected_version': data['expected_version']})
            data['expected_version'] = checked['version']
            data['input_fingerprint'] = checked['input_fingerprint']
    return proposals.submit(factory, user.id, plan_id, data, request_key)


@router.post('/projects/{project_id}/recommendation/approve')
def approve_recommendation(project_id: str, payload: RecommendationAction, user: User = Depends(require_staffing_approver), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    data = _recommendation_plan(factory, user.id, project_id, payload)
    return proposals.approve(factory, user.id, data.pop('plan_id'), data, request_key)


@router.get('/projects/{project_id}/messages')
def messages(project_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_actor(db, user.id); require_project(db, project_id)
    return [projects.message_dict(m) for m in db.query(StaffingMessage).filter_by(project_id=project_id).order_by(StaffingMessage.created_at, StaffingMessage.id).all()]


@router.post('/projects/{project_id}/messages')
def post_message(project_id: str, payload: MessageRequest, user: User = Depends(current_user), factory=Depends(staffing_session_factory)):
    return projects.add_message(factory, user.id, project_id, payload.model_dump(mode='json', exclude_none=True))


@router.get('/projects/{project_id}/plans')
def plans(project_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_actor(db, user.id)
    return proposals.list_plans(db, project_id)


@router.post('/plans/{plan_id}/variants')
def variant(plan_id: str, payload: VariantRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return proposals.variant(factory, user.id, plan_id, payload.model_dump(mode='json', exclude_none=True))


@router.post('/plans/{plan_id}/validate')
def validate(plan_id: str, payload: VersionRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return proposals.validate_plan(factory, user.id, plan_id, payload.model_dump())


@router.post('/plans/{plan_id}/reject')
def reject(plan_id: str, payload: ActionRequest, user: User = Depends(require_staffing_approver), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    return proposals.reject(factory, user.id, plan_id, payload.model_dump(), request_key)


@router.post('/projects/{project_id}/archive')
def archive(project_id: str, payload: VersionRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    return proposals.archive(factory, user.id, project_id, payload.model_dump(), request_key)


@router.post('/projects/{project_id}/complete')
def complete_project(project_id: str, payload: VersionRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    return proposals.complete_project(factory, user.id, project_id, payload.model_dump(), request_key)


@router.post('/projects/{project_id}/cancel')
def cancel_project(project_id: str, payload: VersionRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    return proposals.cancel_project(factory, user.id, project_id, payload.model_dump(), request_key)


@router.post('/allocations/baseline')
def baseline(payload: BaselineRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    return proposals.add_baseline(factory, user.id, payload.model_dump(mode='json'), request_key)


@router.post('/allocations/{allocation_id}/complete')
def complete_allocation(allocation_id: str, payload: VersionRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    return proposals.complete_allocation(factory, user.id, allocation_id, payload.model_dump(), request_key)


@router.post('/allocations/{allocation_id}/cancel')
def cancel_allocation(allocation_id: str, payload: VersionRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    return proposals.cancel_allocation(factory, user.id, allocation_id, payload.model_dump(), request_key)


@router.patch('/allocations/{allocation_id}')
def adjust_allocation(allocation_id: str, payload: AllocationAdjustRequest, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    return proposals.adjust_allocation(factory, user.id, allocation_id, payload.model_dump(mode='json'), request_key)


@router.get('/allocations')
def allocations(db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_actor(db,user.id)
    rows = db.query(ResourceAllocation).order_by(ResourceAllocation.created_at.desc(),ResourceAllocation.id).all()
    return [proposals.allocation_dict(db, row) for row in rows]


@router.post('/setup/finalize')
def finalize(payload: FinalizeRequest, user: User = Depends(require_staffing_approver), factory=Depends(staffing_session_factory), request_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160)):
    return proposals.finalize_setup(factory, user.id, payload.model_dump(), request_key)
