"""Opt-in live checks against real backends (techspec 13.4, tests #16-#18).

Run:  python -m pytest -m live -s
Each test skips unless its variables are set; CI sets none. Each prints one evidence line to copy
into tests/live_evidence.md.
"""
import datetime
import os

import modelspell
import pytest

import agentwiz
from agentwiz.model_call import OUTCOMES
from conftest import events, get_state, request

CLASSIFIED = set(OUTCOMES) - {"error", "stopped"}
pytestmark = pytest.mark.live


def _env(*names):
    values = [os.environ.get(n, "").strip() for n in names]
    if not all(values):
        pytest.skip("set " + ", ".join(names) + " to run this live check")
    return values


def _configure(ui, **fields):
    return request(ui, "/api/config", {"revision": get_state(ui)["revision"], **fields})


def _stream(ui, prompt="Reply with the single word: pong"):
    resp = request(ui, "/api/complete", {"prompt": prompt, "revision": get_state(ui)["revision"]}, raw=True)
    evs = events(resp)
    end = evs[-1]
    assert end["type"] == "result", end
    assert "".join(e["text"] for e in evs if e["type"] == "text") == end["result"]["text"]
    assert end["outcome"] in CLASSIFIED
    return evs, end


def _evidence(backend, model, end):
    print(f"\nEVIDENCE {datetime.date.today().isoformat()} | AgentWiz {agentwiz.__version__} | "
          f"modelSpell {modelspell.__version__} | backend {backend} | model {model} | "
          f"transport {end['result']['transport']} | outcome {end['outcome']}")


def test_live_lmstudio(ui):
    url, model = _env("AGENTWIZ_LIVE_LMSTUDIO_URL", "AGENTWIZ_LIVE_LMSTUDIO_MODEL")
    _configure(ui, kind="lmstudio", base_url=url, model=model)
    ids = [m["id"] for m in request(ui, "/api/models", {"kind": "lmstudio", "base_url": url})["models"]]
    assert model in ids
    evs, end = _stream(ui)
    _evidence("LM Studio", model, end)


def test_live_openrouter(ui):
    _key, model = _env("OPENROUTER_API_KEY", "AGENTWIZ_LIVE_OPENROUTER_MODEL")
    _configure(ui, kind="openrouter", base_url="", model=model)
    assert request(ui, "/api/models", {"kind": "openrouter", "base_url": ""})["models"]
    providers = request(ui, "/api/providers", {"kind": "openrouter", "base_url": "", "model": model})["providers"]
    pinned = next((p for p in providers if p["slug"]), None)
    assert pinned is not None, "no provider with a slug for this model"
    _configure(ui, provider=pinned["slug"])
    evs, end = _stream(ui)
    assert end["result"]["provider"] == pinned["name"]
    _evidence(f"OpenRouter (provider {pinned['slug']})", model, end)


def test_live_llamaserver(ui):
    url, model = _env("AGENTWIZ_LIVE_LLAMASERVER_URL", "AGENTWIZ_LIVE_LLAMASERVER_MODEL")
    _configure(ui, kind="openai_compat", base_url=url, model=model)
    assert request(ui, "/api/models", {"kind": "openai_compat", "base_url": url})["models"]
    evs, end = _stream(ui)
    reasoning = "".join(e["text"] for e in evs if e["type"] == "reasoning")
    assert reasoning == (end["result"]["reasoning"] or "")
    _evidence("llama-server", model, end)
