#!/usr/bin/env python3
"""Owner-run qualification of the library's model blocks (starter blocks addendum, 7.1).

    python scripts/library_eval.py --profile NAME [--block KEY] [--trials N] [--set dev|heldout|all] \
        [--route openrouter|lmstudio|openai_compat] [--profiles-dir PATH] [--config PATH] \
        [--report library_eval_report.json]
    python scripts/library_eval.py --review library_eval_report.json

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
sys.path.insert(0, str(CASES))

import checks  # noqa: E402  (tests/library_eval/checks.py)

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
            raise Refused(f"{path}: HTTP {exc.code} {exc.read()[:500].decode('utf-8', 'replace')}") from None

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
    row = {"failure_reason": failure, "violations": [], "hard_violation": False, "judgement": None}
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
    row["judgement"] = judgement(result["outputs"], expectation, True)
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
    row = classify(result, records, case["expectation"], key, case["inputs"])
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
    return row


# --- the bar ---------------------------------------------------------------------------------------------------------
def bar(runs: list[dict], cases: list[dict], trials: int) -> dict:
    scheduled = len(cases) * trials
    executed = sum(r["outcome"] in ("correct", "incorrect") for r in runs)
    correct = sum(r["outcome"] == "correct" for r in runs)
    critical = {c["id"] for c in cases if c.get("critical")}
    critical_ok = all(r["outcome"] == "correct" for r in runs if r["case"] in critical)
    hard_ok = not any(r.get("hard_violation") for r in runs)
    full = len(cases) == 10 and trials == 3 and len(runs) == 30
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
        elif any(r["outcome"] == "blocked" for r in runs):
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


def evaluate(client: Client, *, profile: str, route: str | None, blocks: list[str], trials: int, which: str,
             case_filter=None, progress=None) -> dict:
    import agentwiz
    import modelspell

    target = prepare(client, profile, route, blocks)
    resolved = verify_route(client, target, blocks)
    report = {"format_version": 2, "target": target, "resolved": resolved, "trials": trials, "set": which,
              "agentwiz_version": agentwiz.__version__, "modelspell_version": modelspell.__version__, "blocks": {}}
    for key in blocks:
        data = load_cases(key)
        cases = [c for c in select(data["cases"], which) if case_filter is None or case_filter(key, c)]
        runs = []
        for trial in range(1, trials + 1):
            for case in cases:
                row = {"case": case["id"], "trial": trial, "set": case["set"], "critical": bool(case.get("critical")),
                       **run_case(client, key, case, resolved[key])}
                if case.get("rubric"):
                    row["rubric"] = case["rubric"]
                    row["owner_mark"] = None
                runs.append(row)
                if progress:
                    progress(key, row)
        report["blocks"][key] = {"required_context": data.get("required_context"), "runs": runs,
                                 "bar": bar(runs, cases, trials)}
    update_verdict(report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--profile", help="an evaluation profile (or an explicitly supplied default)")
    mode.add_argument("--review", type=Path, help="refresh an existing report after marking its rubrics")
    parser.add_argument("--block", action="append", help="a catalog key; repeatable (default: every model block)")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--set", dest="which", choices=["dev", "heldout", "all"], default="dev")
    parser.add_argument("--route", choices=("openrouter", "lmstudio", "openai_compat"),
                        help="assert the route of the selected profile (default: use the profile's route)")
    parser.add_argument("--timeout", type=float, default=eval_setup.timeout_default())
    parser.add_argument("--profiles-dir", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=Path("library_eval_report.json"))
    args = parser.parse_args(argv)
    if args.review:
        report = json.loads(args.review.read_text(encoding="utf-8"))
        if report.get("format_version") != 2 or not report.get("blocks"):
            parser.error("--review requires a report with block results from this version of the evaluator")
        update_verdict(report)
        return write_report(args.review, report)

    known = model_keys()
    blocks = args.block or known
    unknown = [b for b in blocks if b not in known]
    if unknown or args.trials < 1:
        parser.error(f"unknown model block(s): {', '.join(unknown)}" if unknown else "--trials must be at least 1")
    try:
        with tempfile.TemporaryDirectory(prefix="agentwiz-library-eval-") as tmp:
            with eval_setup.session(Path(tmp), args.profile, args.route, args.profiles_dir, args.config) as ui:
                report = evaluate(Client(ui, args.timeout), profile=args.profile, route=args.route, blocks=blocks, trials=args.trials,
                                  which=args.which, progress=lambda k, r: print(
                                      f"{k} {r['case']} trial {r['trial']}: {r['outcome']}", flush=True))
    except (Refused, eval_setup.SetupBlocked, OSError, ValueError) as exc:
        report = {"format_version": 2, "target": {"profile": args.profile, "route": args.route},
                  "verdict": "blocked", "blocks": {},
                  "reason": str(exc) if isinstance(exc, (Refused, eval_setup.SetupBlocked)) else type(exc).__name__}
    return write_report(args.report, report)


def write_report(path: Path, report: dict) -> int:
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
