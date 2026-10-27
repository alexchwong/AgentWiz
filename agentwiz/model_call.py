"""Settings, keys, the model call, outcome classification and error descriptions (techspec 7.4).

Uses only modelSpell's public API and knows nothing about HTTP, so later releases can reuse
`call_model`, `classify_outcome` and `describe_error` as the model-step executor.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

import modelspell
from modelspell import (AuthError, BackendError, ConfigError, ConnectError, Endpoint, ModelSpellError,
                        RequestTimeout, ResponseFormatError, Result, UnsupportedOperation,
                        UnsupportedSetting)

KINDS = ("lmstudio", "openrouter", "openai_compat")
REASONING_LEVELS = ("", "off", "low", "medium", "high")
OUTCOMES = ("complete", "empty", "reasoning_only", "truncated",
            "truncated_no_answer", "error", "stopped")

_FIELDS = ("kind", "base_url", "model", "provider", "reasoning", "max_tokens")


class SettingsError(ValueError):
    """An invalid setting or key. Messages name fields only, never values."""


@dataclass(frozen=True)
class ModelSettings:
    kind: str = "lmstudio"
    base_url: str = ""          # "" means the kind's default URL
    model: str = ""
    provider: str = ""          # OpenRouter provider slug; "" means any
    reasoning: str = ""         # "" means backend default
    max_tokens: int | None = None


@dataclass(frozen=True)
class RequestSnapshot:
    endpoint: Endpoint
    model: str
    kwargs: Mapping[str, Any]   # exactly what complete_kwargs() returned


def settings_from_body(current: ModelSettings, body: Mapping) -> ModelSettings:
    """Partial update: each field takes the body's value if present, else the current value."""
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
    if values["reasoning"] not in REASONING_LEVELS:
        raise SettingsError("reasoning must be one of: (blank), off, low, medium, high")
    mt = values["max_tokens"]
    if mt is not None and not (isinstance(mt, int) and not isinstance(mt, bool) and mt >= 1):
        raise SettingsError("max_tokens must be null or an integer of at least 1")
    Endpoint(values["kind"], values["base_url"] or None)  # validates scheme and openai_compat URL
    if values["kind"] == "openai_compat":
        values["reasoning"] = ""
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


def complete_kwargs(settings: ModelSettings, kind: str) -> dict:
    """Only the complete() arguments that are set."""
    kwargs: dict[str, Any] = {}
    if settings.reasoning != "" and kind != "openai_compat":
        kwargs["reasoning"] = settings.reasoning
    if settings.max_tokens is not None:
        kwargs["max_tokens"] = settings.max_tokens
    if kind == "openrouter" and settings.provider != "":
        kwargs["provider"] = {"order": [settings.provider], "allow_fallbacks": False}
    return kwargs


def call_model(snapshot: RequestSnapshot, messages: list[dict], *,
               on_text: Callable[[str], None], on_reasoning: Callable[[str], None]) -> Result:
    """One modelSpell call. No retries; exceptions propagate unchanged."""
    return modelspell.complete(snapshot.endpoint, snapshot.model, messages,
                               on_text=on_text, on_reasoning=on_reasoning, **snapshot.kwargs)


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
