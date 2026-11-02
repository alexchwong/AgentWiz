"""MCP operations through AgentWiz's routes: techspec 0.1.2 tests #44-#52 and the route-level part of #42.

Orderings are forced with FakeMCP's holds, the connection hook (mcp_client.connect_socket), Events and
the final-write hook; time limits are lowered (per-server limits of 1 second, mcp_ops constants patched).
Waits only bound how long a failing test waits."""
import json
import socket
import threading
import time
from decimal import Decimal

import pytest

from agentwiz import mcp_client, mcp_ops
from conftest import (FakeCall, add_server, cancel, failure_of, get_state, load_tools, mcp_call, mcp_state,
                      request, rest, save_servers, set_token, simple_tool, start_call, status_of, text_result,
                      wait_until)

FAST = {"idle_timeout_s": 60, "call_deadline_s": 60, "discovery_deadline_s": 60}


def hang(call: FakeCall):
    call.start_sse()
    call.hold()


def held_ping(call: FakeCall):
    call.start_sse()
    call.event({"jsonrpc": "2.0", "id": "srv-ping", "method": "ping"})
    call.hold()


class Hook:
    """Blocks mcp_client.connect_socket, once active and while `when()` holds, until released.
    With once=True only the first such connection blocks (later ones, such as notices, pass)."""

    def __init__(self, monkeypatch, when=lambda: True, once=True):
        self.real = mcp_client.connect_socket
        self.release = threading.Event()
        self.entered = threading.Event()
        self.when, self.once = when, once
        self.active = False
        self._lock = threading.Lock()
        monkeypatch.setattr(mcp_client, "connect_socket", self)

    def __call__(self, host, port, timeout):
        with self._lock:
            block = self.active and self.when() and not (self.once and self.entered.is_set())
            if block:
                self.entered.set()
        if block:
            self.release.wait(30)
        return self.real(host, port, timeout)


def final_of(evs):
    finals = [e for e in evs if e["type"] == "final"]
    assert len(finals) == 1, evs
    return finals[0]


def workers(ui):
    return ui.state.mcp_ops.view()["workers"]


def maintenance(ui):
    return ui.state.mcp_ops.view()["maintenance"]


# 42 (route level) --------------------------------------------------------------------------------
def test_mcp_protocol_final_event(ui, make_fake_mcp):
    fake = make_fake_mcp("modern", tools=[
        simple_tool("both", behave=lambda call: call.result({"content": [
            {"type": "text", "text": "a"}, {"type": "image", "data": "x", "mimeType": "image/png"},
            {"type": "text", "text": "b"}], "structuredContent": {"n": 1}, "isError": False}))])
    add_server(ui, "s", fake.url, **FAST)
    end = final_of(mcp_call(ui, "s", "both", '{"q":"x"}'))
    assert end["outcome"] == "ok" and end["era"] == "modern" and end["protocol_version"] == "2026-07-28"
    assert end["result"] == {"text": "a\n\nb", "structured_text": '{"n":1}', "is_error": False,
                             "other_items": [{"type": "image"}]}
    assert end["insertion"]["text"] == '[tool s.both arguments={"q":"x"}]\na\n\nb'
    assert end["insertion_structured"]["text"].endswith('\n{"n":1}')
    assert end["cancel_status"] is None and end["reason"] is None


def test_mcp_protocol_library_servers_share_url(ui, make_fake_mcp):
    fake = make_fake_mcp("modern")

    def by_token(token):
        header = "Alpha" if token == "token-a" else "Beta"
        return [{"name": "t", "inputSchema": {"type": "object",
                                              "properties": {"v": {"type": "string", "x-mcp-header": header}}}}]
    fake.tools_for_token = by_token
    fake.tools = {"t": simple_tool("t")}
    add_server(ui, "a", fake.url, **FAST, token_env="AW_TOKEN_A")
    add_server(ui, "b", fake.url + "/", **FAST, token_env="AW_TOKEN_B")
    import os
    os.environ["AW_TOKEN_A"], os.environ["AW_TOKEN_B"] = "token-a", "token-b"
    try:
        assert final_of(mcp_call(ui, "a", "t", '{"v":"1"}'))["outcome"] == "ok"
        assert final_of(mcp_call(ui, "b", "t", '{"v":"2"}'))["outcome"] == "ok"
    finally:
        del os.environ["AW_TOKEN_A"], os.environ["AW_TOKEN_B"]
    first, second = fake.methods("tools/call")
    assert first["headers"]["mcp-param-alpha"] == "1" and second["headers"]["mcp-param-beta"] == "2"


# 44 --------------------------------------------------------------------------------------------
def test_mcp_session_expiry(ui, make_fake_mcp):
    fake = make_fake_mcp("legacy-session")
    fake.delete_status = 405
    add_server(ui, "s", fake.url, **FAST)
    assert final_of(mcp_call(ui, "s", "echo"))["outcome"] == "ok"
    first_session = fake.methods("tools/call")[0]["headers"]["mcp-session-id"]
    fake.sessions.clear()                         # the server forgot every session
    barrier, ends = threading.Barrier(2), []

    def run():
        barrier.wait()
        ends.append(final_of(mcp_call(ui, "s", "echo"))["outcome"])

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert ends == ["ok", "ok"]
    assert len(fake.methods("initialize")) == 2   # two concurrent expiries, one new session
    expired = [r for r in fake.methods("tools/call") if r["headers"]["mcp-session-id"] == first_session]
    assert 1 <= len(expired) <= 3                 # each expired call resent at most once
    assert wait_until(lambda: [d["headers"]["mcp-session-id"] for d in fake.deletes] == [first_session])


def test_mcp_url_change_during_held_call(ui, make_fake_mcp, monkeypatch):
    a = make_fake_mcp("legacy-session", tools=[simple_tool("hang", behave=hang), simple_tool("echo")])
    b = make_fake_mcp("legacy-session")
    add_server(ui, "s", a.url, **FAST)
    resp, op_id = start_call(ui, "s", "hang")
    assert a.wait_holding(1)
    session_a = a.methods("tools/call")[0]["headers"]["mcp-session-id"]
    add_server(ui, "s", b.url, **FAST)
    assert final_of(mcp_call(ui, "s", "echo"))["outcome"] == "ok"
    assert len(b.methods("tools/call")) == 1 and a.deletes == []

    # Stop, with the worker made to exit before the notice's maintenance thread starts.
    worker_gone, real_notice = threading.Event(), mcp_client.MCPClient.send_notice

    def late_notice(self, *args, **kwargs):
        assert worker_gone.wait(10)
        return real_notice(self, *args, **kwargs)

    monkeypatch.setattr(mcp_client.MCPClient, "send_notice", late_notice)
    answer = {}
    stopper = threading.Thread(target=lambda: answer.setdefault("v", cancel(ui, op_id)))
    stopper.start()
    assert wait_until(lambda: workers(ui) == 0)
    worker_gone.set()
    stopper.join(20)
    assert answer["v"]["status"] == "delivered"
    assert final_of(rest(resp))["outcome"] == "unknown"
    assert wait_until(lambda: len(a.deletes) == 1)
    assert [r["method"] for r in a.requests if r["method"] in ("notifications/cancelled", "DELETE")] == \
        ["notifications/cancelled", "DELETE"]
    assert a.notices[0]["headers"]["mcp-session-id"] == session_a == a.deletes[0]["headers"]["mcp-session-id"]
    assert b.notices == [] and b.deletes == []


def test_mcp_token_change_during_held_call(ui, make_fake_mcp):
    fake = make_fake_mcp("legacy-session", tools=[simple_tool("hang", behave=lambda c: (c.start_sse(), c.hold(),
                                                                                           c.final(text_result("x")))),
                                                  simple_tool("echo")])
    add_server(ui, "s", fake.url, **FAST)
    set_token(ui, "s", "token-one")
    resp, op_id = start_call(ui, "s", "hang")
    assert fake.wait_holding(1)
    old = fake.methods("tools/call")[0]["headers"]["mcp-session-id"]
    set_token(ui, "s", "token-two")
    assert final_of(mcp_call(ui, "s", "echo"))["outcome"] == "ok"
    assert len(fake.methods("initialize")) == 2 and fake.deletes == []
    fake.release.set()
    assert final_of(rest(resp))["outcome"] == "ok"
    assert wait_until(lambda: len(fake.deletes) == 1)
    assert fake.deletes[0]["headers"]["mcp-session-id"] == old


def test_mcp_ending_session_never_borrowed(ui, make_fake_mcp):
    a = make_fake_mcp("legacy-session")
    b = make_fake_mcp("legacy-session")
    gate = threading.Event()
    a.override("DELETE", lambda call: (gate.wait(20), call.raw(200)))
    add_server(ui, "s", a.url, **FAST)
    mcp_call(ui, "s", "echo")
    first = a.methods("tools/call")[0]["headers"]["mcp-session-id"]
    add_server(ui, "s", b.url, **FAST)              # the session's identity stops being current
    assert wait_until(lambda: len(a.methods("DELETE")) == 1)  # passed its gate; held at the fake
    add_server(ui, "s", a.url, **FAST)              # current again
    assert final_of(mcp_call(ui, "s", "echo"))["outcome"] == "ok"
    gate.set()
    assert len(a.methods("initialize")) == 2
    later = [r for r in a.requests if r["method"] != "DELETE" and r["time"] > a.methods("DELETE")[0]["time"]]
    assert later and all(r["headers"].get("mcp-session-id") != first for r in later)


# 45 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("how", ["stop", "deadline"])
def test_mcp_gates(ui, make_fake_mcp, monkeypatch, how):
    fake = make_fake_mcp("dual")
    hook = Hook(monkeypatch)
    add_server(ui, "s", fake.url, idle_timeout_s=60, call_deadline_s=1 if how == "deadline" else 60)
    hook.active = True
    resp, op_id = start_call(ui, "s", "echo")
    assert hook.entered.wait(10)
    if how == "stop":
        assert cancel(ui, op_id)["status"] == "not_sent"
    end = final_of(rest(resp))
    hook.release.set()
    assert wait_until(lambda: workers(ui) == 0)
    assert end["outcome"] == "rejected"
    assert end["reason"] == ("not_sent_stopped" if how == "stop" else "not_sent_deadline")
    assert fake.requests == []


def test_mcp_gates_no_resend_after_abandonment(ui, make_fake_mcp, monkeypatch):
    fake = make_fake_mcp("legacy-session")
    add_server(ui, "s", fake.url, **FAST)
    mcp_call(ui, "s", "echo")
    hook = Hook(monkeypatch)

    def expire(call):
        hook.active = True
        call.raw(404, b'{"error": "Session not found"}', "application/json")

    fake.override("tools/call", expire)
    resp, op_id = start_call(ui, "s", "echo")
    assert hook.entered.wait(10)                  # re-initialising after the expiry: blocked
    cancel(ui, op_id)
    end = final_of(rest(resp))
    hook.release.set()
    assert wait_until(lambda: workers(ui) == 0)
    assert end["outcome"] == "unknown"            # the tools/call gate had been passed
    assert len(fake.methods("tools/call")) == 2 and len(fake.methods("initialize")) == 1


# 46 --------------------------------------------------------------------------------------------
def test_mcp_stop_modern(ui, make_fake_mcp):
    fake = make_fake_mcp("modern", tools=[simple_tool("hang", behave=hang)])
    add_server(ui, "s", fake.url, **FAST)
    resp, op_id = start_call(ui, "s", "hang")
    assert fake.wait_holding(1)
    began = time.monotonic()
    assert cancel(ui, op_id)["status"] == "stream_closed"
    assert wait_until(lambda: fake.disconnects)
    assert fake.disconnects[0]["time"] - began < 10      # long before the 60-second idle timeout
    end = final_of(rest(resp))
    assert end["outcome"] == "unknown" and end["cancel_status"] == "stream_closed"
    assert cancel(ui, op_id)["status"] == "stream_closed"
    assert fake.notices == []


def test_mcp_stop_legacy(ui, make_fake_mcp, monkeypatch):
    fake = make_fake_mcp("legacy-session", tools=[simple_tool("hang", behave=hang)])
    add_server(ui, "s", fake.url, **FAST)
    resp, op_id = start_call(ui, "s", "hang")
    assert fake.wait_holding(1)
    assert cancel(ui, op_id)["status"] == "delivered"
    assert final_of(rest(resp))["outcome"] == "unknown"
    assert cancel(ui, op_id)["status"] == "delivered"
    assert len(fake.notices) == 1
    assert fake.notices[0]["body"]["params"]["requestId"] == fake.methods("tools/call")[0]["body"]["id"]

    # a pending notice: the final event carries "unconfirmed"; a later Stop sees "delivered"
    monkeypatch.setattr(mcp_ops, "CANCEL_ANSWER_S", 0.2)
    fake.notice_hold.clear()
    resp, op_id = start_call(ui, "s", "hang")
    assert fake.wait_holding(1)
    assert cancel(ui, op_id)["status"] == "unconfirmed"
    end = final_of(rest(resp))
    assert end["cancel_status"] == "unconfirmed"
    assert cancel(ui, op_id)["status"] == "unconfirmed"
    fake.notice_hold.set()
    assert wait_until(lambda: cancel(ui, op_id)["status"] == "delivered")
    assert len(fake.notices) == 2


def test_mcp_stop_late_and_after_completion(ui, make_fake_mcp, monkeypatch):
    fake = make_fake_mcp("modern")
    add_server(ui, "s", fake.url, **FAST)
    entered, proceed = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert proceed.wait(10)

    monkeypatch.setattr(mcp_ops, "before_final_write", block)
    resp, op_id = start_call(ui, "s", "echo")
    assert entered.wait(10)
    assert cancel(ui, op_id)["status"] == "not_sent"
    proceed.set()
    evs = rest(resp)
    assert final_of(evs)["outcome"] == "ok" and final_of(evs)["cancel_status"] is None
    assert fake.notices == [] and fake.disconnects == []
    monkeypatch.setattr(mcp_ops, "before_final_write", lambda: None)
    resp, op_id = start_call(ui, "s", "echo")
    assert final_of(rest(resp))["outcome"] == "ok"
    assert cancel(ui, op_id)["status"] == "not_sent"
    fail = failure_of(lambda: cancel(ui, "no-such-operation"))
    assert fail.status == 404 and fail.body["code"] == "no_such_operation"


def test_mcp_stop_legacy_stalled_answer(ui, make_fake_mcp, monkeypatch):
    fake = make_fake_mcp("legacy-session", tools=[simple_tool("p", behave=held_ping)])
    add_server(ui, "s", fake.url, **FAST)
    fake.answer_hold.clear()                      # the answer POST is never acknowledged
    resp, op_id = start_call(ui, "s", "p")
    assert wait_until(lambda: len(fake.answers) == 1)
    cancel(ui, op_id)
    assert final_of(rest(resp))["outcome"] == "unknown"
    assert wait_until(lambda: {d["method"] for d in fake.disconnects} >= {"tools/call", "answer"})
    before = len(fake.requests)
    assert wait_until(lambda: workers(ui) == 0)
    assert [r["method"] for r in fake.requests[before:]] in ([], ["notifications/cancelled"])

    # with the answer's connection blocked, then Stop and release: the answer POST is never sent
    fake2 = make_fake_mcp("legacy-session", tools=[simple_tool("p", behave=held_ping)])
    add_server(ui, "t", fake2.url, **FAST)
    hook = Hook(monkeypatch, when=lambda: bool(fake2.methods("tools/call")))
    hook.active = True
    resp, op_id = start_call(ui, "t", "p")
    assert hook.entered.wait(10)
    cancel(ui, op_id)
    hook.release.set()
    assert final_of(rest(resp))["outcome"] == "unknown"
    assert wait_until(lambda: workers(ui) == 0)
    assert fake2.answers == []


def test_mcp_stop_page_gone(ui, make_fake_mcp):
    def flood(call):
        call.start_sse()
        while True:
            call.progress(1, None, "x" * 1000)
            if call.fake.release.wait(0.01):
                return

    fake = make_fake_mcp("modern", tools=[simple_tool("flood", behave=flood)])
    add_server(ui, "s", fake.url, **FAST)
    resp, op_id = start_call(ui, "s", "flood")
    resp.close()
    op = ui.state.mcp_ops.get(op_id)
    assert wait_until(lambda: op.ctx.abandoned == "page_closed")
    assert wait_until(lambda: fake.disconnects)


def test_mcp_stop_https(ui, make_fake_mcp, test_tls):
    fake = make_fake_mcp("modern", tools=[simple_tool("hang", behave=hang)], tls=test_tls)
    add_server(ui, "s", fake.url, **FAST)
    resp, op_id = start_call(ui, "s", "hang")
    assert fake.wait_holding(1)
    began = time.monotonic()
    assert cancel(ui, op_id)["status"] == "stream_closed"
    assert wait_until(lambda: fake.disconnects) and fake.disconnects[0]["time"] - began < 10
    assert final_of(rest(resp))["outcome"] == "unknown"


# 47 --------------------------------------------------------------------------------------------
def test_mcp_capacity(ui, make_fake_mcp):
    fake = make_fake_mcp("modern", tools=[simple_tool("hang", behave=hang)])
    add_server(ui, "s", fake.url, **FAST)
    streams = [start_call(ui, "s", "hang") for _ in range(8)]
    assert fake.wait_holding(8)
    fail = failure_of(lambda: mcp_call(ui, "s", "hang"))
    assert fail.status == 503 and fail.body["code"] == "busy"
    assert get_state(ui)["mcp"]["operations"]["workers"] == 8
    for resp, op_id in streams:
        cancel(ui, op_id)
        rest(resp)
    assert wait_until(lambda: workers(ui) == 0)


def test_mcp_maintenance_limits(ui, make_fake_mcp, monkeypatch):
    monkeypatch.setattr(mcp_ops, "MAINTENANCE_NETWORK_S", 0.5)
    monkeypatch.setattr(mcp_ops, "CANCEL_ANSWER_S", 0.2)
    fake = make_fake_mcp("legacy-session", tools=[simple_tool("hang", behave=hang)])
    add_server(ui, "s", fake.url, **FAST)
    held = [start_call(ui, "s", "hang") for _ in range(5)]
    assert fake.wait_holding(5)
    hook = Hook(monkeypatch, once=False)
    hook.active = True                            # every notice's connection is now blocked
    statuses = [cancel(ui, op_id)["status"] for _, op_id in held]
    assert statuses == ["unconfirmed"] * 4 + ["not_sent"]
    assert maintenance(ui) == 4
    for resp, _ in held:
        rest(resp)
    limit_passed = time.monotonic() + 0.5
    assert wait_until(lambda: time.monotonic() > limit_passed)   # the clock, not luck: past the limit
    hook.release.set()
    assert wait_until(lambda: maintenance(ui) == 0)
    assert fake.notices == []                     # never delivered late
    assert all(cancel(ui, op_id)["status"] == "failed" for _, op_id in held[:4])


def test_mcp_delete_never_late(ui, make_fake_mcp, monkeypatch):
    monkeypatch.setattr(mcp_ops, "MAINTENANCE_NETWORK_S", 0.5)
    a, b = make_fake_mcp("legacy-session"), make_fake_mcp("legacy-session")
    add_server(ui, "s", a.url, **FAST)
    mcp_call(ui, "s", "echo")
    hook = Hook(monkeypatch)
    hook.active = True
    add_server(ui, "s", b.url, **FAST)            # schedules the DELETE; its connection blocks
    assert hook.entered.wait(10)
    assert maintenance(ui) == 1
    limit_passed = time.monotonic() + 0.5
    assert wait_until(lambda: time.monotonic() > limit_passed)   # past the DELETE's network limit
    hook.release.set()
    assert wait_until(lambda: maintenance(ui) == 0)
    assert a.deletes == []


# 48 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tool, limits, reason", [
    ("headers", {"idle_timeout_s": 1, "call_deadline_s": 60}, "idle_timeout"),
    ("stream", {"idle_timeout_s": 1, "call_deadline_s": 60}, "idle_timeout"),
    ("progress", {"idle_timeout_s": 60, "call_deadline_s": 1}, "deadline"),
])
def test_mcp_timing(ui, make_fake_mcp, tool, limits, reason):
    def progress(call):
        call.start_sse()
        while not call.fake.release.wait(0.05):
            call.progress(1)

    fake = make_fake_mcp("modern", tools=[simple_tool("headers", behave=lambda c: c.hold()),
                                         simple_tool("stream", behave=hang), simple_tool("progress", behave=progress)])
    add_server(ui, "s", fake.url, **limits)
    load_tools(ui, "s")
    began = time.monotonic()
    end = final_of(mcp_call(ui, "s", tool))
    assert end["outcome"] == "unknown" and end["reason"] == reason
    assert time.monotonic() - began < 15


def test_mcp_timing_discovery(ui, make_fake_mcp):
    fake = make_fake_mcp("modern")
    fake.override("tools/list", lambda call: call.hold())
    add_server(ui, "s", fake.url, idle_timeout_s=60, discovery_deadline_s=1)
    began = time.monotonic()
    fail = failure_of(lambda: load_tools(ui, "s"))
    assert fail.status == 400 and fail.body["code"] == "mcp_error" and fail.body["reason"] == "deadline"
    assert time.monotonic() - began < 15


def test_mcp_timing_unread_page(ui, make_fake_mcp):
    def flood(call):
        call.start_sse()
        while True:
            call.progress(1, None, "m" * 65536)
            if call.fake.release.wait(0.001):
                return

    fake = make_fake_mcp("modern", tools=[simple_tool("flood", behave=flood)])
    add_server(ui, "s", fake.url, idle_timeout_s=60, call_deadline_s=1)
    load_tools(ui, "s")
    body = json.dumps({"server": "s", "tool": "flood", "arguments_text": "{}",
                       "revision": mcp_state(ui)["revision"]}).encode()
    page = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    page.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    try:
        page.connect(ui.server_address)
        page.sendall(b"POST /api/mcp/call HTTP/1.0\r\nX-Session-Token: " + ui.token.encode() +
                     b"\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() +
                     b"\r\n\r\n" + body)
        began = time.monotonic()
        page.recv(64)                             # the start of the reply, then nothing more is read
        assert wait_until(lambda: fake.disconnects, timeout=20)
        assert fake.disconnects[0]["time"] - began < 15     # well before the 30-second page timeout
        ops = [o for o in ui.state.mcp_ops._ops.values() if o.kind == "call"]
        assert ops[-1].ctx.abandoned == "deadline"
    finally:
        page.close()


def test_mcp_timing_deadline_reading_page(ui, make_fake_mcp):
    fake = make_fake_mcp("modern", tools=[simple_tool("hang", behave=hang)])
    add_server(ui, "s", fake.url, idle_timeout_s=60, call_deadline_s=1)
    evs = mcp_call(ui, "s", "hang")
    assert [e["type"] for e in evs] == ["started", "final"]
    assert evs[-1]["reason"] == "deadline" and evs[-1]["cancel_status"] == "stream_closed"


# 49 --------------------------------------------------------------------------------------------
def test_mcp_transport(ui, make_fake_mcp, test_tls, monkeypatch):
    fake = make_fake_mcp("modern", tls=test_tls)
    add_server(ui, "s", fake.url, **FAST)
    assert final_of(mcp_call(ui, "s", "echo"))["outcome"] == "ok"
    plain = make_fake_mcp("modern")
    add_server(ui, "p", plain.url, **FAST)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    assert final_of(mcp_call(ui, "p", "echo"))["outcome"] == "ok"
    assert final_of(mcp_call(ui, "s", "echo"))["outcome"] == "ok"


# 50 --------------------------------------------------------------------------------------------
def test_mcp_numbers(ui, make_fake_mcp):
    fake = make_fake_mcp("modern", tools=[simple_tool("echo"), simple_tool("big", behave=lambda call: call.result(
        {"content": [], "structuredContent": {"id": 9007199254740993}}))])
    add_server(ui, "s", fake.url, **FAST)
    for text, key, value in (('{"id":9007199254740993}', "id", 9007199254740993),
                             ('{"ratio":0.10000000000000000001}', "ratio", Decimal("0.10000000000000000001")),
                             ('{"amount":1e400}', "amount", Decimal("1e400"))):
        mcp_call(ui, "s", "echo", text)
        sent = fake.methods("tools/call")[-1]["body"]["params"]["arguments"][key]
        assert sent == value and type(sent) is type(value)
    calls = len(fake.methods("tools/call"))
    fail = failure_of(lambda: mcp_call(ui, "s", "echo", '{"n":' + "1" * 5000 + "}"))
    assert fail.status == 400 and fail.body["code"] == "bad_arguments"
    assert len(fake.methods("tools/call")) == calls
    end = final_of(mcp_call(ui, "s", "big"))
    assert end["result"]["structured_text"] == '{"id":9007199254740993}'
    assert end["insertion"]["text"].endswith('\n{"id":9007199254740993}')


# 51 --------------------------------------------------------------------------------------------
def test_mcp_error_text(ui, make_fake_mcp):
    secret = "tok-SENTINEL-51"
    leaky = lambda call: call.error(-32000, "rejected token " + call.record["headers"].get("authorization", "none"))
    fake = make_fake_mcp("modern", tools=[simple_tool("leak", behave=leaky)], token=None)
    add_server(ui, "s", fake.url, **FAST)
    set_token(ui, "s", secret)
    end = final_of(mcp_call(ui, "s", "leak"))
    assert end["outcome"] == "unknown" and end["jsonrpc_code"] == -32000 and "server_text" not in end
    assert not any(secret.encode() in body for body in ui.transcript)
    set_token(ui, "s", "")
    end = final_of(mcp_call(ui, "s", "leak"))
    assert end["server_text"] == "rejected token none"


# 52 --------------------------------------------------------------------------------------------
def test_mcp_library(make_ui, tmp_path, make_fake_mcp):
    library = tmp_path / "shared_mcp"
    first, second = make_ui(mcp_dir=library), make_ui(mcp_dir=library)
    fake = make_fake_mcp("modern")
    config_rev = get_state(first)["revision"]
    state = add_server(first, "s", fake.url, token_env="MY_TOKEN")
    assert state["revision"] == config_rev                           # separate revisions
    stale = state["mcp"]["revision"]
    add_server(second, "t", fake.url)                                # noticed by the first server
    assert mcp_state(first)["revision"] > stale and sorted(mcp_state(first)["servers"]) == ["s", "t"]
    for path, body in (("/api/mcp/servers", {"servers": {}}), ("/api/mcp/token", {"server": "s", "token": "x"}),
                       ("/api/mcp/tools", {"server": "s"}),
                       ("/api/mcp/call", {"server": "s", "tool": "echo", "arguments_text": "{}"})):
        fail = failure_of(lambda: request(first, path, {**body, "revision": stale}))
        assert fail.status == 409 and fail.body["code"] == "stale_revision"
    assert fake.requests == []

    doc = json.loads((library / "servers.json").read_bytes())
    doc["servers"]["s"]["future_field"], doc["future_top"] = 1, [1]
    (library / "servers.json").write_text(json.dumps(doc))
    set_token(first, "s", "tok-SENTINEL-52")
    add_server(first, "u", fake.url)
    doc = json.loads((library / "servers.json").read_bytes())
    assert doc["future_top"] == [1]
    assert b"tok-SENTINEL-52" not in (library / "servers.json").read_bytes()
    assert doc["servers"]["s"]["future_field"] == 1
    rev = mcp_state(first)["revision"]
    current = {n: {k: v for k, v in s.items() if k in ("url", "idle_timeout_s", "call_deadline_s",
                                                       "discovery_deadline_s", "token_env")}
               for n, s in mcp_state(first)["servers"].items()}
    assert save_servers(first, current)["mcp"]["revision"] == rev     # nothing changed

    for bad in ({"url": "ftp://x/mcp"}, {"url": "http://u:p@127.0.0.1/mcp"}, {"url": "http://h/mcp?q=1"},
                {"url": "http://h/m cp"}, {"url": "http://h/mcp", "idle_timeout_s": 0},
                {"url": "http://h/mcp", "call_deadline_s": 3601}, {"url": "http://h/mcp", "token_env": "1BAD"}):
        assert status_of(lambda: save_servers(first, {**current, "bad": bad})) == 400
    assert status_of(lambda: save_servers(first, {**current, "../x": {"url": "http://h/mcp"}})) == 400

    (library / "servers.json").write_text("{not json")
    before = (library / "servers.json").stat()
    state = mcp_state(first)
    assert state["read_only"] is True and state["servers"] == {} and state["problems"][0]["code"] == "invalid_json"
    fail = failure_of(lambda: save_servers(first, {}))
    assert fail.status == 409 and fail.body["code"] == "read_only"
    assert (library / "servers.json").stat().st_mtime_ns == before.st_mtime_ns
    (library / "servers.json").write_text(json.dumps(doc))           # repaired on disk: no restart needed
    state = mcp_state(first)
    assert state["read_only"] is False and sorted(state["servers"]) == ["s", "t", "u"]
    assert (library / ".servers.lock").is_file()
