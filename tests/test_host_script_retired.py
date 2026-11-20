"""H-1 to H-4: model-generated host scripts stay refused while their detection scaffolding goes (spec v15, 6.3).

Every script here writes a canary file if it is ever executed; each case asserts the canary is absent and that
the model fake received no request beyond the assistance calls H-3a expects.
"""
import json
import os

from agentwiz import jsonio
from conftest import answer, configure, failure_of, get_state, request, save_profile, save_roles, write_block
from test_workflows import port
from agentwiz.workshop_service import digest
from test_workshop import completed, plan_save, send, start_planned


def canary_source(path):
    return f"open({str(path)!r}, 'w').write('ran')\n"


def script_block(name):
    return {"format_version": 3, "name": name, "inputs": {"text": {"contract": {"format": "text"}}},
            "outputs": {"x": {"contract": {"format": "text"}}},
            "steps": [{"id": "s", "kind": "script", "file": "scripts/go.py"}]}


def test_h1_unmanaged_v3_script_block_is_refused_everywhere(ui, server, tmp_path):
    canary = tmp_path / "canary"
    configure(ui, server)
    write_block(ui, "old", script_block("old"), {"scripts/go.py": canary_source(canary)})
    rows = {r["name"]: r for r in request(ui, "/api/blocks")["blocks"]}
    problem = rows["old"]["problem"]
    assert problem["code"] == "unusable"
    assert problem["issues"][0]["location"] == "steps[0].kind"
    assert problem["issues"][0]["message"].startswith("host_script_retired:")
    refusal = failure_of(lambda: request(ui, "/api/blocks/run", {"name": "old", "revision": rows["old"]["revision"],
                                                                "inputs": {"text": "a"}}))
    assert refusal.status == 409 and refusal.body["code"] == "block_unusable"
    inventory = request(ui, f"/api/agent/inventory?revision={get_state(ui)['revision']}")
    assert not [c for c in inventory["capabilities"] if c["kind"] == "block"]
    definition = {"name": "wf", "purpose": "p", "inputs": {"text": port()}, "constants": {},
                  "outputs": {"result": {**port(), "binding": {"kind": "node", "node": "old", "port": "x"}}},
                  "nodes": {"old": {"bindings": {"text": {"kind": "input", "port": "text"}}}}}
    body = {"name": "wf", "file_token": None, "definition_text": jsonio.encode_exact(definition),
            "selections": {"old": {"key": "old", "fingerprint": "0" * 64}}}
    refusal = failure_of(lambda: send(ui, "plans", action="workflow_save", request=body))
    assert refusal.status == 409 and refusal.body["code"] == "unusable"
    assert server.requests == [] and not canary.exists()


def test_h2_imported_script_draft_is_invalid_and_keeps_provenance(ui, server, tmp_path):
    canary = tmp_path / "canary"
    configure(ui, server)
    metadata = {"origin": "llm_generated", "script_origin": "derived_from_llm"}
    draft = {"key": "gen", "definition_text": jsonio.encode_exact(script_block("gen")),
             "files": {"scripts/go.py": canary_source(canary)}, "metadata": dict(metadata)}
    imported = send(ui, "drafts/import", draft=draft)
    assert imported["valid"] is False
    assert [i["location"] for i in imported["issues"]] == ["steps[0].kind"]
    assert imported["issues"][0]["message"].startswith("host_script_retired:")
    assert {k: imported["draft"]["metadata"].get(k) for k in metadata} == metadata
    d = imported["draft"]
    example = {"id": "e", "version": 1, "inputs": {"text": "a"}, "expectation": {"kind": "review", "criterion": ""},
               "verified": False}
    for action, body in (("save", {"draft": d, "expected_identity": None}),
                         ("test", {"draft": d, "inputs": {"text": "a"}}),
                         ("batches", {"candidates": [{"draft": d}, {"draft": d}], "examples": [example]})):
        refusal = failure_of(lambda: send(ui, "plans", action=action, request=body))
        assert refusal.status == 409 and refusal.body["code"] == "block_invalid", (action, refusal.body)
        assert "generated_source" not in json.dumps(refusal.body)
    assert not (ui.workspace.path / "blocks" / "gen").exists()
    assert server.requests == [] and not canary.exists()


def assistant(ui, server):
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"assistant": "p"})
    return send(ui, "preview", starter="count_text", starter_version=2, key="draft_only")["draft"]


def script_behaviour(canary):
    return {"kind": "script", "file": "scripts/go.py", "source": canary_source(canary)}


def proposal_for(action, canary):
    behaviour = script_behaviour(canary)
    if action == "improve":
        payload = {"behaviour": behaviour}
    else:
        payload = {"observations": [], "hypotheses": [], "corrections": {"behaviour": behaviour}}
    return {"format_version": 1, "action": action, "payload": payload, "assumptions": [], "unmet_requirements": []}


def test_h3a_script_shaped_assist_reply_is_refused_by_schema(ui, server, tmp_path):
    canary = tmp_path / "canary"
    draft = assistant(ui, server)
    before = draft["definition_text"]
    for action in ("explain_failure", "improve"):
        server.requests.clear()
        reply = answer(json.dumps({"proposal": proposal_for(action, canary)}))
        server.reply(reply, reply)
        body = {"action": action, "role": "assistant", "draft": draft, "task": "Fix this.", "evidence": {}}
        if action == "improve":
            body = {**body, "sections": ["behaviour"]}
            body.pop("evidence")
        batch = completed(ui, start_planned(ui, "assist", body)["batch_id"])
        row = batch["rows"][0]
        assert len(server.requests) == 2, action                    # the first response and its one repair
        assert row["outcome"] != "complete", row
        assert row.get("proposal_id") is None
        assert send(ui, "preview", draft=draft)["draft"]["definition_text"] == before
    assert request(ui, "/api/blocks")["total"] == 0 and not canary.exists()


def test_h3b_seeded_script_proposal_is_refused_at_apply(ui, server, tmp_path):
    """A legacy proposal with a script behaviour, seeded into the proposal store over a real proposal's
    response; Apply does not revalidate the response schema, so only the guard refuses it."""
    canary = tmp_path / "canary"
    draft = assistant(ui, server)
    service = ui.state.workshop
    for action in ("explain_failure", "improve"):
        response = {"format_version": 1, "action": "improve", "payload": {"purpose": "Reviewed purpose"},
                    "assumptions": [], "unmet_requirements": []}
        server.reply(answer(json.dumps({"proposal": response})))
        batch = completed(ui, start_planned(ui, "assist", {"action": "improve", "role": "assistant", "draft": draft,
                                                           "task": "Improve.", "sections": ["purpose"]})["batch_id"])
        ident = batch["rows"][0]["proposal_id"]
        with service.lock:
            seeded = service.proposals[ident]
            assert seeded["base_digest"] == digest(draft)
            seeded["request"] = {**seeded["request"], "action": action}
            seeded["response"] = proposal_for(action, canary)
        body = {"proposal_id": ident, "draft": draft}
        # The Apply plan already runs the guard; Apply itself refuses the same way.
        refusal = failure_of(lambda: send(ui, "plans", action="proposals/apply", request=body))
        assert refusal.status == 409 and refusal.body["code"] == "generated_script_disabled"
        refusal = failure_of(lambda: send(ui, "proposals/apply", **body, expected_plan="0" * 64, confirmations=[]))
        assert refusal.status == 409 and refusal.body["code"] == "generated_script_disabled"
    assert send(ui, "preview", draft=draft)["draft"]["definition_text"] == draft["definition_text"]
    assert request(ui, "/api/blocks")["total"] == 0
    assert len(server.requests) == 2 and not canary.exists()           # the two real proposals only


def test_h4_inherited_protection_cannot_be_cleared_in_a_draft(ui):
    draft = send(ui, "preview", starter="count_text", starter_version=2, key="guarded")["draft"]
    definition = jsonio.decode_exact(draft["definition_text"])
    definition["context_local_only"] = True
    draft["definition_text"] = jsonio.encode_exact(definition)
    plan_save(ui, draft)
    saved = send(ui, "preview", key="guarded")["draft"]
    assert saved["metadata"]["protection_origin"] == "manual"
    # Inherited protection: a block whose saved definition sets context_local_only without a manual origin.
    write_block(ui, "inherit", {**definition, "name": "inherit"})
    opened = send(ui, "preview", key="inherit")["draft"]
    assert opened["metadata"]["protection_origin"] == "inherited"
    cleared = jsonio.decode_exact(opened["definition_text"])
    cleared["context_local_only"] = False
    opened["definition_text"] = jsonio.encode_exact(cleared)
    refusal = failure_of(lambda: send(ui, "preview", draft=opened))
    assert refusal.status == 409 and refusal.body["code"] == "protection_inherited"
    # Manual protection clears only through the reviewed new-revision path.
    manual = jsonio.decode_exact(saved["definition_text"])
    manual["context_local_only"] = False
    saved["definition_text"] = jsonio.encode_exact(manual)
    pending = send(ui, "preview", draft=saved)["draft"]
    assert pending["metadata"].get("protection_clear_pending") is True
    plan = send(ui, "plans", action="save", request={"draft": pending, "expected_identity": pending["base_digest"]})
    assert "clear_design_protection" in {r["kind"] for r in plan["required_confirmations"]}
