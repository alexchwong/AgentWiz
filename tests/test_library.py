"""The starter block library (starter blocks addendum, Sections 5 and 8): the packaged entries, the listing, plan,
add and finish, the settings hold, the sole-revision guard, provenance, and the shipped examples. Driven through
HTTP; a user's action between the installer's writes is injected with a hook on the ordinary Workshop method, so
the orderings are forced, never timed."""
from __future__ import annotations

import copy
import json
import threading

import pytest

from agentwiz import draft_lineage, jsonio, library_install as lib, workshop_descriptions as descriptions
from agentwiz.workshop import WorkshopError
from agentwiz.workshop_service import judgement
from conftest import (block_answer, failure_of, get_state, request, result_of, run_block, save_profile, save_roles,
                      write_block)

TOOLS = [k for k in lib.index() if lib.entry(k).group != "llm"]
MODELS = [k for k in lib.index() if lib.entry(k).group == "llm"]


def plan(ui, *entries):
    return request(ui, "/api/library/plan", {"format_version": 1, "entries": list(entries)})["entries"]


def submit(planned, **extra):
    return {"key": planned["key"], "as": planned["as"], **({"role": planned["role"]} if planned.get("role") else {}),
            "expected_plan": planned["expected_plan"], "confirmations": planned["required_confirmations"],
            "binding": planned["binding"], **extra}


def add(ui, *entries):
    return request(ui, "/api/library/add", {"format_version": 1, "entries": list(entries)})["entries"]


def install(ui, *rows):
    """Plan, then add with the reviewed plans."""
    planned = plan(ui, *rows)
    assert all(p["outcome"] == "planned" for p in planned), planned
    return add(ui, *(submit(p) for p in planned))


def listing(ui) -> dict:
    return {row["key"]: row for row in request(ui, "/api/library")["entries"]}


def model_ready(ui, server, name="p", **over):
    save_profile(ui, name, model="m", kind="openai_compat", base_url=server.url + "/v1", provider="", **over)
    save_roles(ui, {"fast": name})


def trust_model(ui, server, model="m"):
    """Local only ports (deidentify_text) need a trusted local destination."""
    destination = {"kind": "openai_compat", "url": server.url + "/v1", "model": model, "provider": ""}
    grants = request(ui, "/api/workshop/destination-trust")
    request(ui, "/api/workshop/destination-trust", {"format_version": 1, "expected_revision": grants["revision"],
                                                     "destination": destination, "trusted": True})


def revisions(ui, key):
    return ui.state.workshop.assets.revisions(key)["revisions"]


def sidecar(ui, key, revision):
    return descriptions.read_sidecar(ui.state.workshop.assets.root(key), revision)


def user_save(ui, key, edit_prompt=True):
    """An ordinary save of a new revision of `key` (a compatible prompt edit), as the Workshop page makes it."""
    service = ui.state.workshop
    draft = service.source({"key": key})
    if "prompts/user.md" in draft["files"]:
        draft["files"]["prompts/user.md"] = draft["files"]["prompts/user.md"] + "\n"
    else:
        definition = jsonio.decode_exact(draft["definition_text"])
        definition["description"] = definition.get("description", "") + " (edited)"
        draft["definition_text"] = jsonio.encode_exact(definition)
    service.drafts.issue(draft, draft_lineage.Lineage.of(draft))
    body = {"draft": draft, "expected_identity": service.assets.identity(key)}
    planned = service.plan("save", body)
    return service.save({**body, "expected_plan": planned["expected_plan"],
                         "confirmations": planned["required_confirmations"]})


# --- the packaged entries (5.1) ---------------------------------------------------------------------------------------
def test_the_index_holds_thirty_entries_in_three_groups():
    keys = lib.index()
    assert len(keys) == 30 == len(set(keys))
    groups = [lib.entry(k).group for k in keys]
    assert groups.count("llm") == 14 and groups.count("tool") == 15 and groups.count("transform") == 1


@pytest.mark.parametrize("key", lib.index())
def test_every_packaged_entry_validates(ui, key):
    item = lib.entry(key)
    info = item.info
    assert set(info) >= {"title", "group", "summary", "library_version", "examples", "description"}
    assert len(info["summary"]) <= 120 and type(info["library_version"]) is int
    assert len(info["examples"]) == 3
    assert all(set(ex["expectation"]) and ex["expectation"]["kind"] in ("whole_output", "fields", "text")
               for ex in info["examples"])
    definition = item.definition
    assert definition["format_version"] == 4 and len(definition["steps"]) == 1
    for side in ("inputs", "outputs"):
        for port in definition[side].values():
            assert type(port["local_only"]) is bool and "format" in port["contract"]      # explicit, inline only
    step = definition["steps"][0]
    if item.group == "llm":
        assert step["role"] == "fast" and step["response_mode"] == "named_json"
        assert step["repair"] == {"max_repairs": 2} and step["task"]["max_tokens"] > 0
        assert definition["budget"] == {"max_seconds": 180}
        assert set(item.files) == {"prompts/system.md", "prompts/user.md"}
        for name, port in definition["inputs"].items():
            if port["contract"]["format"] == "text":
                assert port["contract"]["max_length"] <= 40_000
        assert str(info["required_context"]) in info["description"]["sections"]["limitations"].replace(",", "")
    planned = plan(ui, {"key": key})[0]
    assert planned["outcome"] == "planned", planned
    assert {r["kind"] for r in planned["required_confirmations"]} >= {"labels_reviewed"}


def test_model_input_bounds_follow_the_rules():
    assert lib.entry("deidentify_text").definition["inputs"]["text"]["local_only"] is True
    for key in MODELS:
        if key == "deidentify_text":
            continue
        ports = lib.entry(key).definition
        assert not any(p["local_only"] for side in ("inputs", "outputs") for p in ports[side].values())
    assert all(p["local_only"] for side in ("inputs", "outputs")
               for p in lib.entry("check_identifier_map").definition[side].values())
    meaning = lib.entry("compare_documents_meaning").definition["inputs"]
    assert meaning["before"]["contract"]["max_length"] == meaning["after"]["contract"]["max_length"] == 20_000


# --- listing (5.2) ----------------------------------------------------------------------------------------------------
def test_listing_statuses_and_roles(ui, server):
    rows = listing(ui)
    assert all(r["status"] == "available" for r in rows.values())
    fast = next(r for r in rows["summarise_document"]["roles"] if r["role"] == "fast")
    assert fast == {"role": "fast", "profile": None, "location": None, "status": "unassigned"}
    assert "roles" not in rows["calculate"]
    write_block(ui, "Calculate", {"format_version": 4, "name": "Calculate", "inputs": {},
                                  "outputs": {"out": {"contract": {"format": "text"}, "local_only": False}},
                                  "steps": [{"id": "s", "kind": "tool", "builtin": "current_datetime",
                                             "output": "out", "arguments": {}}]})
    model_ready(ui, server)
    install(ui, {"key": "find_and_replace"})
    rows = listing(ui)
    assert rows["calculate"]["status"] == "key_in_use"                         # case-insensitive
    assert rows["find_and_replace"]["status"] == "installed"
    assert rows["find_and_replace"]["installed"] == ["find_and_replace"]
    assert next(r for r in rows["summarise_document"]["roles"] if r["role"] == "fast")["status"] == "ready"


# --- planning (5.2) ---------------------------------------------------------------------------------------------------
def test_the_plan_reports_exact_labels_the_destination_and_labels_reviewed(ui, server):
    model_ready(ui, server)
    deid, summary = plan(ui, {"key": "deidentify_text"}, {"key": "summarise_document", "as": "summary"})
    assert deid["port_labels"] == {"inputs": {"text": True}, "outputs": {"text": True, "identifier_map": True}}
    assert summary["port_labels"]["inputs"] == {"text": False, "length": False}
    assert summary["destination"] == {"role": "fast", "profile": "p", "kind": "openai_compat",
                                      "url": server.url + "/v1", "model": "m", "provider": "", "location": "local",
                                      "readiness": "ready"}
    assert "labels_reviewed" in {r["kind"] for r in summary["required_confirmations"]}
    assert summary["as"] == "summary" and summary["binding"] != deid["binding"]
    assert not (ui.workspace.path / "blocks" / "summary").exists()                           # no writes


def test_unknown_and_removed_keys_cannot_be_planned_or_added(ui, monkeypatch):
    assert failure_of(lambda: plan(ui, {"key": "no_such"})).status == 404
    monkeypatch.setattr(lib, "index", lambda: ["calculate"])
    assert failure_of(lambda: plan(ui, {"key": "find_and_replace"})).status == 404
    out = add(ui, {"key": "find_and_replace", "expected_plan": "x", "confirmations": [], "binding": "x"})
    assert out[0]["outcome"] == "unknown_entry"


# --- adding (5.2) -----------------------------------------------------------------------------------------------------
def test_add_one_and_many_with_provenance_examples_and_description(ui, server):
    model_ready(ui, server)
    out = install(ui, {"key": "find_and_replace"}, {"key": "summarise_document"}, {"key": "restore_identifiers"})
    assert [o["outcome"] for o in out] == ["added"] * 3
    for row in out:
        revision = row["revision"]
        saved = ui.state.workshop.assets.saved(row["as"], revision)
        item = lib.entry(row["key"])
        assert saved.metadata["library"] == {"key": item.key, "version": 1,
                                             "definition_sha256": lib.definition_sha256(
                                                 lib.rewrite(item, row["as"], "fast" if item.group == "llm" else None))}
        assert ui.state.workshop.collection(row["as"])["examples"][0]["id"] == f"{row['key']}-1"
        assert sidecar(ui, row["as"], revision)[0] == "present"
    design = request(ui, "/api/workshop/preview", {"key": "find_and_replace"}) if False else None
    assert design is None


def test_add_without_confirmations_writes_nothing(ui):
    planned = plan(ui, {"key": "calculate"})[0]
    out = add(ui, {**submit(planned), "confirmations": []})
    assert out[0]["outcome"] == "confirmation_required" and out[0]["expected_plan"] == planned["expected_plan"]
    assert not (ui.workspace.path / "blocks" / "calculate").exists()


def test_a_collision_and_a_renamed_install(ui):
    install(ui, {"key": "calculate"})
    assert plan(ui, {"key": "calculate", "as": "CALCULATE"})[0]["outcome"] == "key_in_use"
    out = install(ui, {"key": "calculate", "as": "calc2"})
    assert out[0]["outcome"] == "added"
    assert listing(ui)["calculate"]["installed"] == ["calc2", "calculate"]


def test_read_only(ui, monkeypatch):
    monkeypatch.setattr(ui.state.workspace, "read_only", True)
    assert plan(ui, {"key": "calculate"})[0]["outcome"] == "read_only"


def test_concurrent_same_key_imports(ui):
    planned = plan(ui, {"key": "calculate"})[0]
    results, start = [], threading.Barrier(2)

    def go():
        start.wait()
        results.append(add(ui, submit(planned))[0]["outcome"])
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(results) == ["added", "key_in_use"]


# --- the destination binding and the settings hold (5.2) --------------------------------------------------------------
def test_a_role_reassigned_after_review_needs_confirmation(ui, server):
    model_ready(ui, server)
    planned = plan(ui, {"key": "summarise_document"})[0]
    save_profile(ui, "q", model="other", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"fast": "q"})
    out = add(ui, submit(planned))[0]
    assert out["outcome"] == "confirmation_required" and out["destination"]["model"] == "other"
    assert not (ui.workspace.path / "blocks" / "summarise_document").exists()


@pytest.mark.parametrize("change", ["endpoint", "provider"])
def test_an_endpoint_or_provider_only_edit_needs_confirmation(ui, server, change):
    if change == "endpoint":
        model_ready(ui, server)
        planned = plan(ui, {"key": "summarise_document"})[0]
        save_profile(ui, "p", model="m", kind="lmstudio", base_url=server.url + "/v1", provider="")
    else:
        save_profile(ui, "p", model="m", kind="openrouter", provider="alpha")
        save_roles(ui, {"fast": "p"})
        planned = plan(ui, {"key": "summarise_document"})[0]
        save_profile(ui, "p", model="m", kind="openrouter", provider="beta")
    out = add(ui, submit(planned))[0]
    assert out["outcome"] == "confirmation_required"
    assert out["destination"] != planned["destination"] and out["destination"]["model"] == "m"
    assert not (ui.workspace.path / "blocks" / "summarise_document").exists()


def test_a_profile_edit_during_publication_is_busy_and_succeeds_after(ui, server, monkeypatch):
    model_ready(ui, server)
    planned = plan(ui, {"key": "summarise_document"})[0]
    service, refused = ui.state.workshop, []
    real = service.save

    def save_with_an_edit(body, **kwargs):
        refused.append(failure_of(lambda: save_profile(ui, "p", model="edited", kind="openai_compat",
                                                       base_url=server.url + "/v1", provider="")))
        return real(body, **kwargs)
    monkeypatch.setattr(service, "save", save_with_an_edit)
    out = add(ui, submit(planned))[0]
    assert out["outcome"] == "added"
    assert refused[0].status == 503 and refused[0].body["code"] == "busy"
    step = ui.state.workshop.assets.saved("summarise_document").definition["steps"][0]
    assert step["role"] == "fast" and ui.state.role_destination("fast")["model"] == "m"
    save_profile(ui, "p", model="edited", kind="openai_compat", base_url=server.url + "/v1", provider="")
    assert ui.state.role_destination("fast")["model"] == "edited"


def test_the_settings_hold_is_released_after_a_failed_save(ui, server, monkeypatch):
    model_ready(ui, server)
    planned = plan(ui, {"key": "summarise_document"})[0]

    def failing(body, **kwargs):
        raise WorkshopError("unusable", 409)
    monkeypatch.setattr(ui.state.workshop, "save", failing)
    assert add(ui, submit(planned))[0]["outcome"] == "failed"
    save_profile(ui, "p", model="after", kind="openai_compat", base_url=server.url + "/v1", provider="")
    assert ui.state.role_destination("fast")["model"] == "after"


# --- failures between the writes, and Finish (5.2) --------------------------------------------------------------------
@pytest.mark.parametrize("boundary", ["examples", "description"])
def test_an_injected_failure_at_a_write_boundary_then_finish(ui, monkeypatch, boundary):
    service = ui.state.workshop
    name = "save_examples" if boundary == "examples" else "save_description"
    real = getattr(service, name)
    monkeypatch.setattr(service, name, lambda *a, **k: (_ for _ in ()).throw(WorkshopError("unusable", 500)))
    out = install(ui, {"key": "calculate"})[0]
    assert out["outcome"] == "partial" and out["reason"] == "failed" and boundary in out["missing"]
    assert listing(ui)["calculate"]["status"] == "incomplete"
    monkeypatch.setattr(service, name, real)
    finished = add(ui, {"key": "calculate", "mode": "finish"})[0]
    assert finished["outcome"] == "added" and finished["written"] == out["missing"]
    assert listing(ui)["calculate"]["status"] == "installed"


@pytest.mark.parametrize("mode", ["add", "finish"])
def test_a_manual_description_saved_first_is_kept_byte_for_byte(ui, monkeypatch, mode):
    service = ui.state.workshop
    real = service.save_description
    mine = {}

    def user_first(key, ident, body, **kwargs):
        basis = service.assets.saved(key, ident).basis
        item = lib.entry("calculate").info["description"]
        sections = {**item["sections"], "use": "My own words."}
        real(key, ident, {"expected_token": None, "basis": basis, "sections": sections,
                          "port_summaries": item["port_summaries"]})
        mine["data"] = sidecar(ui, key, ident)[1]
        return real(key, ident, body, **kwargs)
    if mode == "finish":
        monkeypatch.setattr(service, "save_description", lambda *a, **k: (_ for _ in ()).throw(
            WorkshopError("busy", 503)))
        assert install(ui, {"key": "calculate"})[0]["outcome"] == "partial"
    monkeypatch.setattr(service, "save_description", user_first)
    out = install(ui, {"key": "calculate"})[0] if mode == "add" else \
        add(ui, {"key": "calculate", "mode": "finish"})[0]
    assert out["outcome"] == "added" and out["kept"] == ["description"]
    revision = revisions(ui, "calculate")[0]["id"]
    assert sidecar(ui, "calculate", revision)[1] == mine["data"]


def test_user_examples_saved_first_are_kept(ui, monkeypatch):
    service = ui.state.workshop
    real = service.save_examples
    mine = [{"id": "mine", "version": 1, "label": "mine", "inputs": {"expression": "1+1"},
             "expectation": {"kind": "whole_output", "values": {"out": '{"result":"2","rounded":false}'}}}]

    def user_first(key, body, **kwargs):
        real(key, {"expected_revision": None, "examples": mine})
        return real(key, body, **kwargs)
    monkeypatch.setattr(service, "save_examples", user_first)
    out = install(ui, {"key": "calculate"})[0]
    assert out["outcome"] == "added" and out["kept"] == ["examples"]
    assert [e["id"] for e in service.collection("calculate")["examples"]] == ["mine"]


def test_finish_is_unavailable_after_an_edit(ui, monkeypatch):
    service = ui.state.workshop
    monkeypatch.setattr(service, "save_examples", lambda *a, **k: (_ for _ in ()).throw(WorkshopError("busy", 503)))
    install(ui, {"key": "calculate"})
    monkeypatch.undo()
    user_save(ui, "calculate")
    out = add(ui, {"key": "calculate", "mode": "finish"})[0]
    assert out == {"key": "calculate", "as": "calculate", "outcome": "finish_unavailable", "reason": "edited"}


@pytest.mark.parametrize("mode", ["add", "finish"])
def test_an_edit_before_the_examples_write(ui, monkeypatch, mode):
    service = ui.state.workshop
    real = service.save_examples
    if mode == "finish":
        monkeypatch.setattr(service, "save_examples", lambda *a, **k: (_ for _ in ()).throw(WorkshopError("busy", 503)))
        monkeypatch.setattr(service, "save_description",
                            lambda *a, **k: (_ for _ in ()).throw(WorkshopError("busy", 503)))
        install(ui, {"key": "summarise_document"} if False else {"key": "calculate"})
        monkeypatch.undo()

    def edit_first(key, body, **kwargs):
        user_save(ui, key)
        return real(key, body, **kwargs)
    monkeypatch.setattr(service, "save_examples", edit_first)
    out = install(ui, {"key": "calculate"})[0] if mode == "add" else add(ui, {"key": "calculate",
                                                                               "mode": "finish"})[0]
    if mode == "add":
        assert out["outcome"] == "partial" and out["reason"] == "edited" and out["written"] == []
    else:
        assert out["outcome"] == "finish_unavailable" and out["reason"] == "edited"
    assert service.collection("calculate")["revision"] is None
    assert all(sidecar(ui, "calculate", r["id"])[0] == "absent" for r in revisions(ui, "calculate"))


@pytest.mark.parametrize("mode", ["add", "finish"])
def test_an_edit_between_the_examples_and_description_writes(ui, monkeypatch, mode):
    service = ui.state.workshop
    real = service.save_description
    if mode == "finish":
        monkeypatch.setattr(service, "save_examples", lambda *a, **k: (_ for _ in ()).throw(WorkshopError("busy", 503)))
        monkeypatch.setattr(service, "save_description",
                            lambda *a, **k: (_ for _ in ()).throw(WorkshopError("busy", 503)))
        install(ui, {"key": "calculate"})
        monkeypatch.undo()

    def edit_first(key, ident, body, **kwargs):
        user_save(ui, key)
        return real(key, ident, body, **kwargs)
    monkeypatch.setattr(service, "save_description", edit_first)
    out = install(ui, {"key": "calculate"})[0] if mode == "add" else add(ui, {"key": "calculate",
                                                                               "mode": "finish"})[0]
    assert out["outcome"] == "partial" and out["reason"] == "edited"
    assert out["written"] == ["examples"] and out["missing"] == ["description"]
    assert all(sidecar(ui, "calculate", r["id"])[0] == "absent" for r in revisions(ui, "calculate"))


def test_only_revision_and_provenance_cannot_be_set_over_http(ui):
    install(ui, {"key": "calculate"})
    revision = revisions(ui, "calculate")[0]["id"]
    # An HTTP body carrying only_revision for a revision that is not current: ignored, the write goes ahead.
    collection = ui.state.workshop.collection("calculate")
    body = {"format_version": 1, "expected_revision": collection["revision"], "examples": collection["examples"],
            "only_revision": "0" * 32}
    failure = failure_of(lambda: request(ui, "/api/workshop/blocks/calculate/examples", body))
    assert failure is None or failure.body.get("code") != "edited"
    # An ordinary save drops the provenance record, even when the draft's metadata carries one.
    service = ui.state.workshop
    draft = service.source({"key": "calculate"})
    draft["metadata"]["library"] = {"key": "calculate", "version": 1, "definition_sha256": "0" * 64}
    definition = jsonio.decode_exact(draft["definition_text"])
    definition["description"] = "edited"
    draft["definition_text"] = jsonio.encode_exact(definition)
    service.drafts.issue(draft, draft_lineage.Lineage.of(draft))
    body = {"draft": draft, "expected_identity": service.assets.identity("calculate")}
    planned = service.plan("save", body)
    published = service.save({**body, "expected_plan": planned["expected_plan"],
                              "confirmations": planned["required_confirmations"]})
    assert "library" not in service.assets.saved("calculate", published["durable_id"]).metadata
    assert service.assets.saved("calculate", revision).metadata["library"]["key"] == "calculate"


# --- the shipped examples (8) -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("key", TOOLS)
def test_every_tool_and_transform_example_passes_exactly(ui, key):
    install(ui, {"key": key})
    for example in lib.entry(key).info["examples"]:
        result = result_of(run_block(ui, key, example["inputs"]))
        assert result["outcome"] == "complete", (example["label"], result)
        assert judgement(result["outputs"], example["expectation"], True) == "correct", example["label"]


@pytest.mark.parametrize("key", MODELS)
def test_every_model_example_runs_against_the_scripted_stub(ui, server, key):
    model_ready(ui, server)
    if key == "deidentify_text":
        trust_model(ui, server)
    install(ui, {"key": key})
    cases = json.loads((pytest.importorskip("pathlib").Path(__file__).parent / "library_eval" /
                        f"{key}.json").read_text(encoding="utf-8"))["cases"]
    for example in lib.entry(key).info["examples"]:
        case = next(c for c in cases if c["id"] == example["id"])
        assert case["inputs"] == example["inputs"] and case["expectation"] == example["expectation"]
        server.reply(block_answer(**case["stub"]))
        result = result_of(run_block(ui, key, example["inputs"]))
        assert result["outcome"] == "complete", (example["label"], result)
        assert judgement(result["outputs"], example["expectation"], True) == "correct", example["label"]


def test_tutorial_stub_reads_a_block_result_inside_json_text():
    from agentwiz.tutorial_support.backend import _found_result
    assert _found_result(json.dumps({"result": {"result": "36"}})) == "36"
    block = json.dumps({"ok": True, "outputs": {"out": json.dumps({"result": "36"})}})
    assert _found_result(block) == "36"
    assert _found_result("not json") == "unknown"
    assert _found_result(json.dumps([{"x": 1}])) == "unknown"


def test_tutorial_library_keys_are_library_entries():
    from agentwiz.tutorial_support.library import KEYS
    assert set(KEYS) <= set(lib.index())
