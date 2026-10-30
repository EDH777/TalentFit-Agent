import json
from types import SimpleNamespace
import pytest

from app.core.config import settings
from app.models import Candidate, ResumeChunk
from app.services import resume


def add_candidate(db, name, content):
    person = Candidate(name=name, raw_text=content, created_by=2)
    db.add(person)
    db.flush()
    resume.create_chunks(db, person)
    db.commit()
    return person


def test_semantic_retrieval_without_keyword_overlap(db, monkeypatch):
    from app.services import embeddings
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[1.0, 0.0] if '服务端' in value or '后端' in value else [0.0, 1.0] for value in texts])
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    wanted = add_candidate(db, "甲", "拥有服务端系统开发经验")
    add_candidate(db, "乙", "负责财务报表核算工作")
    info = {}
    found = resume.search_candidates(db, "后端", retrieval_info=info)
    assert found[0]["candidate_id"] == wanted.id
    assert info["mode"] == "hybrid"


def test_hybrid_respects_names_and_model_identity(db, monkeypatch):
    from app.services import embeddings
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    first = add_candidate(db, "指定人员", "Java开发")
    add_candidate(db, "无关人员", "服务端经验")
    assert [item["candidate_id"] for item in resume.search_candidates(db, "开发", names=["指定人员"])] == [first.id]
    for chunk in db.query(ResumeChunk).filter_by(chunk_type="child"):
        chunk.embedding_model = "outdated-model"
    db.commit()
    assert resume.search_candidates(db, "无交集语义查询") == []


def test_embedding_failure_falls_back_with_warning(db, monkeypatch):
    from app.services import embeddings
    from app.services.llm import ModelUnavailableError
    person = add_candidate(db, "甲", "Java工程师")
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    def broken(texts): raise ModelUnavailableError("向量服务暂时不可用")
    monkeypatch.setattr(embeddings, "embed_texts", broken)
    info = {}
    assert resume.search_candidates(db, "Java", retrieval_info=info)[0]["candidate_id"] == person.id
    assert info["mode"] == "keyword" and info["warning"]


def test_embedding_batches_and_validates_provider_response(monkeypatch):
    from app.services import embeddings
    monkeypatch.setattr(settings, "openai_api_key", "fake")
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    sizes = []
    class FakeClient:
        def __init__(self, **kwargs): self.embeddings = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, *, input, model, dimensions, encoding_format):
            sizes.append(len(input))
            return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[1.0, 2.0]) for i in reversed(range(len(input)))])
    monkeypatch.setattr(embeddings, "OpenAI", FakeClient)
    result = embeddings.embed_texts([f"内容{i}" for i in range(23)])
    assert sizes == [10, 10, 3] and len(result) == 23


def test_reindex_only_missing_and_updates_are_atomic(db, monkeypatch):
    from app.services import embeddings
    person = add_candidate(db, "甲", "Java开发经历")
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    list(embeddings.reindex_missing(db))
    chunks = db.query(ResumeChunk).filter_by(chunk_type="child").all()
    assert all(json.loads(chunk.embedding_json) == [1.0, 0.0] for chunk in chunks)
    assert list(embeddings.reindex_missing(db))[-1]["indexed_chunks"] == len(chunks)


def test_manual_edit_embedding_failure_preserves_previous_index(db, client, hr, monkeypatch):
    from app.services import embeddings
    from app.services.llm import ModelUnavailableError
    person = add_candidate(db, "原姓名", "Java开发经历")
    old_chunks = [chunk.id for chunk in db.query(ResumeChunk).all()]
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    def broken(texts): raise ModelUnavailableError("嵌入服务失败")
    monkeypatch.setattr(embeddings, "embed_texts", broken)
    response = client.put(f"/api/candidates/{person.id}", headers=hr, json={"name": "新姓名"})
    assert response.status_code == 503
    db.expire_all()
    assert db.get(Candidate, person.id).name == "原姓名"
    assert [chunk.id for chunk in db.query(ResumeChunk).all()] == old_chunks


def test_invalid_vectors_rejected(monkeypatch):
    from app.services import embeddings
    from app.services.llm import ModelUnavailableError
    monkeypatch.setattr(settings, "openai_api_key", "fake")
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    class BadClient:
        def __init__(self, **kwargs): self.embeddings = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs): return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[float('nan'), 0.0])])
    monkeypatch.setattr(embeddings, "OpenAI", BadClient)
    with pytest.raises(ModelUnavailableError): embeddings.embed_texts(["简历"])


def test_index_status_is_json_serializable(db, client, hr, monkeypatch):
    from app.services import embeddings
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    add_candidate(db, "甲", "Java开发")
    json.dumps(embeddings.index_status(db))
    response = client.get("/api/search/status", headers=hr)
    assert response.status_code == 200 and response.json()["mode"] == "hybrid"


def test_reindex_does_not_overwrite_concurrently_replaced_chunks(db, monkeypatch):
    from app.services import embeddings
    person = add_candidate(db, "甲", "OLD content")
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    changed = False
    def embed(texts):
        nonlocal changed
        if not changed:
            changed = True
            # Simulate another request updating the candidate while provider works.
            from sqlalchemy import text
            db.execute(text("UPDATE resume_chunks SET content='NEW content', embedding_json='[0.0,1.0]', embedding_model=:model, embedding_dimensions=2 WHERE chunk_type='child'"), {"model": settings.embedding_model})
            db.commit()
        return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(embeddings, "embed_texts", embed)
    list(embeddings.reindex_missing(db))
    db.expire_all()
    child = db.query(ResumeChunk).filter_by(chunk_type="child").one()
    assert child.content == "NEW content"
    assert json.loads(child.embedding_json) == [0.0, 1.0]


def test_scoped_unindexed_candidate_reports_keyword_not_global_hybrid(db, monkeypatch):
    from app.services import embeddings
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    add_candidate(db, "已索引", "Java开发")
    monkeypatch.setattr(embeddings, "enabled", lambda: False)
    add_candidate(db, "待索引", "Java开发")
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    info = {}
    resume.search_candidates(db, "Java", names=["待索引"], retrieval_info=info)
    assert info["mode"] == "keyword" and info["warning"]


def test_reindex_cancelled_during_provider_call_does_not_commit(db, monkeypatch):
    from app.services import embeddings
    from app.services.events import StreamCancelled
    add_candidate(db, "甲", "Java开发")
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    cancelled = False
    def embed(texts):
        nonlocal cancelled
        cancelled = True
        return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(embeddings, "embed_texts", embed)
    with pytest.raises(StreamCancelled):
        list(embeddings.reindex_missing(db, cancelled=lambda: cancelled))
    db.rollback()
    assert db.query(ResumeChunk).filter_by(chunk_type="child").one().embedding_json is None
