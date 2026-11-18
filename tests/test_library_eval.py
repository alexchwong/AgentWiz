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
    assert agent_eval.rates(rows) == {"correct_choice": {"passed": 1, "judged": 1, "unjudged": 0},
                                      "valid_arguments": {"passed": 0, "judged": 0, "unjudged": 1},
                                      "result_used": {"passed": 0, "judged": 1, "unjudged": 0}}


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


def test_disposable_evaluators_ignore_caller_configuration(tmp_path, monkeypatch, server):
    import eval_setup
    monkeypatch.chdir(tmp_path)
    personal = tmp_path / "config" / "model-servers.ini"
    personal.parent.mkdir()
    personal.write_text("not a valid server configuration")
    monkeypatch.setenv("AGENTWIZ_LIVE_LMSTUDIO_MODEL", "test-model")
    monkeypatch.setenv("AGENTWIZ_LIVE_LMSTUDIO_URL", server.url + "/v1")
    workdir = tmp_path / "evaluation"
    with eval_setup.session(workdir, "lmstudio-eval", "lmstudio") as ui:
        client = agent_eval.Client(ui.url)
        eval_setup.select_chat(client, "lmstudio-eval")
        state = client.json("/api/state")
        target = agent_eval.model_target(state)
        agent_eval.check_target(state, target, require_fast=True)
        assert target["model"] == "test-model"
        assert target["base_url"] == server.url + "/v1"
        assert state["roles"]["map"]["fast"] == "lmstudio-eval"
    assert personal.read_text() == "not a valid server configuration"
    assert not (tmp_path / "model_profiles").exists()
    # Session cleanup must release the workspace lock, including after an exception.
    with pytest.raises(RuntimeError):
        with eval_setup.session(workdir, "lmstudio-eval", "lmstudio"):
            raise RuntimeError("abort")
    with eval_setup.session(workdir, "lmstudio-eval", "lmstudio"):
        pass


@pytest.mark.parametrize("destination", [None, "custom/nested/report.json"])
def test_agent_cli_disposable_and_attached_modes(tmp_path, monkeypatch, server, ui, destination):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTWIZ_LIVE_LMSTUDIO_URL", server.url + "/v1")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps([{"id": "plain", "message": "Say pong", "capabilities": [],
                                      "expected_answer": {"equals": "pong"}}]))
    report = tmp_path / (destination or "reports/agent_eval_report.json")
    report_args = ["--report", str(report)] if destination else []
    server.reply(responses_answer("pong"))
    assert agent_eval.main(["--route", "lmstudio", "--cases", str(cases_path), *report_args]) == 0
    data = json.loads(report.read_text())
    assert data["totals"]["answer_correct"] == 1
    assert data["route"]["kind"] == "lmstudio"
    configure(ui, server)
    server.reply(text_turn("pong"))
    assert agent_eval.main(["--url", ui.url, "--cases", str(cases_path), "--report", str(report)]) == 0
    assert json.loads(report.read_text())["totals"]["answer_correct"] == 1


def test_evaluation_overrides_are_explicit(tmp_path, monkeypatch):
    import eval_setup
    from agentwiz import profiles, server_config
    folder = tmp_path / "external"
    folder.mkdir()
    profiles.write_profiles(folder, {"format_version": 4, "profiles": {
        "target": {"kind": "openai_compat", "model": "stub", "provider": ""}}})
    config = tmp_path / "servers.ini"
    server_config.initialize(config)
    copied, selected_config, servers = eval_setup.materialize(
        tmp_path / "run", "target", "openai_compat", folder, config)
    assert profiles.read_profiles(copied).profiles["target"].model == "stub"
    assert selected_config == config
    assert "openai_compat" in servers
    with pytest.raises(eval_setup.SetupBlocked):
        eval_setup.materialize(tmp_path / "bad", "target", "openrouter", folder, config)
    monkeypatch.setenv("AGENTWIZ_LIVE_OPENAI_COMPAT_MODEL", "other")
    with pytest.raises(eval_setup.SetupBlocked):
        eval_setup.materialize(tmp_path / "ambiguous", "target", None, folder, config)
    monkeypatch.delenv("AGENTWIZ_LIVE_OPENAI_COMPAT_MODEL")
    with pytest.raises(eval_setup.SetupBlocked):
        eval_setup.materialize(tmp_path / "missing", "target", None, folder, tmp_path / "absent.ini")
    assert not (tmp_path / "absent.ini").exists()


@pytest.mark.parametrize("destination", [None, "custom/nested/library.json"])
def test_library_cli_disposable_startup(tmp_path, monkeypatch, server, destination):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTWIZ_LIVE_LMSTUDIO_URL", server.url + "/v1")
    fixture = library_eval.load_cases("classify_text")
    fixture["cases"] = [case for case in fixture["cases"] if "stub" in case]
    monkeypatch.setattr(library_eval, "load_cases", lambda key: fixture)
    server.reply(*(responses_answer(json.dumps(case["stub"])) for case in fixture["cases"]))
    report_path = tmp_path / (destination or "reports/library_eval_report.json")
    report_args = ["--report", destination] if destination else []
    result = library_eval.main(["--profile", "lmstudio-eval", "--route", "lmstudio",
                               "--block", "classify_text", *report_args])
    report = json.loads(report_path.read_text())
    assert result in (0, 3), report
    assert report["resolved"]["classify_text"]["kind"] == "lmstudio"
    assert all(row["outcome"] == "correct" for row in report["blocks"]["classify_text"]["runs"])
    assert not (tmp_path / "config").exists()


def test_invalid_live_endpoint_blocks_without_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTWIZ_LIVE_LMSTUDIO_URL", "not-an-endpoint")
    report = tmp_path / "blocked.json"
    assert library_eval.main(["--profile", "lmstudio-eval", "--block", "classify_text",
                              "--report", str(report)]) == 2
    data = json.loads(report.read_text())
    assert data["verdict"] == "blocked" and data["blocks"] == {}


# These regressions prevent paid qualification runs losing evidence or mis-scoring Text ports.
@pytest.mark.parametrize('route', ['openai_compat', 'lmstudio', 'openrouter'])
def test_missing_source_text_assertion_scores_the_real_heldout_case(ui, server, route):
    save_profile(ui, 'target', model='m', kind=route, base_url=server.url + '/v1',
                 provider='deepinfra/turbo' if route == 'openrouter' else '', api_key='test-key')
    case = next(c for c in cases('answer_from_source') if c['id'] == 'answer_from_source-9')
    output = dict(found=False, answer='Not found in the source.', quotes=[])
    server.reply(responses_answer(json.dumps(output)) if route == 'lmstudio' else block_answer(**output))
    report = run(ui, route=route, blocks=['answer_from_source'], which='all',
                 case_filter=lambda key, c: c['id'] == case['id'])
    row = report['blocks']['answer_from_source']['runs'][0]
    assert row['outcome'] == 'correct' and row['judgement'] == 'correct'
    assert row['outputs']['answer']['text'] == output['answer']
    assert row['attempts'] and row['owner_mark'] is None


def test_wrong_literal_text_is_incorrect_and_text_paths_are_refused(ui, server, monkeypatch):
    target(ui, server)
    server.reply(block_answer(found=False, answer='No manager is named.', quotes=[]))
    report = run(ui, blocks=['answer_from_source'], which='all',
                 case_filter=lambda key, c: c['id'] == 'answer_from_source-9')
    assert report['blocks']['answer_from_source']['runs'][0]['outcome'] == 'incorrect'
    fixture = library_eval.load_cases('answer_from_source')
    fixture['cases'][8]['expectation']['assertions'][1]['path'] = ['invalid']
    monkeypatch.setattr(library_eval, 'load_cases', lambda key: fixture)
    with pytest.raises(library_eval.Refused, match='answer_from_source-9.*expectation'):
        library_eval.preflight(['answer_from_source'], 'all')


def test_fixture_input_failure_is_found_before_any_model_call(ui, server, monkeypatch):
    target(ui, server)
    fixture = library_eval.load_cases('classify_text')
    fixture['cases'][0]['inputs']['labels'] = 'not json'
    monkeypatch.setattr(library_eval, 'load_cases', lambda key: fixture)
    with pytest.raises(library_eval.Refused, match='inputs.labels'):
        run(ui)
    assert server.requests == []


def test_scoring_error_preserves_outputs_checkpoints_and_later_cases(ui, server, tmp_path, monkeypatch):
    from agentwiz import workshop_service
    target(ui, server)
    fixture = [c for c in cases('classify_text') if 'stub' in c]
    server.reply(*(block_answer(**c['stub']) for c in fixture))
    original = workshop_service.judgement
    calls = []
    def score(*args):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError('private-exception-body')
        return original(*args)
    monkeypatch.setattr(workshop_service, 'judgement', score)
    path = tmp_path / 'nested' / 'report.json'
    report = run(ui, report_path=path)
    rows = report['blocks']['classify_text']['runs']
    assert [row['outcome'] for row in rows] == ['correct', 'evaluator_error', 'correct']
    assert rows[1]['outputs'] and rows[1]['attempts'] and rows[1]['diagnostic']['stage'] == 'scoring'
    assert report['verdict'] == 'blocked'
    saved = json.loads(path.read_text())
    assert saved['blocks']['classify_text']['runs'] == rows
    assert 'private-exception-body' not in path.read_text()


def test_cli_interruption_keeps_partial_report_and_review_cannot_pass_it(tmp_path, monkeypatch, server):
    monkeypatch.setenv('AGENTWIZ_LIVE_LMSTUDIO_URL', server.url + '/v1')
    fixture = library_eval.load_cases('classify_text')
    fixture['cases'] = [c for c in fixture['cases'] if 'stub' in c]
    monkeypatch.setattr(library_eval, 'load_cases', lambda key: fixture)
    server.reply(responses_answer(json.dumps(fixture['cases'][0]['stub'])))
    original = library_eval.run_case
    calls = []
    def interrupt(*args):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        return original(*args)
    monkeypatch.setattr(library_eval, 'run_case', interrupt)
    path = tmp_path / 'partial.json'
    assert library_eval.main(['--route', 'lmstudio', '--profile', 'lmstudio-eval', '--block', 'classify_text',
                              '--report', str(path)]) == 3
    data = json.loads(path.read_text())
    assert data['execution'] == 'interrupted' and data['active']['case'] == fixture['cases'][1]['id']
    assert len(data['blocks']['classify_text']['runs']) == 1
    assert library_eval.main(['--review', str(path)]) == 3


def test_no_tool_cohort_offers_its_real_document_distractors(ui, server):
    configure(ui, server, model='m')
    save_profile(ui, 'target', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'target'})
    client = agent_eval.Client(ui.url)
    cohort = COHORTS['cohorts'][0]
    case = next(c for c in cohort['cases'] if c['id'] == 'summarise-none')
    server.reply(text_turn('blue'))
    spec = {'cohorts': [{**cohort, 'cases': [case]}]}
    result = agent_eval.run_cohorts(client, spec, 5, agent_eval.model_target(client.json('/api/state')))
    assert result['cohorts'][0]['cases'][0]['verdict'] == 'passed'
    assert result['counts'] == {'passed': 1, 'failed': 0, 'blocked': 0}
    assert result['cohorts'][0]['cases'][0]['trace'] == []


def test_chat_failure_keeps_invalid_arguments_and_checkpoint(ui, server, tmp_path):
    configure(ui, server, model='m')
    save_profile(ui, 'target', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'target'})
    client = agent_eval.Client(ui.url)
    server.reply(tool_call('aw_calculate', {'expression': []}), text_turn('4'))
    case = {'id': 'bad', 'message': 'Calculate 2+2.', 'capabilities': ['aw_calculate'],
            'expected_capabilities': ['aw_calculate'], 'expected_answer': {'equals': '4'}}
    path = tmp_path / 'chat.json'
    result = agent_eval.run_cohorts(client, {'gate': {'cases': [case]}}, 5,
                                   agent_eval.model_target(client.json('/api/state')), report_path=path)
    row = result['gate']['cases'][0]
    assert row['verdict'] == 'failed' and row['answer'] == '4'
    assert row['trace'][0]['code'] == 'schema_mismatch'
    assert 'expression' in row['trace'][0]['arguments_text']
    assert json.loads(path.read_text())['gate']['cases'][0] == row


def test_passing_chat_gate_does_not_hide_a_blocked_cohort(ui, server, monkeypatch):
    configure(ui, server, model='m')
    save_profile(ui, 'target', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'target'})
    client = agent_eval.Client(ui.url)
    case = {'id': 'answer', 'message': 'Say 4', 'capabilities': ['aw_calculate'],
            'expected_capabilities': [], 'expected_answer': {'equals': '4'}}
    original = agent_eval.run_cohort_case
    def blocked(client, case, *args):
        if case['id'] == 'blocked':
            return {'id': case['id'], 'verdict': 'blocked', 'correct_choice': None,
                    'valid_arguments': None, 'result_used': None}
        return original(client, case, *args)
    monkeypatch.setattr(agent_eval, 'run_cohort_case', blocked)
    server.reply(text_turn('4'))
    spec = {'cohorts': [{'id': 'cohort', 'capabilities': ['aw_calculate'], 'cases': [{**case, 'id': 'blocked'}]}],
            'gate': {'cases': [case]}}
    result = agent_eval.run_cohorts(client, spec, 5, agent_eval.model_target(client.json('/api/state')))
    assert result['gate']['passed'] is True
    assert result['cohorts_verdict'] == result['verdict'] == 'blocked'
    assert result['rates']['correct_choice']['unjudged'] == 1


def test_real_table_gate_reads_then_sorts_through_the_installed_block(ui, server, monkeypatch):
    configure(ui, server, model='m')
    save_profile(ui, 'target', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'target'})
    client = agent_eval.Client(ui.url)
    case = next(c for c in COHORTS['gate']['cases'] if c['id'] == 'gate-table')
    original = agent_eval.resolve
    def prepare_replies(names, inventory, blocks):
        chosen, mapping = original(names, inventory, blocks)
        document = client.json('/api/agent/conversation')['conversation']['documents'][0]['id']
        server.reply(tool_call('aw_read_table', {'document_id': document, 'max_rows': 10}),
                     tool_call(mapping['library:sort_and_dedupe_rows'], {
                         'columns': ['name', 'team', 'score'],
                         'rows': [['Asha', 'blue', '71'], ['Ben', 'red', '64'], ['Asha', 'blue', '71'],
                                  ['Cara', 'red', '88'], ['Dev', 'blue', '59']], 'by': ['score']}, 'c2'),
                     text_turn('Dev is first.'))
        return chosen, mapping
    monkeypatch.setattr(agent_eval, 'resolve', prepare_replies)
    report = agent_eval.run_cohorts(client, {'gate': {'cases': [case]}}, 5,
                                   agent_eval.model_target(client.json('/api/state')))
    row = report['gate']['cases'][0]
    assert row['verdict'] == 'passed', row
    sorted_result = json.loads(row['trace'][1]['result_text'])['result']['out']
    assert sorted_result['rows'][0][0] == 'Dev' and sorted_result['removed'] == 1


def test_all_selected_library_fixtures_pass_preflight_without_model_calls():
    selected = library_eval.preflight(MODELS, 'all')
    assert len(selected) == len(MODELS)


def test_chat_cli_interruption_preserves_completed_custom_cases(tmp_path, monkeypatch, server):
    monkeypatch.setenv('AGENTWIZ_LIVE_LMSTUDIO_URL', server.url + '/v1')
    cases_path = tmp_path / 'cases.json'
    cases_path.write_text(json.dumps([{'id': str(i), 'message': 'Say pong', 'capabilities': [],
                                      'expected_answer': {'equals': 'pong'}} for i in range(2)]))
    server.reply(responses_answer('pong'))
    original = agent_eval.run_case
    def interrupt(client, case, *args):
        if case['id'] == '1':
            raise KeyboardInterrupt()
        return original(client, case, *args)
    monkeypatch.setattr(agent_eval, 'run_case', interrupt)
    path = tmp_path / 'chat.json'
    assert agent_eval.main(['--route', 'lmstudio', '--cases', str(cases_path), '--report', str(path)]) == 3
    data = json.loads(path.read_text())
    assert data['execution'] == 'interrupted' and data['verdict'] == 'incomplete'
    assert data['cases'][0]['answer'] == 'pong' and data['active']['case'] == '1'


def test_shared_auth_failure_stops_library_without_discarding_attempt(ui, server):
    target(ui, server)
    server.reply(Reply(status=401, body=b'{}'))
    report = run(ui)
    rows = report['blocks']['classify_text']['runs']
    assert len(rows) == 1 and rows[0]['model_error']['class'] == 'AuthError'
    assert rows[0]['attempts'] and report['execution'] == report['verdict'] == 'blocked'
    assert len(server.requests) == 1


# Deadline equivalence is evaluation-only; a true date/qualifier/owner error must remain visible.
@pytest.mark.parametrize('due,expected', [('2 May', 'correct'), ('by 2 May', 'correct'),
                                         ('3 May', 'incorrect'), ('before 2 May', 'incorrect'),
                                         ('after 2 May', 'incorrect')])
def test_minutes_deadline_comparison_preserves_raw_outputs(ui, server, due, expected):
    target(ui, server)
    server.reply(block_answer(summary='Two actions.', decisions=[], action_items=[
        {'task': 'book flights', 'owner': 'Ana', 'due': due},
        {'task': 'draft the agenda', 'owner': 'Ben', 'due': None}]))
    report = run(ui, blocks=['minutes_from_transcript'], case_filter=lambda key, c: c['id'].endswith('-7'))
    row = report['blocks']['minutes_from_transcript']['runs'][0]
    assert row['outcome'] == expected
    assert json.loads(row['outputs']['action_items']['text'])[0]['due'] == due
    assert bool(row['judgement_adjustments']) == (due == 'by 2 May')


@pytest.mark.parametrize('word,owners', [('on', ['Ana', 'Ben']), ('by', ['Ben', 'Ana'])])
def test_deadline_equivalence_does_not_apply_without_source_by_or_hide_wrong_owner(ui, server, monkeypatch, word, owners):
    target(ui, server)
    fixture = library_eval.load_cases('minutes_from_transcript')
    fixture['cases'][6]['inputs']['transcript'] = f"Ana: I'll book flights {word} 2 May.\nBen: I'll draft the agenda."
    monkeypatch.setattr(library_eval, 'load_cases', lambda key: fixture)
    server.reply(block_answer(summary='Two actions.', decisions=[], action_items=[
        {'task': 'book flights', 'owner': owners[0], 'due': 'by 2 May'},
        {'task': 'draft the agenda', 'owner': owners[1], 'due': None}]))
    report = run(ui, blocks=['minutes_from_transcript'], case_filter=lambda key, c: c['id'].endswith('-7'))
    assert report['blocks']['minutes_from_transcript']['runs'][0]['outcome'] == 'incorrect'


@pytest.mark.parametrize('key,output,expected', [
    ('minutes_from_transcript', {'summary': 'Send slides.', 'decisions': [],
        'action_items': [{'task': 'send slides', 'owner': 'Someone', 'due': None}]}, 'incorrect'),
    ('minutes_from_transcript', {'summary': 'Send slides.', 'decisions': [],
        'action_items': [{'task': 'send slides', 'owner': None, 'due': None}]}, 'correct'),
    ('table_from_text', {'rows': [['Ana', '30'], ['Ben', 'unknown']]}, 'incorrect'),
    ('table_from_text', {'rows': [['Ana', '30'], ['Ben', '']]}, 'correct'),
])
def test_critical_anonymous_owner_and_missing_cell_rules_remain_enforced(ui, server, key, output, expected):
    target(ui, server)
    server.reply(block_answer(**output))
    report = run(ui, blocks=[key], case_filter=lambda key, c: c['id'].endswith('-4'))
    row = report['blocks'][key]['runs'][0]
    assert row['critical'] is True and row['outcome'] == expected


@pytest.mark.parametrize('reasoning,finish,original_outcome', [
    ('', 'stop', 'empty'), ('thinking', 'stop', 'reasoning_only'),
    ('thinking', 'length', 'truncated_no_answer')])
def test_blank_answer_diagnostics_retain_original_outcome_and_usage(ui, server, reasoning, finish, original_outcome):
    from conftest import delta, DONE
    target(ui, server)
    server.reply(Reply(body=[delta(reasoning=reasoning), delta(finish=finish, usage={
        'prompt_tokens': 100, 'completion_tokens': 2048, 'total_tokens': 2148,
        'completion_tokens_details': {'reasoning_tokens': 2048}}), DONE],
        headers={'Content-Type': 'text/event-stream'}))
    report = run(ui, blocks=['summarise_document'], case_filter=lambda key, c: c['id'].endswith('-4'))
    row = report['blocks']['summarise_document']['runs'][0]
    diagnostic = row['attempt_diagnostics'][0]
    assert row['outcome'] == 'contract_failed' and row['failure_reason'] == 'blank_answer'
    assert diagnostic['outcome'] == original_outcome
    assert diagnostic['finish_reason'] == finish and diagnostic['truncated'] == (finish == 'length')
    assert diagnostic['answer_characters'] == 0
    assert diagnostic['reasoning_characters'] == (len(reasoning) if reasoning else None)
    assert diagnostic['usage']['output_tokens'] == 2048
    assert diagnostic['usage']['reasoning_tokens'] == 2048
    assert diagnostic['task']['max_tokens'] == 2048


def test_summary_extracts_blank_diagnostics_from_existing_report_without_reply_text(tmp_path):
    summary_spec = importlib.util.spec_from_file_location('summarize_library_eval', ROOT / 'scripts/summarize_library_eval.py')
    summary = importlib.util.module_from_spec(summary_spec)
    summary_spec.loader.exec_module(summary)
    record = {'outcome': 'truncated_no_answer', 'check_skipped': 'blank_answer', 'task': {'max_tokens': 2048},
              'result': {'text': '', 'reasoning': 'PRIVATE-REASONING' * 1000, 'finish_reason': 'length',
                         'truncated': True, 'usage': {'output_tokens': 2048, 'reasoning_tokens': 2048}}}
    report = {'format_version': 2, 'trials': 3, 'set': 'all', 'blocks': {'summarise_document': {
        'runs': [{'case': 'summarise_document-4', 'trial': 1, 'outcome': 'contract_failed',
                  'failure_reason': 'blank_answer', 'attempts': [record]}]}}}
    result = summary.summarize(report, ROOT)
    pattern = result['blocks']['summarise_document']['failure_patterns'][0]
    diagnostic = pattern['attempt_diagnostics'][0]['attempts'][0]
    assert diagnostic['outcome'] == 'truncated_no_answer' and diagnostic['reasoning_characters'] == 17000
    assert 'PRIVATE-REASONING' not in json.dumps(result)
    assert result['assessment'] == 'incomplete'


def summary_module():
    spec = importlib.util.spec_from_file_location('summary', ROOT / 'scripts/summarize_library_eval.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def qualification_report(key):
    fixture = cases(key)
    rows = []
    for case in fixture:
        for trial in (1, 2, 3):
            row = {'case': case['id'], 'trial': trial, 'outcome': 'correct', 'hard_violation': False,
                   'inputs': case['inputs'], 'critical': bool(case.get('critical'))}
            if case.get('rubric'):
                row.update(rubric=case['rubric'], owner_mark=None,
                           review_outputs={'corrected': 'Synthetic output for explicit owner review.'})
            rows.append(row)
    report = {'format_version': 2, 'trials': 3, 'set': 'all', 'execution': 'complete',
              'blocks': {key: {'runs': rows, 'bar': library_eval.bar(rows, fixture, 3)}}}
    library_eval.update_verdict(report)
    return report


@pytest.mark.parametrize('outcome,assessment', [('blocked', 'blocked'), ('evaluator_error', 'blocked'),
                                              ('incorrect', 'failed')])
def test_summary_distinguishes_blocked_critical_trials_from_wrong_answers(outcome, assessment):
    report = qualification_report('table_from_text')
    row = next(row for row in report['blocks']['table_from_text']['runs'] if row['critical'])
    row['outcome'] = outcome
    report['blocks']['table_from_text']['bar'] = library_eval.bar(
        report['blocks']['table_from_text']['runs'], cases('table_from_text'), 3)
    library_eval.update_verdict(report)
    summary = summary_module().summarize(report, ROOT)
    result = summary['blocks']['table_from_text']
    assert result['assessment'] == assessment
    assert summary['reported_verdict_disagrees'] is False
    assert result['critical_cases_blocked'] == ([row['case']] if outcome != 'incorrect' else [])
    assert result['critical_cases_failed'] == ([row['case']] if outcome == 'incorrect' else [])


def test_summary_keeps_targeted_success_separate_from_full_catalog_coverage():
    report = qualification_report('table_from_text')
    result = summary_module().summarize(report, ROOT)
    assert result['selected_blocks_assessment'] == 'passed'
    assert result['assessment'] == 'incomplete' and result['catalog_coverage_complete'] is False
    assert result['reported_verdict_disagrees'] is False
    report['execution'] = 'interrupted'
    report['verdict'] = 'incomplete'
    assert summary_module().summarize(report, ROOT)['selected_blocks_assessment'] == 'incomplete'


def test_case_pacing_adds_no_retries_and_keeps_all_outcomes(ui, server, monkeypatch):
    target(ui, server)
    fixture = [case for case in cases('classify_text') if 'stub' in case]
    server.reply(*(block_answer(**case['stub']) for case in fixture))
    pauses = []
    monkeypatch.setattr(library_eval, 'pause_between_cases', pauses.append)
    report = run(ui, case_delay=2)
    assert pauses == [2, 2] and len(server.requests) == 3
    assert [row['outcome'] for row in report['blocks']['classify_text']['runs']] == ['correct'] * 3
    assert report['case_delay'] == 2


def test_grouped_review_export_does_not_approve_and_import_applies_only_explicit_marks(tmp_path):
    source = tmp_path / 'full.json'
    destination = tmp_path / 'review.json'
    source.write_text(json.dumps(qualification_report('proofread')))
    before = source.read_bytes()
    assert library_eval.main(['--export-review', str(source), '--report', str(destination)]) == 0
    assert source.read_bytes() == before
    review = json.loads(destination.read_text())
    assert len(review['cases']) == 3 and all(case['owner_mark'] is None for case in review['cases'])
    assert all(len(case['trials']) == 3 for case in review['cases'])
    review['cases'][0]['owner_mark'] = True
    destination.write_text(json.dumps(review))
    assert library_eval.main(['--apply-review', str(destination), '--source-report', str(source)]) == 3
    report = json.loads(source.read_text())
    rubric = [row for row in report['blocks']['proofread']['runs'] if 'rubric' in row]
    assert sum(row['owner_mark'] is True for row in rubric) == 3
    assert sum(row['owner_mark'] is None for row in rubric) == 6
    assert report['verdict'] == 'rubric_review_pending'


@pytest.mark.parametrize('problem', ['stale', 'evidence', 'duplicate', 'invalid_mark', 'missing_output'])
def test_review_import_refuses_stale_or_changed_evidence_without_touching_source(tmp_path, problem):
    import library_reviews
    source = tmp_path / 'full.json'
    destination = tmp_path / 'review.json'
    report = qualification_report('proofread')
    if problem == 'missing_output':
        min((row for row in report['blocks']['proofread']['runs'] if 'rubric' in row),
            key=lambda row: (row['case'], row['trial']))['review_outputs'] = None
    source.write_text(json.dumps(report))
    library_reviews.export_review(source, destination)
    review = json.loads(destination.read_text())
    review['cases'][0]['owner_mark'] = True
    if problem == 'stale':
        source.write_text(source.read_text() + '\n')
    elif problem == 'evidence':
        review['cases'][0]['trials'][0]['outputs'] = {'corrected': 'Forged'}
    elif problem == 'duplicate':
        review['cases'][1] = review['cases'][0]
    elif problem == 'invalid_mark':
        review['cases'][0]['owner_mark'] = 1
    destination.write_text(json.dumps(review))
    before = source.read_bytes()
    with pytest.raises(library_reviews.ReviewError):
        library_reviews.apply_review(destination, source)
    assert source.read_bytes() == before


def test_individual_trial_review_can_override_a_group_decision(tmp_path):
    import library_reviews
    source = tmp_path / 'full.json'
    destination = tmp_path / 'review.json'
    source.write_text(json.dumps(qualification_report('proofread')))
    library_reviews.export_review(source, destination)
    review = json.loads(destination.read_text())
    case = review['cases'][0]
    case['owner_mark'] = True
    case['trials'][0]['owner_mark'] = False
    destination.write_text(json.dumps(review))
    result, _, changed = library_reviews.apply_review(destination, source)
    rows = [row for row in result['blocks']['proofread']['runs'] if row['case'] == case['case']]
    assert changed == 3 and [row['owner_mark'] for row in rows] == [False, True, True]
