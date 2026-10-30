import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import Candidate, ResumeChunk
from app.services import embeddings, resume


CATEGORIES = ['产品经理', '前端开发工程师', '后端开发工程师', '测试工程师', 'AI应用开发工程师', '现场实施工程师', '硬件工程师', '采购经理']


@pytest.mark.parametrize('category', CATEGORIES)
def test_model_extracts_only_approved_primary_job_category(monkeypatch, category):
    monkeypatch.setattr(resume.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(resume, 'structured', lambda _: {'is_resume': True, 'name': '合成员工', 'job_category': category, 'desired_position': '原始业务职位'})
    result = resume.resume_profile('synthetic.txt', '合成个人职业履历与明确的相关经历内容。' * 3)
    assert result.job_category == category
    assert 'desired_position' not in result.model_dump()


@pytest.mark.parametrize('value', ['研发', '待确认', '', ['测试工程师'], 42, None])
def test_unrecognized_model_category_is_pending_without_rejecting_resume(monkeypatch, value):
    monkeypatch.setattr(resume.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(resume, 'structured', lambda _: {'is_resume': True, 'job_category': value})
    result = resume.resume_profile('synthetic.txt', '合成个人职业履历与明确的相关经历内容。' * 3)
    assert result.is_resume and result.job_category is None


def test_hr_category_crud_filters_and_no_reindex_for_category_only(client, hr, admin, db, monkeypatch):
    monkeypatch.setattr(embeddings, 'enabled', lambda: False)
    original = {'name': '合成员工', 'raw_text': '质量与自动化负责人，接口测试、自动化测试与测试平台维护。'}
    first = client.post('/api/candidates/manual', headers=hr, json={**original, 'job_category': '测试工程师'}).json()
    assert first['job_category'] == '测试工程师' and 'desired_position' not in first
    second = client.post('/api/candidates/manual', headers=hr, json={**original, 'name': '另一合成员工'}).json()
    assert second['job_category'] is None
    assert [r['id'] for r in client.get('/api/candidates?job_category=测试工程师', headers=hr).json()] == [first['id']]
    assert [r['id'] for r in client.get('/api/candidates?job_category=unclassified', headers=hr).json()] == [second['id']]
    assert client.get('/api/candidates?job_category=其他', headers=hr).status_code == 422
    before = [(r.id, r.content, r.enabled, r.embedding_json) for r in db.query(ResumeChunk).filter_by(candidate_id=first['id'])]
    monkeypatch.setattr('app.api.candidates.refresh_document_index', lambda *args: pytest.fail('category-only edits must preserve indexes'))
    route = f"/api/candidates/{first['id']}"
    assert client.put(route, headers=admin, json={'job_category': '产品经理'}).status_code == 403
    assert client.put(route, headers=hr, json={'job_category': '其他'}).status_code == 422
    changed = client.put(route, headers=hr, json={'job_category': '产品经理'}).json()
    assert changed['job_category'] == '产品经理'
    unchanged_form = client.put(route, headers=hr, json={**original, 'tags': first['tags'], 'years_experience': first['years_experience'], 'job_category': '测试工程师'}).json()
    assert unchanged_form['job_category'] == '测试工程师'
    cleared = client.put(route, headers=hr, json={'job_category': None}).json()
    assert cleared['job_category'] is None and 'desired_position' not in cleared
    assert before == [(r.id, r.content, r.enabled, r.embedding_json) for r in db.query(ResumeChunk).filter_by(candidate_id=first['id'])]
    assert db.get(Candidate, first['id']).raw_text == original['raw_text']


def test_job_category_migration_is_nullable_idempotent_and_preserves_old_fields(tmp_path, monkeypatch):
    from app.db import init_db as migration
    engine = create_engine(f'sqlite:///{(tmp_path / "old.db").as_posix()}', hide_parameters=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE candidates(id INTEGER PRIMARY KEY, name VARCHAR(120), desired_position VARCHAR(160), raw_text TEXT)'))
        conn.execute(text("INSERT INTO candidates VALUES (7,'合成员工','原职位','原正文')"))
    monkeypatch.setattr(migration, 'engine', engine)
    monkeypatch.setattr(migration, 'SessionLocal', sessionmaker(bind=engine))
    monkeypatch.setattr(migration.settings, 'admin_password', 'synthetic-only')
    monkeypatch.setattr(migration.settings, 'hr_password', 'synthetic-only')
    migration.init_db()
    migration.init_db()
    assert 'job_category' in {column['name'] for column in inspect(engine).get_columns('candidates')}
    with engine.connect() as conn:
        assert tuple(conn.execute(text('SELECT id,name,raw_text,job_category FROM candidates')).one()) == (7, '合成员工', '原正文', None)
    assert 'desired_position' not in {column['name'] for column in inspect(engine).get_columns('candidates')}
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE candidates SET job_category='其他' WHERE id=7"))
    engine.dispose()


@pytest.mark.parametrize('column_check', [True, False])
def test_migration_widens_existing_check_preserving_indexes_rows_and_relations(tmp_path, monkeypatch, column_check):
    from app.db import init_db as migration
    engine = create_engine(f'sqlite:///{(tmp_path / "checked.db").as_posix()}', hide_parameters=True)
    old_check = "job_category IS NULL OR job_category IN ('产品经理','前端开发工程师','后端开发工程师','测试工程师','AI应用开发工程师','现场实施工程师','硬件工程师')"
    constraint = f' CHECK ({old_check})' if column_check else ''
    table_constraint = '' if column_check else f', CONSTRAINT ck_candidate_job_category CHECK ({old_check})'
    with engine.begin() as conn:
        conn.exec_driver_sql('PRAGMA foreign_keys=ON')
        conn.execute(text(f'CREATE TABLE candidates(id INTEGER PRIMARY KEY, name TEXT CHECK(length(name)>0), desired_position TEXT, raw_text TEXT, job_category VARCHAR(40){constraint}, extra_legacy TEXT{table_constraint})'))
        conn.execute(text("INSERT INTO candidates VALUES(7,'合成员工','旧描述','原正文','测试工程师','额外字段')"))
        conn.execute(text('CREATE UNIQUE INDEX legacy_candidate_name ON candidates(name)'))
        conn.execute(text('CREATE TABLE synthetic_links(candidate_id INTEGER REFERENCES candidates(id), note TEXT)'))
        conn.execute(text("INSERT INTO synthetic_links VALUES(7,'关联记录')"))
        conn.execute(text('CREATE TABLE synthetic_updates(candidate_id INTEGER)'))
        conn.execute(text('CREATE TRIGGER preserve_candidate_trigger AFTER UPDATE OF job_category ON candidates BEGIN INSERT INTO synthetic_updates VALUES(NEW.id); END'))
    monkeypatch.setattr(migration, 'engine', engine)
    monkeypatch.setattr(migration, 'SessionLocal', sessionmaker(bind=engine))
    monkeypatch.setattr(migration.settings, 'admin_password', 'synthetic-only')
    monkeypatch.setattr(migration.settings, 'hr_password', 'synthetic-only')
    migration.init_db()
    migration.init_db()
    with engine.begin() as conn:
        assert tuple(conn.execute(text('SELECT id,name,raw_text,job_category,extra_legacy FROM candidates')).one()) == (7, '合成员工', '原正文', '测试工程师', '额外字段')
        conn.execute(text("UPDATE candidates SET job_category='采购经理' WHERE id=7"))
        assert conn.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
        assert conn.execute(text('SELECT candidate_id FROM synthetic_updates')).scalar() == 7
        assert conn.execute(text('SELECT candidate_id FROM synthetic_links')).scalar() == 7
        assert conn.execute(text('PRAGMA foreign_key_check')).all() == []
        assert 'legacy_candidate_name' in {row[1] for row in conn.execute(text('PRAGMA index_list(candidates)'))}
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE candidates SET job_category='其他' WHERE id=7"))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE candidates SET name='' WHERE id=7"))
    engine.dispose()


def test_removed_position_field_is_not_an_api_input_or_output(client, hr):
    assert client.post('/api/candidates/manual', headers=hr, json={'name': '合成员工', 'raw_text': '供应商谈判与采购管理', 'job_category': '采购经理', 'desired_position': '旧字段'}).status_code == 422
    created = client.post('/api/candidates/manual', headers=hr, json={'name': '合成员工', 'raw_text': '供应商谈判与采购管理', 'job_category': '采购经理'})
    assert created.status_code == 201
    assert 'desired_position' not in created.json()
    candidate_id = created.json()['id']
    assert client.put(f'/api/candidates/{candidate_id}', headers=hr, json={'desired_position': '旧字段'}).status_code == 422
    assert client.get('/api/candidates?q=采购经理', headers=hr).json()[0]['id'] == candidate_id


@pytest.mark.parametrize('existing_category', [False, True])
def test_migration_consolidates_only_explicit_position_aliases_and_backs_up(tmp_path, existing_category):
    import sqlite3
    from app.db.candidate_positions import migrate_candidate_positions
    engine = create_engine(f'sqlite:///{(tmp_path / "positions.db").as_posix()}', hide_parameters=True)
    with engine.begin() as conn:
        extra = ', job_category TEXT' if existing_category else ''
        conn.execute(text(f'CREATE TABLE candidates(id INTEGER PRIMARY KEY, desired_position TEXT, raw_text TEXT{extra})'))
        for number, position in enumerate(['JAVA 工程师', 'Java', 'Java 开发工程师', 'Java 高级开发工程师', 'Java开发工程师', '后端开发工程师', '采购经理/主管', '架构师', '产品经理']):
            conn.execute(text('INSERT INTO candidates(id,desired_position,raw_text) VALUES(:id,:position,:body)'), {'id': number, 'position': position, 'body': '原文不变'})
        if existing_category:
            conn.execute(text("UPDATE candidates SET job_category='测试工程师' WHERE id=8"))
    migrate_candidate_positions(engine)
    migrate_candidate_positions(engine)
    with engine.connect() as conn:
        result = conn.execute(text('SELECT job_category,raw_text FROM candidates ORDER BY id')).all()
    assert [row[0] for row in result] == ['后端开发工程师'] * 6 + ['采购经理', None, '测试工程师' if existing_category else '产品经理']
    assert all(row[1] == '原文不变' for row in result)
    backups = list((tmp_path / 'backups' / 'position-unification').glob('*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute('SELECT desired_position FROM candidates WHERE id=6').fetchone()[0] == '采购经理/主管'
    engine.dispose()
