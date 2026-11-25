#!/usr/bin/env python3
"""Run an opt-in backend integration check with an explicit model ID."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = "tests/test_live.py"
CHECKS = {
    "openrouter": {
        "basic": f"{TEST_FILE}::test_live_openrouter",
        "manual": f"{TEST_FILE}::test_live_openrouter_manual",
        "stop": f"{TEST_FILE}::test_live_stop_openrouter",
        "block-repair": f"{TEST_FILE}::test_live_block_repair_openrouter",
        "webspell": "tests/test_live_webspell.py",
    },
    "lmstudio": {
        "basic": f"{TEST_FILE}::test_live_lmstudio",
        "stop": f"{TEST_FILE}::test_live_stop_local[lmstudio]",
    },
    "llamaserver": {
        "basic": f"{TEST_FILE}::test_live_llamaserver",
        "stop": f"{TEST_FILE}::test_live_stop_local[llamaserver]",
    },
}
LOCAL_URLS = {
    "lmstudio": "http://127.0.0.1:1234/v1",
    "llamaserver": "http://127.0.0.1:8080/v1",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=CHECKS)
    parser.add_argument("--model", required=True, help="exact backend model ID")
    parser.add_argument("--check", default="basic", choices=sorted(
        {check for checks in CHECKS.values() for check in checks}))
    parser.add_argument("--base-url", help="API base URL for a local backend")
    parser.add_argument("--provider", help="pin an OpenRouter provider")
    parser.add_argument("--mcp-url", help="override the managed webSpell MCP URL")
    parser.add_argument("--timeout", type=float, help="seconds per read for backend diagnostics")
    parser.add_argument("--junitxml", type=Path, help="write the pytest results to this file")
    args = parser.parse_args(argv)

    test = CHECKS[args.backend].get(args.check)
    if test is None:
        parser.error(f"--check {args.check} is unavailable for {args.backend}")
    if not args.model.strip():
        parser.error("--model must be non-empty")
    if args.base_url is not None and args.backend not in LOCAL_URLS:
        parser.error("--base-url requires a local backend")
    if args.provider is not None and (args.backend != "openrouter" or args.check == "webspell"):
        parser.error("--provider applies to OpenRouter backend diagnostics")
    if args.mcp_url is not None and args.check != "webspell":
        parser.error("--mcp-url requires --check webspell")

    env = os.environ.copy()
    prefix = "AGENTWIZ_LIVE_" + args.backend.upper()
    env[prefix + "_MODEL"] = args.model
    if args.backend in LOCAL_URLS:
        env[prefix + "_URL"] = args.base_url or env.get(prefix + "_URL") or LOCAL_URLS[args.backend]
    else:
        if not env.get("OPENROUTER_API_KEY", "").strip():
            parser.error("set OPENROUTER_API_KEY before running the check")
        if args.provider is not None:
            env[prefix + "_PROVIDER"] = args.provider
        if args.check == "manual" and not env.get(prefix + "_PROVIDER", "").strip():
            parser.error("--check manual requires --provider")
    if args.mcp_url is not None:
        env["AGENTWIZ_LIVE_MCP_URL"] = args.mcp_url
    if args.timeout is not None:
        env["AGENTWIZ_LIVE_TIMEOUT"] = str(args.timeout)

    command = [sys.executable, "-m", "pytest", "-m", "live", "-s", test]
    if args.junitxml is not None:
        command.extend(["--junitxml", str(args.junitxml.resolve())])
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
