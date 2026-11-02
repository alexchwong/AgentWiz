"""The MCP client against FakeMCP: techspec 0.1.2 tests #41-#43 (and #42's client-level cases).
Identities and operation contexts are built in the test; the routes join at C2 (test_mcp_ops.py)."""
import base64
import time
from decimal import Decimal

import pytest

from agentwiz import mcp_client
from agentwiz.mcp_client import MCPClient, MCPError, OpContext, Snapshot, classify
from conftest import FakeCall, simple_tool, text_result

LEGACY_MODES = ("legacy-stateless", "legacy-session")


def snap(fake, token=None, idle=5, server="s"):
    return Snapshot(server, fake.url, idle, 30, 30, token)


def ctx(seconds=20, progress=None):
    return OpContext(time.monotonic() + seconds, progress)


def run_call(client, fake, tool, arguments, token=None, progress=None):
    c = ctx(progress=progress)
    try:
        return client.call(c, snap(fake, token), tool, arguments)
    finally:
        client.release_all(c)


def outcome_of(fn):
    try:
        res = fn()
        return res.outcome, None
    except MCPError as exc:
        return exc.outcome, exc.reason


# 41 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode, era", [("modern", "modern"), ("dual", "modern"),
                                       ("legacy-stateless", "legacy"), ("legacy-session", "legacy")])
def test_mcp_era_detection(make_fake_mcp, mode, era):
    fake = make_fake_mcp(mode)
    client = MCPClient()
    c = ctx()
    found = client.discover(c, snap(fake))
    client.release_all(c)
    assert found.era == era and [t["name"] for t in found.tools] == ["echo"]
    assert bool(fake.methods("initialize")) == (era == "legacy")
    assert client.era_of(fake.url)[0] == era


def discover_fails(client, fake, token=None):
    c = ctx()
    with pytest.raises(MCPError) as exc:
        client.discover(c, snap(fake, token))
    client.release_all(c)
    return exc.value


def test_mcp_era_detection_edges(make_fake_mcp):
    client = MCPClient()
    fake = make_fake_mcp("legacy-stateless")
    fake.override("server/discover", lambda call: call.error(-32022, "no", 400, {"supported": ["2025-11-25"]}))
    c = ctx()
    assert client.discover(c, snap(fake)).era == "legacy"

    fake = make_fake_mcp("modern")
    fake.override("server/discover", lambda call: call.error(-32022, "no", 400, {"supported": ["2027-01-01"]}))
    assert discover_fails(client, fake).reason == "unsupported_protocol"
    fake.override("server/discover", lambda call: call.error(-32022, "no", 400, {"supported": ["2027-01-01"]}))
    assert outcome_of(lambda: run_call(client, fake, "echo", {})) == ("rejected", "unsupported_protocol")
    assert fake.methods("tools/call") == []

    fake = make_fake_mcp("dual")
    fake.override("server/discover", lambda call: call.error(-32020, "mismatch", 400))
    err = discover_fails(client, fake)
    assert (err.reason, err.http_status, err.jsonrpc_code) == ("http_status", 400, -32020)
    assert fake.methods("initialize") == []

    fake = make_fake_mcp("dual", token="right-token")
    assert discover_fails(client, fake).reason == "credentials"
    assert fake.methods("initialize") == []

    fake = make_fake_mcp("legacy-stateless")
    fake.override("initialize", lambda call: call.result({"protocolVersion": "2024-11-05", "capabilities": {}}))
    assert discover_fails(client, fake).reason == "unsupported_protocol"


def test_mcp_era_changed_not_resent(make_fake_mcp):
    client = MCPClient()
    fake = make_fake_mcp("modern")
    c = ctx()
    client.discover(c, snap(fake))
    fake.mode = "legacy-stateless"   # the server was replaced by a handshake-era one
    assert outcome_of(lambda: run_call(client, fake, "echo", {})) == ("rejected", "era_changed")
    assert len(fake.methods("tools/call")) == 1 and client.era_of(fake.url) is None
    c = ctx()
    assert client.discover(c, snap(fake)).era == "legacy"   # Load tools re-probes
    client.release_all(c)


# 42 --------------------------------------------------------------------------------------------
def sse_echo(call: FakeCall):
    call.start_sse()
    call.progress(1, 2, "half")
    call.final(text_result("streamed"))


@pytest.mark.parametrize("mode", ["modern", "legacy-session"])
def test_mcp_protocol_wire(make_fake_mcp, mode):
    fake = make_fake_mcp(mode, tools=[simple_tool("echo"), simple_tool("sse", behave=sse_echo)])
    client = MCPClient()
    seen = []
    assert run_call(client, fake, "echo", {"text": "a"}).result["content"][0]["text"] == "echo"
    res = run_call(client, fake, "sse", {}, progress=lambda p, t, m: seen.append((p, t, m)))
    assert res.result["content"][0]["text"] == "streamed" and seen == [("1", "2", "half")]
    call = fake.methods("tools/call")[-1]
    if mode == "modern":
        h = call["headers"]
        assert h["mcp-protocol-version"] == "2026-07-28" and h["mcp-method"] == "tools/call" and h["mcp-name"] == "sse"
        meta = call["body"]["params"]["_meta"]
        assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
        assert meta["io.modelcontextprotocol/clientInfo"]["name"] == "AgentWiz"
        assert meta["io.modelcontextprotocol/clientCapabilities"] == {}
    else:
        assert call["headers"]["mcp-protocol-version"] == "2025-11-25"
        assert call["headers"]["mcp-session-id"].startswith("session-")
        initialized = fake.methods("notifications/initialized")
        assert len(initialized) == 1


def test_mcp_protocol_modern_headers(make_fake_mcp):
    annotated = {"type": "object", "properties": {"region": {"type": "string", "x-mcp-header": "Region"},
                                                   "n": {"type": "integer", "x-mcp-header": "Count"}}}
    bad = {"type": "object", "properties": {"x": {"type": "number", "x-mcp-header": "X"}}}
    fake = make_fake_mcp("modern", tools=[simple_tool("café"), simple_tool("hdr", annotated), simple_tool("bad", bad)])
    client = MCPClient()
    c = ctx()
    found = client.discover(c, snap(fake))
    assert [n["name"] for n in found.not_offered] == ["bad"]
    assert found.header_params["hdr"] == ["region", "n"]
    run_call(client, fake, "café", {})
    sent = fake.methods("tools/call")[-1]["headers"]["mcp-name"]
    assert sent == "=?base64?" + base64.b64encode("café".encode()).decode() + "?="
    run_call(client, fake, "hdr", {"region": "eu west", "n": Decimal("3")})
    h = fake.methods("tools/call")[-1]["headers"]
    assert h["mcp-param-region"] == "eu west" and h["mcp-param-count"] == "3"
    calls = len(fake.methods("tools/call"))
    assert outcome_of(lambda: run_call(client, fake, "hdr", {"region": ["x"]})) == ("rejected", "refused_locally")
    assert outcome_of(lambda: run_call(client, fake, "bad", {})) == ("rejected", "not_offered")
    assert len(fake.methods("tools/call")) == calls


def test_mcp_protocol_legacy_server_requests(make_fake_mcp):
    def pinging(call):
        call.start_sse()
        call.event({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"})
        call.event({"jsonrpc": "2.0", "id": "srv-2", "method": "sampling/createMessage", "params": {}})
        assert wait_for(lambda: len(call.fake.answers) == 2)
        call.final(text_result("after ping"))

    fake = make_fake_mcp("legacy-session", tools=[simple_tool("p", behave=pinging)])
    client = MCPClient()
    assert run_call(client, fake, "p", {}).result["content"][0]["text"] == "after ping"
    answers = {a["body"]["id"]: a["body"] for a in fake.answers}
    assert answers["srv-1"]["result"] == {}
    assert answers["srv-2"]["error"]["code"] == -32601
    assert all(a["headers"]["mcp-session-id"].startswith("session-") for a in fake.answers)


def wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_mcp_protocol_pages_and_limits(make_fake_mcp, monkeypatch):
    fake = make_fake_mcp("modern", tools=[simple_tool(f"t{i}") for i in range(5)])
    fake.page_size = 2
    client = MCPClient()
    assert [t["name"] for t in client.discover(ctx(), snap(fake)).tools] == [f"t{i}" for i in range(5)]
    assert len(fake.methods("tools/list")) == 3
    fake.override("tools/list", lambda call: call.result({"tools": [], "nextCursor": "x"}), times=2)
    assert discover_fails(client, fake).reason == "malformed"           # a repeated cursor
    monkeypatch.setattr(mcp_client, "TOOL_LIMIT", 3)
    assert discover_fails(client, fake).reason == "too_large"
    monkeypatch.setattr(mcp_client, "TOOL_LIMIT", 1000)
    # pages each under MESSAGE_LIMIT, together over DISCOVERY_LIMIT
    monkeypatch.setattr(mcp_client, "DISCOVERY_LIMIT", 300)
    assert discover_fails(client, fake).reason == "too_large"


@pytest.mark.parametrize("row, expected", [
    (dict(stage="call", reading="result"), ("ok", None)),
    (dict(stage="call", reading="is_error"), ("tool_error", None)),
    (dict(stage="call", abandoned="stopped"), ("rejected", "not_sent_stopped")),
    (dict(stage="call", abandoned="deadline"), ("rejected", "not_sent_deadline")),
    (dict(stage="call", gate_passed=True, abandoned="stopped"), ("unknown", "stopped")),
    (dict(stage="call", gate_passed=True, abandoned="deadline"), ("unknown", "deadline")),
    (dict(stage="call", gate_passed=True, abandoned="page_closed"), ("unknown", "page_closed")),
    (dict(stage="call", transport="name_resolution"), ("rejected", "name_resolution")),
    (dict(stage="call", transport="connection_refused"), ("rejected", "connection_refused")),
    (dict(stage="call", transport="tls"), ("rejected", "tls")),
    (dict(stage="call", gate_passed=True, transport="lost"), ("unknown", "stream_ended")),
    (dict(stage="call", gate_passed=True, transport="idle_timeout"), ("unknown", "idle_timeout")),
    (dict(stage="call", gate_passed=True, status=401), ("rejected", "credentials")),
    (dict(stage="call", gate_passed=True, status=403), ("rejected", "credentials")),
    (dict(stage="call", gate_passed=True, status=422), ("rejected", "http_status")),
    (dict(stage="call", gate_passed=True, status=307), ("rejected", "redirect")),
    (dict(stage="call", gate_passed=True, status=503), ("unknown", "http_status")),
    (dict(stage="call", gate_passed=True, status=202), ("unknown", "malformed")),
    (dict(stage="call", gate_passed=True, jsonrpc_code=-32602), ("rejected", "jsonrpc_error")),
    (dict(stage="call", gate_passed=True, jsonrpc_code=-32020), ("rejected", "jsonrpc_error")),
    (dict(stage="call", gate_passed=True, jsonrpc_code=-32000), ("unknown", "jsonrpc_error")),
    (dict(stage="call", gate_passed=True, reading="malformed"), ("unknown", "malformed")),
    (dict(stage="call", gate_passed=True, reading="stream_ended"), ("unknown", "stream_ended")),
    (dict(stage="call", gate_passed=True, reading="too_large"), ("unknown", "too_large")),
    (dict(stage="call", gate_passed=True, reading="input_required"), ("unknown", "input_required")),
    (dict(stage="discovery", reading="result"), ("ok", None)),
    (dict(stage="discovery", status=500), ("error", "http_status")),
    (dict(stage="discovery", status=401), ("error", "credentials")),
    (dict(stage="discovery", transport="name_resolution"), ("error", "name_resolution")),
    (dict(stage="discovery", reading="too_large"), ("error", "too_large")),
    (dict(stage="discovery", abandoned="deadline"), ("error", "deadline")),
])
def test_mcp_classify(row, expected):
    c = classify(**row)
    assert (c.outcome, c.reason) == expected


def test_mcp_protocol_outcomes_on_wire(make_fake_mcp):
    def redirect(call):
        call.raw(307, headers={"Location": "http://127.0.0.1:1/elsewhere"})

    fake = make_fake_mcp("modern", tools=[
        simple_tool("ok"),
        simple_tool("terr", behave=lambda call: call.result({"content": [], "isError": True})),
        simple_tool("bad", behave=lambda call: call.error(-32602, "invalid params", status=422)),
        simple_tool("five", behave=lambda call: call.raw(503, b"down")),
        simple_tool("redir", behave=redirect),
        simple_tool("mal", behave=lambda call: (call.start_sse(), call.event(b"data: {not json\n\n"))),
    ])
    client = MCPClient()
    assert outcome_of(lambda: run_call(client, fake, "ok", {})) == ("ok", None)
    assert outcome_of(lambda: run_call(client, fake, "terr", {})) == ("tool_error", None)
    assert outcome_of(lambda: run_call(client, fake, "bad", {})) == ("rejected", "http_status")
    assert outcome_of(lambda: run_call(client, fake, "five", {})) == ("unknown", "http_status")
    assert outcome_of(lambda: run_call(client, fake, "redir", {})) == ("rejected", "redirect")
    assert outcome_of(lambda: run_call(client, fake, "mal", {})) == ("unknown", "malformed")
    redirected = [r for r in fake.methods("tools/call") if r["body"]["params"]["name"] == "redir"]
    assert len(redirected) == 1  # the redirect was not followed, here or elsewhere


def test_mcp_protocol_reading_limits_and_matching(make_fake_mcp, monkeypatch):
    big = "x" * 400

    def unfinished(call):
        call.start_sse()
        call.event(b"data: " + big.encode())       # never terminated

    def comment(call):
        call.start_sse()
        call.event(b": " + big.encode() + b"\n")

    def other_then_mine(call):
        call.start_sse()
        call.event({"jsonrpc": "2.0", "id": 999999999, "result": text_result("not mine")})
        call.final(text_result("mine"))

    def only_other(call):
        call.start_sse()
        call.event({"jsonrpc": "2.0", "id": 999999999, "result": text_result("not mine")})

    def single_other(call):
        call.json({"jsonrpc": "2.0", "id": 999999999, "result": text_result("x")})

    fake = make_fake_mcp("modern", tools=[
        simple_tool("big", behave=lambda call: call.result(text_result(big))),
        simple_tool("unfinished", behave=unfinished), simple_tool("comment", behave=comment),
        simple_tool("other_then_mine", behave=other_then_mine), simple_tool("only_other", behave=only_other),
        simple_tool("single_other", behave=single_other),
        simple_tool("s_iserr", behave=lambda call: call.result({"content": [], "isError": "yes"})),
        simple_tool("s_content", behave=lambda call: call.result({"content": {"type": "text"}})),
        simple_tool("s_text", behave=lambda call: call.result({"content": [{"type": "text", "text": 5}]})),
        simple_tool("s_rtype", behave=lambda call: call.result({"content": [], "resultType": "other"})),
        simple_tool("needs", behave=lambda call: call.result({"content": [], "resultType": "input_required"})),
    ])
    client = MCPClient()
    client.discover(ctx(), snap(fake))
    monkeypatch.setattr(mcp_client, "MESSAGE_LIMIT", 300)
    for tool in ("big", "unfinished", "comment"):
        assert outcome_of(lambda: run_call(client, fake, tool, {})) == ("unknown", "too_large"), tool
    monkeypatch.setattr(mcp_client, "MESSAGE_LIMIT", 33_554_432)
    assert run_call(client, fake, "other_then_mine", {}).result["content"][0]["text"] == "mine"
    assert outcome_of(lambda: run_call(client, fake, "only_other", {})) == ("unknown", "stream_ended")
    assert outcome_of(lambda: run_call(client, fake, "single_other", {})) == ("unknown", "malformed")
    for tool in ("s_iserr", "s_content", "s_text", "s_rtype"):
        assert outcome_of(lambda: run_call(client, fake, tool, {})) == ("unknown", "malformed"), tool
    assert outcome_of(lambda: run_call(client, fake, "needs", {})) == ("unknown", "input_required")


def test_mcp_protocol_discovery_entries(make_fake_mcp):
    annotated = {"type": "object", "properties": {"region": {"type": "string", "x-mcp-header": "Region"}}}
    fake = make_fake_mcp("modern", tools=[simple_tool("hdr", annotated)])
    client = MCPClient()
    # no prior Load tools: the call discovers first, then mirrors the annotated argument
    run_call(client, fake, "hdr", {"region": "eu"})
    assert [r["method"] for r in fake.requests] == ["server/discover", "tools/list", "tools/call"]
    assert fake.methods("tools/call")[-1]["headers"]["mcp-param-region"] == "eu"
    # a tool the server does not list: rejected, no tools/call
    calls = len(fake.methods("tools/call"))
    assert outcome_of(lambda: run_call(client, fake, "missing", {})) == ("rejected", "unknown_tool")
    assert len(fake.methods("tools/call")) == calls
    # -32020 removes the entry; the next call discovers again
    fake.override("tools/call", lambda call: call.error(-32020, "Header mismatch", status=400))
    assert outcome_of(lambda: run_call(client, fake, "hdr", {"region": "eu"})) == ("rejected", "http_status")
    lists = len(fake.methods("tools/list"))
    run_call(client, fake, "hdr", {"region": "eu"})
    assert len(fake.methods("tools/list")) == lists + 1

    # two identities at one URL with different tokens get their own annotations
    other = make_fake_mcp("modern")

    def by_token(token):
        header = "Alpha" if token == "token-a" else "Beta"
        return [{"name": "t", "inputSchema": {"type": "object",
                                              "properties": {"v": {"type": "string", "x-mcp-header": header}}}}]
    other.tools_for_token = by_token
    other.tools = {"t": simple_tool("t")}
    run_call(client, other, "t", {"v": "1"}, token="token-a")
    run_call(client, other, "t", {"v": "2"}, token="token-b")
    first, second = other.methods("tools/call")
    assert first["headers"]["mcp-param-alpha"] == "1" and "mcp-param-beta" not in first["headers"]
    assert second["headers"]["mcp-param-beta"] == "2" and "mcp-param-alpha" not in second["headers"]


# 43 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["modern", "legacy-session"])
def test_mcp_unknown_never_resent(make_fake_mcp, mode):
    fake = make_fake_mcp(mode, tools=[
        simple_tool("cut", behave=lambda call: call.start_sse()),          # stream ends before the response
        simple_tool("bad_gateway", behave=lambda call: call.raw(502, b"bad gateway")),
    ])
    client = MCPClient()
    assert outcome_of(lambda: run_call(client, fake, "cut", {})) == ("unknown", "stream_ended")
    assert len(fake.methods("tools/call")) == 1
    assert outcome_of(lambda: run_call(client, fake, "bad_gateway", {})) == ("unknown", "http_status")
    assert len(fake.methods("tools/call")) == 2
