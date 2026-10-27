"""Opt-in live checks against real backends (techspec 13.4, tests #16-#18).

Optional: a quick end-to-end check against a backend you have running. Variables: DEVEL.md, Tests.

Run:  python -m pytest -m live -s -k <lmstudio|openrouter|llamaserver>
Each test skips unless its variables are set; CI sets none. Each runs against a temporary workspace
and prints one summary line.

Optional variables:
  AGENTWIZ_LIVE_TIMEOUT               seconds to wait for each read from AgentWiz (default 600). AgentWiz
                                      forwards nothing until the model's first visible token, so a
                                      model that loads, queues or reasons silently can take minutes.
  AGENTWIZ_LIVE_OPENROUTER_PROVIDER   provider slug to pin (default: the first one listed). Use it when
                                      your OpenRouter account settings exclude the first provider.
  AGENTWIZ_LIVE_LMSTUDIO_KEY          LM Studio authentication token, when its authentication is on
                                      (required for "Serve on Local Network"). Never printed.
"""
import os

import modelspell
import pytest

import agentwiz
from agentwiz.model_call import OUTCOMES
from conftest import events, get_state, request

CLASSIFIED = set(OUTCOMES) - {"error", "stopped"}
TIMEOUT = float(os.environ.get("AGENTWIZ_LIVE_TIMEOUT", "").strip() or 600)
pytestmark = pytest.mark.live


def _env(*names, optional=()):
    values = [os.environ.get(n, "").strip() for n in names]
    if not all(values):
        extra = f"; optional: {', '.join(optional)}" if optional else ""
        pytest.skip(f"set {', '.join(names)} to run this live check{extra} (see DEVEL.md, Tests)")
    return values


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


def _report(backend, model, end):
    print(f"\nAgentWiz {agentwiz.__version__} | modelSpell {modelspell.__version__} | backend {backend} | "
          f"model {model} | transport {end['result']['transport']} | outcome {end['outcome']}")


def test_live_lmstudio(ui):
    url, model = _env("AGENTWIZ_LIVE_LMSTUDIO_URL", "AGENTWIZ_LIVE_LMSTUDIO_MODEL",
                      optional=("AGENTWIZ_LIVE_LMSTUDIO_KEY", "AGENTWIZ_LIVE_TIMEOUT"))
    key = os.environ.get("AGENTWIZ_LIVE_LMSTUDIO_KEY", "").strip()
    auth = {"api_key": key} if key else {}
    _configure(ui, kind="lmstudio", base_url=url, model=model, **auth)
    ids = [m["id"] for m in _call(ui, "/api/models", {"kind": "lmstudio", "base_url": url, **auth})["models"]]
    assert model in ids, f"{model} is not among LM Studio's models: {ids}"
    evs, end = _stream(ui)
    _report("LM Studio", model, end)


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
    _report(f"OpenRouter (provider {pinned['slug']})", model, end)


def test_live_llamaserver(ui):
    url, model = _env("AGENTWIZ_LIVE_LLAMASERVER_URL", "AGENTWIZ_LIVE_LLAMASERVER_MODEL",
                      optional=("AGENTWIZ_LIVE_TIMEOUT",))
    _configure(ui, kind="openai_compat", base_url=url, model=model)
    assert _call(ui, "/api/models", {"kind": "openai_compat", "base_url": url})["models"]
    evs, end = _stream(ui)
    reasoning = "".join(e["text"] for e in evs if e["type"] == "reasoning")
    assert reasoning == (end["result"]["reasoning"] or "")
    _report("llama-server", model, end)
