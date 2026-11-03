"""The writer barrier and admission gates once shutdown has begun: techspec 0.1.4, 10.10 (R3; review v3 R3, review v4)."""
import threading

import pytest

from agentwiz import errors, state as state_module, writers
from conftest import (Reply, add_server, answer, configure, failure_of, get_state, request, run_block, save_profile,
                      save_roles, wait_until, write_block)
from test_blocks import MODEL_FILES, model_block


def test_the_barrier_counts_registers_and_refuses_after_close():
    barrier = writers.WriterBarrier()
    first = barrier.register("a")
    assert barrier.count() == 1 and barrier.outstanding() == ["a"]
    inherited = first.inherit("b")
    first.release()
    first.release()                                               # a second release does nothing
    assert barrier.count() == 1
    barrier.close()
    with pytest.raises(writers.WritersClosed):
        barrier.register("late")
    again = inherited.inherit("c")                                # inherit works while closing: its holder is counted
    assert barrier.count() == 2
    inherited.release()
    drained = threading.Event()
    threading.Thread(target=lambda: (barrier.wait_drained(5), drained.set()), daemon=True).start()
    assert not drained.wait(0.2)
    again.release()
    assert drained.wait(5) and barrier.count() == 0


def test_every_writing_route_answers_503_once_shutdown_has_begun(ui, server, make_fake_mcp):
    configure(ui, server)
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    write_block(ui, "b", model_block("b"), MODEL_FILES)
    save_roles(ui, {"fast": "p"})
    rev = get_state(ui)["revision"]
    block_rev = request(ui, "/api/blocks")["blocks"][0]["revision"]
    assert ui.state.shutdown(grace_s=0.1, wait=False) == []
    paths = [
        ("/api/profiles/save", {"name": "q", "revision": rev, "kind": "openai_compat", "model": "m",
                                "base_url": server.url + "/v1", "provider": ""}),
        ("/api/roles/save", {"roles": {}, "revision": get_state(ui)["roles"]["revision"]}),
        ("/api/history/switch", {"save_runs": True, "revision": get_state(ui)["history"]["revision"]}),
        ("/api/complete", {"source": "chat", "prompt": "x", "system": "", "revision": rev}),
        ("/api/blocks/run", {"name": "b", "revision": block_rev, "inputs": {"text": "x"}}),
        ("/api/runs/delete", {"run_id": "0" * 12}),
    ]
    for path, body in paths:
        try:
            fail = failure_of(lambda: request(ui, path, body))
        except AssertionError:
            raise AssertionError(f"{path} succeeded after shutdown began") from None
        except Exception as exc:
            raise AssertionError(f"{path}: {type(exc).__name__}") from None
        assert fail.status == 503 and fail.body["code"] == "busy", (path, fail.body)
        assert "shutting down" in fail.body["error"]
    assert server.requests == []
    assert ui.state.shutdown(grace_s=0.1, wait=False) == []       # idempotent: no second cancellation or grace


def test_the_mcp_routes_and_new_block_admission_are_closed(ui, make_fake_mcp):
    fake = make_fake_mcp("modern")
    add_server(ui, "s", fake.url, idle_timeout_s=60, call_deadline_s=60, discovery_deadline_s=60)
    ui.state.shutdown(grace_s=0.1, wait=False)
    fail = failure_of(lambda: request(ui, "/api/mcp/tools", {"server": "s", "revision": get_state(ui)["mcp"]["revision"]}))
    assert fail.status == 503 and fail.body["code"] == "busy"
    with pytest.raises(errors.ShuttingDown):
        ui.state.mcp_ops.reserve()
    assert not ui.state.scripts.try_acquire()
    assert fake.requests == []


def test_a_write_hook_failure_does_not_leak_a_registration(ui, monkeypatch):
    def refuse(label):
        raise RuntimeError("hook")

    monkeypatch.setattr(state_module, "before_write", refuse)
    fail = failure_of(lambda: request(ui, "/api/history/switch", {"save_runs": True,
                                                                   "revision": get_state(ui)["history"]["revision"]}))
    assert fail.status == 500
    assert ui.state.writers.count() == 0
