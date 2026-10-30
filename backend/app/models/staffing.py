"""Additive internal staffing domain; resume identities remain in candidates."""
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.entities import utcnow


def new_id():
    return str(uuid4())


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class Versioned:
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Employee(Identity, Versioned, Base):
    __tablename__ = 'employees'
    employee_no: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(120))
    department_name: Mapped[str] = mapped_column(String(160), default='')
    current_position: Mapped[str] = mapped_column(String(160), default='')
    position_origin: Mapped[str] = mapped_column(String(20), default='resume')
    employment_status: Mapped[str] = mapped_column(String(20), default='active')
    facts_status: Mapped[str] = mapped_column(String(20), default='pending')
    facts_fingerprint: Mapped[str] = mapped_column(String(64), default='')
    facts_extractor_version: Mapped[str] = mapped_column(String(40), default='')
    facts_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EmployeeDocument(Identity, Versioned, Base):
    __tablename__ = 'employee_documents'
    employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'), index=True)
    candidate_id: Mapped[int] = mapped_column(Integer, index=True)
    candidate_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    link_status: Mapped[str] = mapped_column(String(20), default='active')
    linked_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        Index('uq_employee_document_identity', 'candidate_id', 'candidate_created_at', unique=True, sqlite_where=text("link_status='active'")),
        Index('uq_employee_primary_document', 'employee_id', unique=True, sqlite_where=text("link_status='active' AND is_primary=1")),
    )


class EmployeeResumeFact(Identity, Base):
    __tablename__ = 'employee_resume_facts'
    employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'), index=True)
    document_link_id: Mapped[str] = mapped_column(ForeignKey('employee_documents.id'))
    fact_type: Mapped[str] = mapped_column(String(30))
    normalized_key: Mapped[str] = mapped_column(String(160))
    fact_json: Mapped[str] = mapped_column(Text, default='{}')
    source_refs_json: Mapped[str] = mapped_column(Text, default='[]')
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(40))
    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("fact_type IN ('skill','position','project_experience')"),)


class EmployeeCapacityDay(Identity, Base):
    __tablename__ = 'employee_capacity_days'
    employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'), index=True)
    work_date: Mapped[date] = mapped_column(Date)
    capacity_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(40), default='manual_capacity_ledger')
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('employee_id', 'work_date'), CheckConstraint('capacity_units IS NULL OR capacity_units BETWEEN 0 AND 100'))


class CompanyWorkDay(Base):
    __tablename__ = 'company_work_days'
    work_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_workday: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class StaffingResourceState(Base):
    __tablename__ = 'staffing_resource_state'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    bootstrap_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bootstrap_closed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    __table_args__ = (CheckConstraint('id=1'),)


class StaffingProject(Identity, Versioned, Base):
    __tablename__ = 'staffing_projects'
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default='')
    origin_kind: Mapped[str] = mapped_column(String(20), default='managed')
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    requirements_json: Mapped[str] = mapped_column(Text, default='{}')
    requirements_revision: Mapped[int] = mapped_column(Integer, default=0)
    scope_json: Mapped[str] = mapped_column(Text, default='[]')
    business_status: Mapped[str] = mapped_column(String(20), default='draft')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class StaffingMessage(Identity, Base):
    __tablename__ = 'staffing_messages'
    project_id: Mapped[str] = mapped_column(ForeignKey('staffing_projects.id'), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default='')
    requirements_revision: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str | None] = mapped_column(ForeignKey('staffing_runs.id'), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='complete')
    draft_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StaffingRun(Identity, Versioned, Base):
    __tablename__ = 'staffing_runs'
    project_id: Mapped[str] = mapped_column(ForeignKey('staffing_projects.id'), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    requirements_revision: Mapped[int] = mapped_column(Integer)
    replaces_run_id: Mapped[str | None] = mapped_column(ForeignKey('staffing_runs.id'), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='queued')
    checkpoint_thread_id: Mapped[str] = mapped_column(String(36), default=new_id)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fence_token: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    questions_json: Mapped[str] = mapped_column(Text, default='[]')
    result_json: Mapped[str] = mapped_column(Text, default='{}')
    resume_json: Mapped[str] = mapped_column(Text, default='{}')
    mode: Mapped[str] = mapped_column(String(30), default='specialists')
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index('uq_staffing_active_run', 'project_id', unique=True, sqlite_where=text("status IN ('queued','running','waiting_input')")),)


class StaffingPlan(Identity, Versioned, Base):
    __tablename__ = 'staffing_plans'
    project_id: Mapped[str] = mapped_column(ForeignKey('staffing_projects.id'), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey('staffing_runs.id'), nullable=True)
    requirements_revision: Mapped[int] = mapped_column(Integer)
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    assignments_json: Mapped[str] = mapped_column(Text, default='[]')
    evidence_refs_json: Mapped[str] = mapped_column(Text, default='[]')
    solver_status: Mapped[str] = mapped_column(String(30), default='UNKNOWN')
    validation_json: Mapped[str] = mapped_column(Text, default='{}')
    input_fingerprint: Mapped[str] = mapped_column(String(64), default='')
    state: Mapped[str] = mapped_column(String(30), default='draft')


class ResourceAllocation(Identity, Versioned, Base):
    __tablename__ = 'resource_allocations'
    employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('staffing_projects.id'), index=True)
    role_slot_id: Mapped[str] = mapped_column(String(36))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    work_dates_json: Mapped[str] = mapped_column(Text, default='[]')
    units_per_day: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='active')
    origin_plan_id: Mapped[str | None] = mapped_column(ForeignKey('staffing_plans.id'), nullable=True)
    basis: Mapped[str] = mapped_column(Text, default='')
    __table_args__ = (CheckConstraint('units_per_day BETWEEN 1 AND 100'), CheckConstraint('start_date < end_date'))


class StaffingEvent(Base):
    __tablename__ = 'staffing_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('staffing_runs.id'), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(80))
    safe_payload_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('run_id', 'seq'),)


class StaffingAction(Identity, Base):
    __tablename__ = 'staffing_actions'
    project_id: Mapped[str | None] = mapped_column(ForeignKey('staffing_projects.id'), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey('staffing_plans.id'), nullable=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    action_type: Mapped[str] = mapped_column(String(40))
    request_key: Mapped[str] = mapped_column(String(160))
    payload_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('actor_id', 'action_type', 'request_key'),)
