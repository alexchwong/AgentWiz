"""Block script steps: techspec 0.1.4, test #101 and the script parts of #94, #102, #103 (R6-3, D62, P13, R8)."""
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from agentwiz import jsonio, runner, script_step
from conftest import (final_of, failure_of, group_gone, iter_events, poll_block, read_until, request, result_of,
                      run_block, run_folders, stop_block, switch_history, wait_until, write_block)
from test_blocks import script_block

PREAMBLE = "import json, os, sys, time\n"
OK = PREAMBLE + "d = json.load(sys.stdin)\nprint(json.dumps({'x': d['a'], 'y': {'n': 1}}))\n"


def write_script(ui, code, name="sc", timeout_s=5, budget=None):
    block = script_block(name)
    block["steps"][0]["timeout_s"] = timeout_s
    if budget:
        block["budget"] = budget
    write_block(ui, name, block, {"scripts/go.py": code})


def step_of(result):
    return result["steps"][0]


def operation_of(result):
    return step_of(result)["operation"]


def run(ui, text="hi", name="sc"):
    return result_of(run_block(ui, name, {"a": text}))


def test_output_is_read_exactly(ui):
    write_script(ui, PREAMBLE + "sys.stdin.read()\nprint('{\"x\": \"t\", \"y\": {\"id\": 9007199254740993, "
                                "\"r\": 0.10000000000000000001}}')\n")
    result = run(ui)
    assert result["outcome"] == "complete", result
    assert result["outputs"]["y"] == {"format": "json", "text": '{"id":9007199254740993,"r":0.10000000000000000001}'}
    op = operation_of(result)
    assert op["outcome"] == "ok" and op["exit_code"] == 0 and op["signal"] is None
    assert op["cleanup"] == "complete" and op["group_cleaned"] is False and op["file"] == "scripts/go.py"
    assert len(op["sha256"]) == 64 and op["stderr_tail"] == ""
    assert ui.state.scripts.in_flight == 0


def test_the_input_is_the_blocks_inputs_as_one_exact_object(ui):
    code = PREAMBLE + "d = json.load(sys.stdin)\nprint(json.dumps({'x': json.dumps([list(d), [ord(c) for c in d['a']]]), 'y': 1}))\n"
    write_script(ui, code)
    assert run(ui, "q\u00e9")["outputs"]["x"]["text"] == '[["a"], [113, 233]]'


@pytest.mark.parametrize("code, reason_word", [
    (PREAMBLE + "sys.stderr.write('E' * 10 + 'tail-marker')\nsys.exit(3)\n", "exit"),
    (PREAMBLE + "print('not json')\n", "JSON"),
    (PREAMBLE + "print('[1]')\n", "object"),
    (PREAMBLE + "print(json.dumps({'x': 'a'}))\n", "declared"),
    (PREAMBLE + "print(json.dumps({'x': 'a', 'y': 1, 'z': 2}))\n", "declared"),
])
def test_script_failures_are_script_error_with_the_rule_not_the_output(ui, code, reason_word):
    write_script(ui, code)
    result = run(ui)
    op = operation_of(result)
    assert result["outcome"] == "failed" and step_of(result)["failure"] == "script_error"
    assert op["outcome"] == "script_error" and result["outputs"] == {}
    assert reason_word.lower() in (op["reason"] or "").lower(), op
    if reason_word == "exit":
        assert op["exit_code"] == 3 and op["stderr_tail"].endswith("tail-marker")
    assert ui.state.scripts.in_flight == 0


def test_a_text_output_given_a_number_fails_its_check(ui):
    write_script(ui, PREAMBLE + "print(json.dumps({'x': 5, 'y': 1}))\n")
    result = run(ui)
    assert result["outcome"] == "failed" and step_of(result)["failure"] == "output_failed"
    issues = step_of(result)["output_checks"]["x"]["issues"]
    assert issues[0]["location"] == "outputs.x" and issues[0]["message"] == "a text output must be a string"


def test_output_over_the_limit_is_script_error_and_the_group_ends(ui, monkeypatch, spawned):
    monkeypatch.setattr(script_step, "OUTPUT_LIMIT", 1000)
    write_script(ui, PREAMBLE + "sys.stdout.write('a' * 5000)\nsys.stdout.flush()\ntime.sleep(60)\n")
    result = run(ui)
    op = operation_of(result)
    assert op["outcome"] == "script_error" and "larger" in op["reason"]
    assert wait_until(lambda: group_gone(spawned[0]))


def test_stderr_keeps_its_tail(ui, monkeypatch):
    monkeypatch.setattr(script_step, "STDERR_TAIL", 100)
    write_script(ui, PREAMBLE + "sys.stderr.write('a' * 500 + 'END')\nsys.exit(1)\n")
    op = operation_of(run(ui))
    assert len(op["stderr_tail"]) == 100 and op["stderr_tail"].endswith("END") and op["stderr_truncated"] is True


def test_the_environment_is_an_allow_list(ui, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-SENTINEL-ENV")
    monkeypatch.setenv("PYTHONPATH", "/nonexistent")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    allowed = {"PATH", "HOME", "LANG", "TMPDIR", "AGENTWIZ_RUN_ID", "AGENTWIZ_BLOCK"}
    expected = {name: value for name, value in os.environ.items()
                if name in {"PATH", "HOME", "LANG", "TMPDIR"} or name.startswith("LC_")}
    launched = []
    original = script_step.spawn

    def capture_environment(argv, **kwargs):
        launched.append(dict(kwargs["env"]))
        return original(argv, **kwargs)

    monkeypatch.setattr(script_step, "spawn", capture_environment)
    write_script(ui, PREAMBLE + "print(json.dumps({'x': json.dumps(sorted(os.environ)), "
                                "'y': {'run': os.environ.get('AGENTWIZ_RUN_ID'), 'block': os.environ.get('AGENTWIZ_BLOCK')}}))\n")
    result = run(ui)
    assert result["outcome"] == "complete", result
    expected.update(AGENTWIZ_RUN_ID=result["run_id"], AGENTWIZ_BLOCK="sc")
    assert launched == [expected]
    names = jsonio.decode_exact(result["outputs"]["x"]["text"])
    assert "OPENROUTER_API_KEY" not in names and "PYTHONPATH" not in names
    assert "AGENTWIZ_RUN_ID" in names and "AGENTWIZ_BLOCK" in names and "LC_ALL" in names
    # CoreFoundation may add this in the child; it must not be passed at launch.
    runtime_added = {"__CF_USER_TEXT_ENCODING"} if sys.platform == "darwin" else set()
    assert set(names) <= allowed | {n for n in names if n.startswith("LC_")} | runtime_added
    out = jsonio.decode_exact(result["outputs"]["y"]["text"])
    assert out == {"run": result["run_id"], "block": "sc"}


def test_a_script_that_never_reads_a_large_input_does_not_hang(ui):
    write_script(ui, PREAMBLE + "print(json.dumps({'x': 'done', 'y': 1}))\n")
    result = run(ui, "a" * (3 * 1024 * 1024))
    assert result["outcome"] == "complete"


def test_a_script_that_writes_to_both_streams_while_input_is_sent_completes(ui):
    code = PREAMBLE + ("sys.stderr.write('e' * 200000)\nsys.stdout.write(' ' * 200000)\n"
                       "d = json.load(sys.stdin)\nprint(json.dumps({'x': 'ok', 'y': 1}))\n")
    write_script(ui, code)
    assert run(ui, "b" * (1024 * 1024))["outcome"] == "complete"


# --- processes ----------------------------------------------------------------------------------------------------
LEAVES_CHILD = PREAMBLE + ("import subprocess\nc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                           "print(json.dumps({'x': str(c.pid), 'y': 1}))\n")


def test_a_child_left_in_the_group_is_ended_and_the_slot_is_held_until_then(ui, spawned):
    write_script(ui, LEAVES_CHILD)
    result = run(ui)
    op = operation_of(result)
    assert result["outcome"] == "complete" and op["group_cleaned"] is True and op["cleanup"] == "complete"
    child = int(result["outputs"]["x"]["text"])
    assert group_gone(spawned[0])
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0) if not _zombie(child) else (_ for _ in ()).throw(ProcessLookupError())
    assert ui.state.scripts.in_flight == 0


def _zombie(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return False


def test_a_timeout_ends_the_script_and_its_child(ui, fire_timers, spawned):
    code = PREAMBLE + ("import subprocess\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                       "time.sleep(60)\n")
    write_script(ui, code, timeout_s=7)
    resp = run_block(ui, "sc", {"a": "x"}, raw=True)
    it = iter_events(resp)
    read_until(it, "operation")
    assert wait_until(lambda: spawned)
    fire_timers.fire(7)
    result = final_of(list(it))["result"]
    op = operation_of(result)
    assert result["outcome"] == "failed" and step_of(result)["failure"] == "timeout" and op["outcome"] == "timeout"
    assert wait_until(lambda: group_gone(spawned[0])) and ui.state.scripts.in_flight == 0
    assert all(t.cancelled or t.fired for t in fire_timers.timers if t.seconds == 7)


def test_stop_ends_the_group_and_the_run_is_stopped(ui, spawned):
    write_script(ui, PREAMBLE + "time.sleep(60)\n")
    resp = run_block(ui, "sc", {"a": "x"}, raw=True)
    it = iter_events(resp)
    run_id = read_until(it, "started")[-1]["run_id"]
    read_until(it, "operation")
    assert wait_until(lambda: spawned)
    assert stop_block(ui, run_id) == {"status": "stopping"}
    result = final_of(list(it))["result"]
    assert result["outcome"] == "stopped" and step_of(result)["failure"] == "stopped"
    assert operation_of(result)["outcome"] == "stopped"
    assert group_gone(spawned[0]) and ui.state.scripts.in_flight == 0


def test_the_time_budget_ends_a_running_script(ui, fire_timers, spawned):
    write_script(ui, PREAMBLE + "time.sleep(60)\n", budget={"max_seconds": 33})
    resp = run_block(ui, "sc", {"a": "x"}, raw=True)
    it = iter_events(resp)
    read_until(it, "operation")
    assert wait_until(lambda: spawned)
    fire_timers.fire(33)
    result = final_of(list(it))["result"]
    assert result["outcome"] == "budget_exceeded" and step_of(result)["failure"] == "budget_exceeded"
    assert group_gone(spawned[0])


def test_a_stop_that_wins_before_the_launch_starts_no_process(ui, monkeypatch, spawned, tmp_path):
    marker = tmp_path / "ran"
    write_script(ui, PREAMBLE + f"open({str(marker)!r}, 'w').close()\nprint(json.dumps({{'x': 'a', 'y': 1}}))\n")
    original = runner.executor.execute_step

    def stop_first(ctx):
        stop_block(ui, ctx.run.run_id)
        return original(ctx)

    monkeypatch.setattr(runner.executor, "execute_step", stop_first)
    result = run(ui)
    assert result["outcome"] == "stopped" and not marker.exists() and spawned == []
    assert ui.state.scripts.in_flight == 0


def test_a_script_edited_after_admission_is_not_started(ui, monkeypatch, spawned):
    write_script(ui, OK)
    original = runner.executor.execute_step

    def edit_then_run(ctx):
        (ui.workspace.path / "blocks" / "sc" / "scripts" / "go.py").write_text(OK + "# edited\n")
        return original(ctx)

    monkeypatch.setattr(runner.executor, "execute_step", edit_then_run)
    result = run(ui)
    op = operation_of(result)
    assert result["outcome"] == "failed" and step_of(result)["failure"] == "script_error"
    assert op["outcome"] == "rejected" and "changed" in op["reason"] and spawned == []
    assert ui.state.scripts.in_flight == 0


def test_a_launch_failure_is_rejected_and_frees_the_slot(ui, monkeypatch):
    write_script(ui, OK)

    def fail(argv, **kw):
        raise FileNotFoundError(2, "No such file")

    monkeypatch.setattr(script_step, "spawn", fail)
    result = run(ui)
    op = operation_of(result)
    assert op["outcome"] == "rejected" and step_of(result)["failure"] == "script_error"
    assert result["outcome"] == "failed" and ui.state.scripts.in_flight == 0


# --- scratch ------------------------------------------------------------------------------------------------------
def test_the_scratch_folder_is_temporary_unless_the_run_is_saved(ui):
    write_script(ui, PREAMBLE + "print(json.dumps({'x': os.getcwd(), 'y': 1}))\nopen('f.txt', 'w').write('hi')\n")
    where = run(ui)["outputs"]["x"]["text"]
    assert "agentwiz-scratch-" in where and not os.path.exists(where)
    switch_history(ui, True)
    result = run(ui)
    folder = next(p for p in run_folders(ui) if p.name.endswith("-" + result["run_id"]))
    kept = folder / "scratch" / "run"
    assert result["outputs"]["x"]["text"] == str(kept) and (kept / "f.txt").read_text() == "hi"
    assert oct(kept.stat().st_mode & 0o777) == "0o700"


# --- #94 (script half) --------------------------------------------------------------------------------------------
def test_a_script_block_gets_503_when_four_scripts_are_held(ui):
    write_script(ui, OK)
    held = [ui.state.scripts.try_acquire() for _ in range(4)]
    assert all(held)
    fail = failure_of(lambda: run_block(ui, "sc", {"a": "x"}))
    assert fail.status == 503 and fail.body["budget"] == "script processes"
    for _ in held:
        ui.state.scripts.release()
    assert ui.state.store.active_block_runs() == []
    assert run(ui)["outcome"] == "complete"


def test_a_rejected_script_run_starts_nothing(ui, tmp_path, spawned):
    block = script_block("sc")
    block["inputs"]["a"]["contract"] = {"format": "json"}
    write_block(ui, "sc", block, {"scripts/go.py": OK})
    result = result_of(run_block(ui, "sc", {"a": "{bad"}))
    assert result["outcome"] == "rejected" and spawned == [] and ui.state.scripts.in_flight == 0
