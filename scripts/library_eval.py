#!/usr/bin/env python3
"""Owner-run qualification of the library's model blocks (starter blocks addendum, 7.1).

    python scripts/library_eval.py --profile NAME [--block KEY] [--trials N] [--set dev|heldout|all] \
        [--route lmstudio] [--profiles-dir ./model_profiles] [--config ./config/model-servers.ini] \
        [--report library_eval_report.json]

What it does, in order:

1. Creates a disposable workspace in a new temporary folder and starts an in-process AgentWiz on it (loopback
   only). The workspace's profiles are seeded from the shared defaults in --profiles-dir, so the profile named by
   --profile must be one of them. Nothing is written to any other workspace.
2. Binds every role the selected blocks use to that profile, and, when the profile's destination is on this
   machine, records it as a trusted destination in the disposable workspace (local only ports need one).
3. Installs the selected catalog entries through the ordinary installer: the plan call, then the add call with
   the plan's own confirmations and binding.
4. Resolves each installed block's role exactly as the block runner does and refuses to run (exit 2) if the
   resolved route or model differs from the profile's. There is no substitution.
5. Runs every selected case `--trials` times through the ordinary block runner (POST /api/blocks/run).
6. Writes the report: per case and trial the outcome (context_rejected, truncated, contract_failed, incorrect,
   correct), first-pass or repaired, repairs used, effective role and route, input and output tokens, wall time
   and failure reason; per block the bar of 7.1. Rubric cases carry their rubric with `owner_mark: null` for
   the owner to fill in. The target's model file, quantisation, context length, LM Studio version and reasoning
   setting are not visible to AgentWiz, so the report leaves them for the owner (`target.owner_fill`).

The report holds the cases' fixtures and the diagnostics only: no workspace documents and no credentials.
This script is not shipped in the wheel.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CASES = ROOT / "tests" / "library_eval"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CASES))

import checks  # noqa: E402  (tests/library_eval/checks.py)

OUTCOMES = ("context_rejected", "truncated", "contract_failed", "incorrect", "correct")
BAR_CONTRACT, BAR_CORRECT = 29 / 30, 24 / 30


class Refused(Exception):
    """The run cannot qualify anything (exit 2): a missing profile, a route or model mismatch, an install refusal."""


# --- the in-process AgentWiz ---------------------------------------------------------------------------------------
class Client:
    def __init__(self, ui):
        self.ui = ui
        self.root = f"http://127.0.0.1:{ui.server_address[1]}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, body=None, timeout=600):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.root + path, data=data, method="GET" if data is None else "POST",
                                     headers={"X-Session-Token": self.ui.token, "Content-Type": "application/json"})
        try:
            return self.opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise Refused(f"{path}: HTTP {exc.code} {exc.read()[:500].decode('utf-8', 'replace')}") from None

    def json(self, path, body=None):
        return json.loads(self.call(path, body).read())

    def stream(self, path, body, timeout=900):
        resp = self.call(path, body, timeout=timeout)
        return [json.loads(line) for line in resp.read().splitlines() if line.strip()]


def start(workdir: Path, profiles_dir: Path, config: Path):
    from agentwiz import server_config
    from agentwiz.ui.server import UIServer
    from agentwiz.workspace import open_workspace
    server_config.initialize(config)
    workspace = open_workspace(workdir / "workspaces", "library-eval")
    ui = UIServer(0, workspace=workspace, profiles_dir=profiles_dir, mcp_servers_dir=workdir / "mcp_servers",
                  servers=server_config.load(config), config_path=config.absolute())
    threading.Thread(target=ui.serve_forever, daemon=True).start()
    return ui


def stop(ui) -> None:
    ui.shutdown()
    ui.server_close()
    try:
        ui.state.workspace.close()
    except Exception:  # noqa: BLE001 - the folder is temporary
        pass


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
def _local(url: str) -> bool:
    host = urllib.parse.urlsplit(url or "").hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def prepare(client: Client, profile: str, route: str, keys: list[str]) -> dict:
    """Bind roles, trust a local destination, install the entries. Returns the target description."""
    state = client.json("/api/state")
    details = state["profiles"]["details"]
    if profile not in details:
        raise Refused(f"profile {profile!r} is not among the shared defaults ({', '.join(sorted(details)) or 'none'})")
    settings = details[profile]
    if settings.get("kind") != route:
        raise Refused(f"profile {profile!r} uses route {settings.get('kind')!r}, not the target route {route!r}")
    roles = sorted({role for key in keys for role in roles_of(key)})
    client.json("/api/roles/save", {"roles": {**state["roles"]["map"], **{r: profile for r in roles}},
                                    "revision": state["roles"]["revision"]})
    destination = client.ui.state.role_destination(roles[0]) if roles else {}
    trusted = False
    if roles and _local(destination.get("url", "")):
        grants = client.json("/api/workshop/destination-trust")
        client.json("/api/workshop/destination-trust", {
            "format_version": 1, "expected_revision": grants["revision"], "trusted": True,
            "destination": {k: destination[k] for k in ("kind", "url", "model", "provider")}})
        trusted = True
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
    roles_view = client.json("/api/state")["roles"]
    return {"profile": profile, "route": route, "model": settings.get("model"), "base_url": settings.get("base_url"),
            "roles": {r: {"definition": roles_view["definitions"].get(r) or roles_view["defaults"].get(r)}
                      for r in roles},
            "trusted_destination": trusted,
            "owner_fill": {"model_file": None, "quantisation": None, "context_length": None,
                           "lmstudio_version": None, "reasoning": None, "temperature": None, "token_budget": None}}


def verify_route(client: Client, target: dict, keys: list[str]) -> dict:
    """The destination each block's role resolves to, refusing any that is not the target (no substitution)."""
    resolved = {}
    for key in keys:
        for role in roles_of(key):
            dest = client.ui.state.role_destination(role)
            if "unresolved" in dest:
                raise Refused(f"{key}: role {role!r} is unresolved ({dest['unresolved']})")
            if dest["kind"] != target["route"] or dest["model"] != target["model"]:
                raise Refused(f"{key}: role {role!r} resolves to {dest['kind']}/{dest['model']}, "
                              f"not the target {target['route']}/{target['model']}")
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
    errors = " ".join(json.dumps(r.get("error") or "") for r in records)
    row = {"failure_reason": failure, "violations": [], "hard_violation": False, "judgement": None}
    if result.get("outcome") != "complete":
        if failure == "model_error" and "context" in errors.lower():
            row["outcome"] = "context_rejected"
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


def run_case(client: Client, key: str, case: dict) -> dict:
    revision = {b["name"]: b["revision"] for b in client.json("/api/blocks")["blocks"]}[key]
    started = time.monotonic()
    events = client.stream("/api/blocks/run", {"name": key, "revision": revision, "inputs": case["inputs"]})
    seconds = round(time.monotonic() - started, 3)
    finals = [e for e in events if e.get("type") == "final"]
    if len(finals) != 1:
        return {"outcome": "contract_failed", "failure_reason": "no_final_event", "seconds": seconds}
    result = finals[0]["result"]
    step = (result.get("steps") or [{}])[0]
    records = attempt_records(client, step)
    row = classify(result, records, case["expectation"], key, case["inputs"])
    row.update({"first_pass": row["outcome"] in ("correct", "incorrect") and not step.get("repairs_used"),
                "repairs_used": step.get("repairs_used"),
                "role": step.get("role"), "route": (step.get("endpoint") or {}).get("kind"), "model": step.get("model"),
                **usage_of(records), "seconds": seconds})
    return row


# --- the bar ---------------------------------------------------------------------------------------------------------
def bar(key: str, runs: list[dict], cases: list[dict], trials: int) -> dict:
    scheduled = len(cases) * trials
    executed = sum(r["outcome"] in ("correct", "incorrect") for r in runs)
    correct = sum(r["outcome"] == "correct" for r in runs)
    critical = {c["id"] for c in cases if c.get("critical")}
    critical_ok = all(r["outcome"] == "correct" for r in runs if r["case"] in critical)
    hard_ok = not any(r.get("hard_violation") for r in runs)
    full = scheduled == 30
    return {"scheduled": scheduled, "contract_executions": executed, "correct": correct,
            "critical_all_correct": critical_ok, "hard_invariants_held": hard_ok,
            "rubric_pending": sum(1 for c in cases if c.get("rubric")) * trials,
            "full_qualification": full,
            "meets_bar": (full and executed >= 29 and correct >= 24 and critical_ok and hard_ok) if full else None}


def evaluate(client: Client, *, profile: str, route: str, blocks: list[str], trials: int, which: str,
             case_filter=None, progress=None) -> dict:
    target = prepare(client, profile, route, blocks)
    resolved = verify_route(client, target, blocks)
    report = {"format_version": 1, "target": target, "resolved": resolved, "trials": trials, "set": which,
              "blocks": {}}
    for key in blocks:
        data = load_cases(key)
        cases = [c for c in select(data["cases"], which) if case_filter is None or case_filter(key, c)]
        runs = []
        for trial in range(1, trials + 1):
            for case in cases:
                row = {"case": case["id"], "trial": trial, "set": case["set"], "critical": bool(case.get("critical")),
                       **run_case(client, key, case)}
                if case.get("rubric"):
                    row["rubric"] = case["rubric"]
                    row["owner_mark"] = None
                runs.append(row)
                if progress:
                    progress(key, row)
        report["blocks"][key] = {"required_context": data.get("required_context"), "runs": runs,
                                 "bar": bar(key, runs, cases, trials)}
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", required=True, help="a shared default profile (the target)")
    parser.add_argument("--block", action="append", help="a catalog key; repeatable (default: every model block)")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--set", dest="which", choices=["dev", "heldout", "all"], default="dev")
    parser.add_argument("--route", default="lmstudio", help="the target route the profile must use")
    parser.add_argument("--profiles-dir", type=Path, default=Path("./model_profiles"))
    parser.add_argument("--config", type=Path, default=Path("./config/model-servers.ini"))
    parser.add_argument("--report", type=Path, default=Path("library_eval_report.json"))
    args = parser.parse_args(argv)
    known = model_keys()
    blocks = args.block or known
    unknown = [b for b in blocks if b not in known]
    if unknown or args.trials < 1:
        parser.error(f"unknown model block(s): {', '.join(unknown)}" if unknown else "--trials must be at least 1")
    with tempfile.TemporaryDirectory(prefix="agentwiz-library-eval-") as tmp:
        ui = start(Path(tmp), args.profiles_dir.resolve(), args.config)
        try:
            report = evaluate(Client(ui), profile=args.profile, route=args.route, blocks=blocks, trials=args.trials,
                              which=args.which, progress=lambda k, r: print(
                                  f"{k} {r['case']} trial {r['trial']}: {r['outcome']}", flush=True))
        except Refused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        finally:
            stop(ui)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for key, block in report["blocks"].items():
        b = block["bar"]
        print(f"{key}: {b['contract_executions']}/{b['scheduled']} executed, {b['correct']} correct, "
              f"critical {'ok' if b['critical_all_correct'] else 'FAILED'}, "
              f"hard invariants {'held' if b['hard_invariants_held'] else 'VIOLATED'}"
              + ("" if b["meets_bar"] is None else f", bar {'met' if b['meets_bar'] else 'NOT met'}"))
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
