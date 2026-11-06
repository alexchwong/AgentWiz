"""The headless model-step function, called directly with no HTTP: techspec 0.1.3 test #67 (8.4, 8.5)."""
import pytest
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


def prepare(store, server, *, save_runs=False, contract=None, prompt="hi", system=""):
    return model_step.prepare_first(store, snapshot=snapshot(server), system=system, prompt=prompt,
                                    task=TaskSettings(), source="chat", save_runs=save_runs, contract=contract)


def pong_contract():
    return pinned("c", 4, checks.parse_contract({'format': 'text', 'contains': ['pong']}))


def test_checked_request_instructions_and_repairs_freeze_the_captured_definition(server):
    server.reply(answer('wrong'))
    store = runs.AttemptStore()
    captured = pinned('reply', 7, checks.parse_contract({
        'format': 'text', 'description': 'Literal acknowledgement', 'exact': 'pong'}))
    prepared = prepare(store, server, contract=captured, system='Be brief')
    record = model_step.run_attempt(store, prepared)
    generated = output_protocol.single_value_instructions(captured.parsed)
    assert record['contract']['revision'] == 7 and record['contract']['policy'] == 'user'
    assert record['messages_sent'][0]['content'] == 'Be brief\n\n' + generated
    assert server.requests[0]['body']['messages'] == record['messages_sent']
    repair = model_step.repair_plan(record)
    assert repair['messages'][:2] == record['input_messages']
    assert repair['contract'].parsed.raw['exact'] == 'pong'
    assert 'text.exact' in repair['repair_message']


def test_schema_dispatch_refused_before_run_creation(server, monkeypatch):
    captured = pinned('n', 1, checks.parse_contract({
        'format': 'json', 'schema': {'type': 'integer'}}))
    store = runs.AttemptStore()
    monkeypatch.setattr(checks, 'schema_support', lambda: (False, 'damaged'))
    monkeypatch.setattr(store, 'new_run', lambda **kwargs: pytest.fail('created a run'))
    with pytest.raises(checks.ValidationUnavailable):
        prepare(store, server, contract=captured)
    assert server.requests == []


# 67 ---------------------------------------------------------------------------------------------
def test_run_attempt_returns_the_committed_record(server):
    server.reply(answer("pong"))
    store = runs.AttemptStore()
    prepared = prepare(store, server, contract=pong_contract(), system="be brief")
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
    assert record["check"]["overall"] == "pass" and record["model_called"] is True
    assert record["stop_mode"] is None and record["sent"] is None and record["cancel_reason"] is None
    assert record["profile"] == {"name": "p", "modified": True}
    assert record["endpoint"] == {"kind": "openai_compat", "base_url": server.url + "/v1"}
    generated = output_protocol.single_value_instructions(pong_contract().parsed)
    assert record["input_messages"] == record["messages_sent"] == [{"role": "system", "content": "be brief\n\n" + generated},
                                                                    {"role": "user", "content": "hi"}]
    assert record["contract"]["name"] == "c" and record["contract"]["revision"] == 4
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
    prepared = prepare(store, server, save_runs=True, contract=pong_contract())

    def gone(info):
        raise model_step.PageGone()

    record = model_step.run_attempt(store, prepared, model_step.Callbacks(started=gone), sink)
    assert server.requests == []
    assert record["outcome"] == "stopped" and record["cancel_reason"] == "page_closed"
    assert record["model_called"] is False and record["sent"] is False and record["partial"] is None
    assert record["check"] is None and record["check_skipped"] == "not_completed"
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


def test_a_failing_check_is_contract_error_never_a_pass(server, monkeypatch, capfd):
    server.reply(answer("pong"))
    store = runs.AttemptStore()
    prepared = prepare(store, server, contract=pong_contract())

    def broken(contract, text):
        raise RuntimeError("DETAIL")

    monkeypatch.setattr(checks, "check_answer_value", broken)
    record = model_step.run_attempt(store, prepared)
    assert record["outcome"] == "complete" and record["check"]["overall"] == "contract_error"
    assert record["check"]["issues"][0]["message"] == "the checks failed internally"
    assert "DETAIL" not in "".join(capfd.readouterr())


def test_errors_and_outcomes_are_classified(server):
    store = runs.AttemptStore()
    server.reply(Reply(status=500, body=b'{"error": "SECRET-BACKEND-TEXT"}', headers={"Content-Type": "application/json"}))
    record = model_step.run_attempt(store, prepare(store, server, contract=pong_contract()))
    assert record["outcome"] == "error" and record["error"]["class"] == "BackendError"
    assert record["error"]["status"] == 500 and "SECRET-BACKEND-TEXT" not in repr(record)
    assert record["result"] is None and record["check"] is None and record["check_skipped"] == "not_completed"

    server.reply(answer(""))
    record = model_step.run_attempt(store, prepare(store, server, contract=pong_contract()))
    assert record["outcome"] == "empty" and record["check_skipped"] == "blank_answer"

    server.reply(Reply(body=[delta("pon"), delta(finish="length"), DONE], headers=SSE))
    record = model_step.run_attempt(store, prepare(store, server, contract=pong_contract()))
    assert record["outcome"] == "truncated" and record["check"]["overall"] == "fail"


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
