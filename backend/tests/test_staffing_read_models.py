from datetime import date

from app.models.staffing import Employee,StaffingProject,ResourceAllocation,StaffingPlan
from app.services.staffing.common import ensure_state


def test_admin_reads_actual_allocation_ledger_and_latest_plan_state(client,db,admin,hr):
    ensure_state(db)
    e = Employee(display_name='合成台账员工')
    p = StaffingProject(title='合成已有项目',created_by=2,start_date=date(2026,11,2),end_date=date(2026,11,4))
    db.add_all([e,p]);db.flush()
    db.add(ResourceAllocation(employee_id=e.id,project_id=p.id,role_slot_id='historical',start_date=p.start_date,
             end_date=p.end_date,work_dates_json='["2026-11-02"]',units_per_day=40,basis='合成占用依据'))
    db.add(StaffingPlan(project_id=p.id,requirements_revision=1,revision_no=1,state='draft'))
    db.add(StaffingPlan(project_id=p.id,requirements_revision=1,revision_no=2,state='submitted'))
    db.commit()
    response = client.get('/api/staffing/allocations',headers=admin)
    assert response.status_code==200
    row = response.json()[0]
    assert row['display_name']=='合成台账员工' and row['project_title']=='合成已有项目'
    assert row['units_per_day']==40 and row['work_dates']==['2026-11-02']
    assert row['basis']=='合成占用依据' and 'raw_text' not in row
    assert client.get(f'/api/staffing/projects/{p.id}',headers=hr).json()['latest_plan_state']=='submitted'
