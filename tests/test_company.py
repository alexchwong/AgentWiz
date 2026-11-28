from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.request

import pytest

from agentwiz.tutorial_support import backend
from conftest import HTTPFailure, failure_of, request


@pytest.fixture
def company_client(ui):
    request(ui, "/api/tutorial/start", {})
    child = ui.tutorial.child
    request(child, "/api/tutorial/step", {"topic": "company-ceo"})
    chat = child.chat_listener
    chat.token = chat.authority.session

    handoffs = {}

    def raw(path, body=None, **kwargs):
        payload = None if body is None else {"format_version": 1, **body}
        return request(chat, "/api/company/" + path, payload, **kwargs)

    def company(path, body=None, **kwargs):
        """Company API. A `runs` body carrying `brief` is sent as an exact-text handoff first (the UI's
        direct-brief path), so the run is admitted by handoff ID and digest like every CEO run."""
        if path == "runs" and body is not None and "brief" in body:
            body = dict(body)
            brief = body.pop("brief")
            key = (body["intake_id"], brief)
            if key not in handoffs:
                try:
                    handoffs[key] = raw(f"intakes/{body['intake_id']}/handoffs", {"brief": brief})["entry"]
                except HTTPFailure:     # a consumed intake: submit an unknown handoff so admission decides
                    handoffs[key] = "ce_" + "0" * 16
            body.update(handoff_id=handoffs[key], brief_sha256=hashlib.sha256(brief.encode()).hexdigest())
        return raw(path, body, **kwargs)

    def conversation():
        view = request(chat, "/api/agent/conversation")
        if view["conversation"] is None:
            view = request(chat, "/api/agent/conversation", {"format_version": 1})
        return view["conversation"]["id"]

    company.conversation = conversation
    company.agent = lambda path, body=None: request(chat, "/api/agent/" + path, body)
    yield child, company
    ui.tutorial.close()


def create(company, policy="A", **limits):
    settings = company("settings")
    revision = settings["revision"]
    intake = company("intakes", {"revision": revision, "conversation": company.conversation(),
                                 "model_choice": None, "policy": policy, **limits})
    return {"intake_id": intake["id"], "revision": revision, "brief": settings["example"],
            "client_request_id": "one"}


def settled(company, run):
    for _ in range(200):
        state = company(f"runs/{run['id']}")
        if state["outcome"]:
            return state
        time.sleep(.025)
    pytest.fail("CEO run did not settle")


def test_typed_intake_exact_report_and_authority(company_client, monkeypatch):
    child, company = company_client
    captures = []
    real_reply = backend.company_reply

    def capture(messages):
        captures.append(messages)
        result = real_reply(messages)
        if "markdown" in result:
            result["markdown"] = result["markdown"].replace("appeared", "first appeared ✓")
        return result

    monkeypatch.setattr(backend, "company_reply", capture)
    settings = company("settings")
    assert not captures
    assert company("model-class", {"revision": settings["revision"], "binding": "fast",
                                   "class": "Strong"})["bindings"][0]["class"] == "Strong"
    child.state.set_history_switch(True, child.state.view()["history"]["revision"])
    body = create(company)
    intake = body["intake_id"]
    assert company(f"intakes/{intake}/message", {"text": "Ask me to clarify only."})["kind"] == "clarification"
    assert len(captures) == 1
    assert failure_of(lambda: company("runs", {**body, "attachment": "x"})).status == 400
    first = company("runs", body)
    assert company("runs", body)["id"] == first["id"]
    assert failure_of(lambda: company("runs", {**body, "brief": "Changed"})).body["code"] == "changed_duplicate"
    state = settled(company, first)
    assert state["outcome"] == "complete", state
    assert state["pathway"] == "solo" and len(captures) == 2
    payload = json.loads(captures[1][-1]["content"])
    assert payload["brief"] == body["brief"] and "Ask me" not in str(captures[1])
    with company(f"runs/{first['id']}/report", raw=True) as report:
        data = report.read()
        assert report.headers["Cache-Control"] == "no-store"
    folder = child.state.workspace.path / "runs"
    record = json.loads((folder / f"company-{first['id']}.json").read_text())
    assert data == record["report"].encode()
    assert state["report_size"] == len(data)
    assert state["report_digest"] == hashlib.sha256(data).hexdigest()
    assert failure_of(lambda: company(f"runs/{first['id']}/report", token=False)).status == 403
    assert failure_of(lambda: request(child, "/api/company/settings")).status == 403


def test_reserved_attempts_and_forced_workflow_lease(company_client):
    child, company = company_client
    work = create(company, attempts=1)
    message = f"intakes/{work['intake_id']}/message"
    assert failure_of(lambda: company(message, {"text": "Hello"})).body["code"] == "allowance_reserved"
    assert settled(company, company("runs", work))["attempts"] == 1
    body = create(company, policy="C")
    with child.state.workshop.lock:
        child.state.workshop.active = "native-work"
    try:
        assert failure_of(lambda: company("runs", body)).body["code"] == "admission_busy"
    finally:
        with child.state.workshop.lock:
            child.state.workshop.active = None


@pytest.mark.parametrize("policy,expected,count", [("A", "solo", 1), ("D", "specialists", 4),
                                                    ("E", "specialists", 3), ("C", "workflow", 4)])
def test_pathway_dispatch_with_pinned_modelspell(company_client, monkeypatch, policy, expected, count):
    _, company = company_client
    calls = []

    def reply(messages):
        prompt = messages[0]["content"]
        calls.append(prompt)
        data = json.loads(messages[1]["content"]) if messages[1]["content"].startswith("{") else {}
        if "planning object" in prompt:
            decision = {"strategy": expected, "purpose": "Answer the question.", "conditions": ["Name the version."]}
            if expected == "specialists":
                decision["structure"] = "reader_only" if policy == "E" else "reader_checker"
            if expected == "workflow":
                decision.update(preset_id="source_answer", parameters={"question": "Which version introduced Export Preview?", "checklist": ["Name the version."]})
            return decision
        if "Candidate" in prompt or "identified candidate" in prompt:
            return {"candidate_id": data["candidate_id"], "passed": True, "findings": []}
        if "propose a candidate" in prompt:
            return {"answer": "The answer is v1.4.0", "quotes": ["v1.4.0: Introduced Export Preview"]}
        if "Act as the CEO" in prompt:
            result = real_reply(messages)
            if "checker" in data["evidence"]:
                result["checked_candidate_id"] = data["evidence"]["checker"]["candidate_id"]
            return result
        if "source" in prompt.lower() and len(calls) == 2:
            return {"found": True, "answer": "The answer is v1.4.0", "quotes": ["v1.4.0: Introduced Export Preview"]}
        return {"results": [{"item": "Name the version.", "met": "yes", "evidence": "v1.4.0"}],
                "overall": "Checks passed."}

    real_reply = backend.company_reply
    monkeypatch.setattr(backend, "company_reply", reply)
    body = create(company, policy=policy, attempts=count)
    status = settled(company, company("runs", body))
    assert status["outcome"] == "complete", status
    assert status["pathway"] == expected and status["attempts"] == count
    assert len(calls) == count


def test_stop_suppresses_late_report(company_client, monkeypatch):
    _, company = company_client
    entered, unblock = threading.Event(), threading.Event()
    real_reply = backend.company_reply

    def held(messages):
        entered.set()
        unblock.wait(10)
        return real_reply(messages)

    monkeypatch.setattr(backend, "company_reply", held)
    body = create(company)
    result = company("runs", body)
    try:
        assert entered.wait(10)
        assert company(f"runs/{result['id']}/stop", {})["stopping"]
        done = settled(company, result)
        assert done["outcome"] == "stopped" and not done["report_available"]
    finally:
        unblock.set()


def test_duplicate_json_keys_are_rejected(company_client):
    child, _ = company_client
    chat = child.chat_listener
    req = urllib.request.Request(chat.url + "/api/company/intakes",
                                 data=b'{"format_version":1,"format_version":1}',
                                 headers={"X-Session-Token": chat.token, "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as rejected:
        urllib.request.urlopen(req)
    assert rejected.value.code == 400


@pytest.mark.parametrize("name,outcome", [("aw_calculate", "complete"), ("company_calculate_block", "complete"),
                                          ("launch_workflow", "blocked")])
def test_tools_policy_offers_only_validated_capabilities(company_client, monkeypatch, name, outcome):
    _, company = company_client
    real_reply = backend.company_reply
    calls = []

    def script(messages):
        calls.append(messages)
        if len(calls) == 1:
            return {"tool_call": (name, {"expression": "530 * 0.9 * 1.1"})}
        result = real_reply(messages)
        result["evidence_ids"] = ["tool-1"]
        return result

    monkeypatch.setattr(backend, "company_reply", script)
    state = settled(company, company("runs", create(company, "B")))
    assert state["outcome"] == outcome, json.dumps(state)
    assert state["pathway"] == "tools"
    if outcome == "complete":
        assert calls[1][-1]["role"] == "tool"
        assert "524.7" in calls[1][-1]["content"]
    else:
        assert len(calls) == 1 and not state["evidence"]


def test_hard_cap_without_verified_bound_refuses_before_inference(company_client, monkeypatch):
    _, company = company_client
    calls = []
    monkeypatch.setattr(backend, "company_reply", lambda messages: calls.append(messages))
    result = settled(company, company("runs", create(company, token_cap=10000)))
    assert result["outcome"] == "blocked" and result["reason"] == "budget_bound_unavailable"
    assert not calls


def test_fabricated_quotation_never_enters_ledger(company_client, monkeypatch):
    _, company = company_client
    real_reply = backend.company_reply
    calls = []

    def script(messages):
        calls.append(messages)
        result = real_reply(messages)
        result.update(source_quotes=["This text was not in the submitted brief."],
                      evidence_ids=["brief-quote-1"])
        return result

    monkeypatch.setattr(backend, "company_reply", script)
    state = settled(company, company("runs", create(company, attempts=2)))
    assert len(calls) == 2
    assert state["outcome"] == "failed" and state["reason"] == "invalid_quote"
    assert not state["report_available"] and not state["evidence"]


@pytest.mark.parametrize("action", ["approve", "deny", "stop"])
def test_scoped_read_only_tool_approval(company_client, make_fake_mcp, monkeypatch, action):
    from conftest import simple_tool, text_result, wait_until
    from test_agent import mcp_calls, mcp_setup

    child, company = company_client
    tool = simple_tool("records", {"type": "object", "properties": {"n": {"type": "integer"}},
                                   "required": ["n"]}, lambda call: call.result(text_result("synthetic record")))
    fake, names = mcp_setup(child, make_fake_mcp, [tool])
    complete = backend.company_reply

    def script(messages):
        if any(message["role"] == "tool" for message in messages):
            return complete(messages)
        return {"tool_call": (names["records"], {"n": 1})}

    monkeypatch.setattr(backend, "company_reply", script)
    run = company("runs", create(company, "B"))
    path = f"runs/{run['id']}"
    assert wait_until(lambda: company(path)["pending_approval"])
    pending = company(path)["pending_approval"]
    assert pending["arguments_text"] == '{"n":1}' and not mcp_calls(fake)
    decision = {"call_id": pending["call_id"], "arguments_sha256": pending["arguments_sha256"], "decision": action}
    if action == "stop":
        assert company(path + "/stop", {})["stopping"]
    else:
        assert failure_of(lambda: company(path + "/approval", {**decision,
                          "arguments_sha256": "mismatched"})).body["code"] == "not_pending"
        assert company(path + "/approval", decision)["decision"] == action
    state = settled(company, run)
    assert state["outcome"] == {"approve": "complete", "deny": "blocked", "stop": "stopped"}[action], state
    assert len(mcp_calls(fake)) == int(action == "approve")
    if action != "stop":
        assert failure_of(lambda: company(path + "/approval", decision)).body["code"] == "not_pending"


def test_all_preset_graphs_via_company(company_client, monkeypatch):
    child, company = company_client
    catalogue = child.state.company.catalogue
    expected = {
        "source_answer": ("answer", lambda value: "v1.4.0" in value),
        "amendment_comparison": ("assessment", lambda value: ["12 hours", "24 hours"] ==
                                 [row["requirement"] for row in value["conclusions"][:2]]),
        "criteria_evidence": ("criteria", lambda value: not value["complete"] and
                              [row["status"] for row in value["rows"]].count("unknown") == 1),
        "issue_register": ("register", lambda value: value[1]["values"] == ["Ben", "Dana"] and
                           [item["latest"][0]["status"] for item in value] == ["Closed", "In progress", "Open"]),
        "csv_profile": ("statistics", lambda value: value["numeric_count"] == 3
                        and value["sum"] == "8" and value["empty_count"] == value["other_count"] == 1),
        "constraint_screen": ("screened", lambda value: [row["id"] for row in value if row["status"] == "eligible"] == ["B", "C"]),
        "meeting_actions": ("assessment", lambda value: value["actions"][1]["owner"] is None),
        "log_triage": ("assessment", lambda value: "evidence" in value["primary_cause"]),
        "stakeholder_synthesis": ("assessment", lambda value: len(value["prerequisites"]) == 4),
        "corpus_evidence_map": ("assessment", lambda value: len(value["studies"]) == 5),
    }
    assert set(catalogue.presets) == expected.keys()

    for preset in catalogue.presets.values():
        ident = preset["id"]
        trial = catalogue.json(catalogue.manifest["fixtures"][preset["examples"][0]])
        outcomes = iter(trial["nodes"])
        planner_calls = []

        def reply(messages):
            prompt = messages[0]["content"]
            if "planning object" in prompt:
                payload = json.loads(messages[1]["content"])
                planner_calls.append(payload["workflow_presets"])
                return dict(strategy="workflow", purpose="Process the supplied material", conditions=[],
                            preset_id=ident, parameters=trial["parameters"])
            if "Act as the CEO" in prompt:
                payload = json.loads(messages[1]["content"])
                status = payload["required_status"] or "complete"
                return dict(status=status, evidence_ids=[], open_conditions=payload["open_conditions"],
                            markdown=f"Status: {status}\n\n## Result\nFixture run.\n\n## Evidence\n"
                                     "See graph evidence.\n\n## Open conditions\nSee trace.\n")
            return next(outcomes)

        monkeypatch.setattr(backend, "company_reply", reply)
        selection = {"id": ident, "revision": preset["revision"]}
        body = create(company, "C", attempts=preset["model_calls"] + 2, workflow_preset=selection)
        state = settled(company, company("runs", {**body, "brief": trial["brief"]}))
        assert state["attempts"] == preset["model_calls"] + 2, (ident, state)
        assert state["outcome"] == ("partial" if ident == "criteria_evidence" else "complete"), (ident, state)
        assert state["report_available"]
        assert [item["id"] for item in planner_calls[0]] == [ident]
        row = next(item for item in state["trace"] if item["stage"] == "workflow")
        assert row["workflow_preset"] == ident and row["revision"] == preset["revision"]
        key, predicate = expected[ident]
        assert predicate(state["evidence"][key]["value"]), (ident, state["evidence"])
        if ident == "csv_profile":
            assert state["evidence"]["duplicate_count"]["value"] == 1


def test_e_can_choose_workflow_from_eligible_catalogue(company_client, monkeypatch):
    child, company = company_client
    catalogue = child.state.company.catalogue
    preset = catalogue.presets["csv_profile"]
    trial = catalogue.json(catalogue.manifest["fixtures"][preset["examples"][0]])
    reply_default = backend.company_reply
    offered = []

    def reply(messages):
        if "planning object" in messages[0]["content"]:
            payload = json.loads(messages[1]["content"])
            offered.extend(item["id"] for item in payload["workflow_presets"])
            assert "solo" in payload["strategy_inventory"]
            return dict(strategy="workflow", preset_id=preset["id"], parameters=trial["parameters"],
                        conditions=[], purpose="Profile the supplied CSV.")
        return reply_default(messages)

    monkeypatch.setattr(backend, "company_reply", reply)
    body = create(company, "E", attempts=2)
    state = settled(company, company("runs", {**body, "brief": trial["brief"]}))
    assert offered == [preset["id"]]
    assert state["outcome"] == "complete" and state["pathway"] == "workflow"
    assert state["evidence"]["duplicate_count"]["value"] == 1


def test_pinned_workflow_refuses_stale_and_wrong_port(company_client, monkeypatch):
    child, company = company_client
    preset = child.state.company.catalogue.presets["csv_profile"]
    selection = {"id": preset["id"], "revision": preset["revision"]}
    error = failure_of(lambda: create(company, "C", workflow_preset={**selection, "revision": "0" * 64}))
    assert error.body["code"] == "stale_workflow_preset"
    calls = []

    def reply(messages):
        calls.append(messages)
        return dict(strategy="workflow", preset_id=preset["id"], parameters={"column": "invented"},
                    conditions=[], purpose="Profile the supplied CSV.")

    monkeypatch.setattr(backend, "company_reply", reply)
    body = create(company, "C", attempts=2, workflow_preset=selection)
    state = settled(company, company("runs", {**body, "brief": "Item,Units\nA,2\n"}))
    assert state["outcome"] == "blocked" and state["reason"] == "invalid_workflow_input", state
    assert len(calls) == 1 and not state["evidence"]
