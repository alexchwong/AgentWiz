"""Settings, keys, task arguments, the model call, outcome classification and error descriptions
(techspec 0.1.1, 7.4; 0.1.2, 7.2-7.4).

Uses only modelSpell's public API and knows nothing about HTTP, so later releases can reuse
`task_arguments`, `call_model`, `classify_outcome` and `describe_error` as the model-step executor.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

import modelspell
from modelspell import (AuthError, BackendError, ConfigError, ConnectError, Endpoint, ModelInfo,
                        ModelSpellError, RequestTimeout, ResponseFormatError, Result, UnsupportedOperation,
                        UnsupportedSetting)

from .task_settings import LABELS, TaskSettingError, TaskSettings

KINDS = ("lmstudio", "openrouter", "openai_compat")
KIND_NAMES = {"lmstudio": "LM Studio", "openrouter": "OpenRouter", "openai_compat": "OpenAI-compatible servers"}
RETIRED = ("reasoning", "max_tokens")
RETIRED_MESSAGE = ("reasoning and max_tokens are task settings from AgentWiz 0.1.2; "
                   "set them in the request panel")
OPENROUTER_HINT = ("OpenRouter found no provider for this request. Check the provider pinned in the profile "
                   "and your OpenRouter privacy settings")
OUTCOMES = ("complete", "empty", "reasoning_only", "truncated",
            "truncated_no_answer", "error", "stopped")

_FIELDS = ("kind", "base_url", "model", "provider")


class SettingsError(ValueError):
    """An invalid setting or key. Messages name fields only, never values."""


@dataclass(frozen=True)
class ModelSettings:
    kind: str = "lmstudio"
    base_url: str = ""          # "" means the kind's default URL
    model: str = ""
    provider: str = ""          # OpenRouter provider slug; "" means any


@dataclass(frozen=True)
class RequestSnapshot:
    endpoint: Endpoint          # with its key
    model: str
    kind: str
    provider: str               # the profile's OpenRouter provider pin; "" means none


def settings_from_body(current: ModelSettings, body: Mapping) -> ModelSettings:
    """Partial update: each field takes the body's value if present, else the current value.

    A body with the retired `reasoning` or `max_tokens` is refused (Section 7.6)."""
    if any(f in body for f in RETIRED):
        raise SettingsError(RETIRED_MESSAGE)
    values = {f: body[f] if f in body else getattr(current, f) for f in _FIELDS}
    if values["kind"] not in KINDS:
        raise SettingsError(f"kind must be one of {', '.join(KINDS)}")
    for name in ("base_url", "model", "provider"):
        if not isinstance(values[name], str):
            raise SettingsError(f"{name} must be a string")
    if not values["base_url"].isascii():
        # Python's HTTP client cannot send a non-ASCII request line; refuse it here as a setting.
        raise SettingsError("base_url must contain only ASCII characters")
    if any(c in values["base_url"] for c in "@?#"):
        # R1-7: credentials belong in the key field, never in a URL that may be saved or shown.
        raise SettingsError("base_url must not contain a user name, password, query or fragment")
    Endpoint(values["kind"], values["base_url"] or None)  # validates scheme and openai_compat URL
    if values["kind"] != "openrouter":
        values["provider"] = ""
    return ModelSettings(**values)


def validate_key(value: object) -> str:
    """Validate a key typed by the user; returns it stripped. Messages never include the value."""
    if not isinstance(value, str):
        raise SettingsError("API key must be a string")
    key = value.strip()
    if not key:
        raise SettingsError("API key is blank")
    if any(not 0x21 <= ord(c) <= 0x7E for c in key):
        raise SettingsError("API key contains characters that cannot be sent in an HTTP header")
    return key


def build_messages(system: str, prompt: str) -> list[dict]:
    messages = [{"role": "system", "content": system}] if system != "" else []
    messages.append({"role": "user", "content": prompt})
    return messages


def _route_refusal(name: str, kind: str, reasoning: str) -> str:
    label = LABELS.get(name, name)
    if kind == "lmstudio" and name in modelspell.supported_settings("lmstudio", "off"):
        level = f"reasoning level {reasoning}" if reasoning else "the backend-default reasoning level"
        return f"{label} is not sent to LM Studio at {level}; it is sent with reasoning off"
    return f"{label} is not sent to {KIND_NAMES.get(kind, kind)}"


def provider_mapping(task: TaskSettings, kind: str, provider_pin: str) -> dict | None:
    """OpenRouter's provider object (Section 7.4); None for other kinds or when nothing is needed."""
    if kind != "openrouter":
        return None
    mapping: dict[str, Any] = {}
    if provider_pin:
        mapping.update({"order": [provider_pin], "allow_fallbacks": False})
    if task.set_names() - {"max_tokens"}:
        mapping["require_parameters"] = True
    return mapping or None


def task_arguments(task: TaskSettings, kind: str, provider_pin: str) -> dict:
    """The complete() keyword arguments for the set task settings, or TaskSettingError (Section 7.2).

    Checks, in order: reasoning on OpenAI-compatible servers; route support (AgentWiz's wording);
    the provider mapping; values through modelspell.check_settings. Sends nothing.
    """
    if kind == "openai_compat" and task.reasoning:
        raise TaskSettingError("reasoning is not available for OpenAI-compatible servers")
    supported = modelspell.supported_settings(kind, task.reasoning or None)
    for name in sorted(task.set_names() - {"reasoning"}, key=lambda n: list(LABELS).index(n)):
        if name not in supported:
            raise TaskSettingError(_route_refusal(name, kind, task.reasoning))
    arguments = task.values()
    provider = provider_mapping(task, kind, provider_pin)
    if provider is not None:
        arguments["provider"] = provider
    try:
        modelspell.check_settings(kind, **arguments)
    except (ConfigError, UnsupportedSetting) as exc:  # pre-send validation messages (plan 6.13, #3)
        raise TaskSettingError(str(exc)) from None
    return arguments


def lmstudio_model_info(endpoint: Endpoint, model_id: str) -> tuple[ModelInfo | None, str]:
    """The model's discovery entry for LM Studio with reasoning "off" (Section 7.3).

    Returns (entry, "used"), (None, "not_listed") or (None, "failed"). Never raises a ModelSpellError.
    """
    try:
        models = modelspell.list_models(endpoint, timeout=10.0)
    except ModelSpellError:
        return None, "failed"
    for info in models:
        if info.id == model_id:
            return info, "used"
    return None, "not_listed"


def call_model(snapshot: RequestSnapshot, messages: list[dict], arguments: Mapping[str, Any], *,
               model_info: ModelInfo | None = None,
               on_text: Callable[[str], None], on_reasoning: Callable[[str], None]) -> Result:
    """One modelSpell call. No retries; exceptions propagate unchanged."""
    model = model_info if model_info is not None else snapshot.model
    return modelspell.complete(snapshot.endpoint, model, messages,
                               on_text=on_text, on_reasoning=on_reasoning, **arguments)


def classify_outcome(result: Result) -> str:
    blank = result.text.strip() == ""
    reasoning = isinstance(result.reasoning, str) and result.reasoning.strip() != ""
    if result.truncated:
        return "truncated_no_answer" if blank else "truncated"
    if not blank:
        return "complete"
    return "reasoning_only" if reasoning else "empty"


def result_to_dict(result: Result) -> dict:
    return dataclasses.asdict(result)


def describe_error(exc: ModelSpellError, base_url: str) -> str:
    """The only text the page or terminal ever gets for a modelSpell error.

    Errors raised before anything is sent keep their message (it comes from modelSpell's argument
    validation). Every other error gets a fixed description from its class, its public `status`
    and the base URL, because its message can carry backend text that may reflect the key.
    """
    name = type(exc).__name__
    status = getattr(exc, "status", None)
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    if isinstance(exc, (ConfigError, UnsupportedSetting, UnsupportedOperation)) or \
            (isinstance(exc, AuthError) and status is None):
        return f"{name}: {exc}"
    status_part = f"HTTP {status}" if status is not None else "no HTTP status; the stream failed or ended early"
    if isinstance(exc, AuthError):
        return f"AuthError: the backend rejected the credentials (HTTP {status})."
    if isinstance(exc, ResponseFormatError):
        return f"ResponseFormatError: the backend's reply was not in the expected format ({status_part})."
    if isinstance(exc, BackendError) and status is not None and 300 <= status < 400:
        return (f"BackendError: the backend redirected (HTTP {status}); redirects are not followed, "
                "so set the base URL to the final address.")
    if isinstance(exc, BackendError) and status == 200:
        # modelSpell gives errors reported inside an already-accepted stream the status 200.
        return "BackendError: the backend reported an error during the stream."
    if isinstance(exc, BackendError):
        return f"BackendError: the backend reported a failure ({status_part})."
    if isinstance(exc, ConnectError):
        return f"ConnectError: could not connect to {base_url}."
    if isinstance(exc, RequestTimeout):
        return f"RequestTimeout: no data from {base_url} within the timeout."
    return f"{name}: the request failed."
