from test_staffing_foundation import document


def test_employee_read_exposes_only_safe_active_document_links(db):
    from sqlalchemy.orm import sessionmaker
    from app.services.staffing.common import ensure_state
    from app.services.staffing.employees import from_documents, employee_dict
    from app.models.staffing import Employee
    ensure_state(db)
    db.commit()
    factory = sessionmaker(bind=db.get_bind(),expire_on_commit=False)
    first,_,_ = document(factory,2)
    created = from_documents(factory,2,[first])['results'][0]
    with factory() as read:
        result = employee_dict(read,read.get(Employee,created['employee_id']))
    assert len(result['documents'])==1
    link = result['documents'][0]
    assert link['candidate_id']==first['candidate_id'] and link['is_primary']
    assert set(link)=={'id','candidate_id','candidate_created_at','is_primary','version'}


def test_elapsed_workdays_do_not_block_employee_status_change(db):
    from datetime import timedelta
    from sqlalchemy.orm import sessionmaker
    from app.services.staffing.common import ensure_state, business_today, json_dump
    from app.services.staffing.employees import create_employee, update_employee
    from app.models.staffing import StaffingProject, ResourceAllocation
    ensure_state(db)
    db.commit()
    factory = sessionmaker(bind=db.get_bind(),expire_on_commit=False)
    employee = create_employee(factory,2,{'display_name':'历史项目员工'})
    today = business_today()
    with factory.begin() as write:
        project = StaffingProject(title='已完成名额但项目未结束',created_by=2,start_date=today-timedelta(days=1),end_date=today+timedelta(days=7))
        write.add(project); write.flush()
        write.add(ResourceAllocation(employee_id=employee['id'],project_id=project.id,role_slot_id='historical',
             start_date=project.start_date,end_date=project.end_date,work_dates_json=json_dump([(today-timedelta(days=1)).isoformat()]),units_per_day=50))
    result = update_employee(factory,2,employee['id'],{'expected_version':1,'employment_status':'departed'})
    assert result['employment_status']=='departed'
