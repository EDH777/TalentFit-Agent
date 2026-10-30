"""One-time SQLite field consolidation; retain rows, constraints and indexes."""
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from sqlalchemy import inspect

from app.core.job_categories import JOB_CATEGORIES, JOB_CATEGORY_CHECK


def migrate_candidate_positions(engine):
    if engine.dialect.name != 'sqlite' or not inspect(engine).has_table('candidates'):
        return
    with engine.connect() as conn:
        columns = {column['name'] for column in inspect(conn).get_columns('candidates')}
        sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name='candidates'").scalar_one()
        checks = re.compile(r'CHECK\s*\(\s*job_category\s+IS\s+NULL\s+OR\s+job_category\s+IN\s*\([^)]*\)\s*\)', re.IGNORECASE)
        matches = list(checks.finditer(sql))
        widen = 'job_category' in columns and (not matches or any("'采购经理'" not in match.group() for match in matches))
        if 'desired_position' not in columns and not widen:
            return
        conn.commit()
        database = engine.url.database
        if database and database != ':memory:':
            source = Path(database).resolve()
            backup_dir = source.parent / 'backups' / 'position-unification'
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            with sqlite3.connect(str(backup_dir / f'{source.stem}-{stamp}.sqlite3')) as target:
                conn.connection.driver_connection.backup(target)
        foreign_keys = conn.exec_driver_sql('PRAGMA foreign_keys').scalar()
        conn.commit()
        conn.exec_driver_sql('PRAGMA foreign_keys=OFF')
        conn.commit()
        try:
            with conn.begin():
                # SQLite's explicit transaction includes every schema operation.
                conn.exec_driver_sql('BEGIN IMMEDIATE')
                existing_violations = conn.exec_driver_sql('PRAGMA foreign_key_check').all()
                if 'job_category' not in columns:
                    conn.exec_driver_sql(f'ALTER TABLE candidates ADD COLUMN job_category VARCHAR(40) CHECK ({JOB_CATEGORY_CHECK})')
                if widen:
                    objects = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE tbl_name='candidates' AND type IN ('index','trigger') AND sql IS NOT NULL").scalars().all()
                    new_sql = checks.sub(f'CHECK ({JOB_CATEGORY_CHECK})', sql)
                    if not matches:
                        end = new_sql.rfind(')')
                        new_sql = new_sql[:end] + f', CONSTRAINT ck_candidate_job_category CHECK ({JOB_CATEGORY_CHECK})' + new_sql[end:]
                    # Keep the original column definitions and unrelated constraints.
                    new_sql = new_sql.replace(sql[:sql.index('(')], 'CREATE TABLE candidates_position_migration ', 1)
                    conn.exec_driver_sql(new_sql)
                    names = ','.join('"' + column['name'].replace('"', '""') + '"' for column in inspect(conn).get_columns('candidates'))
                    conn.exec_driver_sql(f'INSERT INTO candidates_position_migration ({names}) SELECT {names} FROM candidates')
                    conn.exec_driver_sql('DROP TABLE candidates')
                    conn.exec_driver_sql('ALTER TABLE candidates_position_migration RENAME TO candidates')
                    for statement in objects:
                        conn.exec_driver_sql(statement)
                if 'desired_position' in columns:
                    aliases = {value: value for value in JOB_CATEGORIES} | {
                        'JAVA 工程师': '后端开发工程师', 'Java': '后端开发工程师',
                        'Java 开发工程师': '后端开发工程师', 'Java 高级开发工程师': '后端开发工程师',
                        'Java开发工程师': '后端开发工程师', '采购经理/主管': '采购经理'}
                    for legacy, category in aliases.items():
                        conn.exec_driver_sql('UPDATE candidates SET job_category=? WHERE job_category IS NULL AND trim(desired_position)=?', (category, legacy))
                    conn.exec_driver_sql('ALTER TABLE candidates DROP COLUMN desired_position')
                if conn.exec_driver_sql('PRAGMA foreign_key_check').all() != existing_violations:
                    raise RuntimeError('岗位字段迁移影响了资料关联，已回滚')
        finally:
            conn.exec_driver_sql(f'PRAGMA foreign_keys={int(bool(foreign_keys))}')
            conn.commit()
