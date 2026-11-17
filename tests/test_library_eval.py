"""scripts/library_eval.py and tests/library_eval/checks.py against the scripted stub (starter blocks addendum, 7.1).
The loopback backend supplies scripted replies for the qualification and Chat runners."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from agentwiz import library_install as lib
from agent_helpers import text_turn, tool_call
from conftest import Reply, answer, block_answer, configure, responses_answer, save_profile, save_roles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "library_eval"))
import checks  # noqa: E402

spec = importlib.util.spec_from_file_location("library_eval", ROOT / "scripts" / "library_eval.py")
library_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(library_eval)

MODELS = [k for k in lib.index() if lib.entry(k).group == "llm"]


def cases(key):
    return json.loads((ROOT / "tests" / "library_eval" / f"{key}.json").read_text(encoding="utf-8"))["cases"]


def target(ui, server, name="target", model="m"):
    save_profile(ui, name, model=model, kind="openai_compat", base_url=server.url + "/v1", provider="")


@pytest.mark.parametrize("key", MODELS)
def test_every_case_file_is_well_formed(key):
    data = cases(key)
    assert len(data) == 10
    assert sum(c["set"] == "dev" for c in data) == 7 and sum(c["set"] == "heldout" for c in data) == 3
    assert sum(bool(c.get("shipped")) for c in data) == 3
    assert any(c.get("critical") for c in data)                       # the missing-data case at least
    shipped = {e["id"]: e for e in lib.entry(key).info["examples"]}
    for case in data:
        if case.get("shipped"):
            assert case["inputs"] == shipped[case["id"]]["inputs"]
        if "stub" in case:
            assert checks.check(key, case["inputs"], case["stub"]) == [], case["id"]
    assert key in checks.CHECKS


def test_hard_invariants_catch_their_violations():
    assert checks.check("classify_text", {"labels": '["a","b"]'}, {"label": "c"})
    assert checks.check("classify_text", {"labels": '["a","b"]'}, {"label": "a"}) == []
    src = "The fee is 40 dollars."
    assert checks.check("answer_from_source", {"source": src}, {"found": True, "answer": "40",
                                                                  "quotes": ["fee is 40 dollars"]}) == []
    assert checks.check("answer_from_source", {"source": src}, {"found": True, "answer": "40",
                                                                  "quotes": ["fee is forty dollars"]})
    assert checks.check("answer_from_source", {"source": src}, {"found": False, "answer": "No.", "quotes": []})
    text = "Alice met Bob in [ROOM]."
    good = {"text": "[PERSON_1] met [PERSON_2] in [ROOM].",
            "identifier_map": [{"placeholder": "[PERSON_1]", "original": "Alice", "kind": "PERSON"},
                               {"placeholder": "[PERSON_2]", "original": "Bob", "kind": "PERSON"}]}
    assert checks.check("deidentify_text", {"text": text}, good) == []
    shared = {**good, "identifier_map": [{"placeholder": "[PERSON_1]", "original": "Alice", "kind": "PERSON"},
                                         {"placeholder": "[PERSON_1]", "original": "Bob", "kind": "PERSON"}]}
    assert any("share" in v for v in checks.check("deidentify_text", {"text": text}, shared))
    twice = {"text": "[P_1] met [P_2] in [ROOM].",
             "identifier_map": [{"placeholder": "[P_1]", "original": "Alice", "kind": "PERSON"},
                                {"placeholder": "[P_2]", "original": "Alice", "kind": "PERSON"}]}
    assert any("two placeholders" in v for v in checks.check("deidentify_text", {"text": text}, twice))
    collide = {"text": "[ROOM] met [PERSON_2] in [ROOM].",
               "identifier_map": [{"placeholder": "[ROOM]", "original": "Alice", "kind": "PERSON"}]}
    assert any("collides" in v for v in checks.check("deidentify_text", {"text": text}, collide))
    bad = {"text": "[person] met Bob.", "identifier_map": [{"placeholder": "[person]", "original": "Carol",
                                                              "kind": "PERSON"}]}
    found = checks.check("deidentify_text", {"text": text}, bad)
    assert any("bracket_upper" in v for v in found) and any("does not occur in the input" in v for v in found)
    assert checks.check("summarise_document", {"length": '"short"'}, {"summary": "w " * 81})
    assert checks.check("table_from_text", {"columns": '["a","b"]'}, {"rows": [["1"]]})
    assert checks.check("critique_against_checklist", {"checklist": '["x","y"]', "text": ""},
                        {"results": [{"item": "y", "met": "unclear", "evidence": ""},
                                     {"item": "x", "met": "unclear", "evidence": ""}]})


def run(ui, **over):
    kwargs = dict(profile="target", route="openai_compat", blocks=["classify_text"], trials=1, which="dev",
                  case_filter=lambda key, case: "stub" in case)
    return library_eval.evaluate(library_eval.Client(ui), **{**kwargs, **over})


@pytest.mark.parametrize("route", ["openai_compat", "lmstudio"])
def test_the_runner_installs_resolves_and_scores_against_the_stub(ui, server, route):
    save_profile(ui, "target", model="m", kind=route, base_url=server.url + "/v1", provider="")

    def model_reply(stub):
        return responses_answer(json.dumps(stub)) if route == "lmstudio" else block_answer(**stub)

    server.reply(*(model_reply(c["stub"]) for c in cases("classify_text") if "stub" in c))
    report = run(ui, route=route)
    assert report["resolved"]["classify_text"]["kind"] == route
    assert report["target"]["trusted_destination"] is True and report["target"]["owner_fill"]["model_file"] is None
    rows = report["blocks"]["classify_text"]["runs"]
    assert [r["outcome"] for r in rows] == ["correct"] * 3, json.dumps(rows)[:1500]
    assert all(r["first_pass"] and r["repairs_used"] == 0 and r["route"] == route for r in rows)
    bar = report["blocks"]["classify_text"]["bar"]
    assert bar["scheduled"] == 3 and bar["correct"] == 3 and bar["meets_bar"] is None   # not a full qualification
    assert ui.state.block_library.provenance("classify_text")["key"] == "classify_text"
    # the report holds no credential
    assert "api_key" not in json.dumps(report)


def test_a_label_outside_the_list_is_incorrect_and_a_hard_violation(ui, server):
    target(ui, server)
    first = next(c for c in cases("classify_text") if "stub" in c)
    server.reply(block_answer(label="refunds", reason="x"))
    report = run(ui, case_filter=lambda key, case: case["id"] == first["id"])
    row = report["blocks"]["classify_text"]["runs"][0]
    assert row["outcome"] == "incorrect" and row["hard_violation"] is True
    assert report["blocks"]["classify_text"]["bar"]["hard_invariants_held"] is False


def test_an_answer_that_never_meets_the_contract_is_contract_failed(ui, server):
    target(ui, server)
    first = next(c for c in cases("classify_text") if "stub" in c)
    server.reply(*[answer("not json")] * 4)
    row = run(ui, case_filter=lambda key, case: case["id"] == first["id"])["blocks"]["classify_text"]["runs"][0]
    assert row["outcome"] == "contract_failed" and row["failure_reason"]


def test_a_route_or_model_mismatch_is_refused(ui, server):
    target(ui, server)
    with pytest.raises(library_eval.Refused, match="route"):
        run(ui, route="lmstudio")
    with pytest.raises(library_eval.Refused, match="not among"):
        run(ui, profile="missing")
    # the block's role is later bound to another model: refused, no substitution
    target(ui, server)
    run_ok = library_eval.prepare(library_eval.Client(ui), "target", "openai_compat", ["classify_text"])
    target(ui, server, name="other", model="m2")
    save_roles(ui, {"fast": "other"})
    with pytest.raises(library_eval.Refused, match="not the target"):
        library_eval.verify_route(library_eval.Client(ui), run_ok, ["classify_text"])


def test_rubric_cases_carry_an_owner_mark():
    keyed = {k: [c for c in cases(k) if c.get("rubric")] for k in MODELS}
    assert {k for k, v in keyed.items() if v} == {"answer_from_source", "rewrite_for_audience", "proofread",
                                                    "translate_text", "draft_reply", "compare_documents_meaning"}
    assert all(c["set"] == "heldout" for v in keyed.values() for c in v)


def test_deidentify_runs_on_a_trusted_local_target(ui, server, local_deidentify_candidate):
    target(ui, server)
    stubbed = [c for c in cases("deidentify_text") if "stub" in c]
    server.reply(*[block_answer(**c["stub"]) for c in stubbed])
    report = run(ui, blocks=["deidentify_text"])
    rows = report["blocks"]["deidentify_text"]["runs"]
    assert [r["outcome"] for r in rows] == ["correct"] * len(stubbed), json.dumps(rows)[:1500]
    assert report["blocks"]["deidentify_text"]["bar"]["hard_invariants_held"] is True


def test_openrouter_qualification_binds_the_profile_and_reports_rate_limiting(ui, server, local_deidentify_candidate):
    provider = "deepinfra/turbo"
    save_profile(ui, "target", model="qwen/model", kind="openrouter", base_url=server.url + "/v1",
                 provider=provider, api_key="test-key")
    with pytest.raises(library_eval.Refused, match="Local only"):
        run(ui, route=None, blocks=["deidentify_text"])
    assert server.requests == []

    server.reply(block_answer(**cases("classify_text")[0]["stub"]), Reply(status=429, body=b"{}"),
                 block_answer(**cases("classify_text")[2]["stub"]))
    report = run(ui, route=None)
    assert report["target"]["provider"] == report["resolved"]["classify_text"]["provider"] == provider
    assert report["target"]["roles"]["fast"]["definition"]["location"] == "cloud"
    assert report["target"]["trusted_destination"] is False
    assert report["verdict"] == "blocked"
    rows = report["blocks"]["classify_text"]["runs"]
    assert [r["outcome"] for r in rows] == ["correct", "blocked", "correct"], rows
    assert rows[1]["model_error"]["status"] == 429
    for request in server.requests:
        assert request["body"]["provider"]["order"] == [provider]
        assert request["body"]["provider"]["allow_fallbacks"] is False
    assert "test-key" not in json.dumps(report)


def test_full_qualification_needs_rubric_review_and_three_distinct_trials(tmp_path):
    cases = [{"id": str(i)} for i in range(10)]
    rows = [{"case": c["id"], "trial": trial, "outcome": "correct"} for c in cases for trial in range(1, 4)]
    rows[0].update(rubric=["Preserve the stated quantity"], owner_mark=None)
    bar = library_eval.bar(rows, cases, 3)
    report = {"format_version": 2, "blocks": {"example": {"runs": rows, "bar": bar}}}
    library_eval.update_verdict(report)
    assert report["verdict"] == "rubric_review_pending"
    path = tmp_path / "report.json"
    for mark, verdict, exit_code in ((False, "failed", 1), (True, "passed", 0)):
        rows[0]["owner_mark"] = mark
        path.write_text(json.dumps(report), encoding="utf-8")
        assert library_eval.main(["--review", str(path)]) == exit_code
        assert json.loads(path.read_text(encoding="utf-8"))["verdict"] == verdict
    assert library_eval.bar(rows, cases[:5], 6)["full_qualification"] is False


def test_rubric_report_contains_the_output_with_known_credentials_redacted(ui, server):
    key = "test-rubric-secret"
    save_profile(ui, "target", model="qwen/model", kind="openrouter", base_url=server.url + "/v1",
                 provider="deepinfra/turbo", api_key=key)
    server.reply(block_answer(found=True, answer=f"Closed on public holidays. {key}",
                              quotes=["Closed on Sundays and public holidays."]))
    report = run(ui, route="openrouter", blocks=["answer_from_source"], which="heldout",
                 case_filter=lambda _key, case: case["label"] == "holiday")
    row = report["blocks"]["answer_from_source"]["runs"][0]
    assert row["owner_mark"] is None
    assert row["review_outputs"]["answer"] == "Closed on public holidays. ***"
    assert key not in json.dumps(report)


# --- agent selection cohorts (7.2) ------------------------------------------------------------------------------------
spec_ae = importlib.util.spec_from_file_location("agent_eval", ROOT / "scripts" / "agent_eval.py")
agent_eval = importlib.util.module_from_spec(spec_ae)
spec_ae.loader.exec_module(agent_eval)
COHORTS = json.loads((ROOT / "tests" / "agent_eval" / "cohorts.json").read_text(encoding="utf-8"))


def test_the_cohorts_are_small_and_cover_the_required_cases():
    from agentwiz import agent_tools
    builtin = {agent_tools.capability_name(t) for t in (*agent_tools.DOCUMENT_TOOLS, *agent_tools.UTILITY_TOOLS)}
    groups = [c["capabilities"] for c in COHORTS["cohorts"]] + [c["capabilities"] for c in COHORTS["gate"]["cases"]]
    for names in groups:
        assert len(names) <= agent_eval.COHORT_LIMIT and len(set(names)) == len(names)
        for name in names:
            assert name in builtin or name[len("library:"):] in lib.index(), name
    cases = [case for c in COHORTS["cohorts"] for case in c["cases"]]
    assert any(case["expected_capabilities"] == [] for case in cases)                       # a no-tool case
    assert any({"aw_copy_to_draft", "aw_revise_draft"} & set(case["expected_capabilities"]) for case in cases)
    assert [c["id"] for c in COHORTS["gate"]["cases"]] == ["gate-summarise", "gate-answer", "gate-draft",
                                                           "gate-table", "gate-no-tool"]


class _Local:
    """agent_eval's client, over the test server's token."""
    def __new__(cls, ui):
        return agent_eval.Client(ui.url)


def test_cohort_install_and_resolution_by_block_identity(ui):
    client = _Local(ui)
    blocks = agent_eval.install_entries(client, ["summarise_document", "classify_text"])
    assert blocks == {"summarise_document": "summarise_document", "classify_text": "classify_text"}
    assert agent_eval.install_entries(client, ["classify_text"]) == {"classify_text": "classify_text"}  # reused
    state = client.json("/api/state")
    inventory = client.json("/api/agent/inventory?revision=" + str(state["revision"]))
    chosen, mapping = agent_eval.resolve(["library:classify_text", "aw_calculate"], inventory, blocks)
    assert mapping["library:classify_text"].startswith("block_classify_text_") and mapping["aw_calculate"] == "aw_calculate"
    assert chosen[0]["revision"]
    rows = [{"correct_choice": True, "valid_arguments": None, "result_used": False}]
    assert agent_eval.rates(rows) == {"correct_choice": {"passed": 1, "judged": 1},
                                      "valid_arguments": {"passed": 0, "judged": 0},
                                      "result_used": {"passed": 0, "judged": 1}}


@pytest.mark.parametrize("finish,expression,verdict", [("stop", "2+2", "passed"), ("length", "2+2", "failed"),
                                                        ("stop", "2/0", "failed")])
def test_agent_gate_requires_completed_tools_and_final_answer_on_openrouter(ui, server, finish, expression, verdict):
    provider = "deepinfra/turbo"
    configure(ui, server, kind="openrouter", model="qwen/model", api_key="test-key", provider=provider)
    save_profile(ui, "target", model="qwen/model", kind="openrouter", provider=provider)
    save_roles(ui, {"fast": "target"})
    client = agent_eval.Client(ui.url)
    selected = agent_eval.model_target(client.json("/api/state"))
    server.reply(tool_call("aw_calculate", {"expression": expression}), text_turn("4", finish=finish))
    names = ["aw_calculate"]
    fixture = {"id": "sum", "message": "Use the calculator for 2+2", "capabilities": names,
               "expected_capabilities": names, "expected_answer": {"equals": "4"}}
    result = agent_eval.run_cohorts(client, {"gate": {"cases": [fixture]}}, 5, selected)
    assert result["gate"]["verdict"] == verdict, result
    assert server.requests[0]["body"]["provider"]["order"] == [provider]
    assert server.requests[0]["body"]["provider"]["allow_fallbacks"] is False
