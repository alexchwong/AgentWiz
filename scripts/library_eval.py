#!/usr/bin/env python3
"""Owner-run qualification of the library's model blocks (starter blocks addendum, 7.1).

    python scripts/library_eval.py --profile NAME [--block KEY] [--trials N] [--set dev|heldout|all] \
        [--route openrouter|lmstudio|openai_compat] [--profiles-dir PATH] [--config PATH] \
        [--report reports/library_eval_report.json]
    python scripts/library_eval.py --review reports/library_eval_report.json

What it does, in order:

1. Creates a disposable workspace in a new temporary folder and starts an in-process AgentWiz on it (loopback
   only). The workspace's profiles are seeded from the checked-in evaluation defaults (or explicit --profiles-dir), so the profile named by
   --profile must be one of them. Nothing is written to any other workspace.
2. Binds every role the selected blocks use to that profile, and, when the profile's destination is on this
   machine, records it as a trusted destination in the disposable workspace (local only ports need one).
3. Installs the selected catalog entries through the ordinary installer: the plan call, then the add call with
   the plan's own confirmations and binding.
4. Resolves each installed block's role exactly as the block runner does and refuses to run (exit 2) if the
   resolved destination differs from the profile's.
5. Runs every selected case `--trials` times through the ordinary block runner (POST /api/blocks/run).
6. Writes the per-trial outcome, repair count, effective route and settings, usage, timing and failure reason.
   Rubric cases include the synthetic outputs and `owner_mark: null` for review. The target report leaves
   model context and local installation details for the owner (`target.owner_fill`).

The report holds the cases' fixtures and the diagnostics only: no workspace documents and no credentials.
This script is not shipped in the wheel.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CASES = ROOT / "tests" / "library_eval"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import eval_setup
import eval_reports
import library_reviews
from eval_attempts import attempt_diagnostics
sys.path.insert(0, str(CASES))

import checks  # noqa: E402  (tests/library_eval/checks.py)

def pause_between_cases(seconds):
    time.sleep(seconds)


class Refused(Exception):
    """The evaluation could not run with the selected setup."""


# --- the in-process AgentWiz ---------------------------------------------------------------------------------------
class Client:
    def __init__(self, ui, timeout=None):
        self.timeout = eval_setup.timeout_default() if timeout is None else timeout
        self.ui = ui
        self.root = f"http://127.0.0.1:{ui.server_address[1]}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, body=None, timeout=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.root + path, data=data, method="GET" if data is None else "POST",
                                     headers={"X-Session-Token": self.ui.token, "Content-Type": "application/json"})
        try:
            return self.opener.open(req, timeout=self.timeout if timeout is None else timeout)
        except urllib.error.HTTPError as exc:
            raise Refused(f"{path}: HTTP {exc.code}") from None

    def json(self, path, body=None):
        with self.call(path, body) as resp:
            return json.load(resp)

    def stream(self, path, body, timeout=None):
        with self.call(path, body, timeout=timeout) as resp:
            return [json.loads(line) for line in resp.read().splitlines() if line.strip()]


# --- cases ---------------------------------------------------------------------------------------------------------
def load_cases(key: str) -> dict:
    return json.loads((CASES / f"{key}.json").read_text(encoding="utf-8"))


def model_keys() -> list[str]:
    from agentwiz import library_install as lib
    return [k for k in lib.index() if lib.entry(k).group == "llm"]


def roles_of(key: str) -> list[str]:
    from agentwiz import library_install as lib
    return sorted({step["role"] for step in lib.entry(key).definition.get("steps", []) if step.get("role")})


def select(cases: list[dict], which: str) -> list[dict]:
    wanted = {"dev": {"dev"}, "heldout": {"heldout"}, "all": {"dev", "heldout"}}[which]
    return [c for c in cases if c.get("set") in wanted]


def preflight(blocks, which, case_filter=None):
    """Validate selected synthetic fixtures before contacting any model."""
    from agentwiz import checks as validation, library_install as lib
    from agentwiz.workshop_service import validate_expectation
    from agentwiz.workshop import WorkshopError

    selected = {}
    for key in blocks:
        data = load_cases(key)
        inputs = {name: validation.parse_contract(port['contract'])
                  for name, port in lib.entry(key).definition['inputs'].items()}
        outputs = {name: validation.parse_contract(port['contract'])
                   for name, port in lib.entry(key).definition['outputs'].items()}
        ids = set()
        cases = select(data['cases'], which)
        cases = [case for case in cases if case_filter is None or case_filter(key, case)]
        if not cases:
            raise Refused(f'{key}: no selected cases')
        for case in cases:
            location = f"{key}/{case.get('id', '<missing>')}"
            stage = 'id'
            try:
                if not isinstance(case['id'], str) or case['id'] in ids:
                    raise ValueError()
                ids.add(case['id'])
                stage = 'inputs'
                if set(case['inputs']) != set(inputs):
                    raise ValueError()
                for name, contract in inputs.items():
                    stage = f'inputs.{name}'
                    if not isinstance(case['inputs'][name], str) or validation.check_answer(
                            contract, case['inputs'][name])['overall'] != 'pass':
                        raise ValueError()
                stage = 'expectation'
                expectation = case['expectation']
                if expectation.get('kind') == 'fields':
                    for index, assertion in enumerate(expectation.get('assertions', [])):
                        stage = f'expectation.assertions[{index}]'
                        validate_expectation({'kind': 'fields', 'assertions': [assertion]}, outputs)
                stage = 'expectation'
                validate_expectation(expectation, outputs)
                if case['expectation']['kind'] == 'whole_output':
                    for name, text in case['expectation']['values'].items():
                        stage = f'expectation.values.{name}'
                        if validation.check_answer(outputs[name], text)['overall'] != 'pass':
                            raise ValueError()
            except (ValueError, KeyError, TypeError, AttributeError, WorkshopError):
                raise Refused(f'{location}: invalid fixture at {stage}') from None
        selected[key] = (data, cases)
    return selected


# --- set-up ------------------------------------------------------------------------------------------------------------
def prepare(client: Client, profile: str, route: str | None, keys: list[str]) -> dict:
    """Bind roles, trust a local destination, install the entries. Returns the target description."""
    from agentwiz import library_install as lib

    state = client.json("/api/state")
    details = state["profiles"]["details"]
    if profile not in details:
        raise Refused(f"profile {profile!r} is not among the shared defaults ({', '.join(sorted(details)) or 'none'})")
    settings = details[profile]
    if route is not None and settings["kind"] != route:
        raise Refused(f"profile {profile!r} uses route {settings.get('kind')!r}, not the target route {route!r}")
    local = settings["location"] == "local"
    restricted = [key for key in keys if any(port["local_only"] for ports in lib.ports(lib.entry(key).definition).values()
                                             for port in ports)]
    if restricted and not local:
        raise Refused(f"Local only blocks need a trusted local profile: {', '.join(restricted)}")

    route = settings["kind"]
    target = {"profile": profile, "route": route, "model": settings["model"], "base_url": settings["base_url"],
              "provider": settings["provider"] if route == "openrouter" else "", "trusted_destination": local,
              "routing": {f: settings.get(f) for f in ("routing_mode", "routing_preset", "routing_zdr", "routing_data_collection")}}
    roles = sorted({role for key in keys for role in roles_of(key)})
    role_view = state["roles"]
    definitions = role_view["definitions"]
    for role in roles:
        definition = definitions.get(role) or role_view["defaults"].get(role) or {"description": ""}
        definitions[role] = {**definition, "location": settings["location"]}
    client.json("/api/roles/save", {"roles": {**role_view["map"], **{r: profile for r in roles}},
                                    "definitions": definitions, "revision": role_view["revision"]})
    verify_route(client, target, keys)
    if local:
        grants = client.json("/api/workshop/destination-trust")
        client.json("/api/workshop/destination-trust", {
            "format_version": 1, "expected_revision": grants["revision"], "trusted": True,
            "destination": {"kind": route, "url": target["base_url"], "model": target["model"],
                            "provider": target["provider"]}})
    planned = client.json("/api/library/plan", {"format_version": 1,
                                                 "entries": [{"key": k} for k in keys]})["entries"]
    refused = [p for p in planned if p["outcome"] != "planned"]
    if refused:
        raise Refused("the installer did not plan: " + json.dumps(refused)[:2000])
    added = client.json("/api/library/add", {"format_version": 1, "entries": [
        {"key": p["key"], "as": p["as"], **({"role": p["role"]} if p.get("role") else {}),
         "expected_plan": p["expected_plan"], "confirmations": p["required_confirmations"],
         "binding": p["binding"]} for p in planned]})["entries"]
    failed = [a for a in added if a["outcome"] != "added"]
    if failed:
        raise Refused("the installer did not add: " + json.dumps(failed)[:2000])
    target["roles"] = {role: {"definition": definitions[role]} for role in roles}
    target["owner_fill"] = dict.fromkeys(("context_length", "reasoning", "temperature", "token_budget"))
    if local:
        target["owner_fill"].update(dict.fromkeys(("model_file", "quantisation", "server_version")))
    return target


def verify_route(client: Client, target: dict, keys: list[str]) -> dict:
    """The destination each block's role resolves to, refusing any that is not the target (no substitution)."""
    resolved = {}
    expected = (target["profile"], target["route"], target["base_url"], target["model"], target["provider"])
    for key in keys:
        for role in roles_of(key):
            dest = client.ui.state.role_destination(role)
            if "unresolved" in dest:
                raise Refused(f"{key}: role {role!r} is unresolved ({dest['unresolved']})")
            actual = tuple(dest[field] for field in ("profile", "kind", "url", "model", "provider"))
            if actual != expected:
                raise Refused(f"{key}: role {role!r} resolves to {actual}, not the target {expected}")
            capture = client.ui.state.role_capture(role)
            if {f: getattr(capture.snapshot, f) for f in target["routing"]} != target["routing"]:
                raise Refused(f"{key}: routing configuration changed")
            resolved[key] = {"role": role, **dest}
    return resolved


# --- one run -------------------------------------------------------------------------------------------------------
def decoded_outputs(outputs: dict) -> dict:
    out = {}
    for name, port in outputs.items():
        text = port.get("text", "")
        if port.get("format") == "json":
            try:
                out[name] = json.loads(text)
            except ValueError:
                out[name] = text
        else:
            out[name] = text
    return out


def attempt_records(client: Client, step: dict) -> list[dict]:
    """The step's attempt records, as the run store holds them (errors and reported usage)."""
    records = []
    for attempt_id in step.get("attempts") or []:
        try:
            envelope = client.ui.state.store.envelope(attempt_id)
        except Exception:  # noqa: BLE001 - a record no longer held is reported as missing
            continue
        if isinstance(envelope.get("record"), dict):
            records.append(envelope["record"])
    return records


def usage_of(records: list[dict]) -> dict:
    total = {"input_tokens": 0, "output_tokens": 0}
    seen = False
    for record in records:
        usage = (record.get("result") or {}).get("usage") if isinstance(record.get("result"), dict) else None
        if isinstance(usage, dict):
            for k in total:
                if isinstance(usage.get(k), int):
                    total[k] += usage[k]
                    seen = True
    return total if seen else {"input_tokens": None, "output_tokens": None}


def classify(result: dict, records: list[dict], expectation: dict, key: str, inputs: dict) -> dict:
    from agentwiz.workshop_service import judgement
    step = (result.get("steps") or [{}])[0]
    failure = step.get("failure")
    row = {"failure_reason": failure, "violations": [], "hard_violation": False, "judgement": None,
           "attempt_diagnostics": attempt_diagnostics(records)}
    if result.get("outcome") != "complete":
        errors = [r["error"] for r in records if r.get("error")]
        error = errors[-1] if errors else {}
        kind, status = error.get("class"), error.get("status")
        row["model_error"] = error or None
        if failure == "model_error" and "context" in error.get("description", "").lower():
            row["outcome"] = "context_rejected"
        elif kind in ("AuthError", "BackendError", "ConnectError", "RequestTimeout", "ResponseFormatError",
                      "ConfigError", "UnsupportedSetting", "UnsupportedOperation", "DestinationRefused",
                      "InternalError") or status in (401, 403, 404, 408, 429) or (
                          isinstance(status, int) and status >= 500):
            row["outcome"] = "blocked"
        elif failure == "truncated":
            row["outcome"] = "truncated"
        else:
            row["outcome"] = "contract_failed"
            row["failure_reason"] = failure or result.get("outcome")
        return row
    judged_outputs, adjustments = checks.judgement_outputs(key, inputs, result["outputs"], expectation)
    row["judgement_adjustments"] = adjustments
    row["judgement"] = judgement(judged_outputs, expectation, True)
    row["violations"] = checks.check(key, inputs, decoded_outputs(result["outputs"]))
    row["hard_violation"] = bool(row["violations"]) and key in checks.HARD
    correct = row["judgement"] in ("correct", "unknown") and not row["violations"]
    row["outcome"] = "correct" if correct else "incorrect"
    return row


def run_case(client: Client, key: str, case: dict, destination: dict) -> dict:
    revision = {b["name"]: b["revision"] for b in client.json("/api/blocks")["blocks"]}[key]
    started = time.monotonic()
    try:
        events = client.stream("/api/blocks/run", {"name": key, "revision": revision, "inputs": case["inputs"]})
    except (Refused, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, (Refused, eval_setup.SetupBlocked)) else type(exc).__name__
        return {"outcome": "blocked", "failure_reason": reason, "seconds": round(time.monotonic() - started, 3)}
    seconds = round(time.monotonic() - started, 3)
    finals = [e for e in events if e.get("type") == "final"]
    if len(finals) != 1:
        return {"outcome": "blocked", "failure_reason": "missing_or_duplicate_final", "seconds": seconds}
    result = finals[0]["result"]
    step = (result.get("steps") or [{}])[0]
    records = attempt_records(client, step)
    expected_endpoint = {"kind": destination["kind"], "base_url": destination["url"]}
    if (result.get("outcome") == "complete" and not records) or any(
        record["endpoint"] != expected_endpoint or record["model"] != destination["model"] or
        record["provider"] != destination["provider"] for record in records
    ):
        return {"outcome": "blocked", "failure_reason": "attempt_destination_unverified", "seconds": seconds}
    try:
        row = classify(result, records, case["expectation"], key, case["inputs"])
    except Exception as exc:
        row = {"outcome": "evaluator_error", "failure_reason": type(exc).__name__,
               "diagnostic": eval_reports.error(exc, "scoring")}
    row.update(eval_reports.safe({"inputs": case["inputs"], "expectation": case["expectation"],
                                 "outputs": result.get("outputs"), "result": result,
                                 "attempts": records, "attempt_diagnostics": attempt_diagnostics(records)}, client))
    if case.get("rubric") and result.get("outcome") == "complete":
        from agentwiz.history import redact

        outputs = {name: port["text"] for name, port in result["outputs"].items()}
        row["review_outputs"], _ = redact(outputs, client.ui.state.credentials())
    row.update({"first_pass": row["outcome"] in ("correct", "incorrect") and not step.get("repairs_used"),
                "repairs_used": step.get("repairs_used"),
                "role": step.get("role"), "route": (step.get("endpoint") or {}).get("kind"), "model": step.get("model"),
                "provider": records[-1].get("provider") if records else None,
                "served_by": (records[-1].get("result") or {}).get("provider") if records else None,
                "routing": records[-1].get("routing") if records else None,
                "task": records[-1].get("task") if records else None,
                **usage_of(records), "seconds": seconds})
    return eval_reports.safe(row, client)


# --- the bar ---------------------------------------------------------------------------------------------------------
def bar(runs: list[dict], cases: list[dict], trials: int) -> dict:
    scheduled = len(cases) * trials
    executed = sum(r["outcome"] in ("correct", "incorrect") for r in runs)
    correct = sum(r["outcome"] == "correct" for r in runs)
    critical = {c["id"] for c in cases if c.get("critical")}
    critical_ok = all(r["outcome"] == "correct" for r in runs if r["case"] in critical)
    hard_ok = not any(r.get("hard_violation") for r in runs)
    expected = {(case['id'], trial) for case in cases for trial in range(1, trials + 1)}
    observed = {(row['case'], row['trial']) for row in runs}
    full = len(cases) == 10 and trials == 3 and len(runs) == 30 and observed == expected
    return {"scheduled": scheduled, "contract_executions": executed, "correct": correct,
            "critical_all_correct": critical_ok, "hard_invariants_held": hard_ok,
            "full_qualification": full,
            "meets_bar": (full and executed >= 29 and correct >= 24 and critical_ok and hard_ok) if full else None}


def update_verdict(report: dict) -> None:
    for block in report["blocks"].values():
        scores = block["bar"]
        runs = block["runs"]
        marks = [r.get("owner_mark") for r in runs if "rubric" in r]
        scores["rubric_pending"] = sum(mark is not True and mark is not False for mark in marks)
        if scores["hard_invariants_held"] is False or any(mark is False for mark in marks):
            block["verdict"] = "failed"
        elif any(r["outcome"] in ("blocked", "evaluator_error") for r in runs):
            block["verdict"] = "blocked"
        elif not scores["full_qualification"]:
            block["verdict"] = "incomplete"
        elif not scores["meets_bar"]:
            block["verdict"] = "failed"
        else:
            block["verdict"] = "rubric_review_pending" if scores["rubric_pending"] else "passed"
    verdicts = {block["verdict"] for block in report["blocks"].values()}
    report["verdict"] = next((v for v in ("failed", "blocked", "incomplete", "rubric_review_pending")
                              if v in verdicts), "passed" if verdicts else "blocked")

    if report.get('execution') in ('running', 'interrupted'):
        report['verdict'] = 'incomplete'
    elif report.get('execution') == 'blocked':
        report['verdict'] = 'blocked'


def evaluate(client: Client, *, profile: str, route: str | None, blocks: list[str], trials: int, which: str,
             case_filter=None, progress=None, report=None, report_path=None, case_delay=0) -> dict:
    import agentwiz
    import modelspell

    report = report if report is not None else {}
    report.update(format_version=2, trials=trials, set=which, execution='running', verdict='incomplete',
                  agentwiz_version=agentwiz.__version__, modelspell_version=modelspell.__version__, blocks={})
    def save():
        update_verdict(report)
        eval_reports.checkpoint(report_path, report, client)
    report['active'] = {'stage': 'preflight'}
    save()
    selected = preflight(blocks, which, case_filter)
    report['active'] = {'stage': 'setup'}
    save()
    target = prepare(client, profile, route, blocks)
    resolved = verify_route(client, target, blocks)
    report.update(target=target, resolved=resolved, case_delay=case_delay)
    completed_cases = 0
    for key in blocks:
        data, cases = selected[key]
        runs = []
        block = {'required_context': data.get('required_context'), 'runs': runs, 'bar': bar(runs, cases, trials)}
        report['blocks'][key] = block
        for trial in range(1, trials + 1):
            for case in cases:
                if completed_cases and case_delay:
                    report['active'] = {'block': key, 'case': case['id'], 'trial': trial, 'stage': 'pacing'}
                    save()
                    pause_between_cases(case_delay)
                report['active'] = {'block': key, 'case': case['id'], 'trial': trial, 'stage': 'run'}
                save()
                try:
                    row = run_case(client, key, case, resolved[key])
                except Exception as exc:
                    row = {'outcome': 'evaluator_error', 'failure_reason': type(exc).__name__,
                           'diagnostic': eval_reports.error(exc, 'run')}
                row.update(case=case['id'], trial=trial, set=case['set'], critical=bool(case.get('critical')))
                if case.get('rubric'):
                    row.update(rubric=case['rubric'], owner_mark=None)
                runs.append(row)
                completed_cases += 1
                block['bar'] = bar(runs, cases, trials)
                save()
                if progress:
                    progress(key, row)
                error = row.get('model_error') or {}
                if error.get('class') in ('AuthError', 'ConfigError', 'UnsupportedSetting', 'DestinationRefused') or error.get('status') in (401, 403):
                    report.update(execution='blocked', reason='shared_target_unavailable')
                    save()
                    return report
    report.update(execution='complete', active=None)
    save()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--profile", help="an evaluation profile (or an explicitly supplied default)")
    mode.add_argument("--review", type=Path, help="refresh an existing report after marking its rubrics")
    mode.add_argument("--export-review", type=Path, help="export grouped rubric outputs from this report to --report")
    mode.add_argument("--apply-review", type=Path, help="import explicit case marks into --source-report")
    parser.add_argument("--source-report", type=Path, help="original report to update with --apply-review")
    parser.add_argument("--block", action="append", help="a catalog key; repeatable (default: every model block)")
    parser.add_argument("--deidentify-source", type=Path,
                        help="evaluate the deferred starter folder against an installed candidate")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--set", dest="which", choices=["dev", "heldout", "all"], default="dev")
    parser.add_argument("--route", choices=("openrouter", "lmstudio", "openai_compat"),
                        help="assert the route of the selected profile (default: use the profile's route)")
    parser.add_argument("--case-delay", type=float, default=0, help="seconds between cases (0-30); pacing only, never retries")
    parser.add_argument("--timeout", type=float, default=eval_setup.timeout_default())
    parser.add_argument("--profiles-dir", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=Path("reports/library_eval_report.json"))
    args = parser.parse_args(argv)
    if not 0 <= args.case_delay <= 30:
        parser.error("--case-delay must be between 0 and 30 seconds")
    if args.source_report and not args.apply_review:
        parser.error("--source-report requires --apply-review")
    if args.export_review:
        try:
            count = library_reviews.export_review(args.export_review, args.report)
        except (library_reviews.ReviewError, OSError, ValueError) as exc:
            parser.error(str(exc) if isinstance(exc, library_reviews.ReviewError) else type(exc).__name__)
        print(f"Review {count} grouped cases in {args.report}; original report unchanged")
        return 0
    if args.apply_review:
        if not args.source_report:
            parser.error("--apply-review requires --source-report")
        try:
            reviewed, digest, count = library_reviews.apply_review(args.apply_review, args.source_report)
            update_verdict(reviewed)
            library_reviews.publish_review(args.source_report, reviewed, digest)
        except (library_reviews.ReviewError, OSError, ValueError) as exc:
            parser.error(str(exc) if isinstance(exc, library_reviews.ReviewError) else type(exc).__name__)
        print(f"Applied {count} explicit rubric marks")
        return write_report(args.source_report, reviewed, persist=False)
    if args.review:
        report = json.loads(args.review.read_text(encoding="utf-8"))
        if report.get("format_version") != 2 or not report.get("blocks"):
            parser.error("--review requires a report with block results from this version of the evaluator")
        update_verdict(report)
        return write_report(args.review, report)

    known = model_keys()
    if args.deidentify_source is not None:
        if args.route != "lmstudio" or args.block != ["deidentify_text"]:
            parser.error("--deidentify-source requires --route lmstudio --block deidentify_text")
        known = [*known, "deidentify_text"]
    blocks = args.block or known
    unknown = [b for b in blocks if b not in known]
    if unknown or args.trials < 1:
        parser.error(f"unknown model block(s): {', '.join(unknown)}" if unknown else "--trials must be at least 1")
    report = {"format_version": 2, "target": {"profile": args.profile, "route": args.route},
              "execution": "running", "verdict": "incomplete", "blocks": {}}
    eval_reports.checkpoint(args.report, report)
    try:
        with tempfile.TemporaryDirectory(prefix="agentwiz-library-eval-") as tmp:
            from agentwiz import library_install
            original_library = library_install.LIBRARY
            try:
                if args.deidentify_source is not None:
                    overlay = Path(tmp) / "candidate-library"
                    shutil.copytree(original_library, overlay)
                    candidate = overlay / "deidentify_text"
                    shutil.copytree(args.deidentify_source, candidate, dirs_exist_ok=True)
                    index_file = overlay / "index.json"
                    index = json.loads(index_file.read_text(encoding="utf-8"))
                    if "deidentify_text" not in index["keys"]:
                        index["keys"].append("deidentify_text")
                        index_file.write_text(json.dumps(index), encoding="utf-8")
                    library_install.LIBRARY = overlay
                with eval_setup.session(Path(tmp), args.profile, args.route, args.profiles_dir, args.config) as ui:
                        report = evaluate(Client(ui, args.timeout), profile=args.profile, route=args.route, blocks=blocks, trials=args.trials,
                                          which=args.which, report=report, report_path=args.report, case_delay=args.case_delay, progress=lambda k, r: print(
                                              f"{k} {r['case']} trial {r['trial']}: {r['outcome']}", flush=True))
            finally:
                library_install.LIBRARY = original_library
    except KeyboardInterrupt:
        report.update(execution='interrupted', verdict='incomplete', reason='interrupted')
    except Exception as exc:
        report.update(execution='blocked', verdict='blocked',
                      reason=str(exc) if isinstance(exc, (Refused, eval_setup.SetupBlocked)) else type(exc).__name__,
                      diagnostic=eval_reports.error(exc, (report.get('active') or {}).get('stage', 'setup')))
    update_verdict(report)
    return write_report(args.report, report)


def write_report(path: Path, report: dict, *, persist=True) -> int:
    if persist:
        eval_reports.checkpoint(path, report)
    for key, block in report["blocks"].items():
        b = block["bar"]
        print(f"{key}: {b['contract_executions']}/{b['scheduled']} executed, {b['correct']} correct, "
              f"critical {'ok' if b['critical_all_correct'] else 'FAILED'}, "
              f"hard invariants {'held' if b['hard_invariants_held'] else 'VIOLATED'}"
              f", verdict {block['verdict']}")
    if report.get("reason"):
        print(report["reason"], file=sys.stderr)
    print(f"{report['verdict']}: {path}")
    return {"passed": 0, "failed": 1, "blocked": 2, "rubric_review_pending": 3, "incomplete": 3}[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
