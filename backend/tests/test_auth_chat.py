from app.models import User
from types import SimpleNamespace
from app.services import llm
from app.services.agent import answer
from app.services.resume import create_chunks
from app.models import Candidate


def test_anonymous_and_admin_write_rejected(client, admin):
    assert client.get("/api/candidates").status_code == 401
    assert client.post("/api/candidates/manual", headers=admin, json={"name": "某人", "raw_text": "工作经历"}).status_code == 403
    assert client.delete("/api/candidates/1", headers=admin).status_code == 403


def test_disabled_user_cannot_login(client, db):
    db.query(User).filter_by(username="hr").first().is_active = False
    db.commit()
    assert client.post("/api/auth/login", json={"username": "hr", "password": "test-hr"}).status_code == 401


def test_sessions_are_private(client, hr, admin):
    session_id = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    assert client.get(f"/api/chat/sessions/{session_id}/messages", headers=admin).status_code == 404
    assert client.post(f"/api/chat/sessions/{session_id}/messages", headers=admin, json={"content": "你好"}).status_code == 404


def test_model_unavailable_returns_readable_error(client, hr):
    session_id = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    response = client.post(f"/api/chat/sessions/{session_id}/messages", headers=hr, json={"content": "你好"})
    assert response.status_code == 503
    assert "模型" in response.json()["detail"]
    history = client.get(f"/api/chat/sessions/{session_id}/messages", headers=hr).json()
    assert history[0]["content"] == "你好"


def test_profile_only_uses_named_people(db, monkeypatch):
    db.add_all([
        Candidate(name="测试张三", raw_text="Java 工程师，负责Spring系统开发。", created_by=2),
        Candidate(name="测试李四", raw_text="Java 工程师，负责Spring系统开发。", created_by=2),
    ])
    db.flush()
    for person in db.query(Candidate).all():
        create_chunks(db, person)
    monkeypatch.setattr(llm, "structured", lambda _: {"intent": "person_profile", "query": "Java", "names": ["测试张三"]})
    monkeypatch.setattr(llm, "complete", lambda _: "测试张三的技能为Java。")
    content, intent, sources = answer(db, "分析测试张三的能力")
    assert [person["name"] for person in sources] == ["测试张三"]
    assert intent == "person_profile"


def test_missing_profile_does_not_substitute_other_person(db, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda _: {"intent": "person_profile", "query": "Java", "names": ["不存在的人"]})
    content, _, sources = answer(db, "分析不存在的人")
    assert "不存在的人" in content
    assert not sources


def test_chat_history_and_sources_persist(client, db, hr, monkeypatch):
    record = Candidate(name="测试李四", raw_text="负责Java系统开发的工程师。", created_by=2)
    db.add(record)
    db.flush()
    create_chunks(db, record)
    def plan(messages):
        history = messages[-1]["content"]
        if "他有什么" in history:
            assert "分析测试李四" in history
        return {"intent": "person_profile", "query": "Java", "names": ["测试李四"]}
    monkeypatch.setattr(llm, "structured", plan)
    monkeypatch.setattr(llm, "complete", lambda _: "Java经验，联系电话13800138000，邮箱 test@example.com")
    session_id = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    first = client.post(f"/api/chat/sessions/{session_id}/messages", headers=hr, json={"content": "分析测试李四"})
    assert first.status_code == 200
    second = client.post(f"/api/chat/sessions/{session_id}/messages", headers=hr, json={"content": "他有什么优势"})
    assert second.status_code == 200
    history = client.get(f"/api/chat/sessions/{session_id}/messages", headers=hr).json()
    assert history[-1]["sources"][0]["name"] == "测试李四"
    assert "13800138000" not in history[-1]["content"]
    assert "test@example.com" not in history[-1]["content"]


def test_provider_errors_are_redacted(monkeypatch):
    import httpx
    from openai import APIConnectionError
    from app.core.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "secret-do-not-leak")
    monkeypatch.setattr(settings, "openai_base_url", "https://example.com/v1")
    class BrokenClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            raise APIConnectionError(message="secret-do-not-leak", request=httpx.Request("POST", "https://example.com"))
    monkeypatch.setattr(llm, "OpenAI", BrokenClient)
    import pytest
    with pytest.raises(llm.ModelUnavailableError) as result:
        llm.complete([{"role": "user", "content": "你好"}])
    assert "secret-do-not-leak" not in str(result.value)
