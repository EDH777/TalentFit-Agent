import json
from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.entities import Candidate, ChatMessage, ChatSession


@pytest.mark.parametrize("role", ["hr", "admin"])
def test_delete_own_session_cascades_messages_and_preserves_other_data(client, db, hr, admin, role):
    headers = hr if role == "hr" else admin
    other = admin if role == "hr" else hr
    session_id = client.post("/api/chat/sessions", headers=headers, json={}).json()["id"]
    other_id = client.post("/api/chat/sessions", headers=other, json={}).json()["id"]
    db.add_all([
        ChatMessage(session_id=session_id, role="user", content="测试提问"),
        ChatMessage(session_id=session_id, role="assistant", content="测试回答", trace_json='[{"id":"node"}]'),
        ChatMessage(session_id=other_id, role="user", content="其他账号历史"),
        Candidate(name="合成人才", raw_text="合成数据", created_by=2),
    ])
    db.commit()

    response = client.delete(f"/api/chat/sessions/{session_id}", headers=headers)

    assert response.status_code == 204
    assert response.content == b""
    assert db.query(ChatSession).filter_by(id=session_id).count() == 0
    assert db.query(ChatMessage).filter_by(session_id=session_id).count() == 0
    assert client.get("/api/chat/sessions", headers=headers).json() == []
    assert client.get(f"/api/chat/sessions/{session_id}/messages", headers=headers).status_code == 404
    for suffix in ["messages", "messages/stream"]:
        assert client.post(f"/api/chat/sessions/{session_id}/{suffix}", headers=headers, json={"content": "新消息"}).status_code == 404
    assert client.delete(f"/api/chat/sessions/{session_id}", headers=headers).status_code == 404
    assert client.get(f"/api/chat/sessions/{other_id}/messages", headers=other).json()[0]["content"] == "其他账号历史"
    assert db.query(Candidate).count() == 1
    assert client.post("/api/chat/sessions", headers=headers, json={}).status_code == 201


def test_delete_session_requires_owner_and_authentication(client, db, hr, admin):
    session_id = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    db.add(ChatMessage(session_id=session_id, role="user", content="私有历史"))
    db.commit()

    assert client.delete(f"/api/chat/sessions/{session_id}").status_code == 401
    assert client.delete(f"/api/chat/sessions/{session_id}", headers=admin).status_code == 404
    assert client.delete("/api/chat/sessions/99999", headers=hr).status_code == 404
    assert client.get(f"/api/chat/sessions/{session_id}/messages", headers=hr).json()[0]["content"] == "私有历史"


def test_delete_removes_messages_added_after_the_session_was_loaded(client, db, hr):
    session_id = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    session = db.get(ChatSession, session_id)
    assert session.messages == []
    with sessionmaker(bind=db.get_bind())() as other_db:
        other_db.add(ChatMessage(session_id=session_id, role="assistant", content="另一请求刚保存的回答"))
        other_db.commit()

    assert client.delete(f"/api/chat/sessions/{session_id}", headers=hr).status_code == 204
    assert db.query(ChatMessage).filter_by(session_id=session_id).count() == 0


def test_delete_does_not_remove_a_replacement_session_from_another_account(client, db, hr, admin, monkeypatch):
    from app.api import chat

    session_id = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    owned_session = chat.owned_session

    def replace_after_ownership_check(*args):
        session = owned_session(*args)
        with sessionmaker(bind=db.get_bind())() as other_db:
            other_db.delete(other_db.get(ChatSession, session_id))
            other_db.commit()
            other_db.add(ChatSession(id=session_id, user_id=1, title="另一账号的新会话", created_at=session.created_at + timedelta(seconds=1)))
            other_db.commit()
        return session

    monkeypatch.setattr(chat, "owned_session", replace_after_ownership_check)
    response = client.delete(f"/api/chat/sessions/{session_id}", headers=hr)
    assert response.status_code == 404
    assert client.get("/api/chat/sessions", headers=admin).json()[0]["title"] == "另一账号的新会话"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("replace_session", [False, True])
def test_answer_cannot_save_into_a_concurrently_deleted_session(client, db, hr, monkeypatch, streaming, replace_session):
    session_id = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    original = db.get(ChatSession, session_id)
    original_created_at = original.created_at
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    def delete_during_answer(*args, **kwargs):
        with factory() as other_db:
            other_db.delete(other_db.get(ChatSession, session_id))
            other_db.commit()
            if replace_session:
                other_db.add(ChatSession(id=session_id, user_id=2, title="新创建的会话", created_at=original_created_at + timedelta(seconds=1)))
                other_db.commit()
        return "旧会话回答", "general", []

    def stream_during_delete(*args, **kwargs):
        content, intent, sources = delete_during_answer()
        yield {"type": "result", "content": content, "intent": intent, "sources": sources}

    monkeypatch.setattr("app.api.chat.answer", delete_during_answer)
    monkeypatch.setattr("app.api.chat.stream_answer", stream_during_delete)
    suffix = "messages/stream" if streaming else "messages"
    response = client.post(f"/api/chat/sessions/{session_id}/{suffix}", headers=hr, json={"content": "旧会话提问"})

    if streaming:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert events[-1]["type"] == "error"
        assert "会话" in events[-1]["detail"]
        assert not any(event["type"] == "done" for event in events)
    else:
        assert response.status_code == 404
    assert db.query(ChatMessage).filter_by(session_id=session_id).count() == 0
    sessions = client.get("/api/chat/sessions", headers=hr).json()
    assert [item["title"] for item in sessions] == (["新创建的会话"] if replace_session else [])


@pytest.mark.parametrize("streaming,ownership_check", [(False, 1), (True, 1), (True, 2)])
@pytest.mark.parametrize("replace_session", [False, True])
def test_user_message_cannot_save_after_concurrent_deletion(client, db, hr, monkeypatch, streaming, ownership_check, replace_session):
    from app.api import chat

    session_id = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    owned_session = chat.owned_session
    checks = 0

    def delete_before_first_write(*args):
        nonlocal checks
        session = owned_session(*args)
        checks += 1
        if checks == ownership_check:
            with sessionmaker(bind=db.get_bind())() as other_db:
                other_db.delete(other_db.get(ChatSession, session_id))
                other_db.commit()
                if replace_session:
                    other_db.add(ChatSession(id=session_id, user_id=2, title="新创建的会话", created_at=session.created_at + timedelta(seconds=1)))
                    other_db.commit()
        return session

    monkeypatch.setattr(chat, "owned_session", delete_before_first_write)
    monkeypatch.setattr(chat, "answer", lambda *args, **kwargs: ("旧会话回答", "general", []))
    monkeypatch.setattr(chat, "stream_answer", lambda *args, **kwargs: iter([{"type": "result", "content": "旧会话回答", "intent": "general", "sources": []}]))
    suffix = "messages/stream" if streaming else "messages"
    response = client.post(f"/api/chat/sessions/{session_id}/{suffix}", headers=hr, json={"content": "旧会话提问"})

    if streaming:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert events[-1]["type"] == "error"
        assert "会话" in events[-1]["detail"]
    else:
        assert response.status_code == 404
    assert db.query(ChatMessage).filter_by(session_id=session_id).count() == 0
    assert [item["title"] for item in client.get("/api/chat/sessions", headers=hr).json()] == (["新创建的会话"] if replace_session else [])
