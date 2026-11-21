#!/usr/bin/env python3
"""Owner-run evaluation of agentic Chat on a chosen route (AgentWiz 0.2.2, section 13.3).

Starts a disposable AgentWiz (or attaches explicitly with --url) and drives its HTTP routes
with independently authored cases. Disposable mode selects an evaluation profile and binds fast
automatically. Attached mode uses the current Chat model. OpenRouter tools need Automatic routing
or an explicit provider pin. Custom cases have no threshold.

    python scripts/agent_eval.py --url 'http://127.0.0.1:PORT/?t=TOKEN' --cases cases.json --report reports/agent_eval_report.json \
        [--chat-template NAME]

A cases file is a JSON list. Each case:

    {"id": "b17", "message": "Which part is in bin B17, and how many more reach 10?",
     "capabilities": ["aw_search_documents", "aw_read_document", "aw_calculate"],
     "documents": [{"name": "bins.txt", "text": "bin B17 holds part 007; stock 3", "local_only": false}],
     "expected_first_capability": "aw_search_documents",
     "expected_answer": {"contains": ["007", "7"]}}

`expected_answer` takes "contains" (every string must appear), "equals" (the whole answer, trimmed), or
"equals_casefold" (a trimmed, case-insensitive exact match). The report keeps transport success, valid first
calls and correct answers apart. This script is not shipped in the wheel.

Cohorts (starter blocks addendum, 7.2). The full library cannot be offered in one run (24 capabilities, 65,536
definition bytes), so library selection is measured on small fixed cohorts instead:

    python scripts/agent_eval.py --url 'http://127.0.0.1:PORT/?t=TOKEN' --cohorts tests/agent_eval/cohorts.json \
        --route openrouter --report reports/agent_eval_report.json

Without --url, the workspace and fast binding are prepared automatically.
With --url, use a disposable workspace with fast already bound; evaluation modifies its data. Each `library:<key>` capability
is added through the installer (plan, then add
with the plan's confirmations) unless an installed copy of that entry already exists, and its capability name and
revision are resolved from the inventory after install. A cohort offers at most 8 capabilities. Each case lists
`expected_capabilities`, which must complete in order (an empty list requires no calls). An optional
`expected_summary_source` names the staged document whose full text must reach the summarisation block.
The report gives three rates separately (correct choice, valid arguments, successful use of the result)
and the end-to-end gate's five scenarios, which must all pass before the README claims library support in Chat.
"""
from __future__ import annotations

import argparse
import tempfile
from contextlib import ExitStack
from pathlib import Path
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import eval_setup
import eval_reports


class EvaluationBlocked(Exception):
    """The workspace or target cannot run this evaluation."""


class Client:
    def __init__(self, url: str, token=None, *, mode=None, workshop=None):
        parts = urllib.parse.urlsplit(url)
        self.root = f"{parts.scheme}://{parts.netloc}"
        self.token = token if token is not None else urllib.parse.parse_qs(parts.query).get("t", [""])[0]
        self.mode = mode
        self.workshop = workshop
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


def context_for_case(client, case, *, synthetic):
    mode = getattr(client, "mode", None)
    if mode:
        previous = client.json("/api/agent/conversation")["conversation"]
        if previous and previous.get("private"):
            client.json("/api/agent/conversation/close", {"format_version": 1,
                        "conversation": previous["id"], "confirm": True})
    conversation = client.json("/api/agent/conversation", {"format_version": 1})["conversation"]["id"]
    for document in case.get("documents", []):
        data = document["text"].encode("utf-8")
        # The fixed cohorts use synthetic material and explicitly stage it for both routes.
        local = mode == "local" or (not (mode == "cloud" and synthetic) and document.get("local_only", True))
        with client.call("/api/agent/documents", raw=data, headers={
            "Content-Type": "application/octet-stream", "Content-Length": str(len(data)),
            "X-Filename": urllib.parse.quote(document["name"]), "X-Conversation": conversation,
            "X-Local-Only": "true" if local else "false"}):
            pass
    state = chat_state(client)
    if mode:
        view = client.json("/api/agent/conversation")["conversation"]
        context = {"mode": mode, "role": "fast", "items": [item["id"] for item in view["documents"]]}
        inventory = client.json("/api/agent/inventory", {"format_version": 1, "revision": state["revision"], **context})
    else:
        context = {}
        inventory = client.json("/api/agent/inventory?revision=" + str(state["revision"]))
    return conversation, context, state, inventory


def chat_state(client):
    if not getattr(client, "mode", None):
        return client.json("/api/state")
    settings = client.json("/api/chat/settings")
    slot = settings["roles"]["fast"][client.mode]
    if slot is None:
        raise EvaluationBlocked("Quick chat has no usable model binding")
    state = {**slot, "revision": settings["revision"], "agentwiz_version": settings.get("agentwiz_version"),
             "modelspell_version": settings.get("modelspell_version")}
    state["routing_zdr"] = slot.get("zdr")
    state["routing_data_collection"] = slot.get("data_collection")
    state["roles"] = {"map": {"fast": slot["profile"]}, "unresolved": []}
    state["profiles"] = {"details": {slot["profile"]: dict(state)}}
    return state


def model_target(state: dict) -> dict:
    return {key: state.get(key) for key in ("kind", "base_url", "model", "provider", "routing_mode", "routing_preset", "routing_zdr", "routing_data_collection")}


def check_target(state: dict, target: dict, *, require_fast: bool = False) -> None:
    if model_target(state) != target:
        raise EvaluationBlocked("the selected Chat destination changed")
    if not target["model"]:
        raise EvaluationBlocked("select a model in Chat")
    if require_fast:
        if target["kind"] == "openrouter" and not target["provider"] and target.get("routing_mode") != "automatic":
            raise EvaluationBlocked("select Automatic routing or pin a tool-capable OpenRouter provider in Model settings")
        profile = state["roles"]["map"].get("fast")
        settings = state["profiles"]["details"].get(profile, {})
        if model_target(settings) != target or any(row["role"] == "fast" for row in state["roles"]["unresolved"]):
            raise EvaluationBlocked("bind the fast role to the selected Chat destination in the disposable workspace")


def judge(answer: str | None, expected: dict) -> bool | None:
    if answer is None or not isinstance(expected, dict):
        return None if not isinstance(expected, dict) else False
    if "equals" in expected:
        return answer.strip() == str(expected["equals"]).strip()
    if "equals_casefold" in expected:
        return answer.strip().casefold() == str(expected["equals_casefold"]).strip().casefold()
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
        conversation, context, state, inventory = context_for_case(client, case, synthetic=False)
        if target is not None:
            check_target(state, target, require_fast=False)
        revisions = {c["name"]: c.get("revision") for c in inventory.get("capabilities", [])}
        capabilities = [{"name": n, **({"revision": revisions[n]} if revisions.get(n) is not None else {})}
                        for n in case.get("capabilities", [])]
        request = {"format_version": 1, "conversation": conversation,
                   "revision": state["revision"], "message": case["message"], **context}
        if not case.get("full_inventory"):
            request["capabilities"] = capabilities
        else:
            row["offered"] = inventory.get("preflight", {}).get("offered", [])
            row["definition_bytes"] = inventory.get("preflight", {}).get("definition_bytes")
        with client.call("/api/agent/run", request, timeout=timeout) as response:
            events = [json.loads(line) for line in response if line.strip()]
        final = next((e for e in events if e.get("type") == "final"), None)
        if final is None:
            row["error"] = "the stream ended without a final event"
            raise EvaluationBlocked(row["error"])
        view = final.get("view", {})
        row.update(eval_reports.safe({"answer": final.get("answer"), "trace": view.get("trace", [])}, client))
        row["routing"] = view.get("routing")
        row["served_by"] = view.get("served_provider")
        row["transport_ok"] = final["stop_reason"] not in ("model_error", "busy", "internal_error")
        row["stop_reason"] = final["stop_reason"]
        detail = view.get('stop_detail') or ''
        if not row["transport_ok"]:
            row["error"] = detail or row["stop_reason"]
        row['shared_target_unavailable'] = (final['stop_reason'] == 'configuration_changed' or
            any(detail.startswith(name + ':') for name in
                ('AuthError', 'ConfigError', 'UnsupportedSetting', 'UnsupportedOperation', 'DestinationRefused')))
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
        row["shared_target_unavailable"] = exc.code in (401, 403)
        try:
            row["error"] = json.loads(exc.read()).get("code") or f"HTTP {exc.code}"
        except ValueError:
            row["error"] = f"HTTP {exc.code}"
    except Exception as exc:
        row["error"] = type(exc).__name__
        row["diagnostic"] = eval_reports.error(exc, "case")
    finally:
        row["seconds"] = round(time.monotonic() - started, 2)
    return eval_reports.safe(row, client)


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


def summary_source_matches(trace: list[dict], capability: str, source: str) -> bool:
    expected = source.split()
    found = False
    for action in trace:
        if action.get("capability") != capability or action.get("outcome") != "completed":
            continue
        try:
            arguments = json.loads(action.get("arguments_text", ""))
        except (TypeError, ValueError):
            return False
        text = arguments.get("text") if isinstance(arguments, dict) else None
        if not isinstance(text, str) or text.split() != expected:
            return False
        found = True
    return found


def run_cohort_case(client: Client, case: dict, names: list[str], blocks: dict, timeout: float, target: dict) -> dict:
    started = time.monotonic()
    row = {"id": case["id"], "transport_ok": False, "stop_reason": None, "capabilities_used": None,
           "correct_choice": None, "valid_arguments": None, "result_used": None, "tools_completed": None,
           "verdict": "blocked", "error": None}
    try:
        if len(names) > COHORT_LIMIT:
            raise EvaluationBlocked(f"{case['id']}: a cohort offers at most {COHORT_LIMIT} capabilities")
        conversation, context, state, inventory = context_for_case(client, case, synthetic=True)
        if target is not None:
            check_target(state, target, require_fast=True)
        capabilities, mapping = resolve(names, inventory, blocks)
        with client.call("/api/agent/run", {"format_version": 1, "conversation": conversation,
                                           "revision": state["revision"], "message": case["message"],
                                           "capabilities": capabilities, **context}, timeout=timeout) as response:
            events = [json.loads(line) for line in response if line.strip()]
        finals = [e for e in events if e.get("type") == "final"]
        if len(finals) != 1:
            row["error"] = "the stream did not return exactly one final event"
            raise EvaluationBlocked(row["error"])
        final = finals[0]
        view = final["view"]
        row.update(eval_reports.safe({"answer": final.get("answer"), "trace": view.get("trace", []),
                                      "stop_detail": view.get("stop_detail"), "counters": view.get("counters"),
                                      "usage": view.get("usage")}, client))
        row["routing"] = view.get("routing")
        row["served_by"] = view.get("served_provider")
        trace = view["trace"]
        row["transport_ok"] = final["stop_reason"] not in ("model_error", "busy", "internal_error", "configuration_changed")
        row["stop_reason"] = final["stop_reason"]
        detail = view.get('stop_detail') or ''
        row['shared_target_unavailable'] = (final['stop_reason'] == 'configuration_changed' or
            any(detail.startswith(name + ':') for name in
                ('AuthError', 'ConfigError', 'UnsupportedSetting', 'UnsupportedOperation', 'DestinationRefused')))
        used = [t.get("capability") for t in trace]
        row["capabilities_used"] = used
        expected = [mapping.get(n, n) for n in case.get("expected_capabilities", [])]
        row["correct_choice"] = (not used) if not expected else all(e in used for e in expected)
        row["valid_arguments"] = None if not trace else all(t.get("code") not in INVALID_CODES for t in trace)
        row["result_used"] = judge(final.get("answer"), case.get("expected_answer"))
        row["tools_completed"] = completed_in_order(trace, expected)
        if "expected_summary_source" in case:
            source = next(doc["text"] for doc in case["documents"] if doc["name"] == case["expected_summary_source"])
            row["summary_source_correct"] = summary_source_matches(trace, mapping["library:summarise_document"], source)
        if "expected_draft" in case:
            drafts = view.get("drafts", [])
            row["draft_correct"] = False
            if drafts and row["tools_completed"]:
                with client.call(f"/api/agent/drafts/{drafts[-1]['id']}") as download:
                    row["draft_correct"] = judge(download.read().decode("utf-8"), case["expected_draft"])
        if row["transport_ok"]:
            passed = (row["stop_reason"] == "answered" and row["tools_completed"] and
                      row["valid_arguments"] is not False and row["result_used"] and
                      row.get("summary_source_correct", True) and row.get("draft_correct", True))
            row["verdict"] = "passed" if passed else "failed"
        else:
            row["error"] = view.get("stop_detail") or row["stop_reason"]
    except EvaluationBlocked as exc:
        row["error"] = str(exc)
    except urllib.error.HTTPError as exc:
        row["shared_target_unavailable"] = exc.code in (401, 403)
        try:
            row["error"] = json.loads(exc.read()).get("code") or f"HTTP {exc.code}"
        except ValueError:
            row["error"] = f"HTTP {exc.code}"
    except Exception as exc:
        row["error"] = type(exc).__name__
        row["diagnostic"] = eval_reports.error(exc, "case")
    finally:
        row["seconds"] = round(time.monotonic() - started, 2)
    return eval_reports.safe(row, client)


def rates(rows: list[dict]) -> dict:
    def rate(key):
        judged = [r[key] for r in rows if r[key] is not None]
        return {"passed": sum(1 for v in judged if v), "judged": len(judged), "unjudged": len(rows) - len(judged)}
    return {"correct_choice": rate("correct_choice"), "valid_arguments": rate("valid_arguments"),
            "result_used": rate("result_used")}


def validate_cohorts(spec):
    """Refuse malformed fixture structure before installing entries or sending model requests."""
    ids = set()
    groups = [(cohort['capabilities'], cohort['cases']) for cohort in spec.get('cohorts', [])]
    groups += [(case['capabilities'], [case]) for case in spec.get('gate', {}).get('cases', [])]
    for names, cases in groups:
        if len(names) > COHORT_LIMIT or len(set(names)) != len(names):
            raise EvaluationBlocked('invalid cohort capability list')
        for case in cases:
            if not isinstance(case.get('id'), str) or case['id'] in ids:
                raise EvaluationBlocked('missing or duplicate case id')
            ids.add(case['id'])
            if not isinstance(case.get('message'), str) or not case['message'].strip():
                raise EvaluationBlocked(f"{case['id']}: message is required")
            if not set(case.get('expected_capabilities', [])) <= set(names):
                raise EvaluationBlocked(f"{case['id']}: expected capability is not offered")
            if any(name in names for name in ('aw_read_document', 'aw_search_documents', 'aw_read_table')) and not case.get('documents'):
                raise EvaluationBlocked(f"{case['id']}: document tools need a staged document")
            if 'expected_summary_source' in case:
                sources = [doc for doc in case.get('documents', []) if doc['name'] == case['expected_summary_source']]
                sequence = iter(case.get('expected_capabilities', []))
                if len(sources) != 1 or not all(name in sequence for name in ('aw_read_document', 'library:summarise_document')):
                    raise EvaluationBlocked(f"{case['id']}: summary source checks require one named source and a read before summarisation")


def run_cohorts(client: Client, spec: dict, timeout: float, target: dict, report=None, report_path=None) -> dict:
    out = report if report is not None else {}
    out.update(cohorts=[], gate=None, execution='running', verdict='incomplete')
    def save():
        rows = [row for cohort in out['cohorts'] for row in cohort['cases']]
        out['rates'] = rates(rows)
        out['counts'] = eval_reports.counts(rows)
        eval_reports.checkpoint(report_path, out, client)
    out['active'] = {'stage': 'preflight'}
    save()
    validate_cohorts(spec)
    cohorts, gate = spec.get('cohorts', []), spec.get('gate', {}).get('cases', [])
    every = {n for c in cohorts for n in c['capabilities']} | {n for case in gate for n in case['capabilities']}
    out['active'] = {'stage': 'setup'}
    save()
    blocks = install_entries(getattr(client, "workshop", None) or client, sorted(n[len('library:'):] for n in every if n.startswith('library:')))
    out['installed'] = blocks
    def run(case, names):
        out['active'] = {'case': case['id'], 'stage': 'run'}
        save()
        return run_cohort_case(client, case, names, blocks, timeout, target)
    for cohort in cohorts:
        group = {'id': cohort['id'], 'target': cohort.get('target'), 'cases': []}
        out['cohorts'].append(group)
        for case in cohort['cases']:
            group['cases'].append(run(case, cohort['capabilities']))
            group.update(rates=rates(group['cases']), counts=eval_reports.counts(group['cases']),
                         verdict=eval_reports.verdict(group['cases']))
            save()
            if group['cases'][-1].get('shared_target_unavailable'):
                raise EvaluationBlocked('shared target authentication or configuration failed')
    if gate:
        result = {'cases': [], 'passed': False, 'verdict': 'incomplete'}
        out['gate'] = result
        for case in gate:
            result['cases'].append(run(case, case['capabilities']))
            result['counts'] = eval_reports.counts(result['cases'])
            save()
            if result['cases'][-1].get('shared_target_unavailable'):
                raise EvaluationBlocked('shared target authentication or configuration failed')
        verdict = eval_reports.verdict(result['cases'])
        result.update(verdict=verdict, passed=verdict == 'passed',
                      consequence=None if verdict == 'passed' else
                      'Describe the library blocks for Workshop and workflows only; report Chat as unqualified.')
    out['cohorts_verdict'] = eval_reports.verdict([r for c in out['cohorts'] for r in c['cases']])
    rows = [r for c in out['cohorts'] for r in c['cases']] + (out['gate']['cases'] if out['gate'] else [])
    out.update(execution='complete', active=None, verdict=eval_reports.verdict(rows))
    save()
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    access = parser.add_mutually_exclusive_group()
    access.add_argument("--url", help="legacy combined UI address for older installations")
    access.add_argument("--session-file", type=Path, help="private launch handoff from AgentWiz 0.2.4")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--cases", help="a JSON list of cases")
    which.add_argument("--cohorts", help="a cohorts file (tests/agent_eval/cohorts.json)")
    parser.add_argument("--profile", help="evaluation profile; defaults to <route>-eval")
    parser.add_argument("--profiles-dir", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path, default=Path("reports/agent_eval_report.json"),
                        help="where to write the JSON report (default: reports/agent_eval_report.json)")
    parser.add_argument("--route", choices=("openrouter", "lmstudio", "openai_compat"), help="assert the selected route")
    parser.add_argument("--full-inventory", action="store_true",
                        help="after the fixed cohorts, exercise the complete default inventory")
    parser.add_argument("--chat-template", default=None, help="the server's chat template, recorded as given")
    parser.add_argument("--timeout", type=float, default=eval_setup.timeout_default(), help="seconds per read of a run's stream")
    args = parser.parse_args(argv)
    if args.full_inventory and (not args.cohorts or args.url):
        parser.error("--full-inventory requires --cohorts and a split installation")
    if (args.url or args.session_file) and any((args.profile, args.profiles_dir, args.config)):
        parser.error("--url cannot be combined with disposable configuration options")
    if not (args.url or args.session_file or args.profile or args.route):
        parser.error("disposable evaluation requires --route or --profile")
    report = {"chat_template": args.chat_template, "route": {"kind": args.route}}
    report.update(execution="running", verdict="incomplete")
    eval_reports.checkpoint(args.report, report)
    stack = ExitStack()
    try:
        if args.url:
            client = Client(args.url)
        elif args.session_file:
            if args.session_file.is_symlink() or args.session_file.stat().st_mode & 0o077:
                raise EvaluationBlocked("session file must be private")
            access_info = json.loads(args.session_file.read_text())
            mode = "local" if args.route in ("lmstudio", "openai_compat") else "cloud"
            workshop = Client(access_info["workshop"]["url"], access_info["workshop"]["session"])
            client = Client(access_info["chat"]["url"], access_info["chat"]["session"], mode=mode, workshop=workshop)
        else:
            workdir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="agentwiz-chat-eval-")))
            profile = args.profile or args.route + "-eval"
            ui = stack.enter_context(eval_setup.session(workdir, profile, args.route, args.profiles_dir,
                                                        args.config, split=True))
            workshop = Client(ui.url, ui.authority.entry(ui.url)["session"])
            chat = ui.chat_listener
            state = workshop.json("/api/state")
            if profile not in state["profiles"]["details"]:
                state = workshop.json("/api/profiles/select", {"name": profile, "revision": state["revision"]})
            selected = state["profiles"]["details"][profile]
            mode = selected["location"]
            client = Client(chat.url, chat.authority.entry(chat.url)["session"], mode=mode, workshop=workshop)
            client.ui = ui
            client.json("/api/chat/bindings", {"revision": state["roles"]["revision"],
                                                "bindings": {"fast": {mode: profile}}})
            if mode == "local":
                from agentwiz import outbound_policy
                binding = ui.state.role_capture("fast", mode)
                target_dest = outbound_policy.model_identity(binding.snapshot)
                checked = workshop.json("/api/workshop/destination-trust-preview",
                                        {"format_version": 1, "destination": target_dest})
                workshop.json("/api/workshop/destination-trust", {"format_version": 1,
                              "destination": target_dest, "expected_revision": checked["revision"], "trusted": True})
        state = chat_state(client)
        target = model_target(state)
        report.update(agentwiz_version=state.get("agentwiz_version"), modelspell_version=state.get("modelspell_version"),
                      route=target)
        if args.route is not None and args.route != target["kind"]:
            raise EvaluationBlocked(f"Chat uses {target['kind']}; expected {args.route}")
        check_target(state, target, require_fast=bool(args.cohorts))
        exit_code = evaluate(client, target, args, report)
    except KeyboardInterrupt:
        report.update(execution='interrupted', verdict='incomplete', reason='interrupted')
        exit_code = 3
    except Exception as exc:
        report.update(execution='blocked', verdict='blocked',
                      reason=str(exc) if isinstance(exc, (EvaluationBlocked, eval_setup.SetupBlocked)) else type(exc).__name__,
                      diagnostic=eval_reports.error(exc, (report.get('active') or {}).get('stage', 'setup')))
        exit_code = 2
    finally:
        # Save before cleanup: shutdown failure must not erase completed case evidence.
        eval_reports.checkpoint(args.report, report, locals().get('client'))
        stack.close()
    print(json.dumps({k: report[k] for k in ("verdict", "reason", "rates", "totals") if k in report}))
    return exit_code


def evaluate(client: Client, target: dict, args, report: dict) -> int:
    if args.cohorts:
        with open(args.cohorts, encoding="utf-8") as fh:
            spec = json.load(fh)
        run_cohorts(client, spec, args.timeout, target, report=report, report_path=args.report)
        if args.full_inventory and report["verdict"] == "passed":
            case = {"id": "full-default-inventory", "full_inventory": True,
                    "message": "Use the calculator tool to compute 19 * 23 and report the number.",
                    "expected_first_capability": "aw_calculate", "expected_answer": {"contains": ["437"]},
                    "documents": [{"name": "synthetic.txt", "text": "Example document.",
                                   "local_only": client.mode == "local"}]}
            check = run_case(client, case, args.timeout, target)
            report["full_inventory"] = check
            if (check.get("stop_reason") != "answered" or check.get("first_choice_correct") is not True
                    or check.get("answer_correct") is not True or len(check.get("offered", [])) <= 24):
                report["verdict"] = "failed"
        return {"passed": 0, "failed": 1, "blocked": 2, "incomplete": 3}[report["verdict"]]
    with open(args.cases, encoding="utf-8") as fh:
        cases = json.load(fh)
    rows = []
    report['cases'] = rows
    for case in cases:
        report['active'] = {'case': case['id'], 'stage': 'run'}
        eval_reports.checkpoint(args.report, report, client)
        rows.append(run_case(client, case, args.timeout, target))
        eval_reports.checkpoint(args.report, report, client)
        if rows[-1].get('shared_target_unavailable'):
            raise EvaluationBlocked('shared target authentication or configuration failed')
    counted = lambda key: sum(1 for r in rows if r[key] is True)
    report.update(cases=rows, totals={"cases": len(rows), "transport_ok": counted("transport_ok"),
                                    "first_arguments_valid": counted("first_arguments_valid"),
                                    "first_choice_correct": counted("first_choice_correct"),
                                    "answer_correct": counted("answer_correct")},
                  execution="complete", active=None, verdict="reported",
                  note="Custom cases report results without applying a release threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
