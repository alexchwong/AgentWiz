"""Runs, attempts, Stop, polling and repair through HTTP: techspec 0.1.3 tests #68-#72, #74 and #75
(Part B, 8, 9). Every ordering is forced with the fake backend's holds and the code's hooks, never timing."""
import json
import socket
import threading

import pytest

from agentwiz import history, model_step, runs
from agentwiz.ui import server as server_module
from conftest import (contract_pin, DONE, TEXT_CONTRACT, Reply, answer, complete, configure,
                      delta, failure_of, final_of, get_state, iter_events, poll_attempt,
                      read_run_files, read_until, record_of, request, run_folders, save_contract,
                      status_of, stop_attempt, switch_history, wait_until)

SSE = {"Content-Type": "text/event-stream"}


def start_chat(ui, prompt="hi", **kw):
    rev = get_state(ui)["revision"]
    return request(ui, "/api/complete", {"source": "chat", "prompt": prompt, "system": "", "revision": rev, **kw},
                   raw=True)


def new_folder(ui, known):
    """The one run folder that was not there before (folder names sort by the second, so not by newest)."""
    assert wait_until(lambda: len(set(run_folders(ui)) - known) == 1)
    folder = (set(run_folders(ui)) - known).pop()
    known.add(folder)
    return folder


# 68 ---------------------------------------------------------------------------------------------
def test_ids_are_random_and_routes_check_their_form(ui, server):
    server.reply(answer("ok"))
    configure(ui, server)
    a, b = complete(ui, "hi"), complete(ui, "hi")
    ids = {a[0]["run_id"], b[0]["run_id"]}
    assert len(ids) == 2 and all(len(i) == 12 and i == i.lower() for i in ids)
    for bad in ("", "nope", "abc-1", "0" * 12 + "-0", "0" * 12 + "-01", "0" * 12 + "-1234567", "G" * 12 + "-1"):
        assert status_of(lambda: stop_attempt(ui, bad)) == 400, bad
    assert status_of(lambda: request(ui, "/api/complete/stop", {})) == 400
    assert status_of(lambda: request(ui, "/api/attempts/nope")) == 400


# 69 ---------------------------------------------------------------------------------------------
def held_stop(ui, server, reply, *, wait_for, how):
    """Start a request, wait until the fake reaches `wait_for`, Stop it, and return (stop answer, events)."""
    server.reply(reply)
    configure(ui, server)
    resp = start_chat(ui)
    it = iter_events(resp)
    started = read_until(it, "started")[-1]
    if how == "mid":
        read_until(it, "text")
    else:
        assert server.reached.wait(10)
    if wait_for == "holding":
        assert server.wait_holding(1)
    answered = stop_attempt(ui, started["attempt_id"])
    rest = list(it)
    return started, answered, rest


@pytest.mark.parametrize("name, reply, how", [
    ("a_before_headers", lambda: Reply(hold_headers=True, body=[delta("late")], headers=SSE), "reached"),
    ("b_before_first_token", lambda: Reply(body=[], headers=SSE, hold=True, after_release=[delta("x"), DONE]),
     "holding"),
    ("c_mid_stream", lambda: Reply(body=[delta("par"), delta("tial")], headers=SSE, hold=True,
                                   after_release=[delta("x"), DONE]), "mid"),
])
def test_stop_at_every_stage(ui, server, name, reply, how):
    server.reply(reply())
    configure(ui, server)
    resp = start_chat(ui)
    it = iter_events(resp)
    started = read_until(it, "started")[-1]
    seen = []
    if how == "mid":
        seen = read_until(it, "text") + read_until(it, "text")
    elif how == "holding":
        assert server.reached.wait(10) and server.wait_holding(1)
    else:
        assert server.reached.wait(10) and server.wait_holding(1)
    answered = stop_attempt(ui, started["attempt_id"])
    assert answered == {"status": "stopping"}
    rest = list(it)
    final = final_of(seen + rest)
    rec = final["record"]
    assert final["outcome"] == "stopped" and rec["cancel_reason"] == "user_stop" and rec["stop_mode"] == "immediate"
    assert rec["sent"] is True and rec["stop_requested_at"] is not None and rec["id"] == started["attempt_id"]
    assert server.client_closed.wait(10)                                             # the fake saw the disconnect
    if how == "mid":
        text = "".join(e["text"] for e in seen + rest if e["type"] == "text")
        assert text == "partial" and rec["partial"]["text"] == text
    assert wait_until(lambda: ui.state.budget.in_flight == 0)
    server.release.set()


def test_stop_repeats_and_after_the_commit(ui, server, monkeypatch):
    server.reply(Reply(body=[delta("a")], headers=SSE, hold=True, after_release=[delta("b"), DONE]))
    configure(ui, server)
    entered, proceed = threading.Event(), threading.Event()

    def block():                       # holds the attempt after modelSpell returned, before its end point
        entered.set()
        assert proceed.wait(10)

    monkeypatch.setattr(model_step, "before_end_point", block)
    it = iter_events(start_chat(ui))
    started = read_until(it, "started")[-1]
    read_until(it, "text")
    assert stop_attempt(ui, started["attempt_id"]) == {"status": "stopping"}
    assert entered.wait(10)
    first = poll_attempt(ui, started["attempt_id"])["stop_requested_at"]
    assert first is not None
    assert stop_attempt(ui, started["attempt_id"]) == {"status": "stopping"}       # (d) repeated: same answer
    assert poll_attempt(ui, started["attempt_id"])["stop_requested_at"] == first   # and the first time is kept
    monkeypatch.setattr(model_step, "before_end_point", lambda: None)
    proceed.set()
    rec = final_of(list(it))["record"]
    assert rec["stop_requested_at"] == first and rec["outcome"] == "stopped"
    assert stop_attempt(ui, started["attempt_id"]) == {"status": "already_finished"}   # (e) after the commit
    assert poll_attempt(ui, started["attempt_id"])["record"] == rec                    # the record is unchanged
    assert failure_of(lambda: stop_attempt(ui, "f" * 12 + "-1")).status == 404         # (f)
    assert failure_of(lambda: stop_attempt(ui, "f" * 12 + "-1")).body["code"] == "no_such_attempt"
    server.release.set()


def test_stop_arriving_after_the_answer_finished(ui, server, monkeypatch):
    """0.2.3 port to a Chat attempt. No repair after such a Stop is
    test_runner.py::test_a_stop_after_a_failing_answer_makes_no_repair."""
    configure(ui, server)
    server.reply(answer("pong"))
    entered, proceed = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert proceed.wait(10)

    monkeypatch.setattr(model_step, "before_end_point", block)
    it = iter_events(start_chat(ui))
    started = read_until(it, "started")[-1]
    assert entered.wait(10)
    assert stop_attempt(ui, started["attempt_id"]) == {"status": "stopping"}
    monkeypatch.setattr(model_step, "before_end_point", lambda: None)
    proceed.set()
    rec = final_of(list(it))["record"]
    assert rec["outcome"] == "complete" and rec["stop_requested_at"] is not None and rec["stop_mode"] is None


def test_stop_just_before_a_backend_error_is_a_stop(ui, server, monkeypatch):
    server.reply(Reply(status=500, body=b"{}", headers={"Content-Type": "application/json"}))
    configure(ui, server)
    entered, proceed = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert proceed.wait(10)

    monkeypatch.setattr(model_step, "before_end_point", block)
    it = iter_events(start_chat(ui))
    started = read_until(it, "started")[-1]
    assert entered.wait(10)
    stop_attempt(ui, started["attempt_id"])
    monkeypatch.setattr(model_step, "before_end_point", lambda: None)
    proceed.set()
    rec = final_of(list(it))["record"]
    assert rec["outcome"] == "stopped" and rec["cancel_reason"] == "user_stop" and rec["stop_mode"] == "immediate"
    assert rec["sent"] is None and rec["error"]["class"] == "BackendError"           # rule 3: the error is kept


def test_stop_between_the_end_point_and_the_commit(ui, server, monkeypatch):
    """0.2.3 port to a Chat attempt: the window is held with a call-through wrapper on AttemptStore.commit,
    entered after the end point (the original held it with a slow contract check)."""
    configure(ui, server)
    switch_history(ui, True)
    server.reply(answer("pong"))
    real = runs.AttemptStore.commit
    entered, proceed = threading.Event(), threading.Event()

    def slow(self, attempt_id_, record, saving):
        entered.set()
        assert proceed.wait(10)
        return real(self, attempt_id_, record, saving)

    monkeypatch.setattr(runs.AttemptStore, "commit", slow)
    it = iter_events(start_chat(ui))
    started = read_until(it, "started")[-1]
    assert entered.wait(10)
    assert stop_attempt(ui, started["attempt_id"]) == {"status": "already_finished"}   # past its end point
    proceed.set()
    evs = list(it)
    rec = final_of(evs)["record"]
    assert rec["outcome"] == "complete" and rec["stop_requested_at"] is None
    files = read_run_files(ui, started["run_id"])
    assert list(files["attempts"]) == [1]                                             # the run file is written once


def test_atomic_stop_with_a_held_dispatch(ui, server, monkeypatch):
    """(j) Stop's publication happens before its dispatch, in one decision with the store's lock. 0.2.3: on a
    Chat attempt."""
    configure(ui, server)
    server.reply(Reply(status=500, body=b"{}", headers={"Content-Type": "application/json"}))
    reached, proceed = threading.Event(), threading.Event()
    real = runs.CancelToken._dispatch

    def held(self, actions):
        reached.set()
        assert proceed.wait(10)
        return real(self, actions)

    monkeypatch.setattr(runs.CancelToken, "_dispatch", held)
    enter, go = threading.Event(), threading.Event()

    def block():
        enter.set()
        assert go.wait(10)

    monkeypatch.setattr(model_step, "before_end_point", block)
    it = iter_events(start_chat(ui))
    started = read_until(it, "started")[-1]
    assert enter.wait(10)                                                             # the error has been raised
    stopper = threading.Thread(target=lambda: stop_attempt(ui, started["attempt_id"]))
    stopper.start()
    assert reached.wait(10)                                                           # published, dispatch held
    monkeypatch.setattr(model_step, "before_end_point", lambda: None)
    go.set()                                                                           # the attempt reaches its end point
    rec = final_of(list(it))["record"]
    assert rec["outcome"] == "stopped" and rec["error"] is not None and rec["cancel_reason"] == "user_stop"
    proceed.set()
    stopper.join(10)


# 70 ---------------------------------------------------------------------------------------------
def test_page_closed_commits_a_stopped_attempt(ui, server):
    configure(ui, server)
    switch_history(ui, True)
    server.reply(Reply(body=[delta("part")], headers=SSE, hold=True, after_release=[delta("x")] * 40 + [DONE],
                       chunk_delay=0.02))
    resp = start_chat(ui)
    it = iter_events(resp)
    started = read_until(it, "started")[-1]
    read_until(it, "text")
    resp.close()                                                                       # the page goes away, no Stop
    assert server.wait_holding(1)
    server.release.set()
    assert wait_until(lambda: poll_attempt(ui, started["attempt_id"])["state"] == "committed")
    assert wait_until(lambda: poll_attempt(ui, started["attempt_id"])["save_status"] == "saved")
    rec = poll_attempt(ui, started["attempt_id"])["record"]
    assert rec["outcome"] == "stopped" and rec["cancel_reason"] == "page_closed" and rec["stop_mode"] is None
    assert rec["partial"]["text"].startswith("part")
    assert wait_until(lambda: ui.state.budget.in_flight == 0)                          # returned exactly once
    assert list(read_run_files(ui, started["run_id"])["attempts"]) == [1]


# 71 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("error", [OSError("broken pipe"), TimeoutError("timed out")])
def test_event_write_failures(ui, server, monkeypatch, error):
    configure(ui, server)
    switch_history(ui, True)
    server.reply(answer("pong"))
    known = set(run_folders(ui))
    real = server_module.Handler.write_event

    def failing(kind):
        def write(self, event):
            if event["type"] == kind:
                raise error
            return real(self, event)
        return write

    # (1) started fails for a request: nothing is sent to the model, the attempt is stopped by rule 0.
    monkeypatch.setattr(server_module.Handler, "write_event", failing("started"))
    before = len(server.requests)
    try:
        complete(ui, "second", raw=True).read()
    except Exception:
        pass
    assert wait_until(lambda: ui.state.budget.in_flight == 0)
    assert len(server.requests) == before                                              # the model was never called
    from agentwiz import jsonio
    newest = new_folder(ui, known)
    assert wait_until(lambda: (newest / "request-1.json").exists())
    rec = jsonio.decode_file_exact((newest / "request-1.json").read_bytes())
    assert rec["outcome"] == "stopped" and rec["cancel_reason"] == "page_closed"
    assert rec["model_called"] is False and rec["sent"] is False

    # (3) final fails: the record is unaffected, the attempt file is written once, the budget is released once.
    monkeypatch.setattr(server_module.Handler, "write_event", failing("final"))
    try:
        complete(ui, "third", raw=True).read()
    except Exception:
        pass
    assert wait_until(lambda: ui.state.budget.in_flight == 0)
    newest = new_folder(ui, known)
    assert wait_until(lambda: (newest / "request-1.json").exists())
    rec = jsonio.decode_file_exact((newest / "request-1.json").read_bytes())
    assert rec["outcome"] == "complete" and rec["result"]["text"] == "pong"


# 72 ---------------------------------------------------------------------------------------------
def test_recovery_polling(ui, server, monkeypatch):
    configure(ui, server)
    # (a) the client reads started and some text, then disconnects; polling follows the attempt to its commit.
    server.reply(Reply(body=[delta("one ")], headers=SSE, hold=True, after_release=[delta("two"), delta(finish="stop"), DONE]))
    resp = start_chat(ui)
    it = iter_events(resp)
    started = read_until(it, "started")[-1]
    read_until(it, "text")
    resp.close()
    assert server.wait_holding(1)
    # The server notices the disconnect at its next write; the attempt was running until then.
    assert poll_attempt(ui, started["attempt_id"])["state"] == "running"
    server.release.set()
    assert wait_until(lambda: poll_attempt(ui, started["attempt_id"])["state"] == "committed")
    env = poll_attempt(ui, started["attempt_id"])
    assert env["record_held"] is True and env["record"]["id"] == started["attempt_id"]
    server.release.clear()

    # (b) the save is held: the client reads final with `pending`, disconnects, and polling answers pending then saved.
    switch_history(ui, True)
    server.reply(answer("pong"))
    entered, proceed = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert proceed.wait(10)

    monkeypatch.setattr(history, "before_write", block)
    resp = start_chat(ui)
    it = iter_events(resp)
    final = read_until(it, "final")[-1]
    assert final["save_status"] == "pending"
    resp.close()
    assert entered.wait(10)
    assert poll_attempt(ui, final["attempt_id"])["save_status"] == "pending"
    proceed.set()
    assert wait_until(lambda: poll_attempt(ui, final["attempt_id"])["save_status"] == "saved")
    env = poll_attempt(ui, final["attempt_id"])
    assert env["file"].startswith("runs/") and env["file"].endswith("/request-1.json")

    # (c) a client that disconnects before reading `started`: the attempt still commits, nothing is resent.
    monkeypatch.setattr(history, "before_write", lambda: None)
    switch_history(ui, False)
    server.reply(answer("late"))
    before = len(server.requests)
    sock = socket.socket()
    body = json.dumps({"source": "chat", "prompt": "x", "revision": get_state(ui)["revision"]}).encode()
    sock.connect(ui.server_address)
    sock.sendall(b"POST /api/complete HTTP/1.0\r\nX-Session-Token: " + ui.token.encode() +
                 b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    sock.close()
    assert wait_until(lambda: ui.state.budget.in_flight == 0)
    assert len(server.requests) - before <= 1


def test_polling_envelope_for_unknown_attempts(ui):
    fail = failure_of(lambda: poll_attempt(ui, "f" * 12 + "-1"))
    assert (fail.status, fail.body["code"]) == (410, "not_held")


# 74 ---------------------------------------------------------------------------------------------
# 75 ---------------------------------------------------------------------------------------------
def test_check_on_outcomes(ui, server):
    """Chat has no contract, so no check and no `check_skipped`. The block outcomes are
    test_runner.py::test_block_check_on_outcomes."""
    configure(ui, server)
    server.reply(answer("pong"))
    rec = record_of(complete(ui, "hi"))
    assert rec["contract"] is None and rec["check"] is None and "check_skipped" not in rec


def test_source_and_contract_rules_refuse_before_anything_is_created(ui, server):
    """0.2.3: `checked_request` is retired. Every such body is refused 400 on the source rule, with or without a
    valid pin (the former 404 and 409 cases included); Chat with any `contract` member stays 400. Nothing is
    sent, and no run or runs folder is created. Block pins are
    test_runner.py::test_a_block_pinning_a_damaged_contract_is_refused_and_runs_once_restored."""
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    rev = get_state(ui)["revision"]
    ref = dict(zip(("name", "revision", "digest"), contract_pin(ui, "c")))

    def post(**body):
        fail = failure_of(lambda: request(ui, "/api/complete", {"prompt": "hi", "revision": rev, **body}))
        return fail.status, fail.body

    for contract in (ref, None, "c"):
        assert post(source="chat", contract=contract)[0] == 400
    for body in ({}, {"contract": None}, {"contract": "c"}, {"contract": {"name": "c"}},
                 {"contract": {"name": "c", "revision": 1}}, {"contract": {**ref, "revision": "latest"}},
                 {"contract": {**ref, "digest": "sha256:XYZ"}}, {"contract": {**ref, "extra": 1}},
                 {"contract": ref}, {"contract": {**ref, "name": "missing"}},
                 {"contract": {**ref, "revision": ref["revision"] + 99}},
                 {"contract": {**ref, "digest": "sha256:" + "0" * 64}}):
        status, failure = post(source="checked_request", **body)
        assert status == 400 and "source" in failure["error"], (body, failure)
    assert post(source="nope")[0] == 400 and post()[0] == 400
    assert server.requests == [] and ui.state.store.running_count() == 0 and ui.state.budget.in_flight == 0
    assert not (ui.workspace.path / "runs").exists()


# 99 ---------------------------------------------------------------------------------------------
# --- R-7 server part (0.2.3 spec v15, 4.2 T-1): the compact attempt view -------------------------------------------
COMPACT_KEYS = {"attempt", "outcome", "check", "repair_reason", "availability"}


def compact(ui, attempt_id, query="?view=compact"):
    return request(ui, f"/api/attempts/{attempt_id}{query}")


def test_compact_attempt_view(ui, server, monkeypatch):
    from conftest import block_answer, result_of, run_block
    from test_server_config import _repairing_block
    configure(ui, server)
    _repairing_block(ui)
    server.reply(block_answer(out="nope"), block_answer(out="pong"))
    result = result_of(run_block(ui, "ask", {"text": "pong"}))
    first, second = result["steps"][0]["attempts"]
    held = [compact(ui, first), compact(ui, second)]
    assert held == [{"attempt": 1, "outcome": "complete", "check": "fail", "repair_reason": None, "availability": "held"},
                    {"attempt": 2, "outcome": "complete", "check": "pass", "repair_reason": "check_failed",
                     "availability": "held"}]
    # Without `view`, with or without an unrelated parameter (the token parameter included), today's envelope.
    envelope = poll_attempt(ui, first)
    assert request(ui, f"/api/attempts/{first}?other=1") == envelope
    assert request(ui, f"/api/attempts/{first}?t={ui.token}") == envelope
    # Refusals: another view, view twice, a malformed ID; 410 when neither a record nor a summary is held.
    for query in ("?view=full", "?view=compact&view=compact", "?view="):
        assert failure_of(lambda: compact(ui, first, query)).status == 400
    assert failure_of(lambda: compact(ui, "not-an-id")).status == 400
    assert failure_of(lambda: compact(ui, "0" * 12 + "-1")).status == 410
    # A Chat attempt: no check recorded.
    server.reply(answer("pong"))
    chat = record_of(complete(ui, "ping"))
    assert compact(ui, chat["id"]) == {"attempt": 1, "outcome": "complete", "check": None, "repair_reason": None,
                                       "availability": "held"}
    # Running: null fields.
    server.reply(Reply(body=[delta("po")], hold=True, headers=SSE, after_release=[delta(finish="stop"), DONE]))
    stream = start_chat(ui, "ping")
    started = read_until(iter_events(stream), "started")[-1]
    assert compact(ui, started["attempt_id"]) == {"attempt": 1, "outcome": None, "check": None, "repair_reason": None,
                                                  "availability": "running"}
    server.release.set()
    stream.read()
    # A summary only: records evicted by lowering the record limit.
    monkeypatch.setattr(runs, "RECORD_LIMIT", 0)
    server.release.clear()
    server.reply(answer("pong"))
    record_of(complete(ui, "ping"))
    summarised = compact(ui, second)
    assert set(summarised) == COMPACT_KEYS
    assert summarised == {"attempt": 2, "outcome": "complete", "check": "pass", "repair_reason": None,
                          "availability": "summary"}
    for view in held + [summarised]:
        assert set(view) == COMPACT_KEYS and len(json.dumps(view)) < 1024
