"""Block tool steps: techspec 0.1.4, test #100 and the tool parts of #94, #102, #103 (R6-3, R6-30, R7-5, P10)."""
import threading
from decimal import Decimal

import pytest

from agentwiz import mcp_ops, runner
from conftest import (add_server, failure_of, final_of, iter_events, mcp_call, poll_block, read_until, request,
                      result_of, run_block, save_profile, stop_block, text_result, simple_tool, wait_until, write_block)

FAST = {"idle_timeout_s": 60, "call_deadline_s": 60, "discovery_deadline_s": 60}


def tool_block(name="t", output="json", arguments=None, inputs=None):
    return {"format_version": 3, "name": name,
            "inputs": inputs or {"n": {"contract": {"format": "text"}}},
            "outputs": {"out": {"contract": {"format": output}}},
            "steps": [{"id": "call", "kind": "tool", "server": "docs", "tool": "lookup",
                       "arguments": arguments if arguments is not None else {"id": "{{inputs.n}}"},
                       "output": "out"}]}


def numbers_block():
    return tool_block(inputs={"obj": {"contract": {"format": "json"}}},
                      arguments={"id": "{{inputs.obj.id}}", "ratio": "{{inputs.obj.ratio}}",
                                 "label": "n={{inputs.obj.id}}"})


def setup_tool(ui, make_fake_mcp, behave, block=None, **fake):
    fake_mcp = make_fake_mcp("modern", tools=[simple_tool("lookup", behave=behave)], **fake)
    add_server(ui, "docs", fake_mcp.url, **FAST)
    write_block(ui, "t", block or tool_block())
    return fake_mcp


def step_of(result):
    return result["steps"][0]


def test_typed_fills_reach_the_server_as_exact_numbers(ui, make_fake_mcp):
    fake = setup_tool(ui, make_fake_mcp, lambda call: call.result({"content": [], "structuredContent": {"ok": 1},
                                                                    "isError": False}), numbers_block())
    obj = '{"id":9007199254740993,"ratio":0.10000000000000000001}'
    result = result_of(run_block(ui, "t", {"obj": obj}))
    assert result["outcome"] == "complete", result
    arguments = fake.methods("tools/call")[0]["body"]["params"]["arguments"]
    assert arguments["id"] == 9007199254740993 and arguments["ratio"] == Decimal("0.10000000000000000001")
    assert arguments["label"] == "n=9007199254740993"
    operation = step_of(result)["operation"]
    assert operation["arguments_text"] == ('{"id":9007199254740993,"ratio":0.10000000000000000001,'
                                           '"label":"n=9007199254740993"}')
    assert operation["outcome"] == "ok" and operation["server"] == "docs" and operation["tool"] == "lookup"
    assert operation["era"] == "modern" and operation["result"]["structured_text"] == '{"ok":1}'
    assert result["outputs"]["out"] == {"format": "json", "text": '{"ok":1}'}
    assert [f["where"] for f in step_of(result)["fills"]] == ["arguments.id", "arguments.ratio", "arguments.label"]


def test_structured_content_text_and_fenced_json_outputs(ui, make_fake_mcp):
    replies = iter([
        {"content": [{"type": "text", "text": "ignored"}], "structuredContent": {"a": 1}, "isError": False},
        text_result('```json\n{"b": 2}\n```'),
    ])
    fake = setup_tool(ui, make_fake_mcp, lambda call: call.result(next(replies)))
    first = result_of(run_block(ui, "t", {"n": "1"}))
    assert first["outputs"]["out"]["text"] == '{"a":1}'
    second = result_of(run_block(ui, "t", {"n": "1"}))
    assert second["outcome"] == "complete" and second["outputs"]["out"]["text"] == '{"b":2}'
    assert len(fake.methods("tools/call")) == 2


def test_a_text_contract_takes_the_joined_text(ui, make_fake_mcp):
    setup_tool(ui, make_fake_mcp, lambda call: call.result({"content": [
        {"type": "text", "text": "a"}, {"type": "image", "data": "x", "mimeType": "image/png"},
        {"type": "text", "text": "b"}], "isError": False}), tool_block(output="text"))
    result = result_of(run_block(ui, "t", {"n": "1"}))
    assert result["outputs"]["out"] == {"format": "text", "text": "a\n\nb"}
    assert step_of(result)["operation"]["result"]["other_items"] == [{"type": "image"}]


def test_a_text_contract_falls_back_to_the_structured_text(ui, make_fake_mcp):
    setup_tool(ui, make_fake_mcp, lambda call: call.result({"content": [], "structuredContent": {"k": [1, 2]},
                                                             "isError": False}), tool_block(output="text"))
    assert result_of(run_block(ui, "t", {"n": "1"}))["outputs"]["out"]["text"] == '{"k":[1,2]}'


@pytest.mark.parametrize("behave, outcome, step_outcome, failure, op_outcome", [
    (lambda call: call.result({"content": [{"type": "text", "text": "boom"}], "isError": True}),
     "failed", "failed", "tool_error", "tool_error"),
    (lambda call: call.result(text_result("not json")), "failed", "failed", "output_failed", "ok"),
    (lambda call: call.raw(404, b"nope"), "failed", "failed", "call_rejected", "rejected"),
])
def test_tool_failures_are_never_resent(ui, make_fake_mcp, behave, outcome, step_outcome, failure, op_outcome):
    fake = setup_tool(ui, make_fake_mcp, behave)
    result = result_of(run_block(ui, "t", {"n": "1"}))
    step = step_of(result)
    assert (result["outcome"], step["outcome"], step["failure"]) == (outcome, step_outcome, failure), result
    assert step["operation"]["outcome"] == op_outcome and result["outputs"] == {}
    assert len(fake.methods("tools/call")) == 1                       # nothing is resent (P10)
    assert ui.state.mcp_ops.view()["workers"] <= 1


def test_a_call_that_may_have_run_is_unknown(ui, make_fake_mcp):
    def drop(call):
        call.start_sse()                                  # headers sent, then the stream ends without a result
    setup_tool(ui, make_fake_mcp, drop)
    result = result_of(run_block(ui, "t", {"n": "1"}))
    assert result["outcome"] == "unknown" and step_of(result)["outcome"] == "unknown"
    assert step_of(result)["failure"] == "call_unknown" and step_of(result)["operation"]["outcome"] == "unknown"


def test_an_unknown_server_is_a_rejected_run_with_no_traffic(ui, make_fake_mcp):
    fake = setup_tool(ui, make_fake_mcp, lambda call: call.result(text_result("{}")))
    write_block(ui, "t", {**tool_block(), "steps": [{**tool_block()["steps"][0], "server": "ghost"}]})
    result = result_of(run_block(ui, "t", {"n": "1"}))
    assert result["outcome"] == "rejected" and result["issues"][0]["location"] == "steps[0].server"
    assert fake.methods("tools/call") == [] and ui.state.mcp_ops.view()["workers"] == 0


def test_a_fill_failure_is_rejected_and_releases_its_reservation(ui, make_fake_mcp):
    setup_tool(ui, make_fake_mcp, lambda call: call.result(text_result("{}")),
               tool_block(inputs={"obj": {"contract": {"format": "json"}}}, arguments={"x": "{{inputs.obj.nope}}"}))
    result = result_of(run_block(ui, "t", {"obj": "{}"}))
    assert result["outcome"] == "rejected" and result["issues"][0]["location"].startswith("steps[0].arguments")
    assert ui.state.mcp_ops.view()["workers"] == 0


# --- #94 (tool half) --------------------------------------------------------------------------------------------
def test_a_tool_block_gets_503_when_every_worker_is_taken(ui, make_fake_mcp):
    fake = setup_tool(ui, make_fake_mcp, lambda call: (call.start_sse(), call.hold()))
    held = [ui.state.mcp_ops.reserve() for _ in range(mcp_ops.WORKER_LIMIT)]
    assert all(held)
    fail = failure_of(lambda: run_block(ui, "t", {"n": "1"}))
    assert fail.status == 503 and fail.body["budget"] == "MCP workers" and fail.body["code"] == "busy"
    for r in held:
        r.release()
    assert fake.methods("tools/call") == [] and ui.state.store.active_block_runs() == []
    fake.release.set()


# --- #102 / #103 (tool half) -----------------------------------------------------------------------------------
def test_stop_abandons_a_call_that_was_sent_and_keeps_its_outcome(ui, make_fake_mcp):
    fake = setup_tool(ui, make_fake_mcp, lambda call: (call.start_sse(), call.hold()))
    resp = run_block(ui, "t", {"n": "1"}, raw=True)
    it = iter_events(resp)
    run_id = read_until(it, "started")[-1]["run_id"]
    operation = read_until(it, "operation")[-1]
    assert operation["kind"] == "tool" and operation["state"] == "running"
    assert wait_until(lambda: fake.holding >= 1)
    assert stop_block(ui, run_id) == {"status": "stopping"}
    result = final_of(list(it))["result"]
    step = step_of(result)
    assert result["outcome"] == "stopped" and step["outcome"] == "stopped" and step["failure"] == "stopped"
    assert step["operation"]["outcome"] == "unknown"                  # it may have run; the record says so
    assert step["operation"]["cancel_status"] is not None
    fake.release.set()
    assert wait_until(lambda: ui.state.mcp_ops.view()["workers"] == 0)    # freed when the worker's thread exits


def test_the_time_budget_abandons_a_sent_call_as_unknown(ui, make_fake_mcp, fire_timers):
    fake = setup_tool(ui, make_fake_mcp, lambda call: (call.start_sse(), call.hold()))
    block = tool_block()
    block["budget"] = {"max_seconds": 55}
    write_block(ui, "t", block)
    resp = run_block(ui, "t", {"n": "1"}, raw=True)
    it = iter_events(resp)
    read_until(it, "started")
    read_until(it, "operation")
    assert wait_until(lambda: fake.holding >= 1)
    fire_timers.fire(55)
    result = final_of(list(it))["result"]
    assert result["outcome"] == "budget_exceeded" and step_of(result)["failure"] == "budget_exceeded"
    assert step_of(result)["operation"]["outcome"] == "unknown"
    assert result["cancel_reason"] == "budget_exceeded"
    fake.release.set()


def test_stop_before_the_call_starts_releases_the_reservation(ui, make_fake_mcp, monkeypatch):
    """Cancellation wins the step lock: the call is never started."""
    fake = setup_tool(ui, make_fake_mcp, lambda call: call.result(text_result("{}")))
    original = runner.executor.execute_step
    ids = []

    def stop_first(ctx):
        stop_block(ui, ctx.run.run_id)
        return original(ctx)

    monkeypatch.setattr(runner.executor, "execute_step", stop_first)
    result = result_of(run_block(ui, "t", {"n": "1"}))
    assert result["outcome"] == "stopped" and step_of(result)["operation"] is None
    assert fake.methods("tools/call") == [] and ui.state.mcp_ops.view()["workers"] == 0
    assert ids == []


def test_an_accepted_output_survives_a_late_stop(ui, make_fake_mcp, monkeypatch):
    fake = setup_tool(ui, make_fake_mcp, lambda call: call.result(text_result('{"a":1}')))
    monkeypatch.setattr(runner, "before_run_end", lambda: stop_block(ui, ids[0]) if ids else None)
    ids = []
    resp = run_block(ui, "t", {"n": "1"}, raw=True)
    it = iter_events(resp)
    ids.append(read_until(it, "started")[-1]["run_id"])
    result = final_of(list(it))["result"]
    assert result["outcome"] == "complete" and result["stop_requested_at"] is not None
    assert len(fake.methods("tools/call")) == 1
