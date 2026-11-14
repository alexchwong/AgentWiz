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


def test_repair_refuses_changed_server_before_dispatch(ui, server, make_server):
    configure(ui, server)
    saved = save_contract(ui, "c", JSON_CONTRACT)               # ratio maximum 1 fails this answer
    server.reply(json_answer('{"id":1,"ratio":2}'))
    record = record_of(complete(ui, "extract", source="checked_request",
                                contract=("c", saved["revision"], saved["digest"])))
    assert record["check"]["overall"] == "fail"
    other = make_server()
    configure(ui, other)
    for route in ("/api/repair/preview", "/api/repair"):
        assert failure_of(lambda: request(ui, route, {"attempt_id": record["id"]})).status == 400
    assert other.requests == [] and len(server.requests) == 1