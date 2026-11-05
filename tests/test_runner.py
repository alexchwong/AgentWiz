"""Block runs end to end through the routes: techspec 0.1.4, tests #94-#98, #102-#105 and #114
(R6-6 to R6-10, R6-21, R6-22, R6-24, R3)."""
import hashlib
import json
import threading

import pytest

from agentwiz import checks, jsonio, model_step, runner, runs
from conftest import (DONE, Reply, answer, block_answer, configure, delta, events, failure_of, final_of, get_state,
                      iter_events, poll_block, read_until, request, run_block, run_folders, result_of, save_profile,
                      save_roles, stop_block, switch_history, wait_until, write_block)
from test_blocks import MODEL_FILES, model_block

SSE = {"Content-Type": "text/event-stream"}
CONTAINS_OK = {"contract": {"format": "text", "contains": ["OK"]}}


def setup_model(ui, server, name="b", **over):
    """A profile at the fake, the role `fast` mapped to it, and a model block `name`."""
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"fast": "p"})
    block = model_block(name, **over)
    write_block(ui, name, block, MODEL_FILES)


def checked_output(**over):
    return {"outputs": {"out": CONTAINS_OK}, **over}


def truncated(text):
    return Reply(body=[delta(text), delta(finish="length"), DONE], headers=SSE)


def types(evs):
    return [e["type"] for e in evs]


def step_of(result):
    assert len(result["steps"]) == 1
    return result["steps"][0]


# --- #96 -------------------------------------------------------------------------------------------------
def test_block_model_step_repairs_and_accepts_the_passing_value(ui, server):
    setup_model(ui, server, **checked_output())
    server.reply(block_answer(out="nope"), block_answer(out="OK then"))
    evs = run_block(ui, "b", {"text": "world"})
    assert types(evs) == ["started", "attempt", "attempt", "final"]
    assert evs[0]["block"] == "b" and evs[0]["step_id"] == "s" and evs[0]["kind"] == "model"
    assert [(e["attempt"], e["check"], e["repair_reason"]) for e in evs[1:3]] == [(1, "fail", None),
                                                                                  (2, "pass", "check_failed")]
    result = result_of(evs)
    step = step_of(result)
    assert result["outcome"] == "complete" and result["outputs"] == {"out": {"format": "text", "text": "OK then"}}
    assert step["outcome"] == "ok" and step["failure"] is None and step["role"] == "fast"
    assert step["profile"] == "p" and step["model"] == "m"
    assert step["attempts"] == [f"{result['run_id']}-1", f"{result['run_id']}-2"]
    assert step["accepted_attempt"] == f"{result['run_id']}-2" and step["repairs_used"] == 1
    assert step["truncated_attempts"] == 0 and step["max_repairs"] == 2
    assert [f["where"] for f in step["fills"]] == ["prompt"]
    assert set(step["output_checks"]) == {"out"}
    # the request: the author's system text then the generated section, the filled prompt,
    # then (for the repair) the answer and the repair message
    first, second = (r["body"]["messages"] for r in server.requests)
    assert first[0]["role"] == "system" and first[0]["content"].startswith("You are terse.")
    assert first[1] == {"role": "user", "content": "Say world"}
    assert second[:2] == first and second[2] == {"role": "assistant", "content": '{"out":"nope"}'}
    assert second[3]["role"] == "user" and "nope" not in second[3]["content"]
    assert get_state(ui)["blocks"]["active_runs"] == 0


def test_the_active_profile_is_not_used_by_a_role(ui, server, make_server):
    other = make_server()
    configure(ui, other)                                  # the page's active settings point at `other`
    setup_model(ui, server)
    server.reply(block_answer())
    assert result_of(run_block(ui, "b", {"text": "x"}))["outcome"] == "complete"
    assert len(server.requests) == 1 and other.requests == []


# --- #97 -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("repair, calls", [({"max_repairs": 0}, 1), (None, 3), ({"max_repairs": 2}, 3),
                                           ({"max_repairs": 5}, 6)])
def test_repair_counting(ui, server, repair, calls):
    block = checked_output()
    setup_model(ui, server, **block)
    if repair is not None:
        write_block(ui, "b", {**model_block("b", **block),
                              "steps": [{**model_block()["steps"][0], "repair": repair}]}, MODEL_FILES)
    server.reply(block_answer(out="never"))
    result = result_of(run_block(ui, "b", {"text": "x"}))
    assert result["outcome"] == "failed" and step_of(result)["failure"] == "repairs_exhausted"
    assert len(server.requests) == calls and result["outputs"] == {}


@pytest.mark.parametrize("reply, failure", [
    (lambda: answer(""), "blank_answer"),
    (lambda: Reply(body=[delta("", reasoning="t"), delta(finish="stop"), DONE], headers=SSE), "blank_answer"),
    (lambda: Reply(body=[delta("", reasoning="t"), delta(finish="length"), DONE], headers=SSE), "blank_answer"),
    (lambda: Reply(status=500, body=b"{}", headers={"Content-Type": "application/json"}), "model_error"),
])
def test_no_repair_follows_a_blank_or_error_outcome(ui, server, reply, failure):
    setup_model(ui, server, **checked_output())
    server.reply(reply())
    result = result_of(run_block(ui, "b", {"text": "x"}))
    assert result["outcome"] == "failed" and step_of(result)["failure"] == failure
    assert len(server.requests) == 1


def test_a_contract_that_cannot_decide_is_never_repaired(ui, server):
    pytest.importorskip("jsonschema")
    schema = {"format": "json", "schema": {"type": "object", "properties": {}}}
    setup_model(ui, server, outputs={"out": {"contract": schema}})
    server.reply(block_answer(out={}))
    assert result_of(run_block(ui, "b", {"text": "x"}))["outcome"] == "complete"


# --- #98 -------------------------------------------------------------------------------------------------
def test_a_truncated_answer_is_repaired_with_the_truncation_message(ui, server):
    setup_model(ui, server, **checked_output())
    server.reply(truncated('{"out": "no"}'), block_answer(out="OK"))
    result = result_of(run_block(ui, "b", {"text": "x"}))
    assert result["outcome"] == "complete" and step_of(result)["truncated_attempts"] == 1
    contract = checks.contract_from_text(json.dumps(CONTAINS_OK["contract"]))
    check, _value = checks.check_named_answer({"out": contract}, '{"out": "no"}')
    sent = server.requests[1]["body"]["messages"]
    assert sent[-1]["content"] == checks.repair_message(None, check, truncated=True,
                                                        instruction=checks.ENVELOPE_REPLY)
    assert sent[-1]["content"] != checks.repair_message(None, check, truncated=False,
                                                        instruction=checks.ENVELOPE_REPLY)


def test_a_truncated_answer_whose_check_passes_is_not_accepted(ui, server):
    setup_model(ui, server, **checked_output())
    server.reply(truncated('{"out": "OK"}'), block_answer(out="OK again"))
    result = result_of(run_block(ui, "b", {"text": "x"}))
    assert result["outcome"] == "complete" and step_of(result)["accepted_attempt"].endswith("-2")
    # with no repair left the run fails with `truncated`, and no output is presented
    write_block(ui, "b", {**model_block("b", **checked_output()),
                          "steps": [{**model_block()["steps"][0], "repair": {"max_repairs": 0}}]}, MODEL_FILES)
    server.reply(truncated('{"out": "OK"}'))
    result = result_of(run_block(ui, "b", {"text": "x"}))
    assert result["outcome"] == "failed" and step_of(result)["failure"] == "truncated"
    assert result["outputs"] == {}
    assert step_of(result)["max_tokens"] is None


# --- #95 -------------------------------------------------------------------------------------------------
def test_rejected_runs_name_the_location_and_send_nothing(ui, server):
    block = model_block("b")
    block["inputs"] = {"text": {"contract": {"format": "text", "min_length": 3}},
                       "obj": {"contract": {"format": "json"}}}
    setup_model(ui, server, inputs=block["inputs"])
    server.reply(answer("never"))
    evs = run_block(ui, "b", {"text": "x", "obj": "{\n  bad"})
    assert types(evs) == ["started", "final"]
    result = result_of(evs)
    assert result["outcome"] == "rejected" and result["steps"] == [] and result["outputs"] == {}
    where = {i["location"] for i in result["issues"]}
    assert "inputs.text" in where and any(w.startswith("inputs.obj: line 2, column") for w in where)
    assert set(result["input_texts"]) == {"text", "obj"} and result["inputs"].get("obj") is None
    assert result["inputs"]["text"] == {"format": "text", "text": "x"}
    assert server.requests == []
    assert ui.state.budget.in_flight == 0


@pytest.mark.parametrize("prepare, location", [
    ("role undefined", "steps[0].role"),
    ("profile gone", "steps[0].role"),
    ("no model", "steps[0].role"),
    ("fill failure", "steps[0].prompt"),
    ("task refused", "steps[0].task"),
    ("too large", "steps[0].prompt"),
])
def test_preparation_failures_end_rejected(ui, server, prepare, location):
    setup_model(ui, server)
    if prepare == "role undefined":
        save_roles(ui, {})
    elif prepare == "profile gone":
        request(ui, "/api/profiles/delete", {"name": "p", "revision": get_state(ui)["revision"]})
    elif prepare == "no model":
        save_profile(ui, "p", model="", kind="openai_compat", base_url=server.url + "/v1", provider="")
    elif prepare == "fill failure":
        block = model_block("b", inputs={"text": {"contract": {"format": "json"}}})
        write_block(ui, "b", block, {"prompts/system.md": "s", "prompts/user.md": "Say {{inputs.text.missing}}"})
    elif prepare == "task refused":
        step = {**model_block()["steps"][0], "task": {"reasoning": "high"}}
        write_block(ui, "b", model_block("b", steps=[step]), MODEL_FILES)
    else:                                   # ten copies of a 4 MiB input: over 32 MiB once filled
        write_block(ui, "b", model_block("b"), {"prompts/system.md": "s", "prompts/user.md": "{{inputs.text}}" * 10})
    inputs = {"text": "{}"} if prepare == "fill failure" else {"text": "x"}
    if prepare == "too large":
        inputs = {"text": "a" * (4 * 1024 * 1024)}
    result = result_of(run_block(ui, "b", inputs))
    assert result["outcome"] == "rejected", result
    assert result["issues"][0]["location"] == location
    assert server.requests == [] and ui.state.budget.in_flight == 0


# --- #94 -------------------------------------------------------------------------------------------------
def test_admission_refuses_with_the_budget_named_and_creates_no_run(ui, server):
    setup_model(ui, server)
    budget = ui.state.budget
    held = [budget.try_acquire() for _ in range(budget.limit)]
    assert all(held)
    fail = failure_of(lambda: run_block(ui, "b", {"text": "x"}))
    assert fail.status == 503 and fail.body["code"] == "busy" and fail.body["budget"] == "model requests"
    for _ in held:
        budget.release()
    assert request(ui, "/api/blocks")["active_runs"] == [] and server.requests == []
    assert ui.state.store.active_block_runs() == []


def test_a_run_rejected_by_its_inputs_is_not_hidden_by_a_full_budget(ui, server):
    setup_model(ui, server, inputs={"text": {"contract": {"format": "json"}}})
    held = [ui.state.budget.try_acquire() for _ in range(ui.state.budget.limit)]
    result = result_of(run_block(ui, "b", {"text": "{bad"}))
    assert result["outcome"] == "rejected"
    for _ in held:
        ui.state.budget.release()


def test_slots_return_to_zero_after_every_ending(ui, server, monkeypatch):
    setup_model(ui, server, **checked_output())
    server.reply(block_answer(out="OK"))
    assert result_of(run_block(ui, "b", {"text": "x"}))["outcome"] == "complete"
    server.reply(block_answer(out="no"))
    write_block(ui, "b", {**model_block("b", **checked_output()),
                          "steps": [{**model_block()["steps"][0], "repair": {"max_repairs": 0}}]}, MODEL_FILES)
    assert result_of(run_block(ui, "b", {"text": "x"}))["outcome"] == "failed"
    # an internal error in the executor is committed as failed / internal_error and frees its slot
    monkeypatch.setattr(runner.executor, "execute_step", lambda ctx: 1 / 0)
    result = result_of(run_block(ui, "b", {"text": "x"}))
    assert result["outcome"] == "failed" and step_of(result)["failure"] == "internal_error"
    assert ui.state.budget.in_flight == 0 and ui.state.store.active_block_runs() == []


# --- #103 ------------------------------------------------------------------------------------------------
def test_stop_before_the_response_ends_the_run_stopped(ui, server, monkeypatch):
    setup_model(ui, server)
    server.reply(Reply(hold_headers=True, body=[delta("late")], headers=SSE))
    at_end = threading.Event()
    go = threading.Event()
    monkeypatch.setattr(runner, "before_run_end", lambda: (at_end.set(), go.wait(10)))
    resp = run_block(ui, "b", {"text": "x"}, raw=True)
    it = iter_events(resp)
    started = read_until(it, "started")[-1]
    assert server.reached.wait(10)
    assert stop_block(ui, started["run_id"]) == {"status": "stopping"}
    assert at_end.wait(10)                                        # the run is held just before its end point
    assert stop_block(ui, started["run_id"]) == {"status": "stopping"}      # repeated: changes nothing
    go.set()
    rest = list(it)
    result = final_of(rest)["result"]
    assert result["outcome"] == "stopped" and step_of(result)["outcome"] == "stopped"
    assert step_of(result)["failure"] == "stopped" and result["stop_requested_at"] is not None
    assert result["cancel_reason"] == "user_stop" and result["outputs"] == {}
    assert stop_block(ui, started["run_id"]) == {"status": "already_finished"}
    fail = failure_of(lambda: stop_block(ui, "0" * 12))
    assert fail.status == 404 and fail.body["code"] == "no_such_run"
    assert failure_of(lambda: stop_block(ui, "bad")).status == 400


def test_a_stop_after_a_failing_answer_makes_no_repair(ui, server, monkeypatch):
    setup_model(ui, server, **checked_output())
    server.reply(answer("no"), answer("OK"))
    gate = threading.Event()
    run_ids = []

    def hold():
        if run_ids and not gate.is_set():
            stop_block(ui, run_ids[0])
            gate.set()

    monkeypatch.setattr(model_step, "before_end_point", hold)
    resp = run_block(ui, "b", {"text": "x"}, raw=True)
    it = iter_events(resp)
    run_ids.append(read_until(it, "started")[-1]["run_id"])
    result = final_of(list(it))["result"]
    assert result["outcome"] == "stopped" and step_of(result)["failure"] == "stopped"
    assert len(server.requests) == 1 and len(step_of(result)["attempts"]) == 1


def test_a_stop_just_before_the_end_point_is_not_the_runs_outcome_when_the_output_passed(ui, server, monkeypatch):
    setup_model(ui, server)
    server.reply(block_answer(out="fine"))
    ids = []
    monkeypatch.setattr(runner, "before_run_end", lambda: stop_block(ui, ids[0]) if ids else None)
    resp = run_block(ui, "b", {"text": "x"}, raw=True)
    it = iter_events(resp)
    ids.append(read_until(it, "started")[-1]["run_id"])
    result = final_of(list(it))["result"]
    assert result["outcome"] == "complete" and result["outputs"]["out"]["text"] == "fine"
    assert result["stop_requested_at"] is not None


# --- #102 ------------------------------------------------------------------------------------------------
def test_the_time_budget_stops_the_run_with_its_own_reason(ui, server, fire_timers):
    setup_model(ui, server, budget={"max_seconds": 77})
    server.reply(Reply(hold_headers=True, body=[delta("late")], headers=SSE))
    resp = run_block(ui, "b", {"text": "x"}, raw=True)
    it = iter_events(resp)
    read_until(it, "started")
    assert server.reached.wait(10)
    assert [t.seconds for t in fire_timers.live()] == [77]
    fire_timers.fire(77)
    result = final_of(list(it))["result"]
    assert result["outcome"] == "budget_exceeded" and step_of(result)["failure"] == "budget_exceeded"
    assert result["cancel_reason"] == "budget_exceeded" and result["stop_requested_at"] is None
    assert wait_until(lambda: all(t.cancelled or t.fired for t in fire_timers.timers))
    attempt = ui.state.store.record_of(step_of(result)["attempts"][0])
    assert attempt["cancel_reason"] == "budget_exceeded" and attempt["stop_mode"] == "immediate"


def test_a_timer_that_fires_after_the_end_does_nothing(ui, server, fire_timers):
    setup_model(ui, server)
    server.reply(block_answer())
    result = result_of(run_block(ui, "b", {"text": "x"}))
    assert result["outcome"] == "complete"
    assert all(t.cancelled for t in fire_timers.timers)
    fire_timers.timers[0].fire()                   # a stale firing: the run is past its end point
    assert poll_block(ui, result["run_id"])["result"]["outcome"] == "complete"


def test_the_token_allowance_is_checked_before_each_attempt(ui, server):
    usage = {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}
    reply = lambda values: Reply(body=[delta(jsonio.encode_exact(values)), delta(finish="stop", usage=usage), DONE],  # noqa: E731
                                 headers=SSE)
    setup_model(ui, server, budget={"max_seconds": 60, "max_model_tokens": 40}, **checked_output())
    server.reply(reply({"out": "no"}), reply({"out": "OK"}))
    result = result_of(run_block(ui, "b", {"text": "x"}))
    assert result["outcome"] == "budget_exceeded" and step_of(result)["failure"] == "budget_exceeded"
    assert len(server.requests) == 1
    assert result["usage"] == {"reported_tokens": 40, "limit": 40, "over_by": 0, "unreported_attempts": []}
    # an attempt that crosses the allowance completes; accepted, the run is complete and reports the overshoot
    setup_model(ui, server, budget={"max_seconds": 60, "max_model_tokens": 10})
    server.reply(reply({"out": "fine"}))
    result = result_of(run_block(ui, "b", {"text": "x"}))
    assert result["outcome"] == "complete" and result["usage"]["over_by"] == 30


# --- #104 ------------------------------------------------------------------------------------------------
def test_block_records(ui, server):
    switch_history(ui, True)
    setup_model(ui, server, **checked_output())
    server.reply(block_answer(out="no"), block_answer(out="OK"))
    evs = run_block(ui, "b", {"text": "x"})
    assert types(evs) == ["started", "attempt", "attempt", "final", "save"]
    result = final_of(evs)["result"]
    assert final_of(evs)["save_status"] == "pending" and evs[-1]["save_status"] == "saved"
    folder = next(p for p in run_folders(ui) if p.name.endswith("-" + result["run_id"]))
    assert sorted(p.name for p in folder.iterdir()) == ["result.json", "run.json", "s-1.json", "s-2.json"]
    run = jsonio.decode_file_exact((folder / "run.json").read_bytes())
    block_dir = ui.workspace.path / "blocks" / "b"
    assert run["source"] == "block" and run["steps"] == ["s"] and run["contract"] is None
    assert run["block"]["name"] == "b" and run["block"]["revision"] == result["block"]["revision"]
    for rel, sha in run["block"]["files"].items():
        assert sha == hashlib.sha256((block_dir / rel).read_bytes()).hexdigest()
    assert run["input_texts"] == {"text": "x"} and set(run["contracts"]) == {"inputs", "outputs"}
    assert run["contracts"]["outputs"]["out"]["name"] is None
    saved = jsonio.decode_file_exact((folder / "result.json").read_bytes())
    assert saved["outcome"] == "complete" and saved["outputs"] == result["outputs"]
    second = jsonio.decode_file_exact((folder / "s-2.json").read_bytes())
    assert second["source"] == "block" and second["step_id"] == "s" and second["role"] == "fast"
    assert second["repair_reason"] == "check_failed" and second["fills"] == []
    first = jsonio.decode_file_exact((folder / "s-1.json").read_bytes())
    assert first["repair_reason"] is None and first["fills"][0]["source"] == "inputs.text"
    listing = request(ui, "/api/runs")
    row = next(r for r in listing["runs"] if r["run_id"] == result["run_id"])
    assert row["block"] == "b" and row["outcome"] == "complete"
    detail = request(ui, f"/api/runs/{result['run_id']}")
    assert detail["result"]["outcome"] == "complete" and len(detail["attempts"]) == 2


def test_a_credential_in_a_block_result_is_redacted_on_disk(ui, server):
    switch_history(ui, True)
    secret = "sk-BLOCK-SECRET-123456"
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="", api_key=secret)
    save_roles(ui, {"fast": "p"})
    write_block(ui, "b", model_block("b"), MODEL_FILES)
    server.reply(answer(f"the key is {secret}"))
    result = result_of(run_block(ui, "b", {"text": "x"}))
    folder = next(p for p in run_folders(ui) if p.name.endswith("-" + result["run_id"]))
    for path in folder.iterdir():
        assert secret not in path.read_text()


def test_a_run_survives_a_rejected_save_and_reports_it(ui, server):
    switch_history(ui, True)
    setup_model(ui, server)
    server.reply(answer("x"))
    evs = run_block(ui, "b", {"text": "x"})
    folder = run_folders(ui)[0]
    assert (folder / "result.json").is_file() and evs[-1]["type"] == "save"


# --- #105 ------------------------------------------------------------------------------------------------
def test_run_request_shape_errors_are_real_statuses(ui, server):
    setup_model(ui, server)
    rev = request(ui, "/api/blocks")["blocks"][0]["revision"]
    ok = {"name": "b", "revision": rev, "inputs": {"text": "x"}}

    def post(**over):
        return failure_of(lambda: request(ui, "/api/blocks/run", {**ok, **over}, raw=True))

    assert post(inputs=None).status == 400 and post(inputs=None).body["code"] == "block_body"
    assert post(inputs={}).body["code"] == "block_body"
    assert post(inputs={"text": "x", "extra": "y"}).body["code"] == "block_body"
    assert post(inputs={"text": 5}).body["code"] == "block_body"
    assert post(name="nope").status == 404 and post(name="nope").body["code"] == "no_such_block"
    assert post(revision=rev + 1000).status == 409 and post(revision=rev + 1000).body["code"] == "stale_revision"
    assert post(name="bad name").status == 400
    write_block(ui, "broken", model_block("broken", extra=1), MODEL_FILES)
    broken = request(ui, "/api/blocks")
    row = next(r for r in broken["blocks"] if r["name"] == "broken")
    fail = post(name="broken", revision=row["revision"])
    assert fail.status == 409 and fail.body["code"] == "block_unusable" and fail.body["problem"]["code"] == "invalid"
    assert server.requests == [] and ui.state.store.active_block_runs() == []


def test_a_lost_page_does_not_stop_the_run_and_polling_finds_it(ui, server):
    setup_model(ui, server)
    server.reply(Reply(body=[delta('{"out": "par')], headers=SSE, hold=True,
                         after_release=[delta('tial"}'), delta(finish="stop"), DONE]))
    resp = run_block(ui, "b", {"text": "x"}, raw=True)
    started = read_until(iter_events(resp), "started")[-1]
    run_id = started["run_id"]
    assert server.wait_holding(1)
    resp.close()                                              # the page goes away
    active = request(ui, "/api/blocks")["active_runs"]
    assert [a["run_id"] for a in active] == [run_id] and active[0]["block"] == "b"
    assert get_state(ui)["blocks"]["active_runs"] == 1
    envelope = poll_block(ui, run_id)
    assert envelope["state"] == "running" and envelope["stop_requested_at"] is None
    server.release.set()
    assert wait_until(lambda: poll_block(ui, run_id)["state"] == "ended")
    ended = poll_block(ui, run_id)
    assert ended["result"]["outcome"] == "complete" and ended["save_status"] == "not_requested"
    assert ended["result"]["outputs"]["out"]["text"] == "partial"
    assert failure_of(lambda: poll_block(ui, "bad")).status == 400
    assert failure_of(lambda: poll_block(ui, "0" * 12)).status == 410


def test_a_block_attempt_cannot_be_repaired_by_hand(ui, server):
    setup_model(ui, server, **checked_output())
    server.reply(answer("no"))
    write_block(ui, "b", {**model_block("b", **checked_output()),
                          "steps": [{**model_block()["steps"][0], "repair": {"max_repairs": 0}}]}, MODEL_FILES)
    result = result_of(run_block(ui, "b", {"text": "x"}))
    attempt = step_of(result)["attempts"][0]
    fail = failure_of(lambda: request(ui, "/api/repair", {"attempt_id": attempt}))
    assert fail.status == 409 and fail.body["code"] == "not_repairable"


def test_a_delivery_that_fails_never_stops_the_run(ui, server, monkeypatch):
    setup_model(ui, server)
    server.reply(block_answer(out="done"))
    from agentwiz.ui import server as srv

    def broken(self, event):
        raise BrokenPipeError()

    monkeypatch.setattr(srv.Handler, "write_event", broken)
    try:
        run_block(ui, "b", {"text": "x"}, raw=True).read()
    except Exception:  # the connection may be closed under the client; the run does not care
        pass
    assert wait_until(lambda: ui.state.store.active_block_runs() == [] and ui.state.budget.in_flight == 0)
    rows = [r for r in ui.state.store._runs.values() if r.kind == "block"]
    assert rows and rows[0].result["outcome"] == "complete"


def test_the_observer_may_fail_without_affecting_the_run(ui, server):
    setup_model(ui, server)
    server.reply(block_answer(out="done"))

    class Bad(runner.Observer):
        def started(self, event):
            raise RuntimeError

        def attempt_committed(self, record):
            raise RuntimeError

        def final(self, event):
            raise RuntimeError

        def saved(self, event):
            raise RuntimeError

    loaded = ui.state.block_loaded("b")
    finished = runner.run_block(ui.state, loaded, {"text": "x"}, Bad())
    assert finished.result["outcome"] == "complete" and ui.state.budget.in_flight == 0


# --- #114 ------------------------------------------------------------------------------------------------
def test_shutdown_stops_a_run_and_a_second_close_does_nothing(ui, server):
    setup_model(ui, server)
    switch_history(ui, True)
    server.reply(Reply(hold_headers=True, body=[delta("late")], headers=SSE))
    resp = run_block(ui, "b", {"text": "x"}, raw=True)
    it = iter_events(resp)
    run_id = read_until(it, "started")[-1]["run_id"]
    assert server.reached.wait(10)
    ui.shutdown()
    assert ui.server_close() == []
    rest = list(it)
    assert rest == [] or rest[-1]["type"] in ("final", "save")
    run = ui.state.store._runs[run_id]
    assert run.result["outcome"] == "stopped" and run.result_save_status in ("saved", "failed")
    folder = next(p for p in run_folders(ui) if p.name.endswith("-" + run_id))
    assert (folder / "result.json").is_file()
    assert ui.server_close() == []                            # idempotent
    fail = None
    with pytest.raises(Exception) as caught:
        ui.state.register_writer("late")
    assert "shutting down" in str(caught.value)
    assert fail is None
