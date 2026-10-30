from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models import User


@pytest.fixture
def staffing(tmp_path):
    from app.services.staffing.common import ensure_state
    engine = create_engine('sqlite:///' + str(tmp_path / 'staffing-calendar.db'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        ensure_state(db)
        user = User(username='calendar-hr', password_hash='unused', role='hr')
        db.add(user)
        db.flush()
        actor_id = user.id
    yield factory, actor_id, engine
    engine.dispose()


def test_company_calendar_defaults_to_weekdays_and_only_persists_exceptions(staffing):
    from app.models.staffing import CompanyWorkDay
    from app.services.staffing.capacity import work_calendar, set_work_calendar
    factory, actor, _ = staffing
    with factory() as db:
        days = work_calendar(db, date(2027, 1, 4), date(2027, 1, 11))
    assert [(row['work_date'], row['is_workday'], row['is_default']) for row in days] == [
        ('2027-01-04', True, True), ('2027-01-05', True, True), ('2027-01-06', True, True),
        ('2027-01-07', True, True), ('2027-01-08', True, True), ('2027-01-09', False, True),
        ('2027-01-10', False, True),
    ]
    set_work_calendar(factory, actor, {'expected_revision': 0, 'days': [
        {'work_date': '2027-01-09', 'is_workday': True},
        {'work_date': '2027-01-08', 'is_workday': False},
    ]})
    with factory() as db:
        days = work_calendar(db, date(2027, 1, 4), date(2027, 1, 11))
        assert db.query(CompanyWorkDay).count() == 2
    by_date = {row['work_date']: row for row in days}
    assert by_date['2027-01-09']['is_workday'] is True and by_date['2027-01-09']['is_default'] is False
    assert by_date['2027-01-08']['is_workday'] is False and by_date['2027-01-08']['is_default'] is False


def test_missing_employee_capacity_defaults_to_100_on_company_workday_and_null_is_explicit_unknown(staffing):
    from app.services.staffing.employees import create_employee
    from app.services.staffing.capacity import snapshot, set_capacity_days
    factory, actor, _ = staffing
    employee = create_employee(factory, actor, {'display_name': '默认排期员工'})
    with factory() as db:
        result = snapshot(db, [employee['id']], date(2027, 1, 4), date(2027, 1, 5))
    assert result['data'][0]['capacity_units'] == 100
    assert result['unknown_dates'] == []
    set_capacity_days(factory, actor, employee['id'], {'expected_version': 1, 'days': [{'work_date': '2027-01-04', 'capacity_units': None}]})
    with factory() as db:
        result = snapshot(db, [employee['id']], date(2027, 1, 4), date(2027, 1, 5))
    assert result['data'][0]['capacity_units'] is None
    assert result['unknown_dates'] == [{'employee_id': employee['id'], 'date': '2027-01-04'}]


def test_capacity_day_exposes_allocation_project_details(staffing):
    from app.models.staffing import Employee, ResourceAllocation, StaffingProject
    from app.services.staffing.capacity import snapshot
    factory, actor, _ = staffing
    with factory.begin() as db:
        employee = Employee(display_name='日历员工', employment_status='active')
        project = StaffingProject(title='日历项目', description='', origin_kind='managed', start_date=date(2027, 1, 4), end_date=date(2027, 1, 6), created_by=actor)
        db.add_all([employee, project]); db.flush()
        db.add(ResourceAllocation(employee_id=employee.id, project_id=project.id, role_slot_id='role', start_date=date(2027, 1, 4), end_date=date(2027, 1, 6), work_dates_json='["2027-01-04"]', units_per_day=40, status='active', basis='测试占用'))
    with factory() as db:
        result = snapshot(db, [employee.id], date(2027, 1, 4), date(2027, 1, 6))
    assert result['data'][0]['allocated_units'] == 40
    assert result['data'][0]['allocations'][0]['project_title'] == '日历项目'
    assert result['data'][0]['allocations'][0]['status'] == 'active'
