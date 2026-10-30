"""Translate actual LangGraph task events into safe, UI-facing node lifecycles."""
from datetime import datetime, timezone
from time import perf_counter

from app.services.events import StreamCancelled


def stream_graph(graph, state, cancelled=None):
    executions = {}
    for mode, data in graph.stream(state, stream_mode=["tasks", "custom"]):
        if cancelled and cancelled():
            raise StreamCancelled()
        if mode == "custom":
            yield data
            continue
        identity = data["id"]
        if "input" in data:
            metadata = data.get("metadata") or {}
            node = {
                "id": identity, "node_id": data["name"],
                "title": metadata.get("title") or data["name"],
                "node_type": metadata.get("node_type") or "task",
                "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
            }
            executions[identity] = (node, perf_counter())
            yield {"type": "node_started", "node": node.copy()}
        else:
            node, started = executions[identity]
            node = {**node, "status": "failed" if data.get("error") else "succeeded",
                    "elapsed_ms": round((perf_counter() - started) * 1000)}
            if data.get("error"):
                node["summary"] = "节点执行失败，请稍后重试。"
            else:
                # Never forward task inputs/results: these contain prompts and full resumes.
                summary = (data.get("result") or {}).get("trace_summary")
                if isinstance(summary, str):
                    node["summary"] = summary[:500]
            yield {"type": "node_finished", "node": node}


async def astream_graph(graph, state, *, config, run_id, subgraphs=True, cancelled=None):
    """Staffing adapter: persisted config + nested task namespaces; never forwards state."""
    executions = {}
    async for part in graph.astream(state, config=config, stream_mode=["tasks", "custom"], subgraphs=subgraphs):
        if cancelled and cancelled():
            raise StreamCancelled()
        if subgraphs:
            namespace, mode, data = part
        else:
            mode, data = part
            namespace = ()
        if mode == 'custom':
            yield data
            continue
        identity = f"{run_id}:{'/'.join(namespace)}:{data['id']}"
        if 'input' in data:
            metadata = data.get('metadata') or {}
            node = {'id': identity, 'node_id': data['name'], 'title': metadata.get('title') or data['name'],
                    'namespace': list(namespace), 'node_type': metadata.get('node_type') or 'task',
                    'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat()}
            executions[identity] = (node, perf_counter())
            yield {'type': 'node_started', 'node': node.copy()}
        else:
            previous = executions.get(identity)
            if not previous:
                continue
            node, started = previous
            node = {**node, 'status': 'failed' if data.get('error') else 'interrupted' if data.get('interrupts') else 'succeeded',
                    'elapsed_ms': round((perf_counter() - started) * 1000)}
            summary = (data.get('result') or {}).get('trace_summary') if isinstance(data.get('result'), dict) else None
            if data.get('error'):
                node['summary'] = '节点执行失败，请查看运行状态后重试。'
            elif isinstance(summary, str):
                from app.services.agent import redact_contacts
                node['summary'] = redact_contacts(summary)[:500]
            yield {'type': 'node_finished', 'node': node}
