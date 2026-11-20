"""Server configuration boundary and memory-only OpenRouter credential behavior."""
import json
import concurrent.futures

import pytest

from agentwiz.server_config import ServerConfigError, ServerSettings, initialize, load
from conftest import (Reply, answer, complete, failure_of, get_state, request, save_profile,
                      save_roles, write_ws_file, save_contract, JSON_CONTRACT, json_answer,
                      record_of, configure)


def test_configured_endpoint_and_key_boundary(make_ui, make_server, tmp_path, monkeypatch):
    backend = make_server().reply(Reply(body=b'{"data":[{"id":"example"}]}',
                                        headers={"Content-Type": "application/json"}))
    servers = {kind: ServerSettings(backend.url + "/v1") for kind in
               ("lmstudio", "openrouter", "openai_compat")}
    ui = make_ui(servers=servers, config_path=tmp_path / "config.ini")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    state = get_state(ui)
    assert state["openrouter_key_source"] == "none"
    assert failure_of(lambda: request(ui, "/api/config", {"revision": state["revision"],
                                                           "base_url": "http://example.com/v1"})).status == 400
    assert failure_of(lambda: request(ui, "/api/models", {"kind": "openai_compat",
                                                           "base_url": "http://example.com/v1"})).status == 400
    assert request(ui, "/api/models", {"kind": "openai_compat"})["models"][0]["id"] == "example"
    state = request(ui, "/api/config", {"revision": state["revision"], "kind": "openai_compat", "model": "m"})
    backend.reply(answer())
    assert complete(ui, "hello")
    assert backend.requests[-1]["path"].startswith("/v1/")
    secret = "sk-secret-example-123"
    state = request(ui, "/api/openrouter-key", {"revision": state["revision"], "api_key": secret})
    assert state["openrouter_key_source"] == "session" and secret not in json.dumps(state)
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-secret")
    state = request(ui, "/api/openrouter-key", {"revision": state["revision"], "api_key": ""})
    assert state["openrouter_key_source"] == "env:OPENROUTER_API_KEY"
    for route in ("/api/config", "/api/profiles/save", "/api/models", "/api/providers"):
        body = {"kind": "openai_compat", "revision": state["revision"], "name": "p", "base_url": ""}
        assert failure_of(lambda: request(ui, route, body)).status == 400


def test_initialize_preserves_existing_config(tmp_path):
    path = tmp_path / "config" / "model-servers.ini"
    initialize(path)
    assert load(path)["lmstudio"].base_url == "http://127.0.0.1:1234/v1"
    path.write_text("user-edited", encoding="utf-8")
    initialize(path)
    assert path.read_text(encoding="utf-8") == "user-edited"


@pytest.mark.parametrize("change", [
    lambda text: text.replace("http://127.0.0.1:1234/v1", "http://secret@localhost/v1"),
    lambda text: text.replace("http://127.0.0.1:1234/v1", "http://localhost:bad/v1"),
    lambda text: text.replace("http://127.0.0.1:1234/v1", "http://localhost/has space"),
    lambda text: text + "\n[openrouter]\nbase_url = https://example.com/v1\n",
    lambda text: text.replace("[openrouter]", "[openrouter]\napi_key = secret"),
])
def test_invalid_config_is_private_and_preserved(tmp_path, change):
    path = tmp_path / "servers.ini"
    initialize(path)
    invalid = change(path.read_text())
    path.write_text(invalid)
    with pytest.raises(ServerConfigError) as error:
        load(path)
    assert "secret" not in str(error.value)
    initialize(path)
    assert path.read_text() == invalid


def test_concurrent_initialization_publishes_complete_file(tmp_path):
    path = tmp_path / "config" / "servers.ini"
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: (initialize(path), load(path)), range(12)))
    assert path.stat().st_mode & 0o777 == 0o600


def test_legacy_profiles_use_machine_endpoint_after_restart(make_ui, server, tmp_path, restart):
    servers = {kind: ServerSettings(server.url + "/v1") for kind in
               ("lmstudio", "openrouter", "openai_compat")}
    ui = make_ui(servers=servers)
    folder = ui.workspace.path / "model-profiles"
    # The file is external legacy input; re-open it through the normal restart path.
    write_ws_file(folder / "profiles.json", json.dumps({"format_version": 2, "profiles": {
        "old": {"kind": "openai_compat", "base_url": "http://old.example/v1", "model": "m", "provider": ""}}}))
    ui = restart(ui)
    assert get_state(ui)["legacy_server_profiles"] == ["old"]
    save_roles(ui, {"fast": "old"})
    assert ui.state.resolve_role("fast").endpoint.base_url == server.url + "/v1"
    state = save_profile(ui, "old", kind="openai_compat", model="m2")
    assert "base_url" not in json.loads((folder / "profiles.json").read_text())["profiles"]["old"]
    assert state["profiles"]["modified"] is False
    ui = restart(ui)
    assert ui.state.resolve_role("fast").endpoint.base_url == server.url + "/v1"


# --- R-6h and R-6 (0.2.3 spec v15, 4.2 G-1): a repair after the configured model server changed ---------------
def _repairing_block(ui):
    """An unrestricted v4 model block: no Local only port and no protection, so no destination check runs. Its
    output contract fails the first answer, and one repair is allowed."""
    from conftest import write_block
    save_profile(ui, "p", kind="openai_compat", model="m")
    save_roles(ui, {"fast": "p"})
    write_block(ui, "ask", {"format_version": 4, "name": "ask", "description": "asks a model",
                            "inputs": {"text": {"contract": {"format": "text"}, "local_only": False}},
                            "outputs": {"out": {"contract": {"format": "text", "contains": ["pong"]},
                                                "local_only": False}},
                            "steps": [{"id": "request", "kind": "model", "role": "fast", "system": {"text": ""},
                                       "prompt": {"text": "Say {{inputs.text}}"}, "response_mode": "named_json",
                                       "repair": {"max_repairs": 1}}]})


def _held_failing_answer():
    from conftest import DONE, delta
    return Reply(body=[], hold=True, headers={"Content-Type": "text/event-stream"},
                 after_release=[delta('{"out":"nope"}'), delta(finish="stop"), DONE])


def _reconfigure_while_held(ui, server, replacement):
    assert server.wait_holding(1)
    configure(ui, replacement)
    server.release.set()


def test_headless_repair_after_server_change_is_unchanged(ui, server, make_server):
    """R-6h: pins 0.2.2's headless record, which G-1 leaves as it is."""
    import threading
    from conftest import result_of, run_block
    configure(ui, server)
    _repairing_block(ui)
    other = make_server()
    server.reply(_held_failing_answer())
    out = {}
    worker = threading.Thread(target=lambda: out.update(evs=run_block(ui, "ask", {"text": "pong"})))
    worker.start()
    _reconfigure_while_held(ui, server, other)
    worker.join(30)
    result = result_of(out["evs"])
    assert result["outcome"] == "failed", result
    assert result["steps"][0]["failure"] == "internal_error", result["steps"][0]
    assert len(server.requests) == 1 and other.requests == []


def _spy(monkeypatch):
    from agentwiz import runs
    calls = {"reserve_attempt": [], "abandon_run": []}
    for name in calls:
        original = getattr(runs.AttemptStore, name)

        def wrapper(self, run, *args, _name=name, _original=original):
            if run.kind == "block":
                calls[_name].append(run.run_id)
            return _original(self, run, *args)
        monkeypatch.setattr(runs.AttemptStore, name, wrapper)
    return calls


def _workshop_test(ui):
    from test_workshop import start_planned
    started = start_planned(ui, "test", {"key": "ask", "inputs": {"text": "pong"}})
    return started


@pytest.mark.parametrize("changed", [True, False], ids=["changed", "unchanged"])
def test_workshop_repair_refuses_changed_server_before_reservation(ui, server, make_server, monkeypatch, changed):
    """R-6: through Workshop Test, a repair after the configured server changed is refused `server_changed` before
    its attempt is reserved; nothing reaches either server. The control re-applies the original configuration."""
    from test_workshop import completed
    configure(ui, server)
    _repairing_block(ui)
    other = make_server().reply(answer('{"out":"pong"}'))
    server.reply(_held_failing_answer(), answer('{"out":"pong"}'))
    calls = _spy(monkeypatch)
    started = _workshop_test(ui)
    _reconfigure_while_held(ui, server, other if changed else server)
    batch = completed(ui, started["batch_id"])
    row = batch["rows"][0]
    step = ui.state.store.run_envelope(row["run_id"])["result"]["steps"][0]
    run_ids = set(calls["reserve_attempt"])
    assert run_ids == {row["run_id"]}, calls
    if changed:
        assert step["failure"] == "server_changed", step
        assert len(step["attempts"]) == 1 and step["repairs_used"] == 0
        assert len(calls["reserve_attempt"]) == 1 and calls["abandon_run"] == []
        assert len(server.requests) == 1 and other.requests == []
    else:
        assert step["outcome"] == "ok", step
        assert step["repairs_used"] == 1 and len(step["attempts"]) == 2
        assert len(calls["reserve_attempt"]) == 2 and calls["abandon_run"] == []
        assert len(server.requests) == 2 and other.requests == []
