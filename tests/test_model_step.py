"""The headless model-step function, called directly with no HTTP: techspec 0.1.3 test #67 (8.4, 8.5)."""
from modelspell import Endpoint

from agentwiz import checks, model_step, output_protocol, runs
from agentwiz.model_call import RequestSnapshot
from agentwiz.task_settings import TaskSettings
from conftest import pinned, Reply, answer, delta, DONE

SSE = {"Content-Type": "text/event-stream"}


class Sink:
    def __init__(self, status="saved"):
        self.calls, self.status = [], status

    def write_attempt(self, run, record):
        self.calls.append((run.run_id, record["id"]))
        return {"save_status": self.status, "file": "runs/x/request-1.json"} if self.status == "saved" \
            else {"save_status": "failed", "reason": "no"}


def snapshot(server, kind="openai_compat"):
    return RequestSnapshot(endpoint=Endpoint(kind, server.url + "/v1"), model="m", kind=kind, provider="",
                           profile_name="p", profile_modified=True)


def prepare(store, server, *, save_runs=False, prompt="hi", system=""):
    return model_step.prepare_first(store, snapshot=snapshot(server), system=system, prompt=prompt,
                                    task=TaskSettings(), source="chat", save_runs=save_runs)


# 67 ---------------------------------------------------------------------------------------------
def test_run_attempt_returns_the_committed_record(server):
    server.reply(answer("pong"))
    store = runs.AttemptStore()
    prepared = prepare(store, server, system="be brief")
    events = []
    callbacks = model_step.Callbacks(started=lambda info: events.append(("started", info)),
                                     text=lambda t: events.append(("text", t)),
                                     committed=lambda rec, status: events.append(("committed", status)))
    record = model_step.run_attempt(store, prepared, callbacks)
    aid = runs.attempt_id(prepared.run.run_id, 1)
    assert events[0] == ("started", {"run_id": prepared.run.run_id, "step_id": "request", "attempt": 1,
                                     "attempt_id": aid})
    assert events[-1] == ("committed", "not_requested")
    assert record["id"] == aid and record["outcome"] == "complete" and record["result"]["text"] == "pong"
    assert record["check"] is None and record["contract"] is None and record["model_called"] is True
    assert record["stop_mode"] is None and record["sent"] is None and record["cancel_reason"] is None
    assert record["profile"] == {"name": "p", "modified": True}
    assert record["endpoint"] == {"kind": "openai_compat", "base_url": server.url + "/v1"}
    assert record["input_messages"] == record["messages_sent"] == [{"role": "system", "content": "be brief"},
                                                                    {"role": "user", "content": "hi"}]
    assert record["parent_id"] is None and record["save_run"] is False
    assert store.record_of(aid) == record
    assert "api_key" not in repr(record) and "Bearer" not in repr(record)


def test_the_sink_is_called_once_when_saved_and_never_otherwise(server):
    server.reply(answer("pong"))
    store, sink = runs.AttemptStore(), Sink()
    saved = prepare(store, server, save_runs=True)
    committed = []
    record = model_step.run_attempt(store, saved, model_step.Callbacks(committed=lambda r, s: committed.append(s)), sink)
    assert sink.calls == [(saved.run.run_id, record["id"])] and committed == ["pending"]
    assert store.envelope(record["id"])["save_status"] == "saved"

    not_saved = prepare(store, server, save_runs=False)
    model_step.run_attempt(store, not_saved, model_step.Callbacks(), sink)
    assert len(sink.calls) == 1                                                       # never for an unsaved run
    failed = Sink("failed")
    again = prepare(store, server, save_runs=True)
    rec = model_step.run_attempt(store, again, model_step.Callbacks(), failed)
    assert store.envelope(rec["id"])["save_status"] == "failed" and store.envelope(rec["id"])["reason"] == "no"


def test_a_sink_that_raises_settles_the_save_as_failed(server, capfd):
    class Broken:
        def write_attempt(self, run, record):
            raise RuntimeError("SECRET-DETAIL")

    server.reply(answer("pong"))
    store = runs.AttemptStore()
    prepared = prepare(store, server, save_runs=True)
    record = model_step.run_attempt(store, prepared, model_step.Callbacks(), Broken())
    env = store.envelope(record["id"])
    assert env["save_status"] == "failed" and "SECRET-DETAIL" not in repr(env)
    assert "SECRET-DETAIL" not in "".join(capfd.readouterr())                         # class and locations only


def test_started_raising_page_gone_never_calls_the_model(server):
    server.reply(answer("never sent"))
    store, sink = runs.AttemptStore(), Sink()
    prepared = prepare(store, server, save_runs=True)

    def gone(info):
        raise model_step.PageGone()

    record = model_step.run_attempt(store, prepared, model_step.Callbacks(started=gone), sink)
    assert server.requests == []
    assert record["outcome"] == "stopped" and record["cancel_reason"] == "page_closed"
    assert record["model_called"] is False and record["sent"] is False and record["partial"] is None
    assert record["check"] is None
    assert len(sink.calls) == 1                                                       # the save policy still applies


def test_a_cancelled_token_ends_before_anything_is_sent(server):
    server.reply(answer("never sent"))
    store = runs.AttemptStore()
    prepared = prepare(store, server)
    prepared.run.token.cancel()
    record = model_step.run_attempt(store, prepared)
    assert server.requests == []
    assert record["outcome"] == "stopped" and record["cancel_reason"] == "user_stop"
    assert record["stop_mode"] == "immediate" and record["sent"] is False and record["model_called"] is True


def test_the_end_point_and_commit_happen_once_even_when_a_callback_fails_unexpectedly(server):
    server.reply(answer("pong"))
    store = runs.AttemptStore()
    prepared = prepare(store, server)

    def boom(text):
        raise ValueError("a bug in the caller")

    record = model_step.run_attempt(store, prepared, model_step.Callbacks(text=boom))
    assert record["outcome"] in ("error", "stopped")                                  # committed, not left running
    assert store.envelope(record["id"])["state"] == "committed"
    assert store.run_view(prepared.run.run_id) is None or store.run_view(prepared.run.run_id)["running"] is None


def test_stop_between_the_return_and_the_end_point_keeps_the_answer(server):
    server.reply(answer("pong"))
    store = runs.AttemptStore()
    prepared = prepare(store, server)
    aid = runs.attempt_id(prepared.run.run_id, 1)
    original = model_step.before_end_point
    model_step.before_end_point = lambda: store.stop(aid)
    try:
        record = model_step.run_attempt(store, prepared)
    finally:
        model_step.before_end_point = original
    assert record["outcome"] == "complete" and record["stop_requested_at"] is not None
    assert record["stop_mode"] is None and record["cancel_reason"] is None           # the answer finished first
    assert prepared.run.token.cancelled is True


# --- 0.2.3 (spec v15, 4.2): ports of the Checked request cases to a block's named-envelope plan ---------------------
def prepare_block(store, server, contract, *, system="", prompt="hi", snap=None):
    """Attempt 1 of a new block run, prepared by `prepare_block_first` with a one-output plan (`out`) that
    captures `contract`, and the messages the runner would build for it."""
    from agentwiz import model_call
    run = store.new_block_run(save_runs=False, block={"name": "b"}, inputs={}, input_texts={}, contracts={},
                              budget={}, step_id="request")
    number = store.reserve_attempt(run)
    plan = model_step.OutputPlan((("out", "", contract),))
    generated = output_protocol.instructions(plan.specs())
    messages = model_call.build_messages(f"{system}\n\n{generated}" if system else generated, prompt)
    snap = snap or snapshot(server)
    task = TaskSettings()
    return model_step.prepare_block_first(run, number, snapshot=snap, task=task,
                                          arguments=model_call.task_arguments(task, snap.kind, snap.provider),
                                          messages=messages, plan=plan, role="fast", fills=[])


def block_contract(rule=None):
    return pinned("c", 4, checks.parse_contract(rule or {"format": "text", "contains": ["pong"]}))


def test_automatic_block_repair_retains_selected_policy(server):
    import json
    endpoint = {"tag": "eligible", "provider_name": "eligible",
                "throughput_last_30m": {"p50": 80}, "latency_last_30m": {"p50": 0.1},
                "pricing": {"prompt": "0.0000001", "completion": "0.0000001"},
                "supported_parameters": ["max_tokens"]}
    probe = {"provider": "eligible", "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
    server.reply(Reply(body=json.dumps({"data": {"endpoints": [endpoint]}}).encode()),
                 Reply(body=json.dumps(probe).encode()), answer('{"out":"wrong"}'), answer('{"out":"pong"}'))
    snap = RequestSnapshot(Endpoint("openrouter", server.url, api_key="test-key"), "example/model",
                           "openrouter", "", routing_mode="automatic", routing_data_collection="deny")
    store = runs.AttemptStore()
    first = prepare_block(store, server, block_contract({"format": "text", "exact": "pong"}), prompt="ping", snap=snap)
    parent = model_step.run_attempt(store, first)
    assert parent["check"]["overall"] == "fail"
    number = store.reserve_attempt(first.run)
    repaired = model_step.prepare_repair(first.run, number, parent, lambda *_: "test-key")
    result = model_step.run_attempt(store, repaired)
    assert result["check"]["overall"] == "pass"
    assert result["routing"] == parent["routing"]
    posts = [r["body"] for r in server.requests if r["method"] == "POST"]
    assert len(posts) == 3
    assert posts[-1]["provider"] == posts[-2]["provider"]


def test_block_instructions_and_repairs_freeze_the_captured_definition(server):
    server.reply(answer('{"out":"wrong"}'))
    store = runs.AttemptStore()
    captured = pinned('reply', 7, checks.parse_contract({
        'format': 'text', 'description': 'Literal acknowledgement', 'exact': 'pong'}))
    prepared = prepare_block(store, server, captured, system='Be brief')
    record = model_step.run_attempt(store, prepared)
    assert record['check']['overall'] == 'fail'
    generated = output_protocol.instructions(prepared.plan.specs())
    assert record['messages_sent'][0]['content'] == 'Be brief\n\n' + generated
    assert server.requests[0]['body']['messages'] == record['messages_sent']
    repair = model_step.repair_plan(record)
    assert repair['messages'][:2] == record['input_messages']
    assert repair['plan'].contracts()['out'].raw['exact'] == 'pong'
    assert repair['repair_message'] in [m['content'] for m in repair['messages']]


def test_a_failing_block_check_is_contract_error_never_a_pass(server, monkeypatch, capfd):
    server.reply(answer('{"out":"pong"}'))
    store = runs.AttemptStore()
    prepared = prepare_block(store, server, block_contract())

    def broken(contracts, text):
        raise RuntimeError("DETAIL")

    monkeypatch.setattr(checks, "check_named_answer", broken)
    record = model_step.run_attempt(store, prepared)
    assert record["outcome"] == "complete" and record["check"]["overall"] == "contract_error"
    assert record["check"]["issues"][0]["message"] == "the checks failed internally"
    assert "DETAIL" not in "".join(capfd.readouterr())


def test_block_errors_and_outcomes_are_classified(server):
    store = runs.AttemptStore()
    server.reply(Reply(status=500, body=b'{"error": "SECRET-BACKEND-TEXT"}', headers={"Content-Type": "application/json"}))
    record = model_step.run_attempt(store, prepare_block(store, server, block_contract()))
    assert record["outcome"] == "error" and record["error"]["class"] == "BackendError"
    assert record["error"]["status"] == 500 and "SECRET-BACKEND-TEXT" not in repr(record)
    assert record["result"] is None and record["check"] is None and record["check_skipped"] == "not_completed"

    server.reply(answer(""))
    record = model_step.run_attempt(store, prepare_block(store, server, block_contract()))
    assert record["outcome"] == "empty" and record["check_skipped"] == "blank_answer"

    server.reply(Reply(body=[delta('{"out": "pon'), delta(finish="length"), DONE], headers=SSE))
    record = model_step.run_attempt(store, prepare_block(store, server, block_contract()))
    assert record["outcome"] == "truncated" and record["check"]["overall"] == "fail"


def test_block_schema_dispatch_refused_before_run_creation(ui, server, monkeypatch):
    """§7: an output contract needing schema validation, with schema support unavailable, is refused before a run
    is created, and nothing is sent."""
    from conftest import failure_of, run_block
    from test_runner import setup_model
    setup_model(ui, server, outputs={"out": {"contract": {"format": "json", "schema": {"type": "integer"}}}})
    monkeypatch.setattr(checks, "schema_support", lambda: (False, "damaged"))
    before = ui.state.store.running_count()
    refusal = failure_of(lambda: run_block(ui, "b", {"text": "x"}))
    assert refusal.status == 503 and refusal.body["code"] == "validation_unavailable", refusal.body
    assert server.requests == [] and ui.state.store.running_count() == before
    assert not (ui.workspace.path / "runs").exists() or not any((ui.workspace.path / "runs").iterdir())
