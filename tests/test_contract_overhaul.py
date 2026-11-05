"""Cross-component contract overhaul behaviour where no existing suite is the natural home (overhaul spec 18):
synthesis (C22-C24), domain starters (C25), historical restore and local copies (C16), retained-generation
deletion and registry races (C15, C18) and the packaged tutorial's headless preparation (C30). Model calls use the
deterministic loopback stand-in; races use hooks and events, never sleeps."""
import json
import threading

import pytest

from agentwiz import checks, contract_examples, contract_schema, jsonio, workshop_service
from conftest import (answer, contract_detail, contract_files, contract_item, failure_of, request, save_contract,
                      save_profile, save_roles, switch_history, wait_until, write_block)

GOOD = {"description": "A triage label", "schema": {"type": ["string", "null"],
                                                    "enum": ["routine", "urgent", None]},
        "notes": ["Null means the message could not be classified."]}
# Wrapper-valid, but the nested schema uses an unsupported keyword and a missing `items`.
UNSUPPORTED = {"description": "A list", "schema": {"type": "array", "pattern": "x"}, "notes": []}


def v2(ui, route, **body):
    return request(ui, "/api/contracts/" + route, {"format_version": 2, **body})


def synthesis_body(**over):
    return {"task_description": "Classify a support message into a queue.", "output_name": "priority",
            "definition_text": None, "role": "write_contract", **over}


def setup_role(ui, server):
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    request(ui, "/api/roles/save", {"roles": {"write_contract": "p"}, "revision": request(ui, "/api/state")["roles"]["revision"],
                                    "definitions": {"write_contract": {"description": "Create and revise reusable contracts",
                                                                       "location": "local", "family": "Workshop"}}})


def synthesize(ui, body):
    plan = v2(ui, "synthesis/plan", **body)
    ticket = request(ui, "/api/workshop/tickets", {"format_version": 1})["ticket"]
    started = v2(ui, "synthesis/start", **body, expected_plan=plan["plan"]["expected_plan"], ticket=ticket,
                 confirmations=[{"kind": r["kind"], "digest": r["digest"]} for r in plan["plan"]["required_confirmations"]])
    assert wait_until(lambda: request(ui, "/api/workshop/batches/" + started["batch_id"])["state"] != "running")
    return plan, started, request(ui, "/api/workshop/batches/" + started["batch_id"])


def envelope(value):
    return answer(json.dumps({"proposal": value}))


# C22 ----------------------------------------------------------------------------------------------
def test_synthesis_valid_proposal_applies_as_a_reviewable_draft(ui, server):
    setup_role(ui, server)
    server.reply(envelope(GOOD))
    plan, _started, batch = synthesize(ui, synthesis_body())
    assert plan["destination"]["model"] == "m" and plan["source_draft_digest"] == workshop_service.source_digest(None)
    row = batch["rows"][0]
    assert row["outcome"] == "complete" and row["repairs"] == 0
    applied = v2(ui, "synthesis/apply", proposal_id=row["proposal_id"], definition_text=None)
    definition = jsonio.decode_exact(applied["definition_text"])
    assert definition == {"format": "json", "description": GOOD["description"], "schema": GOOD["schema"]}
    assert applied["notes"] == GOOD["notes"] and applied["projection"]["format"] == "json"
    assert v2(ui, "synthesis/apply", proposal_id=row["proposal_id"], definition_text=None) == applied   # idempotent
    # The prompt and data were sent; nothing was saved or attached.
    sent = server.requests[0]["body"]["messages"][-1]["content"]
    assert "<<<TASK DESCRIPTION" in sent and "Classify a support message" in sent
    assert not contract_files(ui).exists() and request(ui, "/api/blocks")["total"] == 0


def test_unsupported_nested_schema_is_repaired_once_then_refused(ui, server):
    setup_role(ui, server)
    server.reply(envelope(UNSUPPORTED), envelope(GOOD))
    _plan, _started, batch = synthesize(ui, synthesis_body())
    row = batch["rows"][0]
    assert row["outcome"] == "complete" and row["repairs"] == 1 and row.get("proposal_id")
    repair = server.requests[1]["body"]["messages"][-1]["content"]
    assert "outputs.proposal.schema" in repair and "pattern" in repair            # located subset errors
    server.reply(envelope(UNSUPPORTED), envelope(UNSUPPORTED))
    _plan, _started, batch = synthesize(ui, synthesis_body())
    row = batch["rows"][0]
    assert row["outcome"] != "complete" and row["repairs"] == 1 and "proposal_id" not in row
    assert len(server.requests) == 4                                               # one repair, never more


def test_synthesis_capture_records_the_server_validator_and_repairs_with_it():
    parsed = checks.parse_internal_contract({"format": "json", "schema": workshop_service._SYNTHESIS_WRAPPER},
                                            validator=workshop_service.SYNTHESIS_VALIDATOR)
    from conftest import pinned
    copy_ = pinned(None, None, parsed)
    restored = copy_.from_record(copy_.view())
    assert restored.parsed.validator == "contract_synthesis_v1"
    result, _ = checks.check_named_answer({"proposal": restored.parsed}, json.dumps({"proposal": UNSUPPORTED}))
    assert result["overall"] == "fail"
    with pytest.raises(checks.ContractError):
        checks.parse_internal_contract({"format": "json"}, validator="anything_else")
    from agentwiz.model_step import ContractCopy, SnapshotUnavailable
    with pytest.raises(SnapshotUnavailable):
        ContractCopy.from_record({**copy_.view(), "validator": "unknown_validator"})


def _definition_bytes(definition):
    return len(json.dumps(definition, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _sized_schema(target):
    """A supported subset schema (4 nodes, bounded choices) whose complete definition {format, schema} is
    exactly `target` UTF-8 bytes: the size limit, not the node rules, is what decides."""
    choices = [f"{i:04d}" + "x" * 2496 for i in range(200)]
    filler = ["c"]
    schema = {"type": "object", "properties": {"a": {"type": "string", "enum": choices},
                                               "b": {"type": "string", "enum": list(choices)},
                                               "c": {"type": "string", "enum": filler}},
              "required": [], "additionalProperties": False}
    while (gap := target - _definition_bytes({"format": "json", "schema": schema})) > 0:
        room = 4096 - len(filler[-1])
        if room > 0:
            filler[-1] += "y" * min(room, gap)
        else:
            filler.append(f"{len(filler):04d}")
    assert _definition_bytes({"format": "json", "schema": schema}) == target
    return schema


_LIMIT = contract_schema.DEFINITION_BYTES
OVERSIZED = {"description": "", "schema": _sized_schema(1_201_045), "notes": []}
NEAR = _sized_schema(_LIMIT - 100)
# 100 bytes under the limit without a description; the 4096-character description pushes it over.
DESCRIBED_OVER = {"description": "d" * 4096, "schema": NEAR, "notes": []}


@pytest.mark.parametrize("value", [OVERSIZED, DESCRIBED_OVER], ids=["schema", "description"])
def test_synthesis_acceptance_applies_the_complete_definition_limit(value):
    """R2: a wrapper-valid proposal whose nested schema passes the node rules but whose complete definition
    exceeds 1 MiB fails acceptance itself (located, value-redacted), exactly as application would refuse it."""
    contract_schema.validate_user_schema(value["schema"])          # the node rules alone accept it
    parsed = checks.parse_internal_contract({"format": "json", "schema": workshop_service._SYNTHESIS_WRAPPER},
                                            validator=workshop_service.SYNTHESIS_VALIDATOR)
    result, _values = checks.check_named_answer({"proposal": parsed}, json.dumps({"proposal": value}))
    assert result["overall"] == "fail"
    assert [(i["location"], "1 MiB" in i["message"]) for i in result["issues"]] == [("outputs.proposal", True)]
    assert "xxxx" not in json.dumps(result) and "dddd" not in json.dumps(result)
    with pytest.raises(contract_schema.DefinitionError):
        workshop_service.proposal_definition(value)
    under = {**value, "description": ""} if value is DESCRIBED_OVER else None
    if under is not None:
        accepted, _ = checks.check_named_answer({"proposal": parsed}, json.dumps({"proposal": under}))
        assert accepted["overall"] == "pass"
        assert workshop_service.proposal_definition(under) == {"format": "json", "schema": NEAR}


def test_oversized_proposal_is_repaired_once_then_refused(ui, server):
    """R2 through the runner: the oversized original uses the single repair; a valid repair applies and saves
    unchanged; a second oversized answer leaves no usable proposal. Save runs is off throughout."""
    setup_role(ui, server)
    server.reply(envelope(OVERSIZED), envelope(GOOD))
    _plan, _started, batch = synthesize(ui, synthesis_body())
    row = batch["rows"][0]
    assert row["outcome"] == "complete" and row["repairs"] == 1 and row.get("proposal_id")
    repair = server.requests[1]["body"]["messages"][-1]["content"]
    assert "outputs.proposal" in repair and "1 MiB" in repair
    applied = v2(ui, "synthesis/apply", proposal_id=row["proposal_id"], definition_text=None)
    assert jsonio.decode_exact(applied["definition_text"]) == {"format": "json", "description": GOOD["description"],
                                                               "schema": GOOD["schema"]}
    save_contract(ui, "triage", applied["definition_text"], proposal_id=row["proposal_id"])
    assert contract_detail(ui, "triage")["origin"] == "synthesized"
    server.reply(envelope(DESCRIBED_OVER), envelope(OVERSIZED))
    _plan, _started, batch = synthesize(ui, synthesis_body())
    row = batch["rows"][0]
    assert row["outcome"] != "complete" and row["repairs"] == 1 and "proposal_id" not in row
    assert len(server.requests) == 4                                               # one repair, never more
    assert not (ui.workspace.path / "runs").exists()
    assert not (ui.workspace.path / "workshop" / "comparisons").exists()


# C23 ----------------------------------------------------------------------------------------------
def test_synthesis_plan_binding_tickets_kinds_and_staleness(ui, server):
    setup_role(ui, server)
    body = synthesis_body()
    first = v2(ui, "synthesis/plan", **body)
    changed = v2(ui, "synthesis/plan", **synthesis_body(task_description="Something else"))
    assert changed["plan"]["expected_plan"] != first["plan"]["expected_plan"]
    ticket = request(ui, "/api/workshop/tickets", {"format_version": 1})["ticket"]
    refused = failure_of(lambda: v2(ui, "synthesis/start", **synthesis_body(task_description="Something else"),
                                    expected_plan=first["plan"]["expected_plan"], ticket=ticket, confirmations=[]))
    assert (refused.status, refused.body["code"]) == (409, "confirmation_required") and server.requests == []
    server.reply(envelope(GOOD))
    start = {**body, "expected_plan": first["plan"]["expected_plan"], "ticket": ticket, "confirmations": []}
    started = v2(ui, "synthesis/start", **start)
    assert v2(ui, "synthesis/start", **start)["operation_id"] == started["operation_id"]   # exactly once
    assert wait_until(lambda: request(ui, "/api/workshop/batches/" + started["batch_id"])["state"] != "running")
    proposal = request(ui, "/api/workshop/batches/" + started["batch_id"])["rows"][0]["proposal_id"]
    assert len(server.requests) == 1
    # Stale source draft, wrong kinds and invalid bodies.
    draft = '{"format": "json", "schema": {"type": "string"}}'
    stale = failure_of(lambda: v2(ui, "synthesis/apply", proposal_id=proposal, definition_text=draft))
    assert (stale.status, stale.body["code"]) == (409, "proposal_stale")
    block_apply = failure_of(lambda: request(ui, "/api/workshop/plans", {"format_version": 1, "action": "proposals/apply",
                                                                        "request": {"proposal_id": proposal, "draft": None}}))
    assert block_apply.status == 400
    for bad in (synthesis_body(role="assistant"), synthesis_body(output_name="1bad"),
                synthesis_body(task_description="   "), synthesis_body(definition_text='{"format": "text"}'),
                {**synthesis_body(), "policy": "internal"}):
        assert failure_of(lambda: v2(ui, "synthesis/plan", **bad)).status == 400, bad
    expired = failure_of(lambda: v2(ui, "synthesis/apply", proposal_id="0" * 12, definition_text=None))
    assert expired.status == 410


def test_synthesis_stop_yields_no_proposal(ui, server):
    from conftest import DONE, Reply, delta
    setup_role(ui, server)
    server.reply(Reply(body=[delta('{"proposal":')], headers={"Content-Type": "text/event-stream"}, hold=True,
                       after_release=[DONE]))
    body = synthesis_body()
    plan = v2(ui, "synthesis/plan", **body)
    ticket = request(ui, "/api/workshop/tickets", {"format_version": 1})["ticket"]
    started = v2(ui, "synthesis/start", **body, expected_plan=plan["plan"]["expected_plan"], ticket=ticket,
                 confirmations=[])
    assert server.reached.wait(10)
    request(ui, "/api/workshop/batches/" + started["batch_id"] + "/stop", {"format_version": 1})
    server.release.set()
    assert wait_until(lambda: request(ui, "/api/workshop/batches/" + started["batch_id"])["state"] != "running")
    row = request(ui, "/api/workshop/batches/" + started["batch_id"])["rows"][0]
    assert row["outcome"] == "stopped" and "proposal_id" not in row and len(server.requests) == 1


def test_synthesis_without_an_assigned_role_is_a_setup_failure(ui, server):
    failure = failure_of(lambda: v2(ui, "synthesis/plan", **synthesis_body()))
    assert (failure.status, failure.body["code"]) == (409, "role_unresolved") and server.requests == []


# C24 ----------------------------------------------------------------------------------------------
def test_saved_provenance_is_minimal_and_derived_edits_are_distinguished(ui, server):
    setup_role(ui, server)
    server.reply(envelope(GOOD), envelope(GOOD))
    _plan, _started, batch = synthesize(ui, synthesis_body())
    proposal = batch["rows"][0]["proposal_id"]
    applied = v2(ui, "synthesis/apply", proposal_id=proposal, definition_text=None)
    saved = save_contract(ui, "triage", applied["definition_text"], proposal_id=proposal)
    detail = contract_detail(ui, "triage")
    assert detail["origin"] == "synthesized"
    assert set(detail["basis"]) <= {"prompt_id", "prompt_version", "role", "provider_kind", "model", "provider", "note"}
    assert detail["basis"]["prompt_id"] == "write_contract" and detail["basis"]["role"] == "write_contract"
    assert "Classify" not in json.dumps(detail["basis"]) and "Null means" not in json.dumps(detail)
    edited = jsonio.decode_exact(applied["definition_text"])
    edited["description"] = "Edited by the author"
    second = save_contract(ui, "triage", jsonio.encode_exact(edited), saved["file_token"], proposal_id=proposal)
    assert contract_detail(ui, "triage", second["revision"])["origin"] == "derived_from_synthesis"
    manual = save_contract(ui, "triage", '{"format": "text"}', second["file_token"])
    assert contract_detail(ui, "triage", manual["revision"])["origin"] == "manual"
    assert contract_detail(ui, "triage", manual["revision"])["basis"] is None
    # Save runs is off: no transcript, batch or proposal reached the disk.
    assert not (ui.workspace.path / "runs").exists()
    assert not (ui.workspace.path / "workshop" / "comparisons").exists()


def test_save_runs_on_uses_ordinary_redacted_history(ui, server):
    setup_role(ui, server)
    switch_history(ui, True)
    server.reply(envelope(GOOD))
    _plan, _started, batch = synthesize(ui, synthesis_body())
    run_id = batch["rows"][0]["run_id"]
    saved = request(ui, "/api/runs/" + run_id)
    assert saved["result"]["outcome"] == "complete"
    assert not any("contract" in p.name for p in contract_files(ui).parent.glob("contracts/*"))


# C25 ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("identifier", list(contract_examples.STARTERS))
def test_domain_starters_check_structure_not_meaning(identifier):
    definition = contract_examples.starter(identifier)
    contract_schema.validate_user_definition(definition)
    contract = checks.parse_contract(definition)
    examples = contract_examples.examples(identifier)
    for value in examples["valid"]:
        assert checks.check_value(contract, value)["overall"] == "pass", value
    for value in examples["invalid"]:
        assert checks.check_value(contract, value)["overall"] == "fail", value
    for value in examples["semantic_wrong"]:
        assert checks.check_value(contract, value)["overall"] == "pass", value       # structure passes...
    if identifier == "identifier_occurrences":                                       # ...a span check rejects it
        source = examples["source"]
        assert all(source[o["start"]:o["end"]] == o["value"] for o in examples["valid"][0])
        assert not all(source[o["start"]:o["end"]] == o["value"] for o in examples["semantic_wrong"][0])
    if identifier == "invoice":                                                      # ...arithmetic rejects it
        wrong = examples["semantic_wrong"][0]
        assert sum(line["amount"] for line in wrong["lines"]) != wrong["total"]
    if identifier == "clinical_observations":                                        # ...an exact expectation does
        expected = examples["valid"][0][1]
        assert not checks.exact_equal(examples["semantic_wrong"][0][0]["value"], expected["value"])
    assert contract_examples.starter(identifier) is not contract_examples.starter(identifier)   # fresh copies


# C16 ----------------------------------------------------------------------------------------------
def _send(ui, route, **body):
    return request(ui, "/api/workshop/" + route, {"format_version": 1, **body})


def _plan_save(ui, draft, expected=None):
    body = {"draft": draft, "expected_identity": expected}
    plan = _send(ui, "plans", action="save", request=body)
    return _send(ui, "save", **body, expected_plan=plan["expected_plan"],
                 confirmations=[{"kind": r["kind"], "digest": r["digest"]} for r in plan["required_confirmations"]])


def _pinned_draft(ui, key, pin):
    draft = _send(ui, "preview", starter="format_report", key=key)["draft"]
    definition = jsonio.decode_exact(draft["definition_text"])
    definition["outputs"]["out"]["contract"] = pin
    draft["definition_text"] = jsonio.encode_exact(definition)
    return draft


def test_historical_restore_keeps_references_and_local_copy_is_explicit(ui):
    saved = save_contract(ui, "report", '{"format": "text", "min_length": 1}')
    pin = {"name": "report", "revision": 1, "digest": saved["digest"]}
    first = _plan_save(ui, _pinned_draft(ui, "reporter", pin))
    revisions = request(ui, "/api/workshop/blocks/reporter/revisions")["revisions"]
    metadata = revisions[0]["metadata"]
    assert metadata["contract_pins"] == {"inputs": {}, "outputs": {"out": pin}}
    # The live file is later damaged; the historical preview still reads its captured definition.
    path = contract_files(ui) / "report.json"
    good = path.read_bytes()
    path.write_bytes(b"{ damaged")
    restored = _send(ui, "preview", key="reporter", revision=revisions[0]["id"])
    definition = jsonio.decode_exact(restored["draft"]["definition_text"])
    assert definition["outputs"]["out"]["contract"] == pin                 # the reference is preserved, not inlined
    assert "restore_contracts" not in restored["draft"]["metadata"]
    refused = failure_of(lambda: _plan_save(ui, restored["draft"], first["base_digest"]))
    assert refused.status == 409                                           # publication resolves against live files
    local = _send(ui, "preview", draft=restored["draft"], edit={"kind": "make_local_copy", "side": "outputs",
                                                                 "port": "out"})
    copied = jsonio.decode_exact(local["draft"]["definition_text"])["outputs"]["out"]["contract"]
    assert copied == {"format": "text", "min_length": 1} and local["valid"]
    saved_copy = _plan_save(ui, local["draft"], first["base_digest"])
    assert request(ui, "/api/workshop/blocks/reporter/revisions")["revisions"][0]["metadata"]["contract_pins"] == \
        {"inputs": {}, "outputs": {}}
    path.write_bytes(good)
    assert saved_copy["revision_number"] == 2


def test_tampered_generation_contract_maps_are_refused(ui):
    saved = save_contract(ui, "report", '{"format": "text"}')
    _plan_save(ui, _pinned_draft(ui, "reporter", {"name": "report", "revision": 1, "digest": saved["digest"]}))
    revisions = request(ui, "/api/workshop/blocks/reporter/revisions")["revisions"]
    folder = ui.workspace.path / "blocks" / "reporter" / ".workshop" / "revisions" / revisions[0]["id"]
    metadata = jsonio.decode_exact((folder / "workshop.json").read_text())
    metadata["contracts"]["outputs"]["out"] = '{"format": "text", "min_length": 9}'
    (folder / "workshop.json").write_bytes(jsonio.encode_file_exact(metadata))
    assert request(ui, "/api/blocks/reporter")["problem"] is not None
    assert failure_of(lambda: _send(ui, "preview", key="reporter", revision=revisions[0]["id"])).status == 409


# C15, C18 -----------------------------------------------------------------------------------------
def test_retained_generations_block_deletion_and_publication_races_are_refused(ui, monkeypatch):
    saved = save_contract(ui, "shared", '{"format": "text"}')
    pin = {"name": "shared", "revision": 1, "digest": saved["digest"]}
    first = _plan_save(ui, _pinned_draft(ui, "pinned", pin))
    # A newer block generation drops the reference; the retained older one still blocks deletion.
    draft = _send(ui, "preview", key="pinned")["draft"]
    definition = jsonio.decode_exact(draft["definition_text"])
    definition["outputs"]["out"]["contract"] = {"format": "text"}
    draft["definition_text"] = jsonio.encode_exact(definition)
    _plan_save(ui, draft, first["base_digest"])
    token = contract_detail(ui, "shared")["file_token"]
    used = contract_detail(ui, "shared")["used_by"]["items"]
    assert [(u["block"], u["current"], u["port"], u["revision"]) for u in used] == [("pinned", False, "out", 1)]
    refused = failure_of(lambda: v2(ui, "delete", name="shared", expected_file_token=token))
    assert (refused.status, refused.body["code"]) == (409, "contract_in_use")
    assert refused.body["blockers"][0]["generation"] and refused.body["blockers"][0]["current"] is False
    # Orphaned generations outside the retained list are not inspected.
    orphan = ui.workspace.path / "blocks" / "pinned" / ".workshop" / "revisions" / ("f" * 32)
    orphan.mkdir()
    (orphan / "block.json").write_text(json.dumps({"outputs": {"o": {"contract": pin}}}))
    assert len(contract_detail(ui, "shared")["used_by"]["items"]) == 1

    # A contract deletion holding the registry reservation refuses a concurrent publication, and vice versa.
    from agentwiz import contracts as contracts_module
    other = save_contract(ui, "other", '{"format": "text"}')
    entered, release = threading.Event(), threading.Event()

    def hold(*args):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(contracts_module, "before_contract_delete", hold)
    results = {}
    worker = threading.Thread(target=lambda: results.setdefault("delete", v2(
        ui, "delete", name="other", expected_file_token=other["file_token"])))
    worker.start()
    assert entered.wait(10)
    blocked = failure_of(lambda: _plan_save(ui, _pinned_draft(ui, "racer", {"name": "other", "revision": 1,
                                                                             "digest": other["digest"]})))
    assert (blocked.status, blocked.body["code"]) == (409, "busy")
    release.set()
    worker.join(10)
    assert results["delete"]["deleted"] is True
    assert not (ui.workspace.path / "blocks" / "racer").exists()


# C30 (headless) -----------------------------------------------------------------------------------
def test_packaged_tutorial_assets_use_the_new_formats(ui):
    from agentwiz import tutorial, tutorial_topics
    request(ui, "/api/tutorial/start", {})
    child = ui.tutorial.child
    try:
        item = contract_item(child, tutorial_topics.CONTRACT_NAME)
        assert (item["family"], item["latest"], item["problem"]) == ("Tutorial", 1, None)
        assert item["latest_digest"] == contract_schema.contract_digest(jsonio.decode_exact(tutorial_topics.CONTRACT))
        rows = {r["name"]: r for r in request(child, "/api/blocks")["blocks"]}
        assert set(rows) >= set(tutorial_topics.BLOCKS)
        assert all(rows[name]["problem"] is None for name in tutorial_topics.BLOCKS), rows
        tutorial.ensure_contract(child.state)                                   # idempotent
        assert contract_item(child, "fields")["latest"] == 1
        fields = request(child, "/api/blocks/extract_fields")["contracts"]["outputs"]["fields"]
        assert (fields["name"], fields["revision"]) == ("fields", 1)
    finally:
        ui.tutorial.close()
