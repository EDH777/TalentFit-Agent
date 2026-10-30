import asyncio
from typing import TypedDict

from langgraph.graph import START, END, StateGraph


def test_async_nested_graph_trace_accepts_config_and_never_exposes_state():
    from app.services.tracing import astream_graph

    class State(TypedDict, total=False):
        secret: str
        trace_summary: str

    sub = StateGraph(State)
    sub.add_node('read', lambda _: {'trace_summary': '已读取有效来源。'}, metadata={'title': '能力资料', 'node_type': 'tool'})
    sub.add_edge(START, 'read')
    sub.add_edge('read', END)
    main = StateGraph(State)
    main.add_node('specialist', sub.compile(), metadata={'title': '能力 Agent', 'node_type': 'llm'})
    main.add_edge(START, 'specialist')
    main.add_edge('specialist', END)

    async def collect():
        return [event async for event in astream_graph(main.compile(), {'secret': 'private-body'},
                 config={'configurable': {'thread_id': 'server-run'}}, run_id='server-run', subgraphs=True)]

    events = asyncio.run(collect())
    starts = [e['node'] for e in events if e['type'] == 'node_started']
    finishes = [e['node'] for e in events if e['type'] == 'node_finished']
    assert {node['node_id'] for node in starts} == {'specialist', 'read'}
    assert all(node['id'].startswith('server-run:') for node in starts)
    assert {n['id'] for n in starts} == {n['id'] for n in finishes}
    assert all(n['status'] == 'succeeded' for n in finishes)
    assert any(n.get('namespace') for n in starts)
    assert 'private-body' not in str(events) and 'secret' not in str(events)
