"""Script supervision and cleanup ownership: techspec 0.1.4, test #101 (v3 to v5 parts; R8, review v2 R4, R5,
review v3 R1, R2, review v4 R2). Deadlines are fired through `make_timer`; only process readiness uses real time."""
import os
import signal
import threading
import time

import pytest

from agentwiz import script_step
from conftest import (final_of, group_gone, iter_events, read_until, request, result_of, run_block, run_folders,
                      switch_history, wait_until, failure_of, get_state)
from test_scripts import PREAMBLE, operation_of, step_of, write_script


def drive(hub, it, seconds=(2.0, 7.0), limit=30.0):
    """Read the stream to its end on a thread while firing the lowered deadlines (by their length) on demand."""
    box = {}
    thread = threading.Thread(target=lambda: box.update(evs=list(it)), daemon=True)
    thread.start()
    end = time.monotonic() + limit
    while thread.is_alive() and time.monotonic() < end:
        for timer in hub.live():
            if timer.seconds in seconds:
                timer.fire()
        time.sleep(0.05)
    thread.join(5)
    assert not thread.is_alive(), "the run did not end"
    return box["evs"]


def kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_a_writer_outside_the_group_does_not_stop_the_step(ui, fire_timers, tmp_path):
    pidfile = tmp_path / "escaped.pid"
    code = PREAMBLE + ("import subprocess\n"
                       "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
                       "start_new_session=True)\n"
                       f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
                       "print(json.dumps({'x': 'a', 'y': 1}))\n")
    write_script(ui, code)
    it = iter_events(run_block(ui, "sc", {"a": "x"}, raw=True))
    try:
        result = final_of(drive(fire_timers, it))["result"]
        op = operation_of(result)
        assert op["pipes_held"] is True and op["cleanup"] == "complete" and op["outcome"] == "ok"
        assert result["outcome"] == "complete"                       # judged on the bytes read
        assert ui.state.scripts.in_flight == 0
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_every_pipe_is_closed_once_and_a_reused_descriptor_survives(ui, spawned, monkeypatch):
    """The supervisor is the sole closer: no `os.close` on a recorded descriptor number, and a file given the same
    number later is untouched after the old objects are collected."""
    import gc
    recorded = []
    closes = []
    real_spawn, real_close = script_step.spawn, os.close

    class Spy:
        def __init__(self, obj):
            self.obj, self.n, self.count = obj, obj.fileno(), 0
            real = obj.close

            def close():
                self.count += 1
                real()
            obj.close = close

    def spawn(argv, **kw):
        proc = real_spawn(argv, **kw)
        recorded.extend(Spy(p) for p in (proc.stdin, proc.stdout, proc.stderr))
        return proc

    monkeypatch.setattr(script_step, "spawn", spawn)
    monkeypatch.setattr(os, "close", lambda fd: (closes.append(fd), real_close(fd))[1])
    write_script(ui, PREAMBLE + "sys.stdin.close()\nprint(json.dumps({'x': 'a', 'y': 1}))\n")
    assert result_of(run_block(ui, "sc", {"a": "x"}))["outcome"] == "complete"
    assert [s.count for s in recorded] == [1, 1, 1]
    numbers = {s.n for s in recorded}
    assert not numbers & set(closes)                              # never os.close on an object's descriptor
    opened = []
    for _ in range(64):
        f = open(os.devnull, "wb")
        opened.append(f)
        if f.fileno() in numbers:
            break
    recorded.clear()
    gc.collect()
    for f in opened:
        f.write(b"x")                                              # still open and writable
        f.close()


def test_an_error_after_the_launch_ends_the_group_before_the_slot_is_freed(ui, monkeypatch, spawned):
    seen = {}
    real = script_step.after_spawn

    def boom(pgid):
        seen["pgid"] = pgid
        raise RuntimeError("injected")

    monkeypatch.setattr(script_step, "after_spawn", boom)
    write_script(ui, PREAMBLE + "time.sleep(60)\n")
    result = run_block_events_result(ui)
    assert result["outcome"] == "failed" and step_of(result)["failure"] == "internal_error"
    assert group_gone(seen["pgid"]) and ui.state.scripts.in_flight == 0
    assert ui.state.cleanups.cleanups() == []


def run_block_events_result(ui):
    return result_of(run_block(ui, "sc", {"a": "x"}, timeout=60))


def test_unconfirmed_cleanup_is_handed_to_an_owner_that_always_finishes(ui, fire_timers, spawned, monkeypatch):
    switch_history(ui, True)
    monkeypatch.setattr(script_step, "RETAINED_POLL_S", 0.05)
    real_send = script_step.send_group
    monkeypatch.setattr(script_step, "send_group", lambda pgid, sig: None)       # the process cannot be ended
    write_script(ui, PREAMBLE + "time.sleep(60)\n", timeout_s=7)
    it = iter_events(run_block(ui, "sc", {"a": "x"}, raw=True))
    run_id = read_until(it, "started")[-1]["run_id"]
    read_until(it, "operation")
    assert wait_until(lambda: spawned)
    pgid = spawned[0]
    try:
        result = final_of(drive(fire_timers, it))["result"]
        op = operation_of(result)
        assert op["cleanup"] == "retained"
        assert result["outcome"] == "failed" and step_of(result)["failure"] in ("script_error", "timeout")
        # the run has committed, yet the slot, the writer registration and the folder protection are still held
        assert ui.state.scripts.in_flight == 1
        owners = ui.state.cleanups.cleanups()
        assert len(owners) == 1
        assert any("script" in label or "cleanup" in label for label in ui.state.writers.outstanding())
        fail = failure_of(lambda: request(ui, "/api/runs/delete", {"run_id": run_id}))
        assert fail.status == 409 and fail.body["code"] == "run_active"
        # the process is really still alive; once the double lets it die the owner finishes by itself
        assert not group_gone(pgid)
        monkeypatch.setattr(script_step, "send_group", real_send)
        kill_group(pgid)
        assert wait_until(lambda: ui.state.scripts.in_flight == 0, 10)
        assert ui.state.cleanups.cleanups() == [] and ui.state.writers.outstanding() == []
        assert request(ui, "/api/runs/delete", {"run_id": run_id}) == {"deleted": run_id}
    finally:
        kill_group(pgid)


def test_shutdown_services_an_owner_whose_thread_never_started(ui, fire_timers, spawned, monkeypatch):
    monkeypatch.setattr(script_step.Retained, "start", lambda self: (_ for _ in ()).throw(RuntimeError("no thread")))
    real_send = script_step.send_group
    monkeypatch.setattr(script_step, "send_group", lambda pgid, sig: None)
    write_script(ui, PREAMBLE + "time.sleep(60)\n", timeout_s=7)
    it = iter_events(run_block(ui, "sc", {"a": "x"}, raw=True))
    read_until(it, "operation")
    assert wait_until(lambda: spawned)
    pgid = spawned[0]
    try:
        result = final_of(drive(fire_timers, it))["result"]
        assert operation_of(result)["cleanup"] == "retained"
        assert ui.state.scripts.in_flight == 1 and len(ui.state.cleanups.cleanups()) == 1
        monkeypatch.setattr(script_step, "send_group", real_send)
        # Give shutdown itself the bounded window to kill, reap and service the owner.
        assert ui.state.shutdown(grace_s=10, wait=False) == []
        assert ui.state.scripts.in_flight == 0
        assert ui.state.cleanups.cleanups() == [] and ui.state.shutdown(grace_s=0.2, wait=False) == []
    except BaseException:
        real_send(pgid, signal.SIGKILL)
        raise


# --- #114 (script half) ---------------------------------------------------------------------------------------------
def test_closing_the_server_ends_a_waiting_script_its_child_and_the_group(ui, spawned, tmp_path):
    switch_history(ui, True)
    pidfile = tmp_path / "child.pid"
    code = PREAMBLE + ("import subprocess\n"
                       "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                       f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
                       "time.sleep(60)\n")
    write_script(ui, code)
    it = iter_events(run_block(ui, "sc", {"a": "x"}, raw=True))
    run_id = read_until(it, "started")[-1]["run_id"]
    read_until(it, "operation")
    assert wait_until(lambda: spawned and pidfile.exists())
    child = int(pidfile.read_text())
    ui.shutdown()
    assert ui.server_close() == []
    run = ui.state.store._runs[run_id]
    assert run.result["outcome"] == "stopped" and run.result["cancel_reason"] == "user_stop"
    assert group_gone(spawned[0]) and ui.state.scripts.in_flight == 0
    folder = next(p for p in run_folders(ui) if p.name.endswith("-" + run_id))
    assert (folder / "result.json").is_file()
    assert ui.server_close() == []
    with pytest.raises(Exception) as late:
        ui.state.scripts.try_acquire() or (_ for _ in ()).throw(RuntimeError("closed"))
    assert "closed" in str(late.value)
    assert child > 0
