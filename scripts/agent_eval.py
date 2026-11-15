#!/usr/bin/env python3
"""Owner-run evaluation of agentic Chat on a chosen route (AgentWiz 0.2.2, section 13.3).

Drives a running AgentWiz over its HTTP routes with a file of cases whose expected answers were written
independently of any model, and writes a report. It uses whatever model is selected in Chat when it starts, so
choose the route in AgentWiz first. It sets no threshold: the owner decides the release bar (O-6).

    python scripts/agent_eval.py --url 'http://127.0.0.1:PORT/?t=TOKEN' --cases cases.json --report report.json \
        [--chat-template NAME]

A cases file is a JSON list. Each case:

    {"id": "b17", "message": "Which part is in bin B17, and how many more reach 10?",
     "capabilities": ["aw_search_documents", "aw_read_document", "aw_calculate"],
     "documents": [{"name": "bins.txt", "text": "bin B17 holds part 007; stock 3", "local_only": false}],
     "expected_first_capability": "aw_search_documents",
     "expected_answer": {"contains": ["007", "7"]}}

`expected_answer` takes "contains" (every string must appear) or "equals" (the whole answer, trimmed). The report
keeps transport success, valid first calls and correct answers apart. This script is not shipped in the wheel.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class Client:
    def __init__(self, url: str):
        parts = urllib.parse.urlsplit(url)
        self.root = f"{parts.scheme}://{parts.netloc}"
        self.token = urllib.parse.parse_qs(parts.query).get("t", [""])[0]
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path: str, body=None, raw: bytes | None = None, headers=None, timeout=60):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.root + path, data=data, method="GET" if data is None else "POST",
                                     headers={"X-Session-Token": self.token, "Content-Type": "application/json",
                                              **(headers or {})})
        return self.opener.open(req, timeout=timeout)

    def json(self, path: str, body=None):
        return json.loads(self.call(path, body).read())


def judge(answer: str | None, expected: dict) -> bool | None:
    if answer is None or not isinstance(expected, dict):
        return None if not isinstance(expected, dict) else False
    if "equals" in expected:
        return answer.strip() == str(expected["equals"]).strip()
    wanted = expected.get("contains", [])
    wanted = [wanted] if isinstance(wanted, str) else wanted
    return all(str(w) in answer for w in wanted)


def run_case(client: Client, case: dict, timeout: float) -> dict:
    started = time.monotonic()
    row: dict = {"id": case.get("id"), "transport_ok": False, "stop_reason": None, "first_capability": None,
                 "first_capability_expected": case.get("expected_first_capability"), "first_choice_correct": None,
                 "first_arguments_valid": None, "answer_correct": None, "turns": None, "actions": None,
                 "seconds": None, "usage": None, "error": None}
    try:
        conversation = client.json("/api/agent/conversation", {"format_version": 1})["conversation"]["id"]
        for document in case.get("documents", []):
            data = document["text"].encode("utf-8")
            client.call("/api/agent/documents", raw=data, headers={
                "Content-Type": "application/octet-stream", "Content-Length": str(len(data)),
                "X-Filename": urllib.parse.quote(document["name"]), "X-Conversation": conversation,
                "X-Local-Only": "true" if document.get("local_only", True) else "false"}).read()
        state = client.json("/api/state")
        inventory = client.json("/api/agent/inventory?revision=" + str(state["revision"]))
        revisions = {c["name"]: c.get("revision") for c in inventory.get("capabilities", [])}
        capabilities = [{"name": n, **({"revision": revisions[n]} if revisions.get(n) is not None else {})}
                        for n in case.get("capabilities", [])]
        resp = client.call("/api/agent/run", {"format_version": 1, "conversation": conversation,
                                              "revision": state["revision"], "message": case["message"],
                                              "capabilities": capabilities}, timeout=timeout)
        events = [json.loads(line) for line in resp.read().splitlines() if line.strip()]
        final = next((e for e in events if e.get("type") == "final"), None)
        if final is None:
            row["error"] = "the stream ended without a final event"
            return row
        view = final.get("view", {})
        row["transport_ok"] = final["stop_reason"] not in ("model_error", "busy", "internal_error")
        row["stop_reason"] = final["stop_reason"]
        trace = view.get("trace", [])
        if trace:
            first = trace[0]
            row["first_capability"] = first.get("capability")
            row["first_arguments_valid"] = first.get("code") not in ("unknown_capability", "malformed_arguments",
                                                                     "schema_mismatch", "input_check_failed")
            if row["first_capability_expected"] is not None:
                row["first_choice_correct"] = row["first_capability"] == row["first_capability_expected"]
        row["answer_correct"] = judge(final.get("answer"), case.get("expected_answer"))
        row["turns"] = view.get("counters", {}).get("model_turns")
        row["actions"] = view.get("counters", {}).get("actions")
        row["usage"] = view.get("usage")
    except urllib.error.HTTPError as exc:
        try:
            row["error"] = json.loads(exc.read()).get("code") or f"HTTP {exc.code}"
        except ValueError:
            row["error"] = f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        row["error"] = type(exc).__name__
    finally:
        row["seconds"] = round(time.monotonic() - started, 2)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="the address AgentWiz printed, with ?t=<token>")
    parser.add_argument("--cases", required=True, help="a JSON list of cases")
    parser.add_argument("--report", required=True, help="where to write the JSON report")
    parser.add_argument("--chat-template", default=None, help="the server's chat template, recorded as given")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds per read of a run's stream")
    args = parser.parse_args()
    with open(args.cases, encoding="utf-8") as fh:
        cases = json.load(fh)
    client = Client(args.url)
    state = client.json("/api/state")
    rows = [run_case(client, case, args.timeout) for case in cases]
    counted = lambda key: sum(1 for r in rows if r[key] is True)
    report = {"agentwiz_version": state.get("agentwiz_version"), "modelspell_version": state.get("modelspell_version"),
              "route": {"kind": state.get("kind"), "base_url": state.get("base_url"), "model": state.get("model"),
                        "provider": state.get("provider")},
              "chat_template": args.chat_template, "cases": rows,
              "totals": {"cases": len(rows), "transport_ok": counted("transport_ok"),
                         "first_arguments_valid": counted("first_arguments_valid"),
                         "first_choice_correct": counted("first_choice_correct"),
                         "answer_correct": counted("answer_correct")},
              "note": "No threshold is applied; the owner decides the release bar."}
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(json.dumps(report["totals"]))


if __name__ == "__main__":
    sys.exit(main())
