import json
from app.services import llm
from app.services.agent import redact_contacts


def events(response):
    assert response.status_code == 200
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_chat_stream_stages_tokens_and_persistence(client, hr, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda _: {"intent": "general", "names": [], "query": "你好"})
    monkeypatch.setattr(llm, "stream_complete", lambda _: iter(["你好。", "欢迎咨询。"]), raising=False)
    session = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    result = events(client.post(f"/api/chat/sessions/{session}/messages/stream", headers=hr, json={"content": "你好"}))
    assert [item["node"]["node_id"] for item in result if item["type"] == "node_started"] == ["plan", "generate"]
    deltas = [item["content"] for item in result if item["type"] == "delta"]
    assert len(deltas) >= 2 and "".join(deltas) == "你好。欢迎咨询。"
    assert result[-1]["type"] == "done"
    history = client.get(f"/api/chat/sessions/{session}/messages", headers=hr).json()
    assert history[-1]["content"] == "".join(deltas)


def test_split_contacts_never_leak_in_deltas(client, hr, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda _: {"intent": "general", "names": [], "query": "你好"})
    monkeypatch.setattr(llm, "stream_complete", lambda _: iter(["电话13800", "138000，邮箱abc", "@example", ".com。", "安全结论。"]), raising=False)
    session = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    result = events(client.post(f"/api/chat/sessions/{session}/messages/stream", headers=hr, json={"content": "你好"}))
    deltas = "".join(item["content"] for item in result if item["type"] == "delta")
    assert "13800" not in deltas and "abc" not in deltas
    assert "安全结论" in deltas


def test_email_punctuation_is_not_a_safe_stream_boundary(client, hr, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda _: {"intent": "general", "names": [], "query": "你好"})
    monkeypatch.setattr(llm, "stream_complete", lambda _: iter(["Contact alice!", "ops?team@example.com。", "安全结论。"]), raising=False)
    session = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    result = events(client.post(f"/api/chat/sessions/{session}/messages/stream", headers=hr, json={"content": "你好"}))
    deltas = [item["content"] for item in result if item["type"] == "delta"]
    assert all("alice" not in part and "ops" not in part for part in deltas)
    assert "[邮箱已隐藏]" in "".join(deltas)


def test_chat_stream_error_no_false_done(client, hr):
    session = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    result = events(client.post(f"/api/chat/sessions/{session}/messages/stream", headers=hr, json={"content": "你好"}))
    assert result[-1]["type"] == "error"
    assert not any(item["type"] == "done" for item in result)
    history = client.get(f"/api/chat/sessions/{session}/messages", headers=hr).json()
    assert len(history) == 1 and history[0]["role"] == "user"


def test_stream_permissions_before_response(client, hr, admin):
    session = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    assert client.post(f"/api/chat/sessions/{session}/messages/stream", headers=admin, json={"content": "你好"}).status_code == 404
    assert client.post("/api/candidates/upload/stream", headers=admin, files={"files": ("a.txt", b"data")}).status_code == 403


def test_batch_limit_rejected_before_writes(client, hr, db):
    from app.models import Candidate
    result = client.post("/api/candidates/upload/stream", headers=hr, files=[("files", (f"{i}.txt", b"content")) for i in range(4)])
    assert result.status_code == 400
    assert db.query(Candidate).count() == 0


def test_three_files_progress_and_partial_failure(client, hr):
    good = "姓名：测试人\n工作经历：5年Java开发，负责业务系统。项目经验：订单管理平台设计。教育背景：计算机本科。"
    files = [("files", ("one.txt", good.encode())), ("files", ("bad.exe", b"bad")), ("files", ("two.txt", (good + "熟悉Docker").encode()))]
    result = events(client.post("/api/candidates/upload/stream", headers=hr, files=files))
    assert result[0] == {"type": "batch_start", "total": 3}
    assert result[-1] == {"type": "batch_done", "total": 3, "succeeded": 2, "failed": 1}
    for index in [0, 2]:
        progress = [item["progress"] for item in result if item["type"] == "file_progress" and item["file_index"] == index]
        assert progress == sorted(progress) and progress[-1] == 100 and len(progress) >= 5
    assert len([item for item in result if item["type"] == "file_result"]) == 3


def test_cancelled_ingestion_rolls_back_and_removes_uncommitted_file(db, tmp_path):
    import pytest
    from app.services.events import StreamCancelled
    from app.services.resume import persist_upload
    from app.models import Candidate
    def cancel(stage, progress, label):
        if stage == "profile": raise StreamCancelled()
    raw = "姓名：测试人\n工作经历：负责Java开发。教育背景：计算机本科。项目经验：系统项目。".encode()
    with pytest.raises(StreamCancelled):
        persist_upload(db, 2, "cancelled.txt", raw, progress=cancel)
    db.rollback()
    assert not list(tmp_path.iterdir())
    assert db.query(Candidate).count() == 0


def test_mid_stream_failure_keeps_no_successful_assistant(client, hr, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda _: {"intent": "general", "names": [], "query": "你好"})
    def interrupted(_):
        yield "一部分回答。"
        raise llm.ModelUnavailableError("模型连接中断")
    monkeypatch.setattr(llm, "stream_complete", interrupted)
    session = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    result = events(client.post(f"/api/chat/sessions/{session}/messages/stream", headers=hr, json={"content": "你好"}))
    assert any(item["type"] == "delta" for item in result)
    assert result[-1]["type"] == "error" and not any(item["type"] == "done" for item in result)
    assert len(client.get(f"/api/chat/sessions/{session}/messages", headers=hr).json()) == 1


def test_provider_eof_without_finish_is_an_error(monkeypatch):
    from types import SimpleNamespace
    import pytest
    from app.core.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "fake")
    monkeypatch.setattr(settings, "openai_base_url", "https://example.invalid/v1")
    class IncompleteStream:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="部分内容。"), finish_reason=None)])
    class FakeClient:
        def __init__(self, **kwargs): self.chat = SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs): return IncompleteStream()
    monkeypatch.setattr(llm, "OpenAI", FakeClient)
    with pytest.raises(llm.ModelUnavailableError): list(llm.stream_complete([{"role": "user", "content": "你好"}]))


def test_qwen_disables_optional_thinking_in_stream_request(monkeypatch):
    from types import SimpleNamespace
    from app.core.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "fake")
    monkeypatch.setattr(settings, "openai_base_url", "https://example.invalid/v1")
    monkeypatch.setattr(settings, "model_name", "qwen3.6-plus")
    received = {}
    class NormalStream:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你好。"), finish_reason="stop")])
    class FakeClient:
        def __init__(self, **kwargs): self.chat = SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            received.update(kwargs)
            return NormalStream()
    monkeypatch.setattr(llm, "OpenAI", FakeClient)
    assert list(llm.stream_complete([{"role": "user", "content": "你好"}])) == ["你好。"]
    assert received.get("extra_body") == {"enable_thinking": False}
