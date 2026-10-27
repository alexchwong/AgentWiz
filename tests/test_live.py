"""Opt-in live checks against real backends (techspec 13.4, tests #16-#18).

Step-by-step instructions for every backend, and how to record the results: tests/LIVE_CHECKS.md.

Run:  python -m pytest -m live -s     (from tests/ when checking an installed wheel; see the runbook)
Each test skips unless its variables are set; CI sets none. Each prints one EVIDENCE line to copy
into tests/live_evidence.md. The line names the commit of these tests and whether agentwiz was
imported from an installed wheel or from the checkout. Each test runs against a temporary workspace.

Optional variables:
  AGENTWIZ_LIVE_TIMEOUT               seconds to wait for each read from AgentWiz (default 600). AgentWiz
                                      forwards nothing until the model's first visible token, so a
                                      model that loads, queues or reasons silently can take minutes.
  AGENTWIZ_LIVE_OPENROUTER_PROVIDER   provider slug to pin (default: the first one listed). Use it when
                                      your OpenRouter account settings exclude the first provider.
  AGENTWIZ_LIVE_LMSTUDIO_KEY          LM Studio authentication token, when its authentication is on
                                      (required for "Serve on Local Network"). Never printed.
"""
import datetime
import os
import subprocess
import sys
from pathlib import Path

import modelspell
import pytest

import agentwiz
from agentwiz.model_call import OUTCOMES
from conftest import events, get_state, request

CLASSIFIED = set(OUTCOMES) - {"error", "stopped"}
TIMEOUT = float(os.environ.get("AGENTWIZ_LIVE_TIMEOUT", "").strip() or 600)
pytestmark = pytest.mark.live


RUNBOOK = "tests/LIVE_CHECKS.md"


def _env(*names, optional=()):
    values = [os.environ.get(n, "").strip() for n in names]
    if not all(values):
        extra = f"; optional: {', '.join(optional)}" if optional else ""
        pytest.skip(f"set {', '.join(names)} to run this live check{extra} (see {RUNBOOK})")
    return values


def _imported_from() -> str:
    """'installed' when agentwiz comes from the environment's site-packages, otherwise 'checkout'."""
    here = Path(agentwiz.__file__).resolve()
    return "installed" if Path(sys.prefix).resolve() in here.parents else "checkout"


def _commit() -> str:
    """The commit of these tests, with +dirty for uncommitted changes, or 'unknown' without git."""
    folder = str(Path(__file__).resolve().parent)
    try:
        sha = subprocess.run(["git", "-C", folder, "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=30, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", folder, "status", "--porcelain"], capture_output=True,
                               text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (sha + ("+dirty" if dirty else "")) or "unknown"


def _call(ui, path, body=None, **kw):
    return request(ui, path, body, timeout=TIMEOUT, **kw)


def _configure(ui, **fields):
    return _call(ui, "/api/config", {"revision": get_state(ui)["revision"], **fields})


def _stream(ui, prompt="Reply with the single word: pong", hint=""):
    resp = _call(ui, "/api/complete", {"prompt": prompt, "revision": get_state(ui)["revision"]}, raw=True)
    evs = events(resp)
    assert evs, "AgentWiz sent no events"
    end = evs[-1]
    if end["type"] == "error":
        pytest.fail(f"the request ended with an error event: {end['error']}" + (f"\n{hint}" if hint else ""))
    assert end["type"] == "result", f"the stream ended without a final result; last event: {end}"
    assert "".join(e["text"] for e in evs if e["type"] == "text") == end["result"]["text"]
    assert end["outcome"] in CLASSIFIED
    return evs, end


def _evidence(backend, model, end):
    print(f"\nEVIDENCE {datetime.date.today().isoformat()} | AgentWiz {agentwiz.__version__} | "
          f"modelSpell {modelspell.__version__} | backend {backend} | model {model} | "
          f"transport {end['result']['transport']} | outcome {end['outcome']} | "
          f"agentwiz from {_imported_from()} | commit {_commit()}")


def test_live_lmstudio(ui):
    url, model = _env("AGENTWIZ_LIVE_LMSTUDIO_URL", "AGENTWIZ_LIVE_LMSTUDIO_MODEL",
                      optional=("AGENTWIZ_LIVE_LMSTUDIO_KEY", "AGENTWIZ_LIVE_TIMEOUT"))
    key = os.environ.get("AGENTWIZ_LIVE_LMSTUDIO_KEY", "").strip()
    auth = {"api_key": key} if key else {}
    _configure(ui, kind="lmstudio", base_url=url, model=model, **auth)
    ids = [m["id"] for m in _call(ui, "/api/models", {"kind": "lmstudio", "base_url": url, **auth})["models"]]
    assert model in ids, f"{model} is not among LM Studio's models: {ids}"
    evs, end = _stream(ui)
    _evidence("LM Studio", model, end)


def test_live_openrouter(ui):
    _key, model = _env("OPENROUTER_API_KEY", "AGENTWIZ_LIVE_OPENROUTER_MODEL",
                       optional=("AGENTWIZ_LIVE_OPENROUTER_PROVIDER", "AGENTWIZ_LIVE_TIMEOUT"))
    _configure(ui, kind="openrouter", base_url="", model=model)
    ids = [m["id"] for m in _call(ui, "/api/models", {"kind": "openrouter", "base_url": ""})["models"]]
    assert model in ids, f"{model} is not in OpenRouter's model list (expected the form author/slug)"
    providers = _call(ui, "/api/providers", {"kind": "openrouter", "base_url": "", "model": model})["providers"]
    tagged = [p for p in providers if p["slug"]]
    assert tagged, "no provider with a slug for this model"
    print("\nproviders (slug, name): " + ", ".join(f"{p['slug']} ({p['name']})" for p in tagged))
    wanted = os.environ.get("AGENTWIZ_LIVE_OPENROUTER_PROVIDER", "").strip()
    pinned = next((p for p in tagged if p["slug"] == wanted), None) if wanted else tagged[0]
    assert pinned is not None, f"AGENTWIZ_LIVE_OPENROUTER_PROVIDER={wanted} is not one of the slugs listed above"
    print(f"pinned provider: {pinned['slug']} ({pinned['name']}), fallbacks off")
    _configure(ui, provider=pinned["slug"])
    evs, end = _stream(ui, hint=(
        f"The request was pinned to {pinned['slug']} with fallbacks off. If OpenRouter refused it (often HTTP 404 "
        "when account privacy or provider settings exclude that provider), set AGENTWIZ_LIVE_OPENROUTER_PROVIDER "
        "to another slug from the list above."))
    assert end["result"]["provider"] == pinned["name"], (
        f"answered by {end['result']['provider']!r}, but {pinned['slug']} is listed as {pinned['name']!r}")
    _evidence(f"OpenRouter (provider {pinned['slug']})", model, end)


def test_live_llamaserver(ui):
    url, model = _env("AGENTWIZ_LIVE_LLAMASERVER_URL", "AGENTWIZ_LIVE_LLAMASERVER_MODEL",
                      optional=("AGENTWIZ_LIVE_TIMEOUT",))
    _configure(ui, kind="openai_compat", base_url=url, model=model)
    assert _call(ui, "/api/models", {"kind": "openai_compat", "base_url": url})["models"]
    evs, end = _stream(ui)
    reasoning = "".join(e["text"] for e in evs if e["type"] == "reasoning")
    assert reasoning == (end["result"]["reasoning"] or "")
    _evidence("llama-server", model, end)
