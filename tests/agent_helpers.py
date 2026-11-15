"""Fake-endpoint replies and HTTP helpers for agentic Chat tests (0.2.2, 13.1).

The fake backend streams Chat Completions events, so modelSpell's own parser builds every ToolTurn.
"""
from __future__ import annotations

import json

from conftest import DONE, Reply, request, sse_event


def tool_call(name: str, arguments, call_id: str = "call_1", text: str | None = None) -> Reply:
    """One streamed turn holding one tool call (finish 'tool_calls'). `arguments` is a JSON value or raw text."""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    frags = []
    if text:
        frags.append(sse_event(json.dumps({"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})))
    frags.append(sse_event(json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": raw}}]},
        "finish_reason": None}]})))
    frags.append(sse_event(json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})))
    frags.append(DONE)
    return Reply(body=frags, headers={"Content-Type": "text/event-stream"})


def tool_calls(*calls) -> Reply:
    """One turn holding several calls: (name, arguments, call_id) triples."""
    deltas = [{"index": i, "id": cid, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
              for i, (n, a, cid) in enumerate(calls)]
    return Reply(body=[sse_event(json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": deltas},
                                                          "finish_reason": None}]})),
                       sse_event(json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})),
                       DONE], headers={"Content-Type": "text/event-stream"})


def text_turn(text: str, finish: str = "stop") -> Reply:
    return Reply(body=[sse_event(json.dumps({"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})),
                       sse_event(json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})),
                       DONE], headers={"Content-Type": "text/event-stream"})


def start_conversation(ui) -> str:
    return request(ui, "/api/agent/conversation", {"format_version": 1})["conversation"]["id"]


def stage(ui, conversation, text: str, name: str = "notes.txt", local_only: bool = False):
    import http.client
    host, port = ui.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=10)
    data = text.encode("utf-8")
    conn.request("POST", "/api/agent/documents", body=data, headers={
        "X-Session-Token": ui.token, "Content-Length": str(len(data)), "X-Filename": name,
        "X-Conversation": conversation, "X-Local-Only": "true" if local_only else "false"})
    resp = conn.getresponse()
    body = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, body


def run(ui, conversation, message: str, capabilities=(), revision=None, task=None, limits=None, raw=False):
    from conftest import get_state, iter_events
    if revision is None:
        revision = get_state(ui)["revision"]
    body = {"format_version": 1, "conversation": conversation, "revision": revision, "message": message,
            "capabilities": [c if isinstance(c, dict) else {"name": c} for c in capabilities]}
    if task is not None:
        body["task"] = task
    if limits is not None:
        body["limits"] = limits
    resp = request(ui, "/api/agent/run", body, raw=True, timeout=30)
    if raw:
        return resp
    return list(iter_events(resp))


def final(events: list[dict]) -> dict:
    return next(e for e in events if e["type"] == "final")


def sent_bodies(server) -> list[dict]:
    return [r["body"] for r in server.requests if r["method"] == "POST"]


def plain_answer(text: str, finish: str = "stop") -> Reply:
    """A non-streamed Chat Completions reply (the authoring dispatcher does not stream)."""
    body = {"id": "r1", "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                     "finish_reason": finish}]}
    return Reply(body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
