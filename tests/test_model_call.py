"""model_call without HTTP: techspec 0.1.1 13.3 tests #13-#14; 0.1.2 Section 15.3 (test_task_arguments)."""
import pytest
from modelspell import Result

from agentwiz.model_call import classify_outcome, task_arguments
from agentwiz.task_settings import TaskSettings

PIN = {"order": ["deepinfra/turbo"], "allow_fallbacks": False}


@pytest.mark.parametrize("task, kind, pin, expected", [
    # Section 7.4's table: provider pin x (only max_tokens | another setting)
    (TaskSettings(), "openrouter", "", {}),
    (TaskSettings(max_tokens=64), "openrouter", "", {"max_tokens": 64}),
    (TaskSettings(temperature=0.5), "openrouter", "", {"temperature": 0.5, "provider": {"require_parameters": True}}),
    (TaskSettings(reasoning="off"), "openrouter", "", {"reasoning": "off", "provider": {"require_parameters": True}}),
    (TaskSettings(max_tokens=64), "openrouter", "deepinfra/turbo", {"max_tokens": 64, "provider": PIN}),
    (TaskSettings(reasoning="high", max_tokens=64), "openrouter", "deepinfra/turbo",
     {"reasoning": "high", "max_tokens": 64, "provider": {**PIN, "require_parameters": True}}),
    (TaskSettings(advanced={"top_k": 40, "stop": ("x",)}), "openrouter", "",
     {"top_k": 40, "stop": ["x"], "provider": {"require_parameters": True}}),
    # other kinds: no provider; blanks absent
    (TaskSettings(reasoning="off", advanced={"top_k": 3}), "lmstudio", "", {"reasoning": "off", "top_k": 3}),
    (TaskSettings(temperature=0.2, advanced={"seed": 7}), "openai_compat", "", {"temperature": 0.2, "seed": 7}),
    (TaskSettings(), "lmstudio", "", {}),
])
def test_task_arguments(task, kind, pin, expected):
    assert task_arguments(task, kind, pin) == expected


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
