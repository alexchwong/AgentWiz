"""AgentWiz 0.2.4 installed boundaries and private browser reconnect."""
from contextlib import contextmanager
import json
import threading
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
