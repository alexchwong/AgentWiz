"""model_call without HTTP: techspec 13.3 tests #13-#14."""
import pytest
from modelspell import Result

from agentwiz.model_call import ModelSettings, classify_outcome, complete_kwargs


@pytest.mark.parametrize("settings, expected", [
    (ModelSettings(kind="openrouter", provider="deepinfra/turbo", reasoning="high", max_tokens=64),
     {"reasoning": "high", "max_tokens": 64, "provider": {"order": ["deepinfra/turbo"], "allow_fallbacks": False}}),
    (ModelSettings(kind="lmstudio", provider="deepinfra/turbo", reasoning="off"), {"reasoning": "off"}),
    (ModelSettings(kind="openai_compat", base_url="http://x/v1", reasoning="high"), {}),
    (ModelSettings(), {}),
])
def test_complete_kwargs(settings, expected):
    assert complete_kwargs(settings, settings.kind) == expected


def result(text, reasoning, truncated, **kw):
    fields = dict(usage=None, cost=None, finish_reason="length" if truncated else "stop", model=None,
                  provider=None, response_id=None, transport="chat_completions", reasoning_details=None,
                  raw_usage=None)
    fields.update(kw)
    return Result(text=text, reasoning=reasoning, truncated=truncated, **fields)


@pytest.mark.parametrize("res, outcome", [
    (result("ok", None, False), "complete"),
    (result("", None, False), "empty"),
    (result("  \n", None, False), "empty"),
    (result("", "thinking", False), "reasoning_only"),
    (result("ok", None, True), "truncated"),
    (result("", None, True), "truncated_no_answer"),
    (result("", "r", True), "truncated_no_answer"),
    (result("ok", None, False, usage=None, cost=None), "complete"),
])
def test_classify_outcome(res, outcome):
    assert classify_outcome(res) == outcome
