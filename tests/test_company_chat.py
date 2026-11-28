"""CEO mode bound to the Chat conversation: privacy, exact handoffs, recorded execution, history and recovery."""
from __future__ import annotations

import hashlib
import json
import threading

import pytest

from agentwiz import chat_history, company_activity
from agentwiz.agent_loop import Reservation
from agentwiz.tutorial_support import backend
from conftest import failure_of, request
from test_company import company_client, create, settled  # noqa: F401


def stage(messages):
    system = messages[0]["content"]
    if "intake representative" in system:
        return "representative"
    if "planning object" in system:
        return "planner"
    if "identified candidate" in system:
        return "checker"
    if "propose a candidate" in system:
        return "reader"
    if "Act as the CEO" in system:
        return "reporter"
    return "node"


def accounted(view):
    """Every reportable arrival is accounted for by exactly one published update; the last is final."""
    updates = view["activity"]["updates"]
    seqs = [seq for row in updates for seq in row["accounts_for"]]
    assert len(seqs) == len(set(seqs))
    assert updates and updates[-1]["kind"] == "final"
    assert all(company_activity.fits(row["text"]) for row in updates)
    return updates


def history_file(child, conversation):
    return child.state.workspace.path / f"chat-history-{conversation}.json"


def test_local_choice_and_private_conversation_are_refused(company_client):
    child, company = company_client
    settings = company("settings")
    conversation = company.conversation()
    body = {"revision": settings["revision"], "conversation": conversation, "policy": "A"}
    local = failure_of(lambda: company("intakes", {**body, "model_choice": {"kind": "lmstudio", "model": "x"}}))
    assert local.body["code"] == "cloud_model_required"
    assert company("model", {"revision": settings["revision"],
                             "model_choice": None})["model"] == "tutorial/company"
    intake = company("intakes", {**body, "model_choice": None})["id"]
    with child.state.agent.lock:
        child.state.agent.conversation.private = True
    assert failure_of(lambda: company("intakes", {**body, "model_choice": None})).body["code"] == "private_conversation"
    assert failure_of(lambda: company(f"intakes/{intake}/message", {"text": "Hello"})).body["code"] == \
        "private_conversation"
    wrong = failure_of(lambda: company("intakes", {**body, "model_choice": None, "conversation": "conv_00000000"}))
    assert wrong.body["code"] == "no_such_conversation"


def test_representative_sees_only_company_entries(company_client, monkeypatch):
    child, company = company_client
    payloads = []
    real = backend.company_reply

    def reply(messages):
        if stage(messages) == "representative":
            payloads.append(json.loads(messages[1]["content"]))
        return real(messages)

    monkeypatch.setattr(backend, "company_reply", reply)
    body = create(company)
    with child.state.agent.lock:
        child.state.agent.conversation.chat_turns.append(
            {"run_id": "a" * 12, "message": "ORDINARY-CHAT-MARKER", "answer": "ORDINARY-ANSWER",
             "partial": None, "stop_reason": "answered", "trace": [], "sources": []})
    company(f"intakes/{body['intake_id']}/message", {"text": "First company message"})
    company(f"intakes/{body['intake_id']}/message", {"text": "Second company message"})
    assert "ORDINARY" not in json.dumps(payloads)
    assert payloads[1]["prior_company_turns"] == [
        {"message": "First company message", "kind": "clarification", "reply": "Should I cite the exact release-note line?"}]
    assert payloads[1]["last_run"] is None
    view = company.agent("conversation")["conversation"]
    assert [entry["type"] for entry in view["company"]] == ["user", "representative", "user", "representative"]
    assert view["totals"]["input_tokens"] >= 24


def test_handoff_binds_the_exact_brief(company_client, monkeypatch):
    child, company = company_client
    sent = []
    real = backend.company_reply

    def reply(messages):
        if stage(messages) == "reporter":
            sent.append(json.loads(messages[1]["content"])["brief"])
        return real(messages)

    monkeypatch.setattr(backend, "company_reply", reply)
    body = create(company)
    intake = body["intake_id"]
    first = company(f"intakes/{intake}/handoffs", {"brief": "First brief"})["entry"]
    second = company(f"intakes/{intake}/handoffs", {"brief": "Edited brief", "supersedes": first})["entry"]
    start = {"intake_id": intake, "revision": body["revision"], "client_request_id": "x"}
    digest = hashlib.sha256(b"Edited brief").hexdigest()
    assert failure_of(lambda: company("runs", {**start, "handoff_id": first,
                                               "brief_sha256": hashlib.sha256(b"First brief").hexdigest()})
                      ).body["code"] == "handoff_unavailable"
    assert failure_of(lambda: company("runs", {**start, "handoff_id": second, "brief_sha256": "0" * 64})
                      ).body["code"] == "handoff_changed"
    run = company("runs", {**start, "handoff_id": second, "brief_sha256": digest})
    assert settled(company, run)["outcome"] == "complete"
    assert sent == ["Edited brief"]
    assert company(f"handoffs/{second}")["brief"] == "Edited brief"
    entries = {entry["id"]: entry for entry in company.agent("conversation")["conversation"]["company"]}
    assert entries[first]["state"] == "superseded" and entries[second]["state"] == "sent"
    assert entries[second]["origin"] == "edited" and entries[second]["summary"] is None
    assert "brief" not in entries[second] and entries[run["id"]]["state"] == "finished"


def test_representative_handoff_summary_is_optional_and_never_repaired(company_client, monkeypatch):
    child, company = company_client
    calls = []

    def reply(messages):
        calls.append(messages)
        return {"kind": "brief_proposal", "reply": "Proposed.", "brief": "A public brief",
                "handoff": {"objective": "x" * 400, "constraints": [], "deliverable": "y"}}

    monkeypatch.setattr(backend, "company_reply", reply)
    body = create(company)
    company(f"intakes/{body['intake_id']}/message", {"text": "Write a brief"})
    assert len(calls) == 1
    handoff = [e for e in company.agent("conversation")["conversation"]["company"] if e["type"] == "handoff"][0]
    assert handoff["summary"] is None and handoff["origin"] == "representative"


@pytest.mark.parametrize("policy,strategy,kinds", [
    ("A", None, ["ceo_report"]),
    ("D", "specialists", ["ceo_plan", "specialist", "specialist", "ceo_report"]),
    ("C", "workflow", ["ceo_plan", "workflow", "workflow_node", "workflow_node", "model_attempt", "model_attempt",
                       "ceo_report"]),
])
def test_operations_paths_served_models_and_usage(company_client, monkeypatch, policy, strategy, kinds):
    child, company = company_client
    real = backend.company_reply

    def reply(messages):
        name = stage(messages)
        data = json.loads(messages[1]["content"]) if messages[1]["content"].startswith("{") else {}
        if name == "planner":
            decision = {"strategy": strategy, "purpose": "Answer.", "conditions": ["Name the version."]}
            if strategy == "specialists":
                decision["structure"] = "reader_checker"
            if strategy == "workflow":
                decision.update(preset_id="source_answer", parameters={
                    "question": "Which version introduced Export Preview?", "checklist": ["Name the version."]})
            return decision
        if name == "checker":
            return {"candidate_id": data["candidate_id"], "passed": False, "findings": ["Missing citation"]}
        if name == "reader":
            return {"answer": "v1.4.0", "quotes": ["v1.4.0: Introduced Export Preview"]}
        if name == "reporter":
            status = data["required_status"] or "complete"
            return {"status": status, "evidence_ids": [], "open_conditions": data["open_conditions"],
                    "markdown": f"Status: {status}\n\n## Result\nv1.4.0\n\n## Evidence\nNotes.\n\n## Open conditions\n"
                                + ("\n".join(data["open_conditions"]) or "None.") + "\n"}
        return real(messages)

    monkeypatch.setattr(backend, "company_reply", reply)
    state = settled(company, company("runs", create(company, policy, attempts=8)))
    operations = state["activity"]["operations"]
    assert [op["kind"] for op in operations] == kinds, operations
    assert all(row["tier"] != "team" for op in operations for row in op["path"])
    model_ops = [op for op in operations if op["usage"] is not None]
    assert model_ops and all(op["served"]["model"] == "stub" for op in model_ops)
    totals = {key: sum(op["usage"][key] or 0 for op in model_ops) for key in ("input_tokens", "output_tokens")}
    assert totals == {key: state["usage"][key] for key in totals}
    assert all(op["state"] == "complete" for op in operations)
    updates = accounted(state)
    if policy == "D":
        assert [op["path"][0]["label"] for op in operations[1:3]] == ["Reader", "Checker"]
        assert "did not pass the candidate answer (1 finding)" in updates[3]["text"]
        assert state["outcome"] == "partial"
    if policy == "C":
        assert operations[2]["path"][-1]["tier"] == "block" and operations[2]["parent"] == operations[1]["id"]
        assert [op["parent"] for op in operations[4:6]] == [operations[2]["id"], operations[3]["id"]]
        assert any(row["served_model"] == "stub" for row in state["trace"] if row["stage"] == "workflow_model")
        assert "finished all 2 steps" in updates[2]["text"]


def test_planner_status_update_is_published_or_rejected_without_repair(company_client, monkeypatch):
    child, company = company_client
    real = backend.company_reply
    texts = iter(["I will answer this directly from the notes.", " ".join(["long"] * 51)])
    results = []
    for _ in range(2):
        update = next(texts)

        def reply(messages, update=update):
            if stage(messages) == "planner":
                return {"strategy": "solo", "purpose": "Answer.", "conditions": [], "status_update": update}
            return real(messages)

        monkeypatch.setattr(backend, "company_reply", reply)
        state = settled(company, company("runs", create(company, "E")))
        results.append(state)
    first, second = (accounted(state)[1] for state in results)
    assert first["source"] == "ceo" and first["text"] == "I will answer this directly from the notes."
    assert second["source"] == "run_record" and second["text"] == "The CEO will answer directly without delegating."
    assert results[0]["attempts"] == results[1]["attempts"] == 2


def test_stop_and_denial_leave_truthful_records(company_client, monkeypatch, make_fake_mcp):
    from conftest import simple_tool, text_result, wait_until
    from test_agent import mcp_setup

    child, company = company_client
    tool = simple_tool("records", {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
                       lambda call: call.result(text_result("synthetic")))
    _, names = mcp_setup(child, make_fake_mcp, [tool])
    monkeypatch.setattr(backend, "company_reply", lambda messages: {"tool_call": (names["records"], {"n": 1})})
    run = company("runs", create(company, "B"))
    path = f"runs/{run['id']}"
    assert wait_until(lambda: company(path)["pending_approval"])
    live = company(path)
    assert live["activity"]["summary"]["state"] == "awaiting_approval"
    assert live["activity"]["updates"][-1]["kind"] == "approval"
    pending = live["pending_approval"]
    company(path + "/approval", {"call_id": pending["call_id"], "arguments_sha256": pending["arguments_sha256"],
                                 "decision": "deny"})
    state = settled(company, run)
    kinds = {op["kind"]: op for op in state["activity"]["operations"]}
    assert kinds["approval"]["state"] == "failed" and kinds["approval"]["code"] == "tool_denied"
    assert state["outcome"] == "blocked"
    assert "you denied the tool request" in accounted(state)[-1]["text"]

    hold = threading.Event()
    real = backend.company_reply
    monkeypatch.setattr(backend, "company_reply", lambda messages: (hold.wait(10), real(messages))[1])
    run = company("runs", create(company, "A"))
    try:
        assert wait_until(lambda: company(f"runs/{run['id']}")["activity"]["summary"]["state"] == "reporting")
        company(f"runs/{run['id']}/stop", {})
        state = settled(company, run)
    finally:
        hold.set()
    assert state["outcome"] == "stopped" and not state["report_available"]
    assert [op["state"] for op in state["activity"]["operations"]] == ["cancelled"]
    assert accounted(state)[-1]["text"].startswith("Stopped at your request.")
    assert failure_of(lambda: company(f"runs/{run['id']}/report", raw=True)).status == 404


def test_chat_and_company_work_are_mutually_exclusive(company_client, monkeypatch):
    child, company = company_client
    manager = child.state.agent
    body = create(company)
    with manager.lock:
        manager.reserved = Reservation(manager.conversation)
    try:
        assert failure_of(lambda: company(f"intakes/{body['intake_id']}/message", {"text": "Hi"})).body["code"] == \
            "run_active"
    finally:
        with manager.lock:
            manager.reserved = None
    hold = threading.Event()
    real = backend.company_reply
    monkeypatch.setattr(backend, "company_reply", lambda messages: (hold.wait(10), real(messages))[1])
    run = company("runs", body)
    try:
        view = company.agent("conversation")
        assert view["company_active"] == {"kind": "run", "id": run["id"]}
        from agentwiz.agent_loop import AgentError
        with pytest.raises(AgentError) as busy:
            manager.prepare_run({"conversation": view["conversation"]["id"], "revision": body["revision"],
                                 "message": "Hello", "mode": "cloud"}, None)
        assert busy.value.code == "run_active"
        second = create(company)
        assert failure_of(lambda: company(f"intakes/{second['intake_id']}/message", {"text": "Hi"})).body["code"] == \
            "company_busy"
    finally:
        hold.set()
    settled(company, run)
    assert company.agent("conversation")["company_active"] is None


def test_history_v3_round_trip_report_integrity_and_interruption(company_client):
    child, company = company_client
    manager = child.state.agent
    run = company("runs", create(company))
    state = settled(company, run)
    conversation = company.conversation()
    path = history_file(child, conversation)
    saved = json.loads(path.read_text())
    assert saved["format_version"] == 3 and saved["turns"] == []
    run_entry = next(entry for entry in saved["company"] if entry["type"] == "run")
    with company(f"runs/{run['id']}/report", raw=True) as reply:
        data = reply.read()
    assert run_entry["report"]["markdown"].encode() == data
    assert run_entry["report"]["sha256"] == hashlib.sha256(data).hexdigest() == state["report_digest"]
    for prompt in ("planning object", "Act as the CEO", "intake representative"):
        assert prompt not in path.read_text()
    assert "strategy_inventory" not in path.read_text() and "model_inventory" not in path.read_text()

    # Evicted from memory: the report is served from the conversation's entry, digest-checked.
    with child.state.company.lock:
        child.state.company.jobs.clear()
    with company(f"runs/{run['id']}/report", raw=True) as reply:
        assert reply.read() == data
    stored = company(f"runs/{run['id']}")
    assert stored["live"] is False and stored["outcome"] == "complete"

    # A running entry saved before the process ended reopens as interrupted, never as complete.
    running = {**run_entry, "id": "b" * 32, "state": "running", "outcome": None, "report": None}
    manager.record_company(conversation, running)
    tampered = json.loads(path.read_text())
    for entry in tampered["company"]:
        if entry["id"] == run["id"]:
            entry["report"]["markdown"] = entry["report"]["markdown"].replace("v1.4.0", "v9.9.9")
    path.write_text(json.dumps(tampered))
    request(child.chat_listener, "/api/agent/conversation", {"format_version": 1})
    request(child.chat_listener, "/api/chat/history/open", {"id": conversation, "close_private": None})
    entries = {entry["id"]: entry for entry in company.agent("conversation")["conversation"]["company"]}
    assert entries["b" * 32]["state"] == "interrupted" and entries["b" * 32]["reason"] == "process_ended"
    assert failure_of(lambda: company(f"runs/{run['id']}/report", raw=True)).body["code"] == "report_integrity"
    timeline = company.agent("conversation")["conversation"]["timeline"]
    assert [kind for kind, _ in timeline] == ["company"] * len(entries)


def test_chat_without_company_entries_stays_v2(tmp_path):
    store = chat_history.History(tmp_path)
    turn = {"run_id": "c" * 12, "message": "Hi", "answer": "Hello", "partial": None,
            "stop_reason": "answered", "trace": [], "sources": []}
    store.save("conv_12345678", [turn], None, None, [])
    assert json.loads((tmp_path / "chat-history-conv_12345678.json").read_text())["format_version"] == 2
    row = store.reopen("conv_12345678")
    assert row["company"] == [] and row["timeline"] == [["chat", turn["run_id"]]]
