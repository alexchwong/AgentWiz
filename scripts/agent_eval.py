#!/usr/bin/env python3
"""Owner-run evaluation of agentic Chat on a chosen route (AgentWiz 0.2.2, section 13.3).

Drives a running AgentWiz over its HTTP routes with a file of cases whose expected answers were written
independently of any model, and writes a report. It uses whatever model is selected in Chat when it starts, so
choose the route in AgentWiz first. OpenRouter needs an explicit provider pin. Custom cases have no threshold.

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

Cohorts (starter blocks addendum, 7.2). The full library cannot be offered in one run (24 capabilities, 65,536
definition bytes), so library selection is measured on small fixed cohorts instead:

    python scripts/agent_eval.py --url 'http://127.0.0.1:PORT/?t=TOKEN' --cohorts tests/agent_eval/cohorts.json \
        --route openrouter --report report.json

Point it at a disposable workspace with `fast` bound to the selected Chat model. Each `library:<key>` capability
is added through the installer (plan, then add
with the plan's confirmations) unless an installed copy of that entry already exists, and its capability name and
revision are resolved from the inventory after install. A cohort offers at most 8 capabilities. Each case lists
`expected_capabilities`, which must all appear in the run's trace (an empty list: no tool is needed). The report
gives three rates separately (correct choice, valid arguments, successful use of the result) and the end-to-end
gate's five scenarios, which must all pass before the README claims library support in Chat.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class EvaluationBlocked(Exception):
    """The workspace or target cannot run this evaluation."""


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
        with self.call(path, body) as response:
            return json.load(response)


def model_target(state: dict) -> dict:
    return {key: state.get(key) for key in ("kind", "base_url", "model", "provider")}


def check_target(state: dict, target: dict, *, require_fast: bool = False) -> None:
    if model_target(state) != target:
        raise EvaluationBlocked("the selected Chat destination changed")
    if not target["model"]:
        raise EvaluationBlocked("select a model in Chat")
    if require_fast:
        if target["kind"] == "openrouter" and not target["provider"]:
            raise EvaluationBlocked("pin a tool-capable OpenRouter provider in Model settings")
        profile = state["roles"]["map"].get("fast")
        settings = state["profiles"]["details"].get(profile, {})
        if model_target(settings) != target or any(row["role"] == "fast" for row in state["roles"]["unresolved"]):
            raise EvaluationBlocked("bind the fast role to the selected Chat destination in the disposable workspace")


def judge(answer: str | None, expected: dict) -> bool | None:
    if answer is None or not isinstance(expected, dict):
        return None if not isinstance(expected, dict) else False
    if "equals" in expected:
        return answer.strip() == str(expected["equals"]).strip()
    wanted = expected.get("contains", [])
    wanted = [wanted] if isinstance(wanted, str) else wanted
    return all(str(w) in answer for w in wanted)


def run_case(client: Client, case: dict, timeout: float, target: dict | None = None) -> dict:
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
        if target is not None:
            check_target(state, target)
        inventory = client.json("/api/agent/inventory?revision=" + str(state["revision"]))
        revisions = {c["name"]: c.get("revision") for c in inventory.get("capabilities", [])}
        capabilities = [{"name": n, **({"revision": revisions[n]} if revisions.get(n) is not None else {})}
                        for n in case.get("capabilities", [])]
        with client.call("/api/agent/run", {"format_version": 1, "conversation": conversation,
                                           "revision": state["revision"], "message": case["message"],
                                           "capabilities": capabilities}, timeout=timeout) as response:
            events = [json.loads(line) for line in response if line.strip()]
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
    except EvaluationBlocked as exc:
        row["error"] = str(exc)
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


# --- cohorts (starter blocks addendum, 7.2) -----------------------------------------------------------------------------
COHORT_LIMIT = 8
INVALID_CODES = ("unknown_capability", "malformed_arguments", "schema_mismatch", "input_check_failed")


def install_entries(client: Client, keys: list[str]) -> dict:
    """{catalog key: block key}: an installed copy when one exists, otherwise added through plan and add."""
    listing = {row["key"]: row for row in client.json("/api/library")["entries"]}
    blocks, wanted = {}, []
    for key in keys:
        row = listing.get(key)
        if row is None:
            raise EvaluationBlocked(f"{key}: not in the library")
        if row["installed"]:
            blocks[key] = row["installed"][0]
        else:
            wanted.append(key)
    if wanted:
        planned = client.json("/api/library/plan", {"format_version": 1,
                                                    "entries": [{"key": k} for k in wanted]})["entries"]
        bad = [p for p in planned if p["outcome"] != "planned"]
        if bad:
            raise EvaluationBlocked("the installer did not plan: " + json.dumps(bad)[:1000])
        added = client.json("/api/library/add", {"format_version": 1, "entries": [
            {"key": p["key"], "as": p["as"], **({"role": p["role"]} if p.get("role") else {}),
             "expected_plan": p["expected_plan"], "confirmations": p["required_confirmations"],
             "binding": p["binding"]} for p in planned]})["entries"]
        bad = [a for a in added if a["outcome"] != "added"]
        if bad:
            raise EvaluationBlocked("the installer did not add: " + json.dumps(bad)[:1000])
        blocks.update({p["key"]: p["as"] for p in planned})
    return blocks


def resolve(names: list[str], inventory: dict, blocks: dict) -> tuple[list[dict], dict]:
    """The run's capability list and {cohort name: capability name}; library names resolve by block identity."""
    by_block = {c["identity"].get("block"): c for c in inventory.get("capabilities", [])
                if c.get("kind") == "block" and isinstance(c.get("identity"), dict)}
    by_name = {c["name"]: c for c in inventory.get("capabilities", [])}
    chosen, mapping = [], {}
    for name in names:
        found = by_block.get(blocks[name[len("library:"):]]) if name.startswith("library:") else by_name.get(name)
        if found is None:
            raise EvaluationBlocked(f"{name}: not offered by the inventory (stage the documents it needs, or check the block)")
        if found.get("excluded"):
            raise EvaluationBlocked(f"{name}: excluded by the inventory ({found['excluded']})")
        mapping[name] = found["name"]
        chosen.append({"name": found["name"], **({"revision": found["revision"]} if "revision" in found else {})})
    return chosen, mapping


def completed_in_order(trace: list[dict], expected: list[str]) -> bool:
    if not expected:
        return not trace
    successful = iter(action["capability"] for action in trace if action.get("outcome") == "completed")
    return all(name in successful for name in expected)


def run_cohort_case(client: Client, case: dict, names: list[str], blocks: dict, timeout: float, target: dict) -> dict:
    started = time.monotonic()
    row = {"id": case["id"], "transport_ok": False, "stop_reason": None, "capabilities_used": None,
           "correct_choice": None, "valid_arguments": None, "result_used": None, "tools_completed": None,
           "verdict": "blocked", "error": None}
    try:
        if len(names) > COHORT_LIMIT:
            raise EvaluationBlocked(f"{case['id']}: a cohort offers at most {COHORT_LIMIT} capabilities")
        conversation = client.json("/api/agent/conversation", {"format_version": 1})["conversation"]["id"]
        for document in case.get("documents", []):
            data = document["text"].encode("utf-8")
            client.call("/api/agent/documents", raw=data, headers={
                "Content-Type": "application/octet-stream", "Content-Length": str(len(data)),
                "X-Filename": urllib.parse.quote(document["name"]), "X-Conversation": conversation,
                "X-Local-Only": "true" if document.get("local_only", True) else "false"}).read()
        state = client.json("/api/state")
        check_target(state, target, require_fast=True)
        inventory = client.json("/api/agent/inventory?revision=" + str(state["revision"]))
        capabilities, mapping = resolve(names, inventory, blocks)
        with client.call("/api/agent/run", {"format_version": 1, "conversation": conversation,
                                           "revision": state["revision"], "message": case["message"],
                                           "capabilities": capabilities}, timeout=timeout) as response:
            events = [json.loads(line) for line in response if line.strip()]
        finals = [e for e in events if e.get("type") == "final"]
        if len(finals) != 1:
            row["error"] = "the stream did not return exactly one final event"
            return row
        final = finals[0]
        view = final["view"]
        trace = view["trace"]
        row["transport_ok"] = final["stop_reason"] not in ("model_error", "busy", "internal_error", "configuration_changed")
        row["stop_reason"] = final["stop_reason"]
        used = [t.get("capability") for t in trace]
        row["capabilities_used"] = used
        expected = [mapping.get(n, n) for n in case.get("expected_capabilities", [])]
        row["correct_choice"] = (not used) if not expected else all(e in used for e in expected)
        row["valid_arguments"] = None if not trace else all(t.get("code") not in INVALID_CODES for t in trace)
        row["result_used"] = judge(final.get("answer"), case.get("expected_answer"))
        row["tools_completed"] = completed_in_order(trace, expected)
        if "expected_draft" in case:
            drafts = view.get("drafts", [])
            row["draft_correct"] = False
            if drafts and row["tools_completed"]:
                with client.call(f"/api/agent/drafts/{drafts[-1]['id']}") as download:
                    row["draft_correct"] = judge(download.read().decode("utf-8"), case["expected_draft"])
        if row["transport_ok"]:
            passed = (row["stop_reason"] == "answered" and row["tools_completed"] and
                      row["valid_arguments"] is not False and row["result_used"] and row.get("draft_correct", True))
            row["verdict"] = "passed" if passed else "failed"
        else:
            row["error"] = view.get("stop_detail") or row["stop_reason"]
    except EvaluationBlocked as exc:
        row["error"] = str(exc)
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


def rates(rows: list[dict]) -> dict:
    def rate(key):
        judged = [r[key] for r in rows if r[key] is not None]
        return {"passed": sum(1 for v in judged if v), "judged": len(judged)}
    return {"correct_choice": rate("correct_choice"), "valid_arguments": rate("valid_arguments"),
            "result_used": rate("result_used")}


def run_cohorts(client: Client, spec: dict, timeout: float, target: dict) -> dict:
    cohorts, gate = spec.get("cohorts", []), spec.get("gate", {}).get("cases", [])
    every = {n for c in cohorts for n in c["capabilities"]} | {n for case in gate for n in case["capabilities"]}
    blocks = install_entries(client, sorted(n[len("library:"):] for n in every if n.startswith("library:")))
    out = {"cohorts": [], "gate": None}
    for cohort in cohorts:
        rows = [run_cohort_case(client, case, cohort["capabilities"], blocks, timeout, target) for case in cohort["cases"]]
        out["cohorts"].append({"id": cohort["id"], "target": cohort.get("target"), "cases": rows, "rates": rates(rows)})
    if gate:
        rows = [run_cohort_case(client, case, case["capabilities"], blocks, timeout, target) for case in gate]
        verdicts = {r["verdict"] for r in rows}
        verdict = "failed" if "failed" in verdicts else "blocked" if "blocked" in verdicts else "passed"
        passed = verdict == "passed"
        out["gate"] = {"cases": rows, "passed": passed, "verdict": verdict,
                       "consequence": None if passed else ("Describe the library blocks for Workshop and workflows only; "
                                                           "report Chat as untested.")}
    out["rates"] = rates([r for c in out["cohorts"] for r in c["cases"]])
    out["installed"] = blocks
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="the address AgentWiz printed, with ?t=<token>")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--cases", help="a JSON list of cases")
    which.add_argument("--cohorts", help="a cohorts file (tests/agent_eval/cohorts.json)")
    parser.add_argument("--report", required=True, help="where to write the JSON report")
    parser.add_argument("--route", choices=("openrouter", "lmstudio", "openai_compat"), help="assert the selected route")
    parser.add_argument("--chat-template", default=None, help="the server's chat template, recorded as given")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds per read of a run's stream")
    args = parser.parse_args()
    client = Client(args.url)
    report = {"chat_template": args.chat_template, "route": {"kind": args.route}}
    try:
        state = client.json("/api/state")
        target = model_target(state)
        report.update(agentwiz_version=state.get("agentwiz_version"), modelspell_version=state.get("modelspell_version"),
                      route=target)
        if args.route is not None and args.route != target["kind"]:
            raise EvaluationBlocked(f"Chat uses {target['kind']}; expected {args.route}")
        check_target(state, target, require_fast=bool(args.cohorts))
        exit_code = evaluate(client, target, args, report)
    except (EvaluationBlocked, OSError) as exc:
        report.update(verdict="blocked", reason=str(exc) if isinstance(exc, EvaluationBlocked) else type(exc).__name__)
        exit_code = 2
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(json.dumps({k: report[k] for k in ("verdict", "reason", "rates", "totals") if k in report}))
    return exit_code


def evaluate(client: Client, target: dict, args, report: dict) -> int:
    if args.cohorts:
        with open(args.cohorts, encoding="utf-8") as fh:
            spec = json.load(fh)
        report.update(run_cohorts(client, spec, args.timeout, target))
        report["verdict"] = report["gate"]["verdict"] if report["gate"] else "incomplete"
        return {"passed": 0, "failed": 1, "blocked": 2, "incomplete": 3}[report["verdict"]]
    with open(args.cases, encoding="utf-8") as fh:
        cases = json.load(fh)
    rows = [run_case(client, case, args.timeout, target) for case in cases]
    counted = lambda key: sum(1 for r in rows if r[key] is True)
    report.update(cases=rows, totals={"cases": len(rows), "transport_ok": counted("transport_ok"),
                                    "first_arguments_valid": counted("first_arguments_valid"),
                                    "first_choice_correct": counted("first_choice_correct"),
                                    "answer_correct": counted("answer_correct")},
                  note="Custom cases report results without applying a release threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
