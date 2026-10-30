import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.services import agent, llm


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_general_chat_skips_retrieval_and_persists_finished_trace(client, hr, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda _: {"intent": "general", "names": [], "query": "你好"})
    monkeypatch.setattr(llm, "stream_complete", lambda _: iter(["你好。", "欢迎咨询。 "]))
    session = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    result = events(client.post(f"/api/chat/sessions/{session}/messages/stream", headers=hr, json={"content": "你好"}))
    starts = [e["node"] for e in result if e["type"] == "node_started"]
    finishes = [e["node"] for e in result if e["type"] == "node_finished"]
    assert [n["node_id"] for n in starts] == ["plan", "generate"]
    assert all(n["status"] == "running" for n in starts)
    assert all(n["status"] == "succeeded" and n["elapsed_ms"] >= 0 for n in finishes)
    assert result[-2]["type"] == "node_finished" and result[-1]["type"] == "done"
    assert result[-1]["message"]["trace"] == finishes
    history = client.get(f"/api/chat/sessions/{session}/messages", headers=hr).json()
    assert history[-1]["trace"] == finishes and history[0]["trace"] == []


def test_talent_query_really_executes_retrieval(client, hr, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda _: {"intent": "talent_match", "query": "Java", "names": []})
    session = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    result = events(client.post(f"/api/chat/sessions/{session}/messages/stream", headers=hr, json={"content": "推荐Java"}))
    assert [e["node"]["node_id"] for e in result if e["type"] == "node_started"] == ["plan", "retrieve", "generate"]
    assert result[-1]["type"] == "done"


class State(TypedDict, total=False):
    count: int
    trace_summary: str


def test_arbitrary_four_nodes_with_reentry_and_no_raw_state_exposure():
    from app.services.tracing import stream_graph
    graph = StateGraph(State)
    for name in ["alpha", "new_tool", "check", "finish"]:
        graph.add_node(name, lambda s: {"count": s["count"] + 1, "trace_summary": "完成业务处理"},
                       metadata={"title": f"节点{name}", "node_type": "tool"})
    graph.add_edge(START, "alpha")
    graph.add_edge("alpha", "new_tool")
    graph.add_conditional_edges("new_tool", lambda s: "new_tool" if s["count"] < 3 else "check")
    graph.add_edge("check", "finish")
    graph.add_edge("finish", END)
    result = list(stream_graph(graph.compile(), {"count": 0}))
    starts = [e["node"] for e in result if e["type"] == "node_started"]
    assert [n["node_id"] for n in starts] == ["alpha", "new_tool", "new_tool", "check", "finish"]
    assert len({n["id"] for n in starts}) == 5
    assert all(n["title"] == f"节点{n['node_id']}" and n["node_type"] == "tool" for n in starts)
    assert not any(k in json.dumps(result) for k in ['"input"', '"result"', '"count"'])


def test_parallel_executions_keep_their_own_ids():
    from app.services.tracing import stream_graph
    graph = StateGraph(agent.AgentState)
    graph.add_node("a", lambda _: {"trace_summary": "A任务已完成"})
    graph.add_node("b", lambda _: {"trace_summary": "B任务已完成"})
    for name in ["a", "b"]:
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    result = list(stream_graph(graph.compile(), {}))
    starts = {e["node"]["id"] for e in result if e["type"] == "node_started"}
    finishes = {e["node"]["id"] for e in result if e["type"] == "node_finished"}
    assert len(starts) == 2 and starts == finishes
    assert {e["node"]["summary"] for e in result if e["type"] == "node_finished"} == {"A任务已完成", "B任务已完成"}


def test_failure_emits_failed_node_without_raw_exception(client, hr, monkeypatch):
    def fail(_):
        raise llm.ModelUnavailableError("模型暂不可用")
    monkeypatch.setattr(llm, "structured", fail)
    session = client.post("/api/chat/sessions", headers=hr, json={}).json()["id"]
    result = events(client.post(f"/api/chat/sessions/{session}/messages/stream", headers=hr, json={"content": "你好"}))
    failed = [e["node"] for e in result if e["type"] == "node_finished"]
    assert len(failed) == 1 and failed[0]["status"] == "failed"
    assert result[-1]["type"] == "error" and not any(e["type"] == "done" for e in result)
    assert "ModelUnavailableError" not in json.dumps(result)
