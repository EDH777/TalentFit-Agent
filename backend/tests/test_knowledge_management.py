"""Knowledge management uses synthetic records and an isolated database only."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models import Candidate, ResumeChunk
from app.db.session import Base
from app.services import embeddings, knowledge
from app.schemas.knowledge import ChunkSettings
from app.services.llm import ModelUnavailableError


@pytest.fixture
def document(db, monkeypatch, client):
    monkeypatch.setattr(embeddings, "enabled", lambda: False)
    item = Candidate(name="测试人才", raw_text="甲" * 2100 + "乙" * 200, created_by=2,
                     parent_chunk_size=2000, child_chunk_size=500)
    db.add(item)
    db.commit()
    return item


def parents(client, hr, document):
    return client.get(f"/api/knowledge/documents/{document.id}/chunks", headers=hr).json()["items"]


def create_parent(client, hr, document, content="Python开发经验" * 100):
    response = client.post(f"/api/knowledge/documents/{document.id}/chunks", headers=hr, json={"content": content})
    assert response.status_code == 201, response.text
    return response.json()


def children(client, hr, document, parent):
    return client.get(f"/api/knowledge/documents/{document.id}/chunks", headers=hr, params={"parent_id": parent["id"], "page_size": 100}).json()["items"]


def test_internal_chunk_plan_is_bounded_and_does_not_modify_document(document, db):
    config = ChunkSettings(parent_size=2000, child_size=500, child_overlap=60)
    value = knowledge.preview_chunks(document.raw_text, config)
    assert [p['char_count'] for p in value['parents']] == [2000, 300]
    assert value['parent_count'] == 2 and value['child_count'] == 6
    assert db.query(ResumeChunk).count() == 0


def test_parent_toggle_preserves_child_state_and_vectors(client, hr, document, db, monkeypatch):
    parent = create_parent(client, hr, document)
    child = children(client, hr, document, parent)[0]
    row = db.get(ResumeChunk, child["id"])
    row.embedding_json = "[1,0]"
    db.commit()
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    def unavailable(_):
        raise ModelUnavailableError("切换开关不应调用模型")
    monkeypatch.setattr(embeddings, "embed_texts", unavailable)
    route = f"/api/knowledge/documents/{document.id}/chunks/"
    client.patch(route + str(child["id"]), headers=hr, json={"enabled": False})
    client.patch(route + str(parent["id"]), headers=hr, json={"enabled": False})
    assert not any(item["effective_enabled"] for item in children(client, hr, document, parent))
    client.patch(route + str(parent["id"]), headers=hr, json={"enabled": True})
    actual = children(client, hr, document, parent)
    assert actual[0]["enabled"] is False and actual[0]["effective_enabled"] is False
    assert actual[1]["effective_enabled"] is True
    assert db.get(ResumeChunk, child["id"]).embedding_json == "[1,0]"


def test_child_edit_delete_and_parent_cascade_keep_raw_text(client, hr, document, db):
    original = document.raw_text
    parent = create_parent(client, hr, document)
    initial = children(client, hr, document, parent)
    route = f"/api/knowledge/documents/{document.id}/chunks/"
    edited = client.patch(route + str(initial[0]["id"]), headers=hr, json={"content": "独立维护的证据"})
    assert edited.status_code == 200 and edited.json()["content"] == "独立维护的证据"
    assert client.delete(route + str(initial[1]["id"]), headers=hr).status_code == 204
    assert db.get(ResumeChunk, initial[1]["id"]) is None
    assert client.delete(route + str(parent["id"]), headers=hr).status_code == 204
    assert db.query(ResumeChunk).filter_by(candidate_id=document.id).count() == 0
    assert db.execute(text("SELECT count(*) FROM resume_chunks_fts WHERE candidate_id=:id"), {"id": document.id}).scalar() == 0
    assert document.raw_text == original


def test_batch_all_ids_validated_before_mutation_and_role_enforced(client, hr, admin, document, db):
    parent = create_parent(client, hr, document)
    route = f"/api/knowledge/documents/{document.id}/chunks/batch"
    assert client.post(route, headers=admin, json={"ids": [parent["id"]], "action": "disable"}).status_code == 403
    assert client.post(route, headers=hr, json={"ids": [parent["id"], 9999], "action": "disable"}).status_code == 404
    assert db.get(ResumeChunk, parent["id"]).enabled is True
    assert client.post(route, headers=hr, json={"ids": list(range(101)), "action": "delete"}).status_code == 422
    assert client.get(f"/api/knowledge/documents/{document.id}", headers=admin).status_code == 200
    assert client.post("/api/knowledge/documents/batch", headers=hr,
                       json={"ids": [document.id, 9999], "action": "disable"}).status_code == 404
    assert document.enabled is True
    assert client.post("/api/knowledge/documents/batch", headers=hr,
                       json={"ids": [document.id], "action": "disable"}).json() == {"updated": 1}
    assert client.get("/api/knowledge/summary", headers=admin).json()["enabled_child_chunks"] == 0


def test_failed_embedding_rolls_back_content_and_old_index(client, hr, document, db, monkeypatch):
    parent = create_parent(client, hr, document)
    child = children(client, hr, document, parent)[0]
    old_version = document.updated_at
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    def fail(_):
        raise ModelUnavailableError("测试服务不可用")
    monkeypatch.setattr(embeddings, "embed_texts", fail)
    response = client.patch(f"/api/knowledge/documents/{document.id}/chunks/{child['id']}", headers=hr,
                            json={"content": "失败的修改"})
    assert response.status_code == 503
    assert db.get(ResumeChunk, child["id"]).content == child["content"]
    assert db.get(Candidate, document.id).updated_at.replace(tzinfo=None) == old_version.replace(tzinfo=None)


def test_metadata_edit_preserves_manual_chunks_ids_and_states(client, hr, document, db):
    parent = create_parent(client, hr, document, "手动维护的分段")
    child = children(client, hr, document, parent)[0]
    client.patch(f"/api/knowledge/documents/{document.id}/chunks/{child['id']}", headers=hr, json={"enabled": False})
    response = client.put(f"/api/candidates/{document.id}", headers=hr, json={"name": "更新后的姓名", "raw_text": document.raw_text})
    assert response.status_code == 200, response.text
    actual = children(client, hr, document, parent)
    assert [item["id"] for item in actual] == [child["id"]]
    assert actual[0]["content"] == "手动维护的分段" and actual[0]["enabled"] is False


def test_internal_chunk_creation_commits_complete_hierarchy(client, hr, document, db):
    create_parent(client, hr, document, '旧分段')
    progress = []
    knowledge.rebuild_chunks(db, document, progress=lambda stage, value, label: progress.append(value))
    db.commit()
    assert [item['char_count'] for item in parents(client, hr, document)] == [2000, 300]
    assert progress[-1] == 95


def test_failed_internal_index_preparation_preserves_chunks(client, hr, document, db, monkeypatch):
    parent = create_parent(client, hr, document, '保留的手工段')
    monkeypatch.setattr(embeddings, 'enabled', lambda: True)
    def fail(_): raise ModelUnavailableError('测试服务不可用')
    monkeypatch.setattr(embeddings, 'embed_texts', fail)
    with pytest.raises(ModelUnavailableError):
        knowledge.rebuild_chunks(db, document)
    db.rollback()
    assert parents(client, hr, document)[0]['id'] == parent['id']


def test_cancelled_internal_chunk_creation_rolls_back(client, hr, document, db):
    from app.services.events import StreamCancelled
    parent = create_parent(client, hr, document, '取消时保留')
    def progress(stage, value, label):
        if value == 95: raise StreamCancelled()
    with pytest.raises(StreamCancelled):
        knowledge.rebuild_chunks(db, document, progress=progress)
    db.rollback()
    assert parents(client, hr, document)[0]['id'] == parent['id']


def test_parent_edit_replaces_only_its_children_and_supports_literal_search(client, hr, document, db):
    first = create_parent(client, hr, document, "原父段")
    second = create_parent(client, hr, document, "保留父段")
    kept_child = children(client, hr, document, second)[0]
    response = client.patch(f"/api/knowledge/documents/{document.id}/chunks/{first['id']}", headers=hr,
                            json={"content": "新内容100%_保留" * 70})
    assert response.status_code == 200
    new_children = children(client, hr, document, first)
    assert len(new_children) == 2
    assert new_children[0]["char_count"] == 500
    assert children(client, hr, document, second)[0]["id"] == kept_child["id"]
    assert db.execute(text("SELECT count(*) FROM resume_chunks_fts WHERE content LIKE '%原父段%'" )).scalar() == 0
    query = client.get(f"/api/knowledge/documents/{document.id}/chunks", headers=hr,
                       params={"q": "%_", "page": 1, "page_size": 1}).json()
    assert query["total"] == 1 and query["items"][0]["id"] == first["id"]


def test_document_stats_count_effective_indexed_children_and_parent_hits(client, hr, document, db, monkeypatch):
    parent = create_parent(client, hr, document, "数据工程" * 150)
    all_children = children(client, hr, document, parent)
    for item in all_children:
        row = db.get(ResumeChunk, item["id"])
        embeddings.assign(row, [1.0] + [0.0] * (embeddings.settings.embedding_dimensions - 1))
        row.hit_count = 5
    db.get(ResumeChunk, parent["id"]).hit_count = 7
    db.commit()
    route = f"/api/knowledge/documents/{document.id}"
    value = client.get(route, headers=hr).json()
    assert value["stats"]["indexed_child_chunks"] == 2
    assert value["stats"]["hit_count"] == 7
    assert "file_path" not in value["candidate"] and "embedding_json" not in parents(client, hr, document)[0]
    client.patch(route + "/enabled", headers=hr, json={"enabled": False})
    value = client.get(route, headers=hr).json()
    assert value["stats"]["child_chunks"] == 2
    assert value["stats"]["enabled_child_chunks"] == value["stats"]["indexed_child_chunks"] == 0
    assert client.patch(route + "/enabled", headers=hr, json={"enabled": True}).status_code == 200
    assert client.get(route, headers=hr).json()["stats"]["indexed_child_chunks"] == 2


def test_foreign_document_chunks_are_rejected_and_child_can_be_added(client, hr, document, db):
    other = Candidate(name="其他人才", raw_text="其他正文", created_by=2)
    db.add(other)
    db.commit()
    first = create_parent(client, hr, document, "当前文档")
    foreign = create_parent(client, hr, other, "其他文档")
    route = f"/api/knowledge/documents/{document.id}/chunks"
    assert client.post(route, headers=hr, json={"parent_id": foreign["id"], "content": "越权分段"}).status_code == 404
    assert client.post(route + "/batch", headers=hr, json={"ids": [first["id"], foreign["id"]], "action": "delete"}).status_code == 404
    response = client.post(route, headers=hr, json={"parent_id": first["id"], "content": "新增子段"})
    assert response.status_code == 201 and response.json()["chunk_type"] == "child"
    assert len(children(client, hr, document, first)) == 2


def test_idempotent_migration_preserves_legacy_data(db, monkeypatch):
    from app.db import init_db as migration
    monkeypatch.setattr(migration, "engine", db.get_bind())
    monkeypatch.setattr(migration, "SessionLocal", lambda: db)
    monkeypatch.setattr(migration.settings, "admin_username", "admin")
    monkeypatch.setattr(migration.settings, "hr_username", "hr")
    for table, columns in [("candidates", ["enabled", "parent_chunk_size", "child_chunk_size", "child_chunk_overlap", "processed_text", "processing_version", "parent_delimiter", "child_delimiter", "replace_consecutive_spaces", "delete_urls_email"]),
                           ("resume_chunks", ["enabled", "position", "hit_count", "updated_at"])]:
        for column in columns:
            db.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    db.execute(text("ALTER TABLE candidates ADD COLUMN source_type VARCHAR(20) NOT NULL DEFAULT 'manual'"))
    db.execute(text("INSERT INTO candidates(id,name,raw_text,created_by,status,created_at,updated_at,years_experience,tags_json,source_type) VALUES(10,'旧记录','保留的原文',2,'ready',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'','[]','manual')"))
    db.execute(text("INSERT INTO resume_chunks(id,candidate_id,chunk_type,content,created_at) VALUES(10,10,'parent','旧分段',CURRENT_TIMESTAMP)"))
    db.commit()
    original_created_at = db.execute(text('SELECT created_at FROM candidates WHERE id=10')).scalar()
    migration.init_db()
    migration.init_db()
    result = db.get(Candidate, 10)
    assert result.name == "旧记录" and result.raw_text == "保留的原文" and result.enabled is True
    assert (result.parent_chunk_size, result.child_chunk_size, result.child_chunk_overlap) == (4000, 2048, 60)
    assert result.processed_text == result.processing_version == ''
    assert (result.parent_delimiter, result.child_delimiter) == ('<<<RESUME_PARENT>>>', '<<<RESUME_SECTION>>>')
    assert not result.replace_consecutive_spaces and not result.delete_urls_email
    assert 'source_type' not in {row[1] for row in db.execute(text('PRAGMA table_info(candidates)'))}
    assert db.execute(text('SELECT created_at FROM candidates WHERE id=10')).scalar() == original_created_at
    chunk = db.get(ResumeChunk, 10)
    assert chunk.content == "旧分段" and chunk.enabled is True and chunk.hit_count == 0 and chunk.updated_at


def test_parent_delete_obeys_foreign_keys(client, hr, document, db):
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
    parent = create_parent(client, hr, document)
    assert client.delete(f"/api/knowledge/documents/{document.id}/chunks/{parent['id']}", headers=hr).status_code == 204
    assert db.query(ResumeChunk).count() == 0


def test_late_rebuild_cannot_overwrite_a_concurrent_document_change(client, hr, document, db, monkeypatch):
    from fastapi import HTTPException
    from app.services.knowledge import rebuild_chunks
    parent = create_parent(client, hr, document, "并发修改时保留的分段")
    candidate_id = document.id
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    def delayed_vectors(texts):
        # Another request commits while this request is waiting for the model.
        with sessionmaker(bind=db.get_bind(), expire_on_commit=False)() as other:
            row = other.get(Candidate, candidate_id)
            row.enabled = False
            other.commit()
        return [[1.0] + [0.0] * (embeddings.settings.embedding_dimensions - 1) for _ in texts]
    monkeypatch.setattr(embeddings, "embed_texts", delayed_vectors)
    with pytest.raises(HTTPException) as error:
        rebuild_chunks(db, document)
    assert error.value.status_code == 409
    db.rollback()
    assert parents(client, hr, document)[0]["id"] == parent["id"]
    assert db.get(Candidate, candidate_id).enabled is False


@pytest.mark.parametrize('operation', ['create_parent', 'create_child', 'edit_parent', 'edit_child'])
def test_chunk_model_wait_does_not_lock_other_document_writes(tmp_path, monkeypatch, operation):
    from app.services import knowledge
    engine = create_engine('sqlite:///' + (tmp_path / 'concurrency.db').as_posix(), connect_args={'timeout': 0.05})
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('CREATE VIRTUAL TABLE resume_chunks_fts USING fts5(chunk_id UNINDEXED,candidate_id UNINDEXED,content)'))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(embeddings, 'enabled', lambda: False)
    with factory() as db:
        person = Candidate(name='合成甲', raw_text='原正文', created_by=2)
        other = Candidate(name='合成乙', raw_text='其他正文', created_by=2)
        db.add_all([person, other])
        db.commit()
        parent = knowledge.create_chunk(db, person, '原父段', None)
        db.commit()
        child = db.query(ResumeChunk).filter_by(parent_id=parent.id).one()
        other_id = other.id
        def vectors(texts):
            with factory() as concurrent:
                concurrent.get(Candidate, other_id).enabled = False
                concurrent.commit()
            return [[1.0] + [0.0] * (embeddings.settings.embedding_dimensions - 1) for _ in texts]
        monkeypatch.setattr(embeddings, 'enabled', lambda: True)
        monkeypatch.setattr(embeddings, 'embed_texts', vectors)
        if operation.startswith('create'):
            knowledge.create_chunk(db, person, '新增正文', parent if operation == 'create_child' else None)
        else:
            knowledge.update_chunk(db, person, parent if operation == 'edit_parent' else child, {'content': '更新正文'})
        db.commit()
        db.expire_all()
        assert db.get(Candidate, other_id).enabled is False
    engine.dispose()


def test_new_document_defaults_and_generated_chunks_follow_confirmed_caps(client, hr, db):
    from app.schemas.knowledge import ChunkSettings
    config = ChunkSettings()
    assert (config.parent_size, config.child_size) == (4000, 2048)
    response = client.post('/api/candidates/manual', headers=hr, json={'name': '合成边界', 'raw_text': '字' * 9000})
    assert response.status_code == 201
    person = db.get(Candidate, response.json()['id'])
    assert (person.parent_chunk_size, person.child_chunk_size) == (4000, 2048)
    chunks = db.query(ResumeChunk).filter_by(candidate_id=person.id).all()
    assert max(len(row.content) for row in chunks if row.chunk_type == 'parent') == 4000
    assert max(len(row.content) for row in chunks if row.chunk_type == 'child') == 2048


@pytest.mark.parametrize('config', [
    {'parent_size': 4001, 'child_size': 2048},
    {'parent_size': 4000, 'child_size': 2049},
])
def test_internal_configuration_rejects_exceeding_confirmed_caps(config):
    with pytest.raises(ValueError):
        ChunkSettings(**config)


@pytest.mark.parametrize('child_limit', [False, True])
def test_manual_chunk_create_and_edit_apply_parent_child_caps(client, hr, document, db, child_limit):
    parent = create_parent(client, hr, document, '已存在父段')
    route = f'/api/knowledge/documents/{document.id}/chunks'
    limit = 2048 if child_limit else 4000
    payload = {'content': '字' * limit}
    if child_limit:
        payload['parent_id'] = parent['id']
    accepted = client.post(route, headers=hr, json=payload)
    assert accepted.status_code == 201
    payload['content'] += '超'
    before_count = db.query(ResumeChunk).count()
    assert client.post(route, headers=hr, json=payload).status_code == 422
    assert client.patch(route + '/' + str(accepted.json()['id']), headers=hr, json={'content': '字' * (limit + 1)}).status_code == 422
    assert db.query(ResumeChunk).count() == before_count
    assert db.get(ResumeChunk, accepted.json()['id']).content == '字' * limit
