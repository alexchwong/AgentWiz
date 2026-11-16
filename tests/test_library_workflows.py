"""Workflows built from library blocks (starter blocks addendum, 6.2 and 6.3): the two table wirings and the
de-identification chain, end to end against the scripted stub backend. The chain's remote block runs on its own
untrusted model, so remote requests are counted by model name."""
from __future__ import annotations

import pytest

from agentwiz import jsonio
from conftest import block_answer, request, save_profile, save_roles, wait_until
from test_release_gate import decide, done, gate, review, trust
from test_workshop import send
from test_library import install


def node(name, port="out", path=None):
    binding = {"kind": "node", "node": name, "port": port}
    if path is not None:
        binding["path"] = path
    return binding


def output(fmt, binding, local_only=False):
    return {"contract": {"format": fmt}, "local_only": local_only, "binding": binding}


def save_workflow(ui, name, definition, selections):
    body = {"name": name, "file_token": None, "definition_text": jsonio.encode_exact(definition),
            "selections": {n: {"key": key, "revision": ui.state.workshop.assets.revisions(key)["current"]}
                           for n, key in selections.items()}}
    plan = send(ui, "plans", action="workflow_save", request=body)
    return send(ui, f"workflows/{name}/save", request=body, expected_plan=plan["expected_plan"],
                confirmations=plan["required_confirmations"])


def start(ui, name, inputs):
    selection = {"name": name, "inputs": inputs}
    plan = send(ui, "plans", action="workflow_run", request=selection)
    assert not [i for i in plan["descriptor"]["issues"] if i["severity"] == "error"], plan
    ident = send(ui, f"workflows/{name}/run", request=selection, expected_plan=plan["expected_plan"],
                 ticket=send(ui, "tickets")["ticket"], confirmations=plan["required_confirmations"])["operation_id"]
    return ident, "/api/workshop/workflow-jobs/" + ident


def finished(ui, route):
    assert wait_until(lambda: request(ui, route)["outcome"] != "running", timeout=30)
    return request(ui, route)


# --- 6.3: CSV to a sorted Markdown table ------------------------------------------------------------------------------
CSV = 'Order ID,Customer,Total\n010,Ng,5\n007,"Smith, J",12.50\n010,Ng,5\n'


def test_csv_sort_table_wiring(ui):
    assert [o["outcome"] for o in install(ui, {"key": "csv_to_rows"}, {"key": "sort_and_dedupe_rows"},
                                          {"key": "rows_to_table"})] == ["added"] * 3
    definition = {
        "name": "orders", "purpose": "Sorted orders", "inputs": {"csv": {"contract": {"format": "text"},
                                                                         "local_only": False}},
        "constants": {"sort_keys": {"value": ["Order ID"], "local_only": False}},
        "nodes": {"parse": {"bindings": {"csv": {"kind": "input", "port": "csv"}}},
                  "sort": {"bindings": {"columns": node("parse", path=["columns"]),
                                        "rows": node("parse", path=["rows"]),
                                        "by": {"kind": "constant", "name": "sort_keys"}}},
                  "table": {"bindings": {"columns": node("sort", path=["columns"]),
                                         "rows": node("sort", path=["rows"])}}},
        "outputs": {"markdown": output("json", node("table"))}}
    save_workflow(ui, "orders", definition, {"parse": "csv_to_rows", "sort": "sort_and_dedupe_rows",
                                             "table": "rows_to_table"})
    _ident, route = start(ui, "orders", {"csv": CSV})
    result = finished(ui, route)
    assert result["outcome"] == "complete", result
    table = jsonio.decode_exact(result["outputs"]["markdown"]["text"])["markdown"]
    assert table == ("| Order ID | Customer | Total |\n| --- | --- | --- |\n| 007 | Smith, J | 12.50 |\n"
                     "| 010 | Ng | 5 |\n")


# --- 6.3: model rows to a Markdown table ------------------------------------------------------------------------------
def test_model_to_table_wiring(ui, server):
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"fast": "p"})
    install(ui, {"key": "table_from_text"}, {"key": "rows_to_table"})
    definition = {
        "name": "extract", "purpose": "", "inputs": {"text": {"contract": {"format": "text"}, "local_only": False}},
        "constants": {"columns": {"value": ["Name", "Age"], "local_only": False}},
        "nodes": {"rows": {"bindings": {"text": {"kind": "input", "port": "text"},
                                        "columns": {"kind": "constant", "name": "columns"}}},
                  "table": {"bindings": {"columns": {"kind": "constant", "name": "columns"},
                                         "rows": node("rows", "rows")}}},
        "outputs": {"markdown": output("json", node("table"))}}
    save_workflow(ui, "extract", definition, {"rows": "table_from_text", "table": "rows_to_table"})
    server.reply(block_answer(rows=[["Ana", "30"], ["Ben", ""]]))
    _ident, route = start(ui, "extract", {"text": "Ana is 30. Ben's age is unknown."})
    result = finished(ui, route)
    assert result["outcome"] == "complete", result
    assert jsonio.decode_exact(result["outputs"]["markdown"]["text"])["markdown"] == (
        "| Name | Age |\n| --- | --- |\n| Ana | 30 |\n| Ben |  |\n")


# --- 6.2: de-identify, check, gate, remote, restore -------------------------------------------------------------------
SOURCE = "Alice approved [DRAFT]."
GOOD = {"text": "[PERSON_1] approved [DRAFT].",
        "identifier_map": [{"placeholder": "[PERSON_1]", "original": "Alice", "kind": "PERSON"}]}


def deid_workflow(ui, server):
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_profile(ui, "r", model="r", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"fast": "p", "remote": "r"})
    trust(ui, server, "m")                                       # only the de-identifying model is trusted
    out = install(ui, {"key": "deidentify_text"}, {"key": "check_identifier_map"},
                  {"key": "rewrite_for_audience", "as": "remote_rewrite", "role": "remote"},
                  {"key": "restore_identifiers"})
    assert [o["outcome"] for o in out] == ["added"] * 4, out
    review_row = gate(node("check", path=["text"]), scan=node("check", path=["pairs"]))
    definition = {
        "name": "deid", "purpose": "De-identify, review, send, restore",
        "inputs": {"source": {"contract": {"format": "text"}, "local_only": True}},
        "constants": {"instruction": {"value": "Plain English.", "local_only": False}},
        "nodes": {"deid": {"bindings": {"text": {"kind": "input", "port": "source"}}},
                  "check": {"bindings": {"original": {"kind": "input", "port": "source"},
                                         "text": node("deid", "text"), "pairs": node("deid", "identifier_map")}},
                  "review": review_row,
                  "remote": {"bindings": {"text": node("review", "released"),
                                          "instruction": {"kind": "constant", "name": "instruction"}}},
                  "restore": {"bindings": {"text": node("remote", "text"),
                                           "pairs": node("check", path=["pairs"])}}},
        "outputs": {"restored": output("text", node("restore"), True),
                    "unmapped": output("json", node("check", path=["unmapped_tokens"]), True)}}
    save_workflow(ui, "deid", definition, {"deid": "deidentify_text", "check": "check_identifier_map",
                                           "remote": "remote_rewrite", "restore": "restore_identifiers"})


def remote_requests(server) -> int:
    return sum(1 for r in server.requests if r["method"] == "POST" and isinstance(r.get("body"), dict)
               and r["body"].get("model") == "r")


def test_the_chain_keeps_a_preserved_token_and_restores(ui, server):
    deid_workflow(ui, server)
    server.reply(block_answer(**GOOD), block_answer(text="[PERSON_1] approved [DRAFT]."))
    ident, route = start(ui, "deid", {"source": SOURCE})
    current = review(ui, route)
    assert current["payload"] == "[PERSON_1] approved [DRAFT]." and remote_requests(server) == 0
    decide(ui, ident, current)
    result = done(ui, route)
    assert result["outcome"] == "complete", result
    assert result["outputs"]["restored"]["text"] == "Alice approved [DRAFT]."
    assert jsonio.decode_exact(result["outputs"]["unmapped"]["text"]) == ["[DRAFT]"]
    assert remote_requests(server) == 1


@pytest.mark.parametrize("reply", [
    {"text": "PERSON_1 approved [DRAFT].",                                       # a malformed placeholder
     "identifier_map": [{"placeholder": "PERSON_1", "original": "Alice", "kind": "PERSON"}]},
    {"text": "[PERSON_1] approved [DRAFT].",                                     # one placeholder, two originals
     "identifier_map": [{"placeholder": "[PERSON_1]", "original": "Alice", "kind": "PERSON"},
                        {"placeholder": "[PERSON_1]", "original": "approved", "kind": "OTHER"}]},
    {"text": "[PERSON_1] approved [DRAFT]. [PERSON_2]",                          # one original, two placeholders
     "identifier_map": [{"placeholder": "[PERSON_1]", "original": "Alice", "kind": "PERSON"},
                        {"placeholder": "[PERSON_2]", "original": "Alice", "kind": "PERSON"}]},
    {"text": "[DRAFT] approved [DRAFT].",                                        # collides with a source token
     "identifier_map": [{"placeholder": "[DRAFT]", "original": "Alice", "kind": "PERSON"}]},
], ids=["malformed", "two_originals", "two_placeholders", "collision"])
def test_a_bad_map_fails_the_check_before_the_gate_with_no_remote_request(ui, server, reply):
    deid_workflow(ui, server)
    server.reply(block_answer(**reply))
    ident, route = start(ui, "deid", {"source": SOURCE})
    result = finished(ui, route)
    assert result["outcome"] != "complete" and result.get("phase") != "awaiting_release"
    detail = request(ui, route + "?node=check")
    assert "invalid_map" in jsonio.encode_exact(detail)
    assert request(ui, route)["nodes"]["review"]["outcome"] == "not_run"
    assert remote_requests(server) == 0


def test_a_declined_gate_makes_no_remote_request(ui, server):
    deid_workflow(ui, server)
    server.reply(block_answer(**GOOD))
    ident, route = start(ui, "deid", {"source": SOURCE})
    decide(ui, ident, review(ui, route), "reject")
    assert done(ui, route)["outcome"] == "release_rejected"
    assert remote_requests(server) == 0
