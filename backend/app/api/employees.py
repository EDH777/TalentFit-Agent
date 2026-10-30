"""Employee staffing resources; write services always open fresh transactions."""
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db.session import SessionLocal, get_db
from app.models import User
from app.models.staffing import Employee, StaffingResourceState
from app.services.staffing import capacity, employees, evidence
from app.services.staffing.common import fail

router = APIRouter(prefix='/api/staffing', tags=['staffing-employees'])


def staffing_session_factory():
    return SessionLocal


def require_staffing_editor(user: User = Depends(current_user)):
    if user.role != 'hr':
        fail(403, 'ROLE_DENIED', '只有人事可以维护员工资源')
    return user


def require_staffing_approver(user: User = Depends(current_user)):
    if user.role != 'admin':
        fail(403, 'ROLE_DENIED', '只有管理员可以审批资源占用')
    return user


@router.get('/employees')
def list_employees(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [employees.employee_dict(db, row, include_facts=False) for row in db.query(Employee).order_by(Employee.created_at, Employee.id).all()]


@router.post('/employees')
def create_employee(payload: dict, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return employees.create_employee(factory, user.id, payload)


@router.post('/employees/from-documents')
def from_documents(payload: dict, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return employees.from_documents(factory, user.id, payload.get('documents', []))


@router.patch('/employees/{employee_id}')
def update_employee(employee_id: str, payload: dict, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return employees.update_employee(factory, user.id, employee_id, payload)


@router.put('/employees/{employee_id}/documents')
def set_documents(employee_id: str, payload: dict, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return employees.set_documents(factory, user.id, employee_id, payload)


@router.get('/employees/{employee_id}/resume-facts')
def resume_facts(employee_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return evidence.current_facts(db, employee_id)


@router.post('/employees/{employee_id}/resume-facts/retry')
def retry_resume_facts(employee_id: str, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return employees.retry_facts(factory, user.id, employee_id)


@router.get('/employees/{employee_id}/capacity-days')
def capacity_days(employee_id: str, start_date: date, end_date: date, db: Session = Depends(get_db), user: User = Depends(current_user)):
    employees.require_employee(db, employee_id)
    return capacity.snapshot(db, [employee_id], start_date, end_date)


@router.put('/employees/{employee_id}/capacity-days')
def set_capacity_days(employee_id: str, payload: dict, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return capacity.set_capacity_days(factory, user.id, employee_id, payload)


@router.get('/work-calendar')
def work_calendar(start_date: date, end_date: date, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {'days': capacity.work_calendar(db, start_date, end_date)}


@router.put('/work-calendar')
def set_calendar(payload: dict, user: User = Depends(require_staffing_editor), factory=Depends(staffing_session_factory)):
    return capacity.set_work_calendar(factory, user.id, payload)


@router.get('/setup')
def setup_state(db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(StaffingResourceState, 1)
    if not row:
        fail(503, 'SOURCE_UNAVAILABLE', '资源台账尚未初始化')
    return {'revision': row.revision, 'bootstrap_closed': row.bootstrap_closed_at is not None,
        'bootstrap_closed_at': row.bootstrap_closed_at, 'bootstrap_closed_by': row.bootstrap_closed_by}
