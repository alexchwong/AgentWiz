"""Run history through HTTP: techspec 0.1.3 tests #76-#79 (Part D, 10). Orderings are forced with the fake
backend's holds and the code's hooks (`history.before_write`, `history.remove_tree`)."""
import itertools
import json
import os
import threading

import pytest

from agentwiz import history, jsonio, runs
from agentwiz.workspace import open_workspace
from conftest import (DONE, TEXT_CONTRACT, Reply, add_server, answer, complete, configure, delta,
                      failure_of, final_of, get_state, history_dir, iter_events, poll_attempt, read_run_files,
                      read_until, record_of, request, run_folders, save_contract, set_token,
                      status_of, switch_history, wait_until, write_ws_file)

SSE = {"Content-Type": "text/event-stream"}


def held_chat(ui, **body):
    rev = get_state(ui)["revision"]
    return request(ui, "/api/complete", {"source": "chat", "prompt": "hi", "system": "", "revision": rev, **body}, raw=True)


def workspace_json(ui) -> dict:
    return jsonio.decode_file((ui.workspace.path / "workspace.json").read_bytes())


# 76 ---------------------------------------------------------------------------------------------
def repairing_block(ui, server):
    """A saved-or-not block `b` whose first answer fails its check and whose one repair passes."""
    from test_server_config import _repairing_block
    configure(ui, server)
    _repairing_block(ui)


def start_block(ui):
    from conftest import block_revision
    return request(ui, "/api/blocks/run", {"name": "ask", "revision": block_revision(ui, "ask"),
                                           "inputs": {"text": "pong"}}, raw=True)


def held_failure():
    return Reply(body=[delta('{"out":"nope"}')], headers=SSE, hold=True, after_release=[delta(finish="stop"), DONE])


def test_the_switch_is_read_when_a_block_run_is_created(ui, server):
    """0.2.3 port to a block run that repairs automatically, with its first reply held."""
    repairing_block(ui, server)
    assert get_state(ui)["history"]["save_runs"] is False
    # A run created while saving is off is never written, even if saving is turned on before it ends.
    server.reply(held_failure(), answer('{"out":"pong"}'))
    resp = start_block(ui)
    assert server.wait_holding(1)
    switch_history(ui, True)
    server.release.set()
    evs = [json.loads(line) for line in resp.read().splitlines() if line.strip()]
    result = next(e for e in evs if e["type"] == "final")["result"]
    attempts = result["steps"][0]["attempts"]
    assert len(attempts) == 2
    assert [poll_attempt(ui, a)["save_status"] for a in attempts] == ["not_requested"] * 2
    assert not history_dir(ui).exists() or run_folders(ui) == []
    server.release.clear()
    # A run created while saving is on is written, even if saving is turned off before it ends.
    server.reply(held_failure(), answer('{"out":"pong"}'))
    resp = start_block(ui)
    assert server.wait_holding(1)
    switch_history(ui, False)
    server.release.set()
    evs = [json.loads(line) for line in resp.read().splitlines() if line.strip()]
    result = next(e for e in evs if e["type"] == "final")["result"]
    assert wait_until(lambda: len(run_folders(ui)) == 1)
    folder = run_folders(ui)[0]
    assert wait_until(lambda: sorted(p.name for p in folder.iterdir()) == ["request-1.json", "request-2.json", "result.json", "run.json"])
    # Turning saving off deleted nothing.
    assert get_state(ui)["history"]["save_runs"] is False and len(run_folders(ui)) == 1


def test_switch_revision_read_only_restart_and_unknown_fields(make_ui, tmp_path, restart):
    ws = open_workspace(tmp_path / "ws_switch", "w")
    path = ws.path / "workspace.json"
    raw = jsonio.decode_file(path.read_bytes())
    raw["future_field"] = {"keep": [1, 2]}
    path.write_bytes(jsonio.encode_file(raw))
    ui = make_ui(workspace=ws)
    make_ui.opened.append(ws)
    state = get_state(ui)["history"]
    assert state == {"save_runs": False, "revision": 1, "read_only": False, "path": str(ws.path / "runs"),
                     "short_credential_held": False}
    stale = failure_of(lambda: request(ui, "/api/history/switch", {"save_runs": True, "revision": 99}))
    assert (stale.status, stale.body["code"]) == (409, "stale_revision")
    assert status_of(lambda: request(ui, "/api/history/switch", {"save_runs": "yes", "revision": 1})) == 400
    assert status_of(lambda: request(ui, "/api/history/switch", {"save_runs": True})) == 400
    config_revision = get_state(ui)["revision"]
    on = request(ui, "/api/history/switch", {"save_runs": True, "revision": 1})
    assert on["history"]["save_runs"] is True and on["history"]["revision"] == 2
    assert on["revision"] == config_revision                                    # a settings dialog never goes stale (P11)
    same = request(ui, "/api/history/switch", {"save_runs": True, "revision": 2})
    assert same["history"]["revision"] == 2                                     # a change that changes nothing
    assert workspace_json(ui)["save_runs"] is True and workspace_json(ui)["future_field"] == {"keep": [1, 2]}
    again = restart(ui)
    assert get_state(again)["history"]["save_runs"] is True                     # survives a restart

    damaged = tmp_path / "ws_damaged"
    write_ws_file(damaged / "ro" / "workspace.json", "{ nope")
    ro = open_workspace(damaged, "ro")
    make_ui.opened.append(ro)
    ro_ui = make_ui(workspace=ro)
    view = get_state(ro_ui)["history"]
    assert view["save_runs"] is False and view["read_only"] is True
    fail = failure_of(lambda: request(ro_ui, "/api/history/switch", {"save_runs": True, "revision": view["revision"]}))
    assert (fail.status, fail.body["code"]) == (409, "read_only")


def test_a_workspace_that_was_saving_still_saves_after_restart(ui, server, restart):
    switch_history(ui, True)
    again = restart(ui)
    server.reply(answer("ok"))
    configure(again, server)
    evs = complete(again, "hi")
    assert evs[-1]["type"] == "save" and evs[-1]["save_status"] == "saved"


# 77 ---------------------------------------------------------------------------------------------
LONG_KEY = "sk-SENTINEL-redaction-0123456789"


def saved_text(ui, run_id) -> str:
    folder = next(p for p in run_folders(ui) if p.name.endswith("-" + run_id))
    return "".join((folder / f.name).read_text() for f in folder.iterdir())


def test_redaction(ui, server, make_server, monkeypatch):
    configure(ui, server, api_key=LONG_KEY)
    switch_history(ui, True)
    server.reply(answer(f"your key is {LONG_KEY} indeed"))
    evs = complete(ui, f"my key is {LONG_KEY}")
    rec = record_of(evs)
    assert LONG_KEY in rec["result"]["text"]                                  # memory and the page: not redacted
    files = read_run_files(ui, evs[0]["run_id"])
    assert files["attempts"][1]["redacted"] is True and files["run"]["redacted"] is True
    assert files["attempts"][1]["result"]["text"] == "your key is *** indeed"
    assert files["run"]["inputs"]["prompt"] == "my key is ***"
    assert LONG_KEY not in saved_text(ui, evs[0]["run_id"])

    # A key cleared while the request is held is still redacted.
    other = make_server()
    other.reply(Reply(body=[delta("x ")], headers=SSE, hold=True,
                      after_release=[delta(f"{LONG_KEY}-2"), delta(finish="stop"), DONE]))
    configure(ui, other, api_key=f"{LONG_KEY}-2")
    it = iter_events(held_chat(ui))
    started = read_until(it, "started")[-1]
    read_until(it, "text")
    configure(ui, other, api_key="")
    other.release.set()
    final = final_of(list(it))
    assert final["record"]["result"]["text"] == f"x {LONG_KEY}-2"
    assert "-2" not in saved_text(ui, started["run_id"]).replace("request-2", "") or \
        f"{LONG_KEY}-2" not in saved_text(ui, started["run_id"])
    assert read_run_files(ui, started["run_id"])["attempts"][1]["result"]["text"] == "x ***"

    # A key from the environment is redacted too.
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key-0123456789")
    env_server = make_server().reply(answer("echo env-key-0123456789 echo"))
    configure(ui, env_server, kind="openrouter", api_key="")
    evs = complete(ui, "hi")
    assert read_run_files(ui, evs[0]["run_id"])["attempts"][1]["result"]["text"] == "echo *** echo"

    # A typed MCP token echoed in a prompt.
    add_server(ui, "s", "http://127.0.0.1:9/mcp")
    set_token(ui, "s", "mcp-token-0123456789")
    server.reply(answer("ok"))
    configure(ui, server)
    evs = complete(ui, "the token is mcp-token-0123456789")
    files = read_run_files(ui, evs[0]["run_id"])
    assert files["attempts"][1]["input_messages"][-1]["content"] == "the token is ***"
    assert "mcp-token-0123456789" not in saved_text(ui, evs[0]["run_id"])
    assert get_state(ui)["history"]["short_credential_held"] is False


def test_a_short_credential_is_saved_as_given_and_flagged(ui, server):
    configure(ui, server, api_key="short12")
    switch_history(ui, True)
    assert get_state(ui)["history"]["short_credential_held"] is True
    server.reply(answer("short12 is here"))
    evs = complete(ui, "hi")
    files = read_run_files(ui, evs[0]["run_id"])
    assert files["attempts"][1]["result"]["text"] == "short12 is here" and files["attempts"][1]["redacted"] is False
    configure(ui, server, api_key="")
    assert get_state(ui)["history"]["short_credential_held"] is True            # held for the life of the process


def test_records_never_hold_a_key(ui, server):
    configure(ui, server, api_key=LONG_KEY)
    switch_history(ui, True)
    server.reply(answer("ok"))
    evs = complete(ui, "hi")
    assert LONG_KEY not in json.dumps(record_of(evs))
    assert LONG_KEY not in saved_text(ui, evs[0]["run_id"])


# 78 ---------------------------------------------------------------------------------------------
def test_save_failure_when_runs_is_a_file(ui, server):
    configure(ui, server)
    switch_history(ui, True)
    (ui.workspace.path / "runs").write_text("a file where the folder should be")
    server.reply(answer("ok"))
    evs = complete(ui, "hi")
    rec = final_of(evs)["record"]
    assert rec["outcome"] == "complete"                                        # the run continues
    assert evs[-1]["type"] == "save" and evs[-1]["save_status"] == "failed" and "run folder" in evs[-1]["reason"]
    env = poll_attempt(ui, rec["id"])
    assert env["save_status"] == "failed" and env["reason"] == evs[-1]["reason"]
    assert env["record"] == rec


def test_save_failure_when_the_folder_disappears(ui, server, monkeypatch):
    """0.2.3 port to a Chat run: its folder is created before the attempt is written."""
    configure(ui, server)
    switch_history(ui, True)
    server.reply(answer("pong"))
    import shutil
    calls, folders = [], []

    def vanish():
        calls.append(1)
        folders.extend(run_folders(ui))
        shutil.rmtree(folders[0])

    monkeypatch.setattr(history, "before_write", vanish)
    evs = complete(ui, "hi")
    assert evs[-1]["type"] == "save" and evs[-1]["save_status"] == "failed"
    assert evs[-1]["reason"] == "the run's folder no longer exists"
    assert not folders[0].exists() and run_folders(ui) == []                     # never recreated
    assert calls == [1]                                                          # exactly one write attempted
    assert record_of(evs)["outcome"] == "complete"                               # the record is unchanged


# 79 ---------------------------------------------------------------------------------------------
@pytest.fixture
def clock(monkeypatch):
    ticks = itertools.count(1)
    monkeypatch.setattr(runs, "utc_now", lambda: f"2026-11-02T00:00:{next(ticks):02d}.000Z")


def test_listing_and_reading(ui, server, monkeypatch, clock):
    monkeypatch.setattr(history, "LIST_LIMIT", 2)
    save_contract(ui, "c", TEXT_CONTRACT)
    configure(ui, server)
    switch_history(ui, True)
    server.reply(answer("pong"))
    ids = [complete(ui, f"prompt {n}")[0]["run_id"] for n in range(3)]
    listing = request(ui, "/api/runs")
    assert listing["total"] == 3 and listing["shown"] == 2
    assert [r["run_id"] for r in listing["runs"]] == ids[:0:-1]                   # newest first, only the newest 2
    row = listing["runs"][0]
    assert row["source"] == "chat" and row["attempts"] == 1 and row["outcome"] == "complete" and row["problem"] is None
    assert row["folder"].endswith(ids[2])

    from test_server_config import _repairing_block
    from conftest import block_answer, result_of, run_block
    _repairing_block(ui)
    server.reply(block_answer(out="nope"), block_answer(out="pong"))
    checked_id = result_of(run_block(ui, "ask", {"text": "pong"}))["run_id"]
    opened = request(ui, f"/api/runs/{checked_id}")
    assert opened["run"]["run_id"] == checked_id and opened["problems"] == []
    assert [a["attempt"] for a in opened["attempts"]] == [1, 2]
    assert opened["attempts"][0]["check"]["overall"] == "fail" and opened["attempts"][1]["check"]["overall"] == "pass"
    row = request(ui, "/api/runs")["runs"][0]
    assert row["run_id"] == checked_id and row["attempts"] == 2 and row["check"] == "pass"

    # A damaged attempt file is listed in `problems` and the rest are returned.
    folder = next(p for p in run_folders(ui) if p.name.endswith(checked_id))
    (folder / "request-2.json").write_text("{ broken")
    damaged = request(ui, f"/api/runs/{checked_id}")
    assert [a["attempt"] for a in damaged["attempts"]] == [1]
    assert len(damaged["problems"]) == 1 and damaged["problems"][0]["file"].endswith("request-2.json")
    assert damaged["problems"][0]["code"] == "invalid" and "broken" not in json.dumps(damaged["problems"])
    (folder / "run.json").write_text("[")
    listed = next(r for r in request(ui, "/api/runs")["runs"] if r["run_id"] == checked_id)
    assert listed["problem"]["file"].endswith("run.json") and listed["attempts"] == 2

    assert failure_of(lambda: request(ui, "/api/runs/" + "f" * 12)).status == 404
    assert failure_of(lambda: request(ui, "/api/runs/" + "f" * 12)).body["code"] == "no_such_run"
    assert status_of(lambda: request(ui, "/api/runs/not-a-run")) == 400


def test_delete_a_run(ui, server, tmp_path):
    configure(ui, server)
    switch_history(ui, True)
    server.reply(answer("ok"))
    run_id = complete(ui, "hi")[0]["run_id"]
    other_id = complete(ui, "again")[0]["run_id"]
    assert request(ui, "/api/runs/delete", {"run_id": run_id}) == {"deleted": run_id}
    assert [p.name[-12:] for p in run_folders(ui)] == [other_id]
    assert failure_of(lambda: request(ui, "/api/runs/delete", {"run_id": run_id})).body["code"] == "no_such_run"
    assert status_of(lambda: request(ui, "/api/runs/delete", {"run_id": "nope"})) == 400
    assert status_of(lambda: request(ui, "/api/runs/delete", {})) == 400

    # A symbolic link in runs/ is not followed or deleted.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    link = history_dir(ui) / ("20261102-000000-" + "a" * 12)
    link.symlink_to(outside, target_is_directory=True)
    assert "a" * 12 not in [r["run_id"] for r in request(ui, "/api/runs")["runs"]]
    assert failure_of(lambda: request(ui, "/api/runs/" + "a" * 12)).status == 404
    fail = failure_of(lambda: request(ui, "/api/runs/delete", {"run_id": "a" * 12}))
    assert (fail.status, fail.body["code"]) == (409, "not_deletable")
    assert link.is_symlink() and (outside / "keep.txt").read_text() == "keep"


def test_delete_is_refused_while_a_save_is_pending_or_read_only(make_ui, server, tmp_path, monkeypatch):
    ui = make_ui()
    configure(ui, server)
    switch_history(ui, True)
    entered, proceed = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert proceed.wait(10)

    monkeypatch.setattr(history, "before_write", block)
    server.reply(answer("ok"))
    it = iter_events(held_chat(ui))
    started = read_until(it, "started")[-1]
    assert entered.wait(10)
    fail = failure_of(lambda: request(ui, "/api/runs/delete", {"run_id": started["run_id"]}))
    assert (fail.status, fail.body["code"]) == (409, "run_active")             # a save is pending
    proceed.set()
    list(it)
    monkeypatch.setattr(history, "before_write", lambda: None)
    assert wait_until(lambda: poll_attempt(ui, started["attempt_id"])["save_status"] == "saved")
    assert request(ui, "/api/runs/delete", {"run_id": started["run_id"]}) == {"deleted": started["run_id"]}

    write_ws_file(tmp_path / "ro" / "ro" / "workspace.json", "{ nope")
    ro = open_workspace(tmp_path / "ro", "ro")
    make_ui.opened.append(ro)
    ro_ui = make_ui(workspace=ro)
    fail = failure_of(lambda: request(ro_ui, "/api/runs/delete", {"run_id": "a" * 12}))
    assert (fail.status, fail.body["code"]) == (409, "read_only")


class Held:
    """Replaces history.remove_tree: blocks until released, then removes (really, or by a scripted failure)."""

    def __init__(self, monkeypatch, behave=None):
        self.entered, self.proceed, self.real = threading.Event(), threading.Event(), history.remove_tree
        self.behave = behave

        def remove(folder):
            self.entered.set()
            assert self.proceed.wait(10)
            return (self.behave or self.real)(folder)

        monkeypatch.setattr(history, "remove_tree", remove)


def saved_run(ui, server):
    """One finished, saved Chat run, still in memory."""
    configure(ui, server)
    switch_history(ui, True)
    server.reply(answer("pong"))
    first = record_of(complete(ui, "hi"))
    return first, next(p for p in run_folders(ui) if p.name.endswith(first["run_id"]))


def delete_in_thread(ui, run_id, out):
    def go():
        try:
            out["ok"] = request(ui, "/api/runs/delete", {"run_id": run_id})
        except Exception as exc:  # HTTPFailure
            out["fail"] = exc

    t = threading.Thread(target=go)
    t.start()
    return t


def test_deletion_reservation_settles_deleting_then_deleted(ui, server, monkeypatch):
    """0.2.3 port of the reservation half: while the removal is held the run reads `deleting`, then `deleted`."""
    first, folder = saved_run(ui, server)
    held = Held(monkeypatch)
    out = {}
    worker = delete_in_thread(ui, first["run_id"], out)
    assert held.entered.wait(10)
    assert ui.state.store.run_view(first["run_id"])["history_state"] == "deleting"
    held.proceed.set()
    worker.join(10)
    assert out["ok"] == {"deleted": first["run_id"]} and not folder.exists()
    assert ui.state.store.run_view(first["run_id"])["history_state"] == "deleted"


def test_a_repair_admitted_first_blocks_the_deletion(ui, server):
    """0.2.3 port to a saved block run whose automatic repair is held at the fake."""
    from test_server_config import _repairing_block
    configure(ui, server)
    switch_history(ui, True)
    _repairing_block(ui)
    server.reply(answer('{"out":"nope"}'), Reply(body=[delta('{"out":')], headers=SSE, hold=True,
                                                 after_release=[delta('"pong"}'), delta(finish="stop"), DONE]))
    resp = start_block(ui)
    assert server.wait_holding(1)
    run_id = next(e for e in iter_events(resp) if e["type"] == "started")["run_id"]
    folder = next(p for p in run_folders(ui) if p.name.endswith(run_id))
    fail = failure_of(lambda: request(ui, "/api/runs/delete", {"run_id": run_id}))
    assert (fail.status, fail.body["code"]) == (409, "run_active")
    assert folder.exists() and (folder / "request-1.json").exists()          # untouched
    server.release.set()
    resp.read()
    assert wait_until(lambda: (folder / "result.json").exists())


def test_deletion_that_fails_before_removing_anything_leaves_the_history_usable(ui, server, monkeypatch):
    first, folder = saved_run(ui, server)

    def refuse(path):
        raise history.RemoveError("Permission denied", changed=False)

    held = Held(monkeypatch, refuse)
    held.proceed.set()
    fail = failure_of(lambda: request(ui, "/api/runs/delete", {"run_id": first["run_id"]}))
    assert (fail.status, fail.body["code"]) == (500, "delete_failed") and "Permission denied" in fail.body["error"]
    assert ui.state.store.run_view(first["run_id"])["history_state"] == "intact"       # restored
    opened = request(ui, f"/api/runs/{first['run_id']}")                                # the history stays usable
    assert opened["problems"] == [] and [a["attempt"] for a in opened["attempts"]] == [1]
    monkeypatch.setattr(history, "remove_tree", held.real)
    assert status_of(lambda: request(ui, "/api/runs/delete", {"run_id": first["run_id"]})) == 200   # retry works
    assert not folder.exists()


def test_a_partial_removal_marks_the_history_incomplete_until_a_deletion_succeeds(ui, server, monkeypatch):
    first, folder = saved_run(ui, server)
    real = history.remove_tree

    def partial(path):
        os.unlink(path / "request-1.json")
        raise history.RemoveError("Input/output error", changed=True)

    monkeypatch.setattr(history, "remove_tree", partial)
    fail = failure_of(lambda: request(ui, "/api/runs/delete", {"run_id": first["run_id"]}))
    assert (fail.status, fail.body["code"]) == (500, "delete_failed")
    assert ui.state.store.run_view(first["run_id"])["history_state"] == "history_incomplete"
    assert folder.exists() and not (folder / "request-1.json").exists()

    # A retry that fails again without changing anything leaves it incomplete (v4), clears the reservation.
    def nothing(path):
        raise history.RemoveError("Permission denied", changed=False)

    monkeypatch.setattr(history, "remove_tree", nothing)
    fail = failure_of(lambda: request(ui, "/api/runs/delete", {"run_id": first["run_id"]}))
    assert (fail.status, fail.body["code"]) == (500, "delete_failed")
    assert ui.state.store.run_view(first["run_id"])["history_state"] == "history_incomplete"
    assert poll_attempt(ui, first["id"])["save_status"] == "saved"            # earlier statuses unchanged
    assert poll_attempt(ui, first["id"])["record"] == first

    # A failure that does not say whether anything changed also leaves it incomplete, never intact.
    def unknown(path):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(history, "remove_tree", unknown)
    assert status_of(lambda: request(ui, "/api/runs/delete", {"run_id": first["run_id"]})) == 500
    assert ui.state.store.run_view(first["run_id"])["history_state"] == "history_incomplete"

    monkeypatch.setattr(history, "remove_tree", real)
    assert status_of(lambda: request(ui, "/api/runs/delete", {"run_id": first["run_id"]})) == 200
    assert ui.state.store.run_view(first["run_id"])["history_state"] == "deleted" and not folder.exists()


def test_writes_after_deletion_or_a_partial_removal_fail_and_write_nothing(ui, server):
    """After 0.2.3 no HTTP path reaches these branches (a run's history is deleted only after it ended); they stay
    as defensive code, covered here directly."""
    first, folder = saved_run(ui, server)
    store = ui.state.store
    run = store._runs[first["run_id"]]
    record = poll_attempt(ui, first["id"])["record"]
    for state, reason in ((runs.HISTORY_INCOMPLETE, "the run's history is incomplete after a failed deletion"),
                          (runs.HISTORY_DELETED, "the run's history was deleted")):
        with store._lock:
            run.history_state = state
        before = sorted(p.name for p in folder.iterdir())
        assert ui.state.history.write_attempt(run, {**record, "attempt": 2, "id": first["run_id"] + "-2"}) == \
            {"save_status": "failed", "reason": reason}
        assert ui.state.history.write_result(run, {"outcome": "complete"}) == {"save_status": "failed", "reason": reason}
        assert sorted(p.name for p in folder.iterdir()) == before


def test_remove_tree_removes_without_following_links(tmp_path):
    root = tmp_path / "x"
    (root / "sub").mkdir(parents=True)
    (root / "a.json").write_text("{}")
    (root / "sub" / "b.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    (root / "link").symlink_to(outside, target_is_directory=True)
    history.remove_tree(root)
    assert not root.exists() and (outside / "keep").read_text() == "keep"
    with pytest.raises(history.RemoveError) as caught:
        history.remove_tree(tmp_path / "missing")
    assert caught.value.changed is False
    link = tmp_path / "ln"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(history.RemoveError) as caught:
        history.remove_tree(link)
    assert caught.value.changed is False and (outside / "keep").exists()
