"""AgentWiz 0.2.4 installed boundaries and private browser reconnect."""
from contextlib import contextmanager
import json
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request

from agentwiz import outbound_policy, server_config, webspell
from agentwiz.state import ProcessState
from agentwiz.ui.server import Handler, UIServer
from agentwiz.ui.surfaces import ChatListener, origin
from agentwiz.workspace import open_workspace

from test_page import browser  # noqa: F401


@contextmanager
def listeners(tmp_path, url):
    config = tmp_path / "config"
    server_config.initialize(config)
    servers = server_config.load(config)
    servers["lmstudio"] = server_config.ServerSettings(url)
    workspace = open_workspace(tmp_path / "workspaces", "test")
    workshop = UIServer(workspace=workspace, profiles_dir=tmp_path / "profiles",
                        mcp_servers_dir=tmp_path / "mcp", servers=servers, production=True)
    chat = ChatListener(0, workshop, Handler)
    workshop.chat_listener = chat
    pairs = [(listener, threading.Thread(target=listener.serve_forever, daemon=True))
             for listener in (workshop, chat)]
    try:
        for _, thread in pairs:
            thread.start()
        yield workshop, chat
    finally:
        for listener, thread in pairs:
            listener.shutdown()
            thread.join()
        chat.server_close()
        workshop.server_close()
        workshop.close_workspace()


def call(listener, path, body=None, *, token=None, request_origin=None):
    base = origin(listener)
    headers = {"Content-Type": "application/json",
               "X-Session-Token": token if token is not None else listener.authority.entry(base)["session"]}
    if request_origin:
        headers["Origin"] = request_origin
    request = urllib.request.Request(base + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, json.load(reply)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_two_origins_and_tool_inventory(tmp_path, server):
    with listeners(tmp_path, server.url + "/v1") as (workshop, chat):
        chat_token = chat.authority.entry(origin(chat))["session"]
        assert call(workshop, "/api/state", token=chat_token)[0] == 403
        assert call(workshop, "/api/state", request_origin=origin(chat))[0] == 403
        assert call(chat, "/api/library/add", {"format_version": 1, "entries": []})[0] == 403
        assert call(workshop, "/api/agent/conversation")[0] == 403
        status, settings = call(chat, "/api/chat/settings")
        assert status == 200 and len(settings["roles"]) == 6 and not settings["starter_install_failures"]
        status, inventory = call(chat, "/api/agent/inventory", {
            "format_version": 1, "revision": settings["revision"], "mode": "cloud",
        })
        assert status == 200 and inventory["preflight"]["ok"], inventory
        assert len(inventory["preflight"]["offered"]) > 24
        starters = [c for c in inventory["capabilities"] if c["kind"] == "block"]
        assert len(starters) == 13


def test_private_reconnect_then_stop(browser, tmp_path, server, monkeypatch):
    # Hold the model transport before it can produce a response. Reload uses the already accepted run.
    go = threading.Event()
    entered = threading.Event()
    import modelspell

    def pending(endpoint, model, messages, *, cancel, **kwargs):
        entered.set()
        go.wait(25)
        from modelspell import Cancelled
        stopped = Cancelled()
        stopped.sent = False
        raise stopped

    monkeypatch.setattr(modelspell, "complete_with_tools", pending)
    with listeners(tmp_path, server.url + "/v1") as (workshop, chat):
        state: ProcessState = workshop.state
        destination = outbound_policy.model_identity(state.role_capture("fast", "local").snapshot)
        outbound_policy.update(state, {"destination": destination, "expected_revision": None, "trusted": True})
        page = browser.new_page()
        try:
            page.goto(chat.authority.launch_url(origin(chat)))
            page.locator("#mode").select_option("local")
            page.locator("#task").fill("Private synthetic text")
            page.locator("#run").click()
            assert entered.wait(10)
            first_id = state.agent.view()["active_run"]
            assert first_id and state.agent.view()["conversation"]["private"]
            page.reload()
            page.locator("#stop").wait_for(state="visible")
            page.locator("#stop").click()
            from conftest import wait_until
            assert wait_until(lambda: state.agent._run(first_id).token.cancelled, 5)
            go.set()
            page.locator("#stop").wait_for(state="hidden")
            assert state.agent.view()["conversation"]["recent_runs"] == [first_id]
            assert page.locator("#mode").input_value() == "local"
            page.locator("#mode").select_option("cloud")
            assert page.locator("#run").is_disabled()
            assert not server.requests
        finally:
            go.set()
            page.close()


def test_webspell_pin_and_exact_argument_provenance():
    assert set(webspell.SCHEMAS) == {"web_search_results", "web_evidence_passages", "web_page_text"}
    assert webspell.prompt_urls("Read https://example.org/a") == {"https://example.org/a"}
    assert webspell.prompt_urls("Read http://127.0.0.1/a and https://user@example.org/a") == set()
    assert webspell.result_urls({"sources": [{"url": "https://example.org/a"}],
                                 "text": "https://example.org/b"}) == {"https://example.org/a"}


def test_full_cloud_inventory_with_webspell_and_attachment(tmp_path, server):
    with listeners(tmp_path, server.url + "/v1") as (workshop, chat):
        state = workshop.state
        state.save_mcp_servers({"webspell": {"url": server.url + "/mcp"}}, state.view()["mcp"]["revision"])
        snap = state.mcp_snapshot_for_block("webspell")
        tools = [{"name": name, "inputSchema": schema, "description": name}
                 for name, schema in webspell.SCHEMAS.items()]
        state.mcp_client.observe(snap.identity, tools, "legacy", "2025-03-26", server_name="webspell")
        ident = call(chat, "/api/agent/conversation", {"format_version": 1})[1]["conversation"]["id"]
        path = origin(chat) + "/api/agent/documents"
        header = {"X-Session-Token": chat.authority.entry(origin(chat))["session"],
                  "X-Conversation": ident, "X-Filename": "public.txt", "X-Local-Only": "false",
                  "Content-Type": "application/octet-stream"}
        upload = urllib.request.Request(path, data=b"Public synthetic fixture", headers=header)
        with urllib.request.urlopen(upload, timeout=10) as reply:
            document = json.load(reply)["document"]
        # An unchecked staged file is invisible to the model's inventory until selected.
        revision = call(chat, "/api/chat/settings")[1]["revision"]
        body = {"format_version": 1, "revision": revision, "mode": "cloud", "items": [document["id"]]}
        inventory = call(chat, "/api/agent/inventory", body)[1]
        assert inventory["preflight"]["ok"], inventory
        assert len(inventory["preflight"]["offered"]) == 38
        assert inventory["preflight"]["definition_bytes"] < 65_536
        assert not server.requests
        # A switch change disables the integration even if it was previously offered.
        assert call(chat, "/api/chat/tools", {"webspell": False})[0] == 200
        excluded = call(chat, "/api/agent/inventory", body)[1]
        assert len(excluded["preflight"]["offered"]) == 35
        # The trusted Local route never advertises this loopback service.
        assert call(chat, "/api/chat/tools", {"webspell": True})[0] == 200
        dest = outbound_policy.model_identity(state.role_capture("fast", "local").snapshot)
        outbound_policy.update(state, {"destination": dest, "expected_revision": None, "trusted": True})
        local = call(chat, "/api/agent/inventory", {**body, "mode": "local"})[1]
        assert local["preflight"]["ok"] and len(local["preflight"]["offered"]) == 35
        assert not server.requests


def test_openrouter_file_key_requires_private_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENROUTER_API_KEY=synthetic-key\n")
    path.chmod(0o644)
    assert server_config.openrouter_file_key(path) is None
    path.chmod(0o600)
    assert server_config.openrouter_file_key(path) == "synthetic-key"
    assert server_config.openrouter_file_key(path, forbidden_roots=(tmp_path,)) is None
    link = tmp_path / "link"
    link.symlink_to(path)
    assert server_config.openrouter_file_key(link) is None


def _chat_model_stub(monkeypatch, answer, calls):
    import modelspell
    from agentwiz import model_call

    def complete(endpoint, model, messages, *, on_text, **arguments):
        calls.append((endpoint.kind, model, list(messages), arguments))
        on_text(answer)
        return modelspell.Result(answer, None, None, None, "stop", False, model, None, None,
                                 "chat_completions", None, None)

    monkeypatch.setattr(model_call, "resolve_request", lambda _snapshot, arguments, **_kw: (arguments, None))
    monkeypatch.setattr(modelspell, "complete", complete)
    monkeypatch.setattr(modelspell, "complete_with_tools", lambda endpoint, model, messages, *, tools, **kwargs:
                        SimpleNamespace(result=complete(endpoint, model, messages, **kwargs), clean=True, tool_calls=[]))


def test_chat_picker_transcript_and_saved_sidebar(browser, tmp_path, server, monkeypatch):
    import modelspell
    from playwright.sync_api import expect

    calls = []
    _chat_model_stub(monkeypatch, """## Answer
**Bold** and [public](https://example.org/).

| A | B |
| --- | --- |
| one | two |

<img src="https://example.org/unsafe.png">
![remote](https://example.org/image.png)
[unsafe](javascript:alert%281%29)
""", calls)
    lookups = []
    catalog = {
        "openrouter": ("demo/alternative", "Cloud Alternative", ["low", "medium"]),
        "lmstudio": ("qwen-local", "Local Qwen", ["off"]),
        "openai_compat": ("gguf-alias", "GGUF Alias", ["high"]),
    }

    def list_models(endpoint, **_kwargs):
        lookups.append(endpoint.kind)
        identifier, name, levels = catalog[endpoint.kind]
        return [modelspell.ModelInfo(identifier, name, None, None, True, True, tuple(levels), None, {})]

    monkeypatch.setattr(modelspell, "list_models", list_models)
    with listeners(tmp_path, server.url + "/v1") as (workshop, chat):
        other_kind = workshop.state.chat_snapshot(workshop.state.view()["revision"], "fast", "local",
                                                  {"kind": "openai_compat", "model": "gguf-alias"})
        outbound_policy.update(workshop.state, {"destination": outbound_policy.model_identity(other_kind),
                                                "expected_revision": None, "trusted": True})
        page = browser.new_page()
        page_errors = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            page.goto(chat.authority.launch_url(origin(chat)))
            page.locator('[data-test="toggle-history"]').click()
            expect(page.locator('[data-test="chat-history"]')).to_be_hidden()
            page.locator('[data-test="toggle-history"]').click()
            expect(page.locator('[data-test="chat-history"]')).to_be_visible()

            page.locator('[data-test="openrouter-key"]').click()
            page.locator('[data-test="openrouter-key-input"]').fill("synthetic-key-not-a-real-credential")
            page.locator("#save-key").click()
            expect(page.locator("#key-dialog")).to_be_hidden()
            page.locator('[data-test="chat-settings"]').click()
            page.locator("#builtins").uncheck()
            page.locator("#webspell").uncheck()
            page.locator('[data-close="settings"]').click()

            page.locator('[data-test="model-picker"]').click()
            expect(page.get_by_role("option", name="Cloud Alternative", exact=False)).to_be_visible()
            page.locator('[data-test="model-source"]').select_option("lmstudio")
            expect(page.get_by_role("option", name="Local Qwen", exact=False)).to_be_visible()
            page.locator('[data-test="model-source"]').select_option("openai_compat")
            expect(page.get_by_role("option", name="GGUF Alias", exact=False)).to_be_visible()
            page.locator('[data-test="model-search"]').fill("gguf")
            page.locator('[data-test="model-search"]').press("ArrowDown")
            page.locator('[data-test="model-search"]').press("Enter")
            expect(page.locator("#mode")).to_have_value("local")
            expect(page.locator('[data-test="chat-reasoning"]')).to_be_disabled()
            page.locator("#mode").select_option("cloud")

            page.locator('[data-test="model-picker"]').click()
            page.locator('[data-test="model-search"]').fill("alternative")
            expect(page.get_by_role("option", name="Cloud Alternative", exact=False)).to_be_visible()
            page.locator('[data-test="model-search"]').press("Enter")
            page.locator('[data-test="chat-reasoning"]').select_option("medium")
            page.locator('[data-test="new-chat"]').wait_for()
            page.locator('[data-test="chat-message"]').fill("Synthetic cloud question")
            page.locator('[data-test="chat-send"]').click()

            expect(page.locator('[data-test="prompt-bubble"]')).to_have_text("Synthetic cloud question")
            expect(page.locator('[data-test="turn-answer"]').locator("h2")).to_have_text("Answer")
            expect(page.locator('[data-test="turn-answer"]').locator("strong")).to_have_text("Bold")
            expect(page.locator('[data-test="turn-answer"]').locator("td")).to_have_count(2)
            expect(page.locator('[data-test="turn-answer"]').locator("a")).to_have_count(1)
            expect(page.locator('[data-test="turn-answer"]').locator("img")).to_have_count(0)
            expect(page.locator('[data-test="chat-message"]')).to_be_empty()
            expect(page.locator('[data-test="saved-chats"]').get_by_role("button")).to_have_count(0)

            page.locator('[data-test="save-chat"]').check()
            saved = page.locator('[data-test="saved-chats"]').get_by_role("button", name="Synthetic cloud question", exact=True)
            expect(saved).to_be_enabled()
            page.locator('[data-test="new-chat"]').click()
            expect(page.locator('[data-test="prompt-bubble"]')).to_have_count(0)
            saved.click()
            expect(page.locator('[data-test="prompt-bubble"]')).to_have_text("Synthetic cloud question")
            expect(page.locator('[data-test="model-picker"]')).to_have_text("demo/alternative")
            page.reload()
            expect(page.locator('[data-test="turn-answer"]').locator("h2")).to_have_text("Answer")
            assert calls[0][0:2] == ("openrouter", "demo/alternative")
            assert calls[0][3]["reasoning"] == "medium"
            assert lookups[:3] == ["openrouter", "lmstudio", "openai_compat"]
            assert not page_errors
        finally:
            page.close()


def test_saved_chat_restart_context_and_private_boundary(tmp_path, server, monkeypatch):
    from agentwiz import chat_history

    calls = []
    _chat_model_stub(monkeypatch, "A cloud answer.", calls)

    def run(workshop, conversation_id, message, **extra):
        state = workshop.state
        body = {"revision": state.view()["revision"], "conversation": conversation_id,
                "mode": "cloud", "message": message, "builtins": False, "webspell": False, **extra}
        agent_run = state.agent.prepare_run(body, None)
        assert agent_run.run()["stop_reason"] == "answered"
        return agent_run.run_id

    with listeners(tmp_path, server.url + "/v1") as (workshop, chat):
        workspace = workshop.state.workspace.path
        ident = call(chat, "/api/agent/conversation", {"format_version": 1})[1]["conversation"]["id"]
        key = "synthetic-archive-redaction-canary"
        state = workshop.state
        state.set_openrouter_key(key, state.view()["revision"])
        first = run(workshop, ident, "Public question " + key)
        archive = workspace / f"chat-history-{ident}.json"
        assert not archive.exists()
        assert call(chat, "/api/chat/history/save", {"conversation": ident, "enabled": True})[0] == 200
        assert archive.stat().st_mode & 0o777 == 0o600
        assert key not in archive.read_text()
        assert call(workshop, "/api/chat/history")[0] == 403

    with listeners(tmp_path, server.url + "/v1") as (workshop, chat):
        state = workshop.state
        status, view = call(chat, "/api/chat/history/open", {"id": ident, "close_private": None})
        assert status == 200 and view["conversation"]["restored_runs"] == [first]
        assert call(chat, "/api/agent/runs/" + first)[1]["message"] == "Public question ***"
        run(workshop, ident, "Follow-up", use_context=True)
        assert [(row["role"], row["content"]) for row in calls[-1][2][1:]] == [
            ("user", "Public question ***"), ("assistant", "A cloud answer."), ("user", "Follow-up")]
        run(workshop, ident, "Independent turn", use_context=False)
        assert [row["content"] for row in calls[-1][2][1:]] == ["Independent turn"]

        local = outbound_policy.model_identity(state.role_capture("fast", "local").snapshot)
        outbound_policy.update(state, {"destination": local, "expected_revision": None, "trusted": True})
        private = run(workshop, ident, "Private transcript canary", mode="local")
        assert state.agent.view()["conversation"]["private"]
        assert not archive.exists()
        assert list(workspace.glob(f"{chat_history.PREFIX}*.json")) == []
        assert call(chat, "/api/chat/models", {"kind": "openrouter", "mode": "cloud"})[0] == 409
        assert call(chat, "/api/chat/history/save", {"conversation": ident, "enabled": True})[0] == 409
        assert call(chat, "/api/chat/history/open", {"id": ident, "close_private": None})[0] == 409
        assert state.agent.view()["conversation"]["recent_runs"][-1] == private
        assert not any("Private transcript canary" in path.read_text()
                       for path in workspace.glob("*.json") if path.is_file())
        assert call(chat, "/api/agent/conversation", {"format_version": 1})[0] == 409
        assert call(chat, "/api/agent/conversation", {"format_version": 1, "close_private": ident})[0] == 200


def test_workspace_change_preserves_private_chat_until_confirmed_close(tmp_path, server, monkeypatch):
    _chat_model_stub(monkeypatch, "Private answer.", [])
    with listeners(tmp_path, server.url + "/v1") as (workshop, chat):
        root = workshop.workspace_root
        open_workspace(root, "existing").close()
        state = workshop.state
        local = outbound_policy.model_identity(state.role_capture("fast", "local").snapshot)
        outbound_policy.update(state, {"destination": local, "expected_revision": None, "trusted": True})
        ident = call(chat, "/api/agent/conversation", {"format_version": 1})[1]["conversation"]["id"]
        run = state.agent.prepare_run({"revision": state.view()["revision"], "conversation": ident,
                                       "mode": "local", "message": "Make a private draft",
                                       "builtins": False, "webspell": False}, None)
        assert run.run()["stop_reason"] == "answered"
        draft = run.conversation.create_draft(run.run_id, "private", "txt", "Private draft.", True,
                                              lambda: True)
        for action, name in (("switch", "existing"), ("create", "new")):
            status, body = call(workshop, f"/api/workspaces/{action}", {"format_version": 1, "name": name})
            assert (status, body.get("code")) == (409, "private_close_confirmation")
        assert not (root / "new").exists()
        assert call(workshop, "/api/workspaces/switch", {"format_version": 1, "name": "test"})[0] == 200
        assert call(chat, "/api/agent/conversation")[1]["conversation"]["id"] == ident
        base = origin(chat)
        request = urllib.request.Request(f"{base}/api/agent/drafts/{draft.id}?conversation={ident}",
                                         headers={"X-Session-Token": chat.authority.entry(base)["session"]})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.read() == b"Private draft."
        assert call(chat, "/api/agent/conversation", {"format_version": 1, "close_private": ident})[0] == 200
        status, body = call(workshop, "/api/workspaces/switch", {"format_version": 1, "name": "existing"})
        assert status == 200 and body["workspace"]["name"] == "existing"


def test_interrupted_archive_refuses_reopen(tmp_path, server, monkeypatch):
    _chat_model_stub(monkeypatch, "Public answer", [])
    with listeners(tmp_path, server.url + "/v1") as (workshop, chat):
        state = workshop.state
        ident = call(chat, "/api/agent/conversation", {"format_version": 1})[1]["conversation"]["id"]
        run = state.agent.prepare_run({"revision": state.view()["revision"], "conversation": ident,
                                       "mode": "cloud", "message": "Public question",
                                       "builtins": False, "webspell": False}, None)
        run.run()
        state.agent.change_chat_save(ident, True)
        state.agent.chat_history.suspend(ident)
    with listeners(tmp_path, server.url + "/v1") as (_, chat):
        assert call(chat, "/api/chat/history")[1]["chats"][0]["ready"] is False
        status, error = call(chat, "/api/chat/history/open", {"id": ident, "close_private": None})
        assert status == 409 and error["code"] == "chat_history_interrupted"
        assert call(chat, "/api/chat/history/delete", {"id": ident})[0] == 200
        assert not call(chat, "/api/chat/history")[1]["chats"]
