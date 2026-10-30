"""Explicit November 2026 demo schedule. Never infer real staff availability."""
import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.models import User
from app.models.staffing import CompanyWorkDay, Employee, EmployeeCapacityDay, ResourceAllocation, StaffingProject, StaffingResourceState, new_id
from app.services.staffing.common import fail, json_dump, require_actor, resource_write
from app.services.staffing.employees import employee_dict

WORK_DATES = ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06',
              '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13']
REST_DATES = ['2026-11-07', '2026-11-08', '2026-11-14', '2026-11-15']
UNKNOWN_DAY = '2026-11-16'
TITLES = ['演示_现有系统维护', '演示_客户需求梳理', '演示_供应商导入']
BASIS = '演示排期，仅用于功能测试，不代表真实项目或人员承诺。'


def seed_demo(factory, actor_id):
    """One transaction, HR only, idempotent, refuses existing conflicting data."""
    with resource_write(factory) as db:
        require_actor(db, actor_id, 'hr')
        if db.get(StaffingResourceState, 1).bootstrap_closed_at:
            fail(409, 'BOOTSTRAP_CLOSED', '初始化已关闭，不能补入演示已有项目')
        previous = db.query(StaffingProject).filter(StaffingProject.title.in_(TITLES)).all()
        if previous:
            if len(previous) == 3 and all(p.origin_kind == 'baseline' and p.description == BASIS for p in previous):
                return {'already_initialized': True, 'project_count': 3}
            fail(409, 'DEMO_CONFLICT', '演示项目名称已被使用，请先人工核对，不会覆盖')
        people = db.query(Employee).filter_by(employment_status='active').order_by(Employee.created_at, Employee.id).all()
        selected, used = {}, set()
        for alias, role in [('后端A', '后端开发工程师'), ('后端B', '后端开发工程师'), ('前端A', '前端开发工程师'),
                            ('测试A', '测试工程师'), ('产品A', '产品经理'), ('采购A', '采购经理')]:
            person = next((p for p in people if p.id not in used and employee_dict(db, p)['current_position'] == role), None)
            if person is None:
                fail(409, 'DEMO_SOURCE_MISSING', f'演示需要明确关联主简历的{role}，请先维护岗位')
            used.add(person.id)
            selected[alias] = person
        ids = [p.id for p in people]
        dates = [date.fromisoformat(day) for day in WORK_DATES]
        if db.query(EmployeeCapacityDay.id).filter(EmployeeCapacityDay.employee_id.in_(ids), EmployeeCapacityDay.work_date.in_(dates)).first():
            fail(409, 'DEMO_CONFLICT', '所选日期已有可投入记录，不会覆盖')
        if db.query(ResourceAllocation.id).filter(ResourceAllocation.employee_id.in_(ids), ResourceAllocation.status.in_(['active', 'applied']),
                ResourceAllocation.start_date < date(2026, 11, 14), ResourceAllocation.end_date > date(2026, 11, 2)).first():
            fail(409, 'DEMO_CONFLICT', '期间已有人员安排，不会叠加演示数据')
        calendar = [(d, True) for d in [*WORK_DATES, UNKNOWN_DAY]] + [(d, False) for d in REST_DATES]
        for raw, working in calendar:
            day = date.fromisoformat(raw)
            old = db.get(CompanyWorkDay, day)
            if old and old.is_workday != working:
                fail(409, 'DEMO_CONFLICT', '已有工作日历与演示日期不同，不会覆盖')
            if not old:
                db.add(CompanyWorkDay(work_date=day, is_workday=working, updated_by=actor_id))
        for person in people:
            person.version += 1
            for day in dates:
                db.add(EmployeeCapacityDay(employee_id=person.id, work_date=day, capacity_units=100, source_kind='demo_staffing_202611'))
        specifications = [
            (TITLES[0], '2026-11-02', '2026-11-07', WORK_DATES[:5], [('后端A', 60), ('前端A', 40), ('测试A', 100)]),
            (TITLES[1], '2026-11-09', '2026-11-14', WORK_DATES[5:], [('产品A', 50), ('后端B', 30)]),
            (TITLES[2], '2026-11-02', '2026-11-14', WORK_DATES, [('采购A', 40)]),
        ]
        projects = []
        for title, start, end, days, arrangements in specifications:
            project = StaffingProject(title=title, description=BASIS, origin_kind='baseline', business_status='active',
                start_date=date.fromisoformat(start), end_date=date.fromisoformat(end), created_by=actor_id)
            db.add(project); db.flush()
            projects.append({'id': project.id, 'title': title})
            for alias, percent in arrangements:
                db.add(ResourceAllocation(employee_id=selected[alias].id, project_id=project.id, role_slot_id=new_id(),
                    start_date=project.start_date, end_date=project.end_date, work_dates_json=json_dump(days),
                    units_per_day=percent, status='active', basis=f'{BASIS} {alias}，每日{percent}%。'))
        db.flush()
        return {'already_initialized': False, 'initialized_employee_count': len(people), 'daily_records': len(people) * len(WORK_DATES),
            'calendar_records': len(calendar), 'allocation_records': 6, 'projects': projects, 'work_dates': WORK_DATES,
            'unknown_day': UNKNOWN_DAY, 'bootstrap_kept_open': True,
            'people': {alias: {'employee_id': p.id, 'display_name': p.display_name} for alias, p in selected.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='明确写入当前配置数据库的演示排期')
    args = parser.parse_args()
    if not args.apply:
        parser.error('请先查看脚本的明确日期与演示安排，写入须传 --apply')
    from app.db.session import SessionLocal, engine
    if engine.url.get_backend_name() != 'sqlite':
        raise RuntimeError('此本地演示脚本仅支持SQLite')
    backup = ROOT / 'data/backups/2026-10-27-demo-initialization'
    backup.mkdir(parents=True, exist_ok=True)
    target = backup / ('before-demo-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.db')
    source = Path(engine.url.database).resolve()
    with sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True) as read, sqlite3.connect(target) as write:
        read.backup(write)
    with SessionLocal() as db:
        actor = db.query(User).filter_by(role='hr', is_active=True).order_by(User.id).first()
        if not actor:
            raise RuntimeError('缺少有效人事账号')
        actor_id = actor.id
    result = seed_demo(SessionLocal, actor_id)
    directory = ROOT / 'data/demos/2026-11-staffing'
    directory.mkdir(parents=True, exist_ok=True)
    if not result['already_initialized']:
        (directory / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key not in ('people', 'projects')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
