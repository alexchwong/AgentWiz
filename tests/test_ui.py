"""AgentWiz server through HTTP: techspec 13.3 tests #1-#12 (0.1.1: #1 and #5 extended). The page's
JavaScript is exercised through the UI (README, "Trying it out"), not here."""
import json
import socket
import threading
import urllib.request

import modelspell

import agentwiz
from agentwiz import model_call
from conftest import (DONE, KEY, Reply, answer, base, complete, configure, delta, failure_of, get_state,
                      request, sse_event, status_of, wait_until)

SSE = {"Content-Type": "text/event-stream"}


def last(evs):
    return evs[-1]


def texts(evs, kind="text"):
    return "".join(e["text"] for e in evs if e["type"] == kind)


# 1 ---------------------------------------------------------------------------------------------
def test_access_state_and_page(ui):
    assert ui.server_address[0] == "127.0.0.1"
    for suffix in ("/", "/?t=wrong", "/?t=%C3%A9"):
        assert status_of(lambda: urllib.request.urlopen(base(ui) + suffix, timeout=10)) == 403
    assert status_of(lambda: request(ui, "/api/state", token=False)) == 403
    assert status_of(lambda: request(ui, "/api/config", {"revision": 1}, token=False)) == 403
    page = urllib.request.urlopen(ui.url, timeout=10)
    assert page.status == 200
    assert page.headers["Content-Type"].startswith("text/html")
    assert page.read()
    state = get_state(ui)
    assert state["agentwiz_version"] == agentwiz.__version__
    assert state["modelspell_version"] == modelspell.__version__
    assert state["revision"] == 1
    assert state["key_source"] == "none"
    assert state["workspace"]["read_only"] is False and state["workspace"]["created"] is True
    assert state["profiles"]["names"] == [] and state["profiles"]["active"] is None
    assert "reasoning" not in state and "max_tokens" not in state
    assert state["task"]["saved"] == {"reasoning": "", "temperature": None, "max_tokens": None, "advanced": {}}
    assert state["task"]["read_only"] is False and state["task"]["revision"] == 1
    assert state["supported"][""] == ["max_tokens", "temperature", "top_p"]
    assert "top_k" in state["supported"]["off"]


def test_startup_without_hostname_resolution(make_ui, monkeypatch):
    def no_dns(*args, **kwargs):
        raise AssertionError("loopback startup must not resolve hostnames")

    monkeypatch.setattr(socket, "getfqdn", no_dns)
    ui = make_ui()
    assert ui.server_address[0] == "127.0.0.1"
    assert ui.server_address[1] > 0
    assert get_state(ui)["revision"] == 1


# 2 ---------------------------------------------------------------------------------------------
def test_keys_write_only_bound_and_source(ui, make_server, monkeypatch):
    a, b = make_server().reply(answer()), make_server().reply(answer())
    assert configure(ui, a, api_key=KEY)["key_source"] == "session"
    assert configure(ui, b)["key_source"] == "none"
    complete(ui, "hi")
    assert "authorization" not in b.requests[-1]["headers"]
    assert configure(ui, a)["key_source"] == "session"
    complete(ui, "hi")
    assert a.requests[-1]["headers"]["authorization"] == f"Bearer {KEY}"
    assert configure(ui, a, api_key="")["key_source"] == "none"

    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key-0001")
    assert configure(ui, a, kind="openrouter", api_key="session-key-0001")["key_source"] == "session"
    assert configure(ui, a, kind="openrouter", api_key="")["key_source"] == "env:OPENROUTER_API_KEY"
    complete(ui, "hi")
    assert a.requests[-1]["headers"]["authorization"] == "Bearer env-key-0001"
    assert not any(KEY.encode() in body or b"session-key-0001" in body for body in ui.transcript)


# 3 ---------------------------------------------------------------------------------------------
def test_failures_never_reveal_keys(ui, make_server, monkeypatch, capfd):
    # (a) a typed key that cannot be sent in a header is refused, and nothing changes
    before = get_state(ui)
    crlf = "sk-SENTINEL-typed\r\nX-Injected: 1"
    fail = failure_of(lambda: request(ui, "/api/config", {"revision": before["revision"], "api_key": crlf}))
    assert fail.status == 400
    assert get_state(ui)["key_source"] == before["key_source"]

    # (b) an environment key with CR/LF inside it (modelSpell strips only the ends)
    c = make_server().reply(answer())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-SENTINEL-env\r\nX")
    configure(ui, c, kind="openrouter")
    evs = complete(ui, "hi")
    assert len(evs) == 1 and evs[0]["type"] == "error" and evs[0]["error"].startswith("InternalError:")
    assert c.requests == []
    fail = failure_of(lambda: request(ui, "/api/models", {"kind": "openrouter", "base_url": c.url + "/v1"}))
    assert fail.status == 500 and fail.body["code"] == "internal"
    assert c.requests == []
    monkeypatch.delenv("OPENROUTER_API_KEY")

    # (c) a backend that reflects the key JSON-escaped, which literal redaction misses
    escaped = 'sk-SENTINEL-"quoted"'
    reflected = json.dumps({"error": {"message": "invalid key", "received": "Bearer " + escaped}}).encode()
    d = make_server().reply(Reply(status=401, body=reflected, headers={"Content-Type": "application/json"}))
    configure(ui, d, api_key=escaped)
    evs = complete(ui, "hi")
    assert len(evs) == 1 and evs[0]["type"] == "error" and evs[0]["error"].startswith("AuthError:")
    fail = failure_of(lambda: request(ui, "/api/models", {"kind": "openai_compat", "base_url": d.url + "/v1",
                                                          "api_key": escaped}))
    assert fail.status == 400 and fail.body["error"].startswith("AuthError:")

    captured = capfd.readouterr()
    assert not any(b"SENTINEL" in body for body in ui.transcript)
    assert "SENTINEL" not in captured.out + captured.err


# 4 ---------------------------------------------------------------------------------------------
def test_complete_streams_and_maps_settings(ui, server):
    usage = {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
    server.reply(Reply(body=[delta(reasoning="think "), delta(reasoning="hard"), delta("an"), delta("s"),
                             delta(finish="stop"), delta(usage=usage), DONE], headers=SSE))
    configure(ui, server, kind="openrouter", api_key="k", provider="deepinfra/turbo")
    evs = complete(ui, "task", system="sys", task={"reasoning": "high", "max_tokens": 64})
    end = last(evs)
    assert end["type"] == "result" and end["outcome"] == "complete"
    assert texts(evs) == end["result"]["text"] == "ans"
    assert texts(evs, "reasoning") == end["result"]["reasoning"] == "think hard"
    sent = server.requests[-1]["body"]
    assert sent["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    assert sent["provider"] == {"order": ["deepinfra/turbo"], "allow_fallbacks": False, "require_parameters": True}
    assert sent["reasoning"] == {"effort": "high"}
    assert sent["max_tokens"] == 64
    assert sent["stream"] is True
    complete(ui, "task", system="")
    assert server.requests[-1]["body"]["messages"] == [{"role": "user", "content": "task"}]


# 5 ---------------------------------------------------------------------------------------------
def test_errors(ui, server):
    server.reply(Reply(status=500, body=b'{"error": "boom"}', headers={"Content-Type": "application/json"}))
    configure(ui, server)
    evs = complete(ui, "hi")
    assert len(evs) == 1 and evs[0]["type"] == "error" and evs[0]["error"].startswith("BackendError:")
    rev = get_state(ui)["revision"]
    for bad in ({"kind": "nope"}, {"kind": "openai_compat", "base_url": ""}, {"reasoning": "low"},
                {"max_tokens": 64},
                {"kind": "openai_compat", "base_url": server.url + "/vé"}):
        assert status_of(lambda: request(ui, "/api/config", {"revision": rev, **bad})) == 400
    assert get_state(ui)["revision"] == rev
    assert status_of(lambda: request(ui, "/api/complete", {"prompt": "hi"})) == 400
    assert status_of(lambda: request(ui, "/api/models", {"kind": "openai_compat", "base_url": server.url + "/v1",
                                                         "api_key": 123})) == 400
    # R1-7: no user name, password, query or fragment in a base URL, in settings or discovery
    host, sent = server.url.split("//", 1)[1], len(server.requests)
    for url in (f"http://user:pass@{host}/v1", server.url + "/v1?q=1", server.url + "/v1#f"):
        assert status_of(lambda: request(ui, "/api/config", {"revision": rev, "kind": "openai_compat",
                                                             "base_url": url})) == 400
        assert status_of(lambda: request(ui, "/api/models", {"kind": "openai_compat", "base_url": url})) == 400
    assert get_state(ui)["revision"] == rev
    assert len(server.requests) == sent  # refused before anything was sent


# 6 ---------------------------------------------------------------------------------------------
def test_discovery_passthrough(ui, make_server):
    saved, typed = make_server(), make_server()
    configure(ui, saved)
    models = json.dumps({"data": [{"id": "zeta"}, {"id": "alpha", "owned_by": "x"}]}).encode()
    endpoints = json.dumps({"data": {"endpoints": [
        {"provider_name": "DeepInfra", "tag": "deepinfra/turbo", "context_length": 8192},
        {"provider_name": "Groq", "tag": "groq"}]}}).encode()
    typed.reply(Reply(body=models, headers={"Content-Type": "application/json"}),
                Reply(body=endpoints, headers={"Content-Type": "application/json"}))
    got = request(ui, "/api/models", {"kind": "openai_compat", "base_url": typed.url + "/v1"})["models"]
    assert [m["id"] for m in got] == ["alpha", "zeta"]
    assert all("raw" not in m for m in got)
    assert typed.requests[0]["path"] == "/v1/models" and saved.requests == []
    got = request(ui, "/api/providers", {"kind": "openrouter", "base_url": typed.url + "/v1", "api_key": "k",
                                         "model": "author/slug"})["providers"]
    assert [p["slug"] for p in got] == ["deepinfra/turbo", "groq"]
    assert typed.requests[1]["path"] == "/v1/models/author/slug/endpoints"


# 7 ---------------------------------------------------------------------------------------------
def test_disconnect_cancels_upstream(ui, server):
    server.reply(Reply(body=[delta("a")], headers=SSE, hold=True, after_release=[delta("b")] * 20, chunk_delay=0.02))
    configure(ui, server)
    resp = complete(ui, "hi", raw=True)
    assert json.loads(resp.readline())["type"] == "text"
    resp.close()
    assert server.wait_holding(1)
    server.release.set()
    assert server.client_closed.wait(10)
    assert wait_until(lambda: ui.state.budget.in_flight == 0)


# 8 ---------------------------------------------------------------------------------------------
def test_stale_revision(ui, server):
    server.reply(answer())
    r1 = get_state(ui)["revision"]
    r2 = configure(ui, server, model="m-second-tab", revision=r1)["revision"]
    fail = failure_of(lambda: complete(ui, "hi", revision=r1))
    assert fail.status == 409 and fail.body["code"] == "stale_revision"
    assert server.requests == []
    fail = failure_of(lambda: request(ui, "/api/config", {"revision": r1, "model": "m-stale"}))
    assert fail.status == 409 and fail.body["code"] == "stale_revision"
    state = get_state(ui)
    assert (state["revision"], state["model"]) == (r2, "m-second-tab")

    # Both models differ from the current one, so whichever save wins really changes something.
    barrier, results = threading.Barrier(2), {}

    def save(model):
        barrier.wait()
        results[model] = status_of(lambda: request(ui, "/api/config", {"revision": r2, "model": model}))

    threads = [threading.Thread(target=save, args=(m,)) for m in ("m-race-1", "m-race-2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results.values()) == [200, 409]
    state = get_state(ui)
    winner = next(m for m, s in results.items() if s == 200)
    assert state["revision"] == r2 + 1 and state["model"] == winner


# 9 ---------------------------------------------------------------------------------------------
def test_snapshot_used_for_accepted_request(ui, make_server, monkeypatch):
    a, b = make_server().reply(answer()), make_server().reply(answer())
    entered, proceed = threading.Event(), threading.Event()
    real = model_call.call_model

    def paused(*args, **kwargs):
        entered.set()
        assert proceed.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(model_call, "call_model", paused)
    configure(ui, a, model="m-a", api_key="KEY_A")
    out = {}
    worker = threading.Thread(target=lambda: out.setdefault("evs", complete(ui, "hi", task={"max_tokens": 32})))
    worker.start()
    assert entered.wait(10)
    configure(ui, b, model="m-b", api_key="KEY_B")
    proceed.set()
    worker.join(10)
    assert last(out["evs"])["type"] == "result"
    assert len(a.requests) == 1
    assert a.requests[0]["body"]["model"] == "m-a" and a.requests[0]["body"]["max_tokens"] == 32
    assert a.requests[0]["headers"]["authorization"] == "Bearer KEY_A"
    assert b.requests == []


# 10 --------------------------------------------------------------------------------------------
def test_budget(ui, server):
    server.reply(Reply(body=[delta("a")], headers=SSE, hold=True,
                       after_release=[delta("b")] * 5 + [delta(finish="stop"), DONE], chunk_delay=0.02))
    configure(ui, server)
    streams = [complete(ui, "hi", raw=True) for _ in range(8)]
    assert all(json.loads(s.readline())["type"] == "text" for s in streams)
    assert server.wait_holding(8)
    fail = failure_of(lambda: complete(ui, "hi"))
    assert fail.status == 503 and fail.body["code"] == "busy"
    streams[0].close()
    assert status_of(lambda: complete(ui, "hi")) == 503  # a stopped request still counts
    server.release.set()
    assert wait_until(lambda: ui.state.budget.in_flight == 0)
    for s in streams[1:]:
        s.read()
        s.close()
    assert last(complete(ui, "hi"))["type"] == "result"


# 11 --------------------------------------------------------------------------------------------
def test_page_write_timeout(make_ui, make_server):
    ui = make_ui(page_timeout=0.5)
    # (a) a client that never reads: the write times out and the upstream request is cancelled
    flood = make_server().reply(Reply(body=[delta("x" * (256 * 1024))] * 128, headers=SSE, hold=True))
    configure(ui, flood)
    body = json.dumps({"prompt": "hi", "revision": get_state(ui)["revision"]}).encode()
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    try:
        client.connect(ui.server_address)
        client.sendall(b"POST /api/complete HTTP/1.0\r\nX-Session-Token: " + ui.token.encode() +
                       b"\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() +
                       b"\r\n\r\n" + body)
        assert flood.client_closed.wait(10)
        assert wait_until(lambda: ui.state.budget.in_flight == 0)
    finally:
        client.close()
    # (b) a long wait before the first token is unaffected by the page timeout
    slow = make_server().reply(answer(delay=1.5))
    configure(ui, slow)
    end = last(complete(ui, "hi"))
    assert end["type"] == "result" and end["outcome"] == "complete"


# 12 --------------------------------------------------------------------------------------------
def test_nonfinite_values_in_result(ui, server):
    server.reply(Reply(body=[delta("ok"), delta(finish="stop"),
                             sse_event('{"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1,'
                                       '"weird":NaN,"big":1e400}}'), DONE], headers=SSE))
    configure(ui, server)
    end = last(complete(ui, "hi"))  # parsed strictly: NaN or Infinity would fail here
    assert end["type"] == "result"
    assert end["result"]["raw_usage"]["weird"] is None and end["result"]["raw_usage"]["big"] is None
    assert end["nonfinite_replaced"] == ["result.raw_usage.weird", "result.raw_usage.big"]

