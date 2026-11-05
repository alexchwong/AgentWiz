"""Runs, attempts, Stop, polling and repair through HTTP: techspec 0.1.3 tests #68-#72, #74 and #75
(Part B, 8, 9). Every ordering is forced with the fake backend's holds and the code's hooks, never timing."""
import json
import os
import socket
import threading

import pytest

from agentwiz import history, model_step, runs
from agentwiz.ui import server as server_module
from conftest import (contract_pin, contract_token, DONE, JSON_CONTRACT, JSON_CONTRACT_NO_SCHEMA, TEXT_CONTRACT, Reply, answer, complete, configure, contract_item,
                      delta, failure_of, file_stat, final_of, get_state, iter_events, json_answer, poll_attempt,
                      read_run_files, read_until, record_of, repair_attempt, request, run_folders, save_contract,
                      status_of, stop_attempt, switch_history, wait_until)

SSE = {"Content-Type": "text/event-stream"}


def checked(ui, name, prompt="hi", **kw):
    return complete(ui, prompt, source="checked_request", contract=contract_pin(ui, name), **kw)


def start_checked(ui, name, prompt="hi", **kw):
    """The open stream of a Checked request, for tests that act while it runs."""
    rev = get_state(ui)["revision"]
    return request(ui, "/api/complete", {"source": "checked_request", "prompt": prompt, "system": "", "revision": rev,
                                         "contract": dict(zip(("name", "revision", "digest"), contract_pin(ui, name))),
                                         **kw}, raw=True)


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
def test_identity_and_run_files(ui, server):
    save_contract(ui, "needs_numbers", JSON_CONTRACT)          # ratio has maximum 1: a structural rule
    configure(ui, server)
    switch_history(ui, True)
    good = '{"id": 9007199254740993, "ratio": 0.10000000000000000001}'
    server.reply(json_answer('{"id": 1, "ratio": 2}'), json_answer('{"id": 2, "ratio": 3}'), json_answer(good, fenced=True))
    first = checked(ui, "needs_numbers")
    started = first[0]
    run_id = started["run_id"]
    assert started == {"type": "started", "run_id": run_id, "step_id": "request", "attempt": 1,
                       "attempt_id": f"{run_id}-1"}
    one = record_of(first)
    assert one["check"]["overall"] == "fail" and one["parent_id"] is None
    folder = run_folders(ui)[0]
    stats = {"request-1.json": file_stat(folder / "request-1.json")}

    second = repair_attempt(ui, one["id"])
    assert second[0]["attempt"] == 2 and second[0]["attempt_id"] == f"{run_id}-2" and second[0]["run_id"] == run_id
    two = record_of(second)
    assert two["parent_id"] == f"{run_id}-1" and two["check"]["overall"] == "fail"
    stats["request-2.json"] = file_stat(folder / "request-2.json")
    third = repair_attempt(ui, two["id"])
    assert third[0]["attempt_id"] == f"{run_id}-3"
    three = record_of(third)
    assert three["parent_id"] == f"{run_id}-2" and three["check"]["overall"] == "pass"
    assert (one["step_id"], two["step_id"], three["step_id"]) == ("request",) * 3
    assert [r["attempt"] for r in (one, two, three)] == [1, 2, 3]

    files = read_run_files(ui, run_id)
    assert sorted(f.name for f in files["folder"].iterdir()) == ["request-1.json", "request-2.json", "request-3.json",
                                                                 "run.json"]
    assert files["run"]["run_id"] == run_id and files["run"]["steps"] == ["request"]
    assert files["run"]["contract"] == {"name": "needs_numbers", "revision": one["contract"]["revision"],
                                        "digest": one["contract"]["digest"]}
    for name, before in stats.items():
        assert file_stat(files["folder"] / name) == before                  # each file written once
    for number in (1, 2, 3):
        saved = files["attempts"][number]
        assert saved["id"] == f"{run_id}-{number}" and saved["redacted"] is False
        assert saved["contract"]["text"] == one["contract"]["text"]         # the same captured definition
        assert saved["contract"]["digest"] == one["contract"]["digest"] and saved["contract"]["policy"] == "user"
    assert files["attempts"][3]["check"]["overall"] == "pass"
    assert all(oct(os.stat(files["folder"] / n).st_mode & 0o777) == "0o600" for n in files["attempts"] and
               ("run.json", "request-1.json"))
    assert oct(os.stat(files["folder"]).st_mode & 0o777) == "0o700"


def test_ids_are_random_and_routes_check_their_form(ui, server):
    server.reply(answer("ok"))
    configure(ui, server)
    a, b = complete(ui, "hi"), complete(ui, "hi")
    ids = {a[0]["run_id"], b[0]["run_id"]}
    assert len(ids) == 2 and all(len(i) == 12 and i == i.lower() for i in ids)
    for bad in ("", "nope", "abc-1", "0" * 12 + "-0", "0" * 12 + "-01", "0" * 12 + "-1234567", "G" * 12 + "-1"):
        assert status_of(lambda: stop_attempt(ui, bad)) == 400, bad
        assert status_of(lambda: request(ui, "/api/repair/preview", {"attempt_id": bad})) == 400
        assert status_of(lambda: request(ui, "/api/repair", {"attempt_id": bad})) == 400
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
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    server.reply(answer("nope"))
    entered, proceed = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert proceed.wait(10)

    monkeypatch.setattr(model_step, "before_end_point", block)
    it = iter_events(start_checked(ui, "c"))
    started = read_until(it, "started")[-1]
    assert entered.wait(10)
    assert stop_attempt(ui, started["attempt_id"]) == {"status": "stopping"}
    monkeypatch.setattr(model_step, "before_end_point", lambda: None)
    proceed.set()
    rec = final_of(list(it))["record"]
    assert rec["outcome"] == "complete" and rec["stop_requested_at"] is not None and rec["stop_mode"] is None
    assert rec["check"]["overall"] == "fail"
    fail = failure_of(lambda: repair_attempt(ui, rec["id"]))                          # the run is closed to repairs
    assert (fail.status, fail.body["code"]) == (409, "run_stopped")
    fail = failure_of(lambda: request(ui, "/api/repair/preview", {"attempt_id": rec["id"]}))
    assert (fail.status, fail.body["code"]) == (409, "run_stopped")


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
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    switch_history(ui, True)
    server.reply(answer("pong"))
    from agentwiz import checks
    real = checks.check_answer_value
    entered, proceed = threading.Event(), threading.Event()

    def slow(contract, text):
        entered.set()
        assert proceed.wait(10)
        return real(contract, text)

    monkeypatch.setattr(checks, "check_answer_value", slow)
    it = iter_events(start_checked(ui, "c"))
    started = read_until(it, "started")[-1]
    assert entered.wait(10)
    assert stop_attempt(ui, started["attempt_id"]) == {"status": "already_finished"}   # past its end point
    proceed.set()
    evs = list(it)
    rec = final_of(evs)["record"]
    assert rec["outcome"] == "complete" and rec["stop_requested_at"] is None and rec["check"]["overall"] == "pass"
    files = read_run_files(ui, started["run_id"])
    assert list(files["attempts"]) == [1]                                             # the run file is written once


def test_stop_addresses_one_attempt(ui, server):
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    server.reply(answer("nope"), Reply(body=[delta("pong")], headers=SSE, hold=True,
                                       after_release=[delta(" more"), delta(finish="stop"), DONE]))
    first = record_of(checked(ui, "c"))
    run_id = first["run_id"]
    assert first["check"]["overall"] == "fail"
    it = iter_events(repair_attempt(ui, first["id"], raw=True))
    second = read_until(it, "started")[-1]
    read_until(it, "text")
    # (k) Stop for the ended attempt 1 cancels nothing; the repair completes normally.
    assert stop_attempt(ui, first["id"]) == {"status": "already_finished"}
    server.release.set()
    rec2 = final_of(list(it))["record"]
    assert rec2["outcome"] == "complete" and rec2["stop_requested_at"] is None
    assert stop_attempt(ui, second["attempt_id"]) == {"status": "already_finished"}   # and for the finished repair
    assert poll_attempt(ui, second["attempt_id"])["record"] == rec2

    # (l) In a fresh run, Stop for the repair while it is held cancels the run.
    server.release.clear()
    server.reply(answer("nope"), Reply(body=[delta("par")], headers=SSE, hold=True, after_release=[DONE]))
    first = record_of(checked(ui, "c"))
    it = iter_events(repair_attempt(ui, first["id"], raw=True))
    started = read_until(it, "started")[-1]
    read_until(it, "text")
    assert stop_attempt(ui, started["attempt_id"]) == {"status": "stopping"}
    rec = final_of(list(it))["record"]
    assert rec["outcome"] == "stopped" and rec["stop_mode"] == "immediate" and rec["partial"]["text"] == "par"
    server.release.set()


def test_atomic_stop_with_a_held_dispatch(ui, server, monkeypatch):
    """(j) Stop's publication happens before its dispatch, in one decision with the store's lock."""
    save_contract(ui, "c", TEXT_CONTRACT)
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
    it = iter_events(start_checked(ui, "c"))
    started = read_until(it, "started")[-1]
    assert enter.wait(10)                                                             # the error has been raised
    stopper = threading.Thread(target=lambda: stop_attempt(ui, started["attempt_id"]))
    stopper.start()
    assert reached.wait(10)                                                           # published, dispatch held
    monkeypatch.setattr(model_step, "before_end_point", lambda: None)
    go.set()                                                                           # the attempt reaches its end point
    rec = final_of(list(it))["record"]
    assert rec["outcome"] == "stopped" and rec["error"] is not None and rec["cancel_reason"] == "user_stop"
    fail = failure_of(lambda: repair_attempt(ui, rec["id"]))
    assert (fail.status, fail.body["code"]) == (409, "run_stopped")
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
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    switch_history(ui, True)
    server.reply(answer("nope"), answer("pong"))
    first = record_of(checked(ui, "c"))
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

    # (2) the same for a repair; the parent stays repairable.
    try:
        repair_attempt(ui, first["id"], raw=True).read()
    except Exception:
        pass
    assert wait_until(lambda: ui.state.budget.in_flight == 0)
    assert len(server.requests) == before
    env = poll_attempt(ui, f"{first['run_id']}-2")
    assert env["record"]["outcome"] == "stopped" and env["record"]["cancel_reason"] == "page_closed"

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
def test_repair_preview_and_send(ui, server):
    save_contract(ui, "c", JSON_CONTRACT)
    configure(ui, server, kind="openai_compat", model="the-model")
    good = '{"id": 9007199254740993, "ratio": 0.10000000000000000001}'
    server.reply(json_answer('{"id": 1, "ratio": 2}'), json_answer('{"id": 3, "ratio": 4}'), json_answer(good))
    first = record_of(complete(ui, "extract", system="be exact", source="checked_request",
                               contract=contract_pin(ui, "c"), task={"temperature": 0.25}))
    assert first["check"]["overall"] == "fail"
    preview = request(ui, "/api/repair/preview", {"attempt_id": first["id"]})
    assert preview["parent_id"] == first["id"] and preview["attempt"] == 2
    assert [m["role"] for m in preview["messages"]] == ["system", "user", "assistant", "user"]
    assert preview["messages"][2]["content"] == first["result"]["text"]
    assert preview["messages"][3]["content"] == preview["repair_message"]
    assert preview["repair_message"].startswith("The previous answer did not satisfy the required output contract.")
    assert "$.ratio" in preview["repair_message"]
    assert preview["settings"] == {"kind": "openai_compat", "base_url": server.url + "/v1", "model": "the-model",
                                   "provider": "", "task": {"temperature": 0.25}}
    assert preview["contract"] == {"name": "c", "revision": first["contract"]["revision"],
                                   "digest": first["contract"]["digest"]}
    assert request(ui, "/api/repair/preview", {"attempt_id": first["id"]}) == preview      # nothing was reserved

    # Everything the screen could change is changed; the repair still uses the captured settings.
    configure(ui, server, model="another-model")
    request(ui, "/api/task_settings/save", {"task": {"temperature": 0.9}, "revision": get_state(ui)["task"]["revision"]})
    save_contract(ui, "c", JSON_CONTRACT_NO_SCHEMA, contract_token(ui, "c"))      # appends revision 2
    assert contract_item(ui, "c")["latest"] == 2
    second = record_of(repair_attempt(ui, first["id"], raw=False))
    sent = server.requests[-1]["body"]
    assert sent["model"] == "the-model" and sent["temperature"] == 0.25
    assert sent["messages"] == preview["messages"] and second["messages_sent"] == preview["messages"]
    assert second["input_messages"] == first["input_messages"] and second["parent_id"] == first["id"]
    assert second["contract"] == first["contract"] and second["check"]["overall"] == "fail"   # the captured contract

    # Two successive repairs send only input, the latest answer and the latest repair message.
    preview2 = request(ui, "/api/repair/preview", {"attempt_id": second["id"]})
    assert [m["role"] for m in preview2["messages"]] == ["system", "user", "assistant", "user"]
    assert preview2["messages"][2]["content"] == second["result"]["text"] != first["result"]["text"]
    third = record_of(repair_attempt(ui, second["id"]))
    assert third["check"]["overall"] == "pass" and len(third["messages_sent"]) == 4


def test_repair_refusals(ui, server):
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)

    def code(attempt_id, path="/api/repair"):
        fail = failure_of(lambda: request(ui, path, {"attempt_id": attempt_id}))
        return fail.status, fail.body["code"]

    server.reply(answer("pong"))
    passing = record_of(checked(ui, "c"))
    assert code(passing["id"]) == (409, "not_repairable") and code(passing["id"], "/api/repair/preview") == (409, "not_repairable")
    server.reply(answer(""))
    assert code(record_of(checked(ui, "c"))["id"]) == (409, "not_repairable")                     # an empty outcome
    server.reply(Reply(status=500, body=b"{}", headers={"Content-Type": "application/json"}))
    assert code(record_of(checked(ui, "c"))["id"]) == (409, "not_repairable")                     # an error outcome
    save_contract(ui, "schemaless", '{"format": "json"}')
    server.reply(answer("not json at all"))
    failed_parse = record_of(checked(ui, "schemaless"))
    assert failed_parse["check"]["overall"] == "fail"                                              # parse fails: repairable
    server.reply(answer("9" * 5000))
    incomplete = record_of(checked(ui, "schemaless"))
    assert incomplete["check"]["overall"] == "incomplete" and code(incomplete["id"]) == (409, "not_repairable")
    chat = record_of(complete(ui, "hi"))
    assert code(chat["id"]) == (409, "not_repairable")                                             # no contract, no check

    # Only the latest attempt: an older one is refused.
    server.reply(answer("nope"), answer("still nope"))
    first = record_of(checked(ui, "c"))
    second = record_of(repair_attempt(ui, first["id"]))
    assert code(first["id"]) == (409, "not_latest")
    assert request(ui, "/api/repair/preview", {"attempt_id": second["id"]})["attempt"] == 3
    assert code("f" * 12 + "-3") == (404, "no_such_attempt")


def test_concurrent_repairs_give_one_stream_and_one_409(ui, server):
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    server.reply(answer("nope"), Reply(body=[delta("pong")], headers=SSE, hold=True,
                                       after_release=[delta(finish="stop"), DONE]))
    first = record_of(checked(ui, "c"))
    results = {}

    def attempt(n):
        try:
            resp = repair_attempt(ui, first["id"], raw=True)
            it = iter_events(resp)
            results[n] = ("stream", read_until(it, "started")[-1], it)
        except Exception as exc:   # HTTPFailure
            results[n] = ("refused", exc)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    kinds = sorted(v[0] for v in results.values())
    assert kinds == ["refused", "stream"]
    refused = next(v[1] for v in results.values() if v[0] == "refused")
    assert (refused.status, refused.body["code"]) == (409, "not_latest")
    server.release.set()
    stream = next(v for v in results.values() if v[0] == "stream")
    assert final_of(list(stream[2]))["record"]["outcome"] == "complete"


def test_repair_is_refused_when_the_budget_is_full(make_ui, server):
    ui = make_ui(request_limit=1)
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    server.reply(answer("nope"), Reply(body=[delta("x")], headers=SSE, hold=True, after_release=[DONE]))
    first = record_of(checked(ui, "c"))
    holder = start_chat(ui)
    it = iter_events(holder)
    read_until(it, "text")
    fail = failure_of(lambda: repair_attempt(ui, first["id"]))
    assert (fail.status, fail.body["code"]) == (503, "busy")
    server.release.set()
    list(it)
    assert wait_until(lambda: ui.state.budget.in_flight == 0)
    again = repair_attempt(ui, first["id"])                                                       # the number was given back
    assert again[0]["attempt"] == 2


def test_repair_after_a_stopped_attempt(ui, server):
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    server.reply(Reply(body=[delta("par")], headers=SSE, hold=True, after_release=[DONE]))
    it = iter_events(start_checked(ui, "c"))
    started = read_until(it, "started")[-1]
    read_until(it, "text")
    stop_attempt(ui, started["attempt_id"])
    rec = final_of(list(it))["record"]
    assert rec["outcome"] == "stopped"
    fail = failure_of(lambda: repair_attempt(ui, rec["id"]))
    assert fail.status == 409 and fail.body["code"] in ("run_stopped", "not_repairable")
    server.release.set()


# 75 ---------------------------------------------------------------------------------------------
def test_check_on_outcomes(ui, server):
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    server.reply(answer("pong"))
    rec = record_of(checked(ui, "c"))
    assert rec["outcome"] == "complete" and rec["check"]["overall"] == "pass" and "check_skipped" not in rec
    server.reply(Reply(body=[delta("pon"), delta(finish="length"), DONE], headers=SSE))
    rec = record_of(checked(ui, "c"))
    assert rec["outcome"] == "truncated" and rec["check"]["overall"] == "fail"                 # truncated is checked
    server.reply(answer(""))
    rec = record_of(checked(ui, "c"))
    assert rec["outcome"] == "empty" and rec["check"] is None and rec["check_skipped"] == "blank_answer"
    server.reply(Reply(body=[delta("", reasoning="thinking"), delta(finish="stop"), DONE], headers=SSE))
    rec = record_of(checked(ui, "c"))
    assert rec["outcome"] == "reasoning_only" and rec["check_skipped"] == "blank_answer"
    server.reply(Reply(body=[delta("", reasoning="thinking"), delta(finish="length"), DONE], headers=SSE))
    rec = record_of(checked(ui, "c"))
    assert rec["outcome"] == "truncated_no_answer" and rec["check_skipped"] == "blank_answer"
    server.reply(Reply(status=500, body=b"{}", headers={"Content-Type": "application/json"}))
    rec = record_of(checked(ui, "c"))
    assert rec["outcome"] == "error" and rec["check"] is None and rec["check_skipped"] == "not_completed"
    # Chat has no contract, so no check and no `check_skipped`.
    server.reply(answer("pong"))
    rec = record_of(complete(ui, "hi"))
    assert rec["contract"] is None and rec["check"] is None and "check_skipped" not in rec


def test_source_and_contract_rules_refuse_before_anything_is_created(ui, server):
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    rev = get_state(ui)["revision"]
    ref = dict(zip(("name", "revision", "digest"), contract_pin(ui, "c")))

    def post(**body):
        fail = failure_of(lambda: request(ui, "/api/complete", {"prompt": "hi", "revision": rev, **body}))
        return fail.status

    assert post(source="chat", contract=ref) == 400
    assert post(source="chat", contract=None) == 400
    assert post(source="checked_request") == 400
    assert post(source="checked_request", contract=None) == 400
    assert post(source="checked_request", contract="c") == 400
    assert post(source="checked_request", contract={"name": "c"}) == 400
    assert post(source="checked_request", contract={"name": "c", "revision": "1"}) == 400
    assert post(source="checked_request", contract={"name": "c", "revision": True}) == 400
    assert post(source="checked_request", contract={"name": "../c", "revision": 1}) == 400
    assert post(source="checked_request", contract={"name": "c", "revision": 1}) == 400          # no digest
    assert post(source="checked_request", contract={**ref, "revision": "latest"}) == 400        # never "latest"
    assert post(source="checked_request", contract={**ref, "digest": "sha256:XYZ"}) == 400
    assert post(source="checked_request", contract={**ref, "extra": 1}) == 400
    assert post(source="nope") == 400 and post() == 400
    assert server.requests == [] and ui.state.store.running_count() == 0 and ui.state.budget.in_flight == 0
    # Missing contracts and revisions; a selected digest that no longer matches.
    assert post(source="checked_request", contract={**ref, "name": "missing"}) == 404
    assert post(source="checked_request", contract={**ref, "revision": ref["revision"] + 99}) == 404
    assert post(source="checked_request", contract={**ref, "digest": "sha256:" + "0" * 64}) == 409
    assert server.requests == [] and not (ui.workspace.path / "runs").exists()
    # Appending a newer revision does not invalidate the reviewed older pin (spec 13.1).
    save_contract(ui, "c", '{"format": "text", "contains": ["other"]}', contract_token(ui, "c"))
    server.reply(answer("pong"))
    rec = record_of(complete(ui, "hi", source="checked_request", contract=tuple(ref.values())))
    assert rec["contract"]["revision"] == ref["revision"] == 1 and rec["check"]["overall"] == "pass"
    assert "pong" in rec["contract"]["text"] and "AgentWiz output protocol" in rec["messages_sent"][0]["content"]


def test_a_damaged_contract_is_refused_and_usable_after_repair(ui, server):
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    good_digest = contract_pin(ui, "c")[2]
    path = ui.workspace.path / "contracts" / "c.json"
    good = path.read_bytes()
    path.write_bytes(b"{ damaged")
    os.utime(path, ns=(os.stat(path).st_mtime_ns + 5_000_000_000,) * 2)
    pin = ("c", 1, good_digest)
    fail = failure_of(lambda: complete(ui, "hi", source="checked_request", contract=pin))
    assert (fail.status, fail.body["code"]) == (409, "contract_unusable")
    assert server.requests == [] and ui.state.store.running_count() == 0
    path.write_bytes(good)
    os.utime(path, ns=(os.stat(path).st_mtime_ns + 10_000_000_000,) * 2)
    server.reply(answer("pong"))
    rec = record_of(complete(ui, "hi", source="checked_request", contract=pin))
    assert rec["check"]["overall"] == "pass"


# 99 ---------------------------------------------------------------------------------------------
def test_checked_request_repair_reason(ui, server):
    """0.1.4, 12.2: a repair of a truncated answer carries the truncation message and `repair_reason`."""
    from agentwiz import checks
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    # a truncated answer whose check passes: outcome truncated, check pass, and no repair
    server.reply(Reply(body=[delta("pong"), delta(finish="length"), DONE], headers=SSE))
    rec = record_of(checked(ui, "c"))
    assert rec["outcome"] == "truncated" and rec["check"]["overall"] == "pass" and rec["repair_reason"] is None
    fail = failure_of(lambda: repair_attempt(ui, rec["id"]))
    assert fail.status == 409 and fail.body["code"] == "not_repairable"
    # a truncated answer whose check fails: the preview, the repair's messages and its record all say `truncated`
    server.reply(Reply(body=[delta("pon"), delta(finish="length"), DONE], headers=SSE), answer("pong"))
    rec = record_of(checked(ui, "c"))
    assert rec["outcome"] == "truncated" and rec["check"]["overall"] == "fail"
    preview = request(ui, "/api/repair/preview", {"attempt_id": rec["id"]})
    assert preview["repair_reason"] == "truncated"
    contract = checks.contract_from_text(TEXT_CONTRACT)
    assert preview["repair_message"] == checks.repair_message(contract, rec["check"], truncated=True)
    repaired = record_of(repair_attempt(ui, rec["id"]))
    assert repaired["repair_reason"] == "truncated" and repaired["messages_sent"] == preview["messages"]
    assert repaired["role"] is None and repaired["fills"] == []
    # a complete answer that fails its check: `check_failed`
    server.reply(answer("nope"), answer("pong"))
    rec = record_of(checked(ui, "c"))
    assert request(ui, "/api/repair/preview", {"attempt_id": rec["id"]})["repair_reason"] == "check_failed"
    assert record_of(repair_attempt(ui, rec["id"]))["repair_reason"] == "check_failed"
    assert rec["repair_reason"] is None and rec["role"] is None and rec["fills"] == []
