"""Opt-in release check against managed webSpell and OpenRouter."""
import json
import os
import time

import pytest

from agentwiz import agent_capabilities, outbound_policy, webspell
from agentwiz.mcp_client import MCPClient, OpContext, Snapshot
from test_024_surfaces import call, listeners, origin
from test_live import _env
from scripts.agent_eval import Client

pytestmark = pytest.mark.live

PUBLIC_URL = "https://example.com/"
QUERY = "IANA example domains"


def test_live_webspell_release(tmp_path, server, monkeypatch):
    key, = _env("OPENROUTER_API_KEY")
    url = os.environ.get("AGENTWIZ_LIVE_MCP_URL", "http://127.0.0.1:8765/mcp")
    snapshot = Snapshot(webspell.SERVER, url)
    mcp = MCPClient()
    mcp.discover(OpContext(time.monotonic() + 30), snapshot)
    assert webspell.compatible(mcp, snapshot)
    operations = [
        ("web_search_results", {"query": QUERY}, "sources"),
        ("web_evidence_passages", {"query": "Example Domain", "urls": [PUBLIC_URL], "max_passages": 2}, "passages"),
        ("web_page_text", {"url": PUBLIC_URL}, "text"),
    ]
    for tool, args, field in operations:
        result = mcp.call(OpContext(time.monotonic() + 60), snapshot, tool, args)
        assert result.outcome == "ok", (tool, result.outcome)
        payload = result.result.get("structuredContent") or {}
        expected_status = {"complete", "partial"} if field == "passages" else {"complete"}
        assert payload.get("status") in expected_status and payload.get(field), (
            tool, payload.get("status"), payload.get("reason"))
        if field == "text":
            assert "example domain" in payload["text"].lower()
        print(f"PASS {tool} at {url}")

    with listeners(tmp_path, server.url + "/v1") as (workshop, chat):
        state = workshop.state
        state.set_openrouter_key(key, state.view()["revision"])
        state.save_mcp_servers({webspell.SERVER: {"url": url}}, state.view()["mcp"]["revision"])
        status, discovery = call(chat, "/api/chat/webspell", {"mode": "cloud"})
        assert status == 200, discovery
        base = origin(chat)
        client = Client(base, chat.authority.entry(base)["session"])
        settings = client.json("/api/chat/settings")
        revision = settings["revision"]
        model = os.environ.get("AGENTWIZ_LIVE_OPENROUTER_MODEL") or settings["roles"]["fast"]["cloud"]["model"]
        page_tool = agent_capabilities.mcp_name(url, webspell.SERVER, "web_page_text")
        search_tool = agent_capabilities.mcp_name(url, webspell.SERVER, "web_search_results")
        dispatched = []
        original_call = state.mcp_client.call

        def observed_call(ctx, destination, tool, args):
            dispatched.append((destination.url, tool, args))
            return original_call(ctx, destination, tool, args)

        monkeypatch.setattr(state.mcp_client, "call", observed_call)

        def new_chat():
            return client.json("/api/agent/conversation", {"format_version": 1})["conversation"]["id"]

        def cloud_turn(tool, message, deny=False):
            body = {"format_version": 1, "conversation": new_chat(), "revision": revision, "mode": "cloud",
                    "message": message, "capabilities": [{"name": tool}],
                    "model_choice": {"kind": "openrouter", "model": model}}
            final = None
            with client.call("/api/agent/run", body, timeout=90) as stream:
                for line in stream:
                    event = json.loads(line)
                    if event.get("state") == "awaiting_approval":
                        client.json("/api/agent/approve", {"format_version": 1, "run_id": event["run_id"],
                                    "call_id": event["call_id"], "decision": "deny"})
                        assert deny, "Exact public page read unexpectedly required approval"
                    if event["type"] == "final":
                        final = event
            assert final is not None, "Cloud Chat ended without a final event"
            return final

        final = cloud_turn(page_tool, f"Use the offered page tool to read {PUBLIC_URL} and report its heading.")
        assert final["stop_reason"] == "answered" and "example domain" in (final["answer"] or "").lower(), final
        assert any(row["capability"] == page_tool and row["outcome"] == "completed"
                   for row in final["view"]["trace"]), final
        assert len(dispatched) == 1 and dispatched[0][:2] == (url, "web_page_text"), dispatched
        assert dispatched[0][2]["url"] == PUBLIC_URL
        print("PASS Cloud Chat public page read", final["view"]["served_route"])

        final = cloud_turn(search_tool, f"Call the offered search tool for {QUERY!r} with domains ['iana.org']. "
                           "If refused, report the refusal without attempting another search.", deny=True)
        assert any(row["capability"] == search_tool and row["outcome"] == "denied"
                   for row in final["view"]["trace"]), final
        assert len(dispatched) == 1, "Denied search reached webSpell"
        print("PASS Cloud Chat approval denial")

        dest = outbound_policy.model_identity(state.role_capture("fast", "local").snapshot)
        outbound_policy.update(state, {"destination": dest, "expected_revision": None, "trusted": True})
        revision = client.json("/api/chat/settings")["revision"]
        body = {"format_version": 1, "revision": revision, "mode": "local", "conversation": new_chat()}
        inventory = client.json("/api/agent/inventory", body)
        assert inventory["preflight"]["ok"], inventory
        offered = inventory["preflight"]["offered"]
        assert all(agent_capabilities.mcp_name(url, webspell.SERVER, tool) not in offered
                   for tool in webspell.SCHEMAS)
        status, error = call(chat, "/api/agent/run",
                             {**body, "message": "Use the web tool", "capabilities": [{"name": page_tool}]})
        assert status == 409 and error["code"] == "capability_unavailable", error
        assert len(dispatched) == 1 and not server.requests
        print("PASS Local only inventory and dispatch refusal")
