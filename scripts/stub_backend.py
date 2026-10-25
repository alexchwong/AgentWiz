#!/usr/bin/env python3
"""Scripted OpenAI-compatible backend for the manual walkthrough (techspec 11.3). Not shipped.

    python scripts/stub_backend.py [--port 8099]

In AgentWiz choose "OpenAI-compatible" with base URL http://127.0.0.1:8099/v1. The model ID picks
the behaviour (Load models lists them). Each request and each client disconnect is logged here.
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = {
    "complete": "Streams a short answer, stop, usage.",
    "complete-reasoning": "Streams reasoning (reasoning_content) then an answer.",
    "empty": "stop with no content.",
    "reasoning-only": "Reasoning, then stop with no content.",
    "truncated": "Some content, then finish_reason length.",
    "truncated-no-answer": "length with no content and no reasoning.",
    "truncated-reasoning": "Reasoning, then length with no content.",
    "error-500": "HTTP 500.",
    "slow": "Completes after 3 s of tokens (keyboard-guard check).",
    "slow-first": "First token after 20 s.",
    "endless": "One token every 0.2 s until the client goes.",
    "hold": "One token every 10 s until the client goes (budget check).",
    "nonfinite": "Completes with usage containing NaN and 1e400.",
    "bigint": "Completes with usage.prompt_tokens = 9007199254740993.",
}
USAGE = '{"prompt_tokens":12,"completion_tokens":8,"total_tokens":20}'


def log(message: str) -> None:
    print(f"[stub {time.strftime('%H:%M:%S')}] {message}", flush=True)


def chunk(content=None, reasoning=None, finish=None, usage_json=None) -> bytes:
    d = {}
    if content is not None:
        d["content"] = content
    if reasoning is not None:
        d["reasoning_content"] = reasoning
    text = json.dumps({"id": "stub-1", "model": "stub", "choices": [{"index": 0, "delta": d, "finish_reason": finish}]})
    if usage_json is not None:  # literal text, so NaN, 1e400 and big integers reach the client unchanged
        text = text[:-1] + ',"usage":' + usage_json + "}"
    return b"data: " + text.encode() + b"\n\n"


DONE = b"data: [DONE]\n\n"


def words(text: str) -> list[bytes]:
    return [chunk(w) for w in text.replace(" ", " \0").split("\0")]


SCRIPTS = {
    "complete": lambda: words("The stub backend says hello.") + [chunk(finish="stop", usage_json=USAGE), DONE],
    "complete-reasoning": lambda: [chunk(reasoning=w) for w in ["Thinking ", "about ", "it…"]]
    + words("Here is the answer.") + [chunk(finish="stop", usage_json=USAGE), DONE],
    "empty": lambda: [chunk(finish="stop", usage_json=USAGE), DONE],
    "reasoning-only": lambda: [chunk(reasoning="I thought, but said nothing."), chunk(finish="stop"), DONE],
    "truncated": lambda: words("This answer is cut") + [chunk(finish="length"), DONE],
    "truncated-no-answer": lambda: [chunk(finish="length"), DONE],
    "truncated-reasoning": lambda: [chunk(reasoning="Long reasoning that ran out of tokens"), chunk(finish="length"), DONE],
    "nonfinite": lambda: words("Non-finite usage follows.") + [chunk(finish="stop"), chunk(
        usage_json='{"prompt_tokens":1,"completion_tokens":1,"weird":NaN,"big":1e400}'), DONE],
    "bigint": lambda: words("Big integer usage follows.") + [chunk(finish="stop"), chunk(
        usage_json='{"prompt_tokens":9007199254740993,"completion_tokens":3}'), DONE],
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]})
        self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            model = json.loads(raw).get("model", "")
        except ValueError:
            model = ""
        log(f"request model={model!r}")
        if model == "error-500":
            return self._json(500, {"error": {"message": "stub failure"}})
        if model not in MODELS:
            return self._json(404, {"error": {"message": f"unknown stub model {model!r}"}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self._stream(model)
        except OSError:
            log(f"client disconnected (model={model!r})")

    def _send(self, data: bytes) -> None:
        self.wfile.write(data)
        self.wfile.flush()

    def _stream(self, model: str) -> None:
        if model == "slow":
            for i in range(15):
                self._send(chunk(f"tick{i} "))
                time.sleep(0.2)
            return self._send(chunk(finish="stop", usage_json=USAGE) + DONE)
        if model == "slow-first":
            time.sleep(20)
            for w in words("Finally, the first tokens."):
                self._send(w)
                time.sleep(0.2)
            return self._send(chunk(finish="stop") + DONE)
        if model in ("endless", "hold"):
            interval = 0.2 if model == "endless" else 10.0
            i = 0
            while True:
                self._send(chunk(f"t{i} "))
                i += 1
                time.sleep(interval)
        for c in SCRIPTS[model]():
            self._send(c)
            time.sleep(0.05)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scripted OpenAI-compatible backend for the AgentWiz walkthrough")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.daemon_threads = True
    log(f"listening on http://127.0.0.1:{args.port}/v1  models: {', '.join(MODELS)}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
