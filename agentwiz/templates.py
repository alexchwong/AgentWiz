"""Block templates: placeholder scanning (at load) and filling (at run time). Pure functions (0.1.4, 8.6).

A placeholder is exactly `{{inputs.<path>}}` with no spaces. There are no expressions, conditions, loops,
filters or schema placeholders, and nothing here ever renders a contract (R6-23). Other `{{...}}` text, such
as `{{name}}` in an example or JSON braces, is left as it is. A `{{` followed by optional spaces and `inputs`
or `steps` that is not exactly a placeholder is a load error, so a stray space never silently sends literal
text to a model (P7). `{{steps.<id>.<output>}}` is refused until 0.2.1.

Messages name locations and rules, never values.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from . import jsonio

PLACEHOLDER = re.compile(r"\{\{(inputs(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\}\}")
_STEPS_PLACEHOLDER = re.compile(r"\{\{steps(?:\.[A-Za-z_][A-Za-z0-9_]*)+\}\}")
_SUSPECT = re.compile(r"\{\{\s*(?:inputs|steps)(?![A-Za-z0-9_])")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

MALFORMED = "malformed placeholder; write {{inputs.<name>}} with no spaces"
STEPS_LATER = "steps placeholders arrive in 0.2.1"


@dataclass(frozen=True)
class Placeholder:
    text: str                 # "{{inputs.x.y}}"
    path: tuple[str, ...]     # ("x", "y")


class TemplateError(ValueError):
    """A load error in a template: `location` names where, the message the rule."""

    def __init__(self, location: str, message: str):
        super().__init__(f"{location}: {message}")
        self.location = location
        self.message = message


class FillError(ValueError):
    """A placeholder that cannot be filled. `location` is the step field (`prompt`, `system`,
    `arguments<path>`); the message names the placeholder, never a value."""

    def __init__(self, location: str, message: str):
        super().__init__(f"{location}: {message}")
        self.location = location
        self.message = message


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan_text(text: str, where: str) -> list[Placeholder]:
    """The placeholders of `text`, in order. Raises TemplateError (located at `where`) for a malformed
    placeholder or a `steps` placeholder."""
    found: list[Placeholder] = []
    for match in _SUSPECT.finditer(text):
        pos = match.start()
        exact = PLACEHOLDER.match(text, pos)
        if exact is not None:
            parts = tuple(exact.group(1).split(".")[1:])
            found.append(Placeholder(exact.group(0), parts))
            continue
        if _STEPS_PLACEHOLDER.match(text, pos) is not None:
            raise TemplateError(where, f"{STEPS_LATER} (line {_line_of(text, pos)})")
        raise TemplateError(where, f"{MALFORMED} (line {_line_of(text, pos)})")
    return found


def _segment(key: str) -> str:
    return f".{key}" if _IDENT.match(key) else "[" + jsonio.encode_exact(key) + "]"


def _walk(template: object, path: str):
    """Yield (path, string) for every string value of a JSON-like template; object keys are never templated."""
    if isinstance(template, str):
        yield path, template
    elif isinstance(template, Mapping):
        for key, value in template.items():
            yield from _walk(value, path + _segment(str(key)))
    elif isinstance(template, (list, tuple)):
        for index, value in enumerate(template):
            yield from _walk(value, f"{path}[{index}]")


def scan_arguments(template: object) -> list[tuple[str, Placeholder]]:
    """Every (where, placeholder) in the string values of a tool step's `arguments`, at any depth.
    `where` is `arguments` plus the JSON path. Raises TemplateError located at that path."""
    found: list[tuple[str, Placeholder]] = []
    for where, text in _walk(template, "arguments"):
        for placeholder in scan_text(text, where):
            found.append((where, placeholder))
    return found


def _resolve(placeholder: Placeholder, where: str, values: Mapping[str, Any]) -> Any:
    name, rest = placeholder.path[0], placeholder.path[1:]
    if name not in values:
        raise FillError(where, f"{placeholder.text}: the input has no value")
    value = values[name]
    for part in rest:
        if not isinstance(value, Mapping):
            raise FillError(where, f"{placeholder.text}: a path part is applied to a value that is not an object")
        if part not in value:
            raise FillError(where, f"{placeholder.text}: the path has a key that the value does not hold")
        value = value[part]
    return value


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fill(placeholder: Placeholder, where: str, inserted: str) -> dict:
    return {"where": where, "placeholder": placeholder.text, "source": "inputs." + ".".join(placeholder.path),
            "sha256": _digest(inserted)}


def fill_text(text: str, where: str, values: Mapping[str, Any]) -> tuple[str, list[dict]]:
    """Fill the placeholders of `text`. A string value is inserted as it is; any other value as its exact JSON
    text, `jsonio.encode_exact(value)` (compact). Returns (filled text, fills with provenance)."""
    fills: list[dict] = []
    out: list[str] = []
    last = 0
    for match in _SUSPECT.finditer(text):
        exact = PLACEHOLDER.match(text, match.start())
        if exact is None:
            continue        # load already refused it; a stray one is left as it is
        placeholder = Placeholder(exact.group(0), tuple(exact.group(1).split(".")[1:]))
        value = _resolve(placeholder, where, values)
        inserted = value if isinstance(value, str) else jsonio.encode_exact(value)
        out.append(text[last:exact.start()])
        out.append(inserted)
        last = exact.end()
        fills.append(_fill(placeholder, where, inserted))
    out.append(text[last:])
    return "".join(out), fills


def _fill_value(template: object, path: str, values: Mapping[str, Any], fills: list[dict]) -> object:
    if isinstance(template, str):
        whole = PLACEHOLDER.fullmatch(template)
        if whole is not None:
            placeholder = Placeholder(whole.group(0), tuple(whole.group(1).split(".")[1:]))
            value = _resolve(placeholder, path, values)
            fills.append(_fill(placeholder, path, jsonio.encode_exact(value)))
            return value
        filled, found = fill_text(template, path, values)
        fills.extend(found)
        return filled
    if isinstance(template, Mapping):
        return {key: _fill_value(value, path + _segment(str(key)), values, fills) for key, value in template.items()}
    if isinstance(template, (list, tuple)):
        return [_fill_value(value, f"{path}[{index}]", values, fills) for index, value in enumerate(template)]
    return template


def fill_arguments(template: object, values: Mapping[str, Any]) -> tuple[object, list[dict]]:
    """Fill a tool step's `arguments`. A string value that is exactly one placeholder becomes the value itself,
    with its own JSON type (a number stays a number, digit for digit; an object stays an object); a string with
    a placeholder and other text is filled as text. Object keys are never filled. Returns (arguments, fills);
    a typed fill's provenance hashes `encode_exact(value)`."""
    fills: list[dict] = []
    return _fill_value(template, "arguments", values, fills), fills
