"""Contracts and checks: contract parsing, answer parsing, every check, results and the repair message
(techspec 0.1.3, Section 7; R4-1 to R4-4, R4-11 to R4-13, R4-31 to R4-33, R4-38).

A contract says what an answer should look like: text or JSON, with content checks and, for JSON, an
optional JSON Schema. The checks compare an answer with it deterministically and report every problem
with its location, in words a user and a model can both act on.

Pure functions. This module knows nothing of HTTP or files (P5); 0.1.4's step executor reuses it for
inputs and outputs. Numbers are exact: integers are `int`, numbers with a fraction or an exponent are
`decimal.Decimal`, and no float is ever used for a checked value. Standard library only, except that the
schema path imports the required `jsonschema` dependency lazily.
"""
from __future__ import annotations

import decimal
import importlib
import importlib.metadata
import json
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NamedTuple

from . import contract_schema, jsonio
from .jsonio import INT_DIGITS_LIMIT, ExactLimit, JSONSyntax, encode_exact

ISSUES_SHOWN = 20
DIALECT = "https://json-schema.org/draft/2020-12/schema"
FORMAT_VERSION = 1
SCHEMA_RANGE = ">=4.26,<5"
CLIP = 200                      # a value written into a message is cut at this many characters
CONTRACT_ISSUES_LIMIT = 100     # a ContractError lists at most this many issues
EXTRA_HINT = "repair the AgentWiz installation (jsonschema>=4.26,<5 is required)"

FORMATS = ("text", "json")
_TOP_LEVEL = ('format', 'description', 'schema', *contract_schema.TEXT_RULES)


class ContractError(ValueError):
    """A contract cannot be applied. `issues` is a list of {"location", "message"}; messages name rules,
    never values. `total` counts every issue found, `issues` holds the first CONTRACT_ISSUES_LIMIT."""

    def __init__(self, issues: list[dict], total: int | None = None):
        self.issues = list(issues[:CONTRACT_ISSUES_LIMIT])
        self.total = len(issues) if total is None else total
        first = self.issues[0] if self.issues else {"location": "contract", "message": "is invalid"}
        more = f" (and {self.total - 1} more)" if self.total > 1 else ""
        super().__init__(f"{first['location']}: {first['message']}{more}")


class ValidationUnavailable(Exception):
    """Schema work cannot be dispatched with a damaged validator installation."""


def require_validation(contracts) -> None:
    """Refuse schema-dependent work before reserving a run or dispatching a step."""
    if any(contract.schema is not None for contract in contracts) and not schema_support()[0]:
        raise ValidationUnavailable(EXTRA_HINT)


@dataclass(frozen=True)
class Contract:
    format: str                  # "text" | "json"
    strict: bool                 # retained capture layout; user/internal policies always use False
    description: str
    schema: object               # exact-parsed JSON value (object or boolean), or None
    checks: tuple                # retained capture layout; obsolete definition checks are never executed
    raw: Mapping                 # recognised exact definition
    policy: str = 'user'          # server-owned capture policy, never parsed from user definitions


# --- required validator availability -----------------------------------------------------------
def _jsonschema_version() -> str | None:
    try:
        return importlib.metadata.version("jsonschema")
    except importlib.metadata.PackageNotFoundError:
        return None


def schema_support() -> tuple[bool, str]:
    """(available, "jsonschema 4.26.0") or (False, the reason). An installed version outside the
    supported range counts as missing, so a major version is adopted only by changing the range."""
    version = _jsonschema_version()
    if version is None:
        return False, "jsonschema is not installed"
    found = re.match(r"(\d+)\.(\d+)", version)
    if not found or not (4, 26) <= (int(found[1]), int(found[2])) < (5, 0):
        return False, f"jsonschema {version} is outside the supported range {SCHEMA_RANGE}"
    try:
        importlib.import_module("jsonschema")
        importlib.import_module("referencing")
    except Exception:  # a broken dependency must not enable schema dispatch
        return False, "jsonschema cannot be imported"
    return True, f"jsonschema {version}"


def _libs():
    return importlib.import_module("jsonschema"), importlib.import_module("referencing")


# --- exact numbers (7.3) -----------------------------------------------------------------------
def exact_context() -> decimal.Context:
    """The context every comparison, equality test and schema validation runs in (R4-32): 2,100 digits,
    the full exponent range (so a tiny or huge value is neither flushed to zero nor to infinity), and
    traps on every arithmetic condition including Inexact (so a result that would need more digits
    raises instead of being rounded silently). Rounded and Clamped are not trapped: they can fire
    without the value changing."""
    return decimal.Context(prec=2100, rounding=decimal.ROUND_HALF_EVEN, Emin=decimal.MIN_EMIN,
                           Emax=decimal.MAX_EMAX,
                           traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow,
                                  decimal.Underflow, decimal.Subnormal, decimal.Inexact])


def _is_whole(number: Decimal) -> bool:
    _sign, digits, exponent = number.as_tuple()
    if exponent >= 0:
        return True
    return not any(digits[-(-exponent):])


def _integer_digits(number: Decimal) -> int:
    """Digits of the integer form of a whole decimal."""
    return 1 if number == 0 else number.adjusted() + 1


class _TooBig(Exception):
    """A whole-number decimal whose integer form would exceed 4,300 digits; `path` locates it."""

    def __init__(self, path: tuple):
        super().__init__("a whole number has more than 4,300 digits")
        self.path = path


def _float_to_decimal(value: float) -> Decimal:
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("a non-finite number cannot be checked")
    return Decimal(repr(value))


def _whole_to_int(number: Decimal, path: tuple) -> Decimal | int:
    if _is_whole(number):
        if _integer_digits(number) > INT_DIGITS_LIMIT:
            raise _TooBig(path)
        return int(number)
    return number


def _validation_copy(value: Any) -> Any:
    """A copy in which every whole-number decimal is an int, so 1 and 1.0 are alike everywhere (7.4).
    Fractional decimals stay exact. The copy exists only for validation. Raises _TooBig."""
    if isinstance(value, float):
        value = _float_to_decimal(value)
    if isinstance(value, Decimal):
        return _whole_to_int(value, ())
    if not isinstance(value, (dict, list)):
        return value
    root: Any = {} if isinstance(value, dict) else []
    stack = [(value, root, ())]
    while stack:
        source, target, path = stack.pop()
        items = source.items() if isinstance(source, dict) else enumerate(source)
        for key, item in items:
            where = path + (key,)
            if isinstance(item, float):
                item = _float_to_decimal(item)
            if isinstance(item, Decimal):
                out: Any = _whole_to_int(item, where)
            elif isinstance(item, dict):
                out = {}
                stack.append((item, out, where))
            elif isinstance(item, list):
                out = []
                stack.append((item, out, where))
            else:
                out = item
            if isinstance(target, dict):
                target[key] = out
            else:
                target.append(out)
    return root


# --- locations and messages ----------------------------------------------------------------------
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_LINE_LOCATION = re.compile(r"line (\d+), column (\d+)\Z")


def _format_path(root: str, parts) -> str:
    out = [root]
    for part in parts:
        if isinstance(part, int):
            out.append(f"[{part}]")
        elif _IDENT.match(part):
            out.append("." + part)
        else:
            out.append("[" + json.dumps(part, ensure_ascii=True) + "]")
    return "".join(out)


def _path_key(parts) -> tuple:
    """Sorts `$` paths part by part: array indexes as numbers, names as strings."""
    return (2, tuple((0, p, "") if isinstance(p, int) else (1, 0, p) for p in parts))


def _clip(text: str) -> str:
    return text if len(text) <= CLIP else text[:CLIP] + f"... (cut at {CLIP} characters)"


def _show(value: Any) -> str:
    """A value as it appears in a message: exact JSON text, cut at 200 characters."""
    try:
        return _clip(encode_exact(value))
    except (TypeError, ValueError):
        return "(a value that cannot be written)"


def _bare(value: Any) -> str:
    return value if isinstance(value, str) else _show(value)


class _Issue(NamedTuple):
    key: tuple          # sort key, derived from the location
    location: str
    message: str
    check: str


def _answer_issue(check: str, message: str) -> _Issue:
    return _Issue((0,), "answer", message, check)


def _path_issue(check: str, parts, message: str) -> _Issue:
    return _Issue(_path_key(parts), _format_path("$", parts), message, check)


def _line_issue(check: str, line: int, column: int, message: str) -> _Issue:
    return _Issue((1, line, column), f"line {line}, column {column}", message, check)


def parse_contract(obj: object) -> Contract:
    """Recognise a user definition before it can reach the value validator."""
    try:
        contract_schema.validate_user_definition(obj)
    except contract_schema.DefinitionError as exc:
        raise ContractError(exc.issues, exc.total) from None
    return Contract(obj['format'], False, obj.get('description', ''), obj.get('schema'), (), obj)


def contract_from_text(text: str) -> Contract:
    """decode_exact, then parse_contract. Reader failures become located contract errors."""
    if not isinstance(text, str):
        raise ContractError([{'location': 'contract', 'message': 'source must be text'}])
    try:
        size = len(text.encode('utf-8'))
    except UnicodeEncodeError:
        raise ContractError([{'location': 'contract', 'message': 'source must be valid Unicode'}]) from None
    if size > contract_schema.DEFINITION_BYTES:
        raise ContractError([{'location': 'contract', 'message': 'source exceeds 1 MiB', 'code': 'too_large'}])
    try:
        obj = jsonio.decode_exact(text)
    except JSONSyntax as exc:
        raise ContractError([{"location": f"line {exc.line}, column {exc.column}",
                              "message": "the contract is not valid JSON"}]) from None
    except ExactLimit as exc:
        where = f"line {exc.line}, column {exc.column}" if exc.line is not None else "contract"
        raise ContractError([{"location": where, "message": str(exc)}]) from None
    except ValueError as exc:
        raise ContractError([{"location": "contract", "message": str(exc)}]) from None
    return parse_contract(obj)


def parse_internal_contract(obj: object) -> Contract:
    """Construct a server-owned rich-schema capture, never a user policy bypass.

    Callers must generate this definition themselves. No policy selector belongs
    in a contract document, request body or block declaration.
    """
    if not isinstance(obj, dict):
        raise ContractError([{'location': 'contract', 'message': 'must be an object'}])
    issues = [{'location': key, 'message': 'is not an internal contract member'}
              for key in obj if key not in ('format', 'description', 'schema')]
    fmt = obj.get('format')
    if fmt not in FORMATS:
        issues.append({'location': 'format', 'message': 'must be text or json'})
    if 'description' in obj and not isinstance(obj['description'], str):
        issues.append({'location': 'description', 'message': 'must be a string'})
    schema = obj.get('schema')
    if 'schema' in obj:
        if fmt != 'json' or not isinstance(schema, (dict, bool)):
            issues.append({'location': 'schema', 'message': 'must be an internal JSON schema'})
        elif not schema_support()[0]:
            issues.append({'location': 'schema', 'message': 'validation_unavailable: repair the installation'})
        elif isinstance(schema, dict):
            issues.extend(schema_issues(schema))
    if issues:
        raise ContractError(issues)
    return Contract(fmt, False, obj.get('description', ''), schema, (), obj, policy='internal')


# --- the schema rules of 7.4: dialect, local references, validity -------------------------------------
_SCHEMA_ONE = ("items", "contains", "additionalProperties", "propertyNames", "if", "then", "else", "not",
               "unevaluatedItems", "unevaluatedProperties", "contentSchema")
_SCHEMA_LIST = ("prefixItems", "allOf", "anyOf", "oneOf")
_SCHEMA_MAP = ("properties", "patternProperties", "dependentSchemas", "$defs")
_MISSING = object()


def _pointer(resource: Any, fragment: str) -> Any:
    node = resource
    for token in urllib.parse.unquote(fragment).split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(node):
            node = node[int(token)]
        else:
            return _MISSING
    return node


def _anchors(resource: Any) -> dict:
    """$anchor and $dynamicAnchor names inside a resource's schema positions (not crossing into a
    subresource that has its own $id)."""
    found: dict = {}
    pending = [resource]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, dict) or id(node) in seen:
            continue
        seen.add(id(node))
        for keyword in ("$anchor", "$dynamicAnchor"):
            if isinstance(node.get(keyword), str):
                found.setdefault(node[keyword], node)
        for keyword in _SCHEMA_ONE:
            if keyword in node:
                pending.append(node[keyword])
        for keyword in _SCHEMA_LIST:
            if isinstance(node.get(keyword), list):
                pending.extend(node[keyword])
        for keyword in _SCHEMA_MAP:
            if isinstance(node.get(keyword), dict):
                pending.extend(v for v in node[keyword].values() if not (isinstance(v, dict) and "$id" in v))
    return found


def _resolve_local(resource: Any, fragment: str, anchors: dict) -> Any:
    if fragment == "":
        return resource
    if fragment.startswith("/"):
        return _pointer(resource, fragment)
    if fragment not in anchors:
        anchors[fragment] = _anchors(resource).get(fragment, _MISSING)
    return anchors[fragment]


def _walk_schema(root: Any) -> list[dict]:
    """Dialect and reference policy (7.4; R4-33, R4-38). Visits schema positions only, so a `$schema`
    key inside literal data (const, enum, default, examples) is data and is not judged. Records what it
    has visited, so a recursive schema terminates. A local reference is followed when it resolves, and
    its target is treated as a schema even where it sits in a position that would otherwise hold data;
    one that does not resolve is not refused here: it fails when validation follows it."""
    issues: list[dict] = []
    visited: set[tuple] = set()
    anchor_caches: dict[int, dict] = {}
    pending: list[tuple] = [(root, root, ())]
    while pending:
        node, resource, path = pending.pop()
        if not isinstance(node, dict) or (id(node), id(resource)) in visited:
            continue
        visited.add((id(node), id(resource)))
        if "$schema" in node and node["$schema"] not in (DIALECT, DIALECT + "#"):
            issues.append({"location": _format_path("schema", path + ("$schema",)),
                           "message": "declares a JSON Schema dialect other than Draft 2020-12"})
        if node is not root and isinstance(node.get("$id"), str):
            resource = node
        for keyword in ("$ref", "$dynamicRef"):
            if keyword not in node:
                continue
            reference = node[keyword]
            where = path + (keyword,)
            if not isinstance(reference, str) or not reference.startswith("#"):
                issues.append({"location": _format_path("schema", where),
                               "message": "must be a string starting with '#': only local references are supported"})
                continue
            target = _resolve_local(resource, reference[1:], anchor_caches.setdefault(id(resource), {}))
            if target is not _MISSING:
                pending.append((target, resource, where))
        for keyword in _SCHEMA_ONE:
            if keyword in node:
                pending.append((node[keyword], resource, path + (keyword,)))
        for keyword in _SCHEMA_LIST:
            if isinstance(node.get(keyword), list):
                pending.extend((v, resource, path + (keyword, i)) for i, v in enumerate(node[keyword]))
        for keyword in _SCHEMA_MAP:
            if isinstance(node.get(keyword), dict):
                pending.extend((v, resource, path + (keyword, k)) for k, v in node[keyword].items())
    return issues


def schema_issues(schema: Any) -> list[dict]:
    """The internal schema rules, for a schema that is not a plain boolean. The caller
    checks schema_support() first. Messages name locations and rules, never values."""
    jsonschema, _referencing = _libs()
    try:
        copy = _validation_copy(schema)
    except _TooBig as exc:
        return [{"location": _format_path("schema", exc.path),
                 "message": f"a whole number has more than {INT_DIGITS_LIMIT:,} digits"}]
    with decimal.localcontext(exact_context()):
        try:
            jsonschema.Draft202012Validator.check_schema(copy)
        except jsonschema.SchemaError as exc:
            return [{"location": _format_path("schema", exc.absolute_path),
                     "message": f"is not a valid Draft 2020-12 schema (the metaschema's {exc.validator!s} rule fails here)"}]
        except Exception:  # noqa: BLE001 - the schema could not even be checked
            return [{"location": "schema", "message": "could not be checked as a Draft 2020-12 schema"}]
    try:
        return _walk_schema(schema)
    except RecursionError:
        return [{"location": "schema", "message": "is nested too deeply"}]


# --- reading an answer (7.2) ------------------------------------------------------------------------
_OPEN_FENCE = re.compile(r"( {0,3})(`{3,}|~{3,})(.*)\Z")
_UNPARSED = object()
NO_VALUE = _UNPARSED     # check_answer_value: "no value was produced" (a parsed JSON null is a value)


def fenced_blocks(text: str) -> list[dict]:
    """The fenced code blocks of `text` as CommonMark defines them: an opening line of three or more
    backticks or tildes, indented by at most three spaces and optionally followed by an info string,
    closed by a line of at least as many of the same character; an unclosed block runs to the end of
    the answer. Each block is {"open_line": 1-based, "content": text}."""
    lines = re.split(r"\r\n|\r|\n", text)
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        opening = _OPEN_FENCE.match(lines[i])
        if not opening or (opening[2][0] == "`" and "`" in opening[3]):
            i += 1
            continue
        indent, fence = len(opening[1]), opening[2]
        closing = re.compile(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*\Z")
        content: list[str] = []
        j = i + 1
        closed = False
        while j < len(lines):
            if closing.match(lines[j]):
                closed = True
                break
            line = lines[j]
            content.append(line[min(indent, len(line) - len(line.lstrip(" "))):])
            j += 1
        blocks.append({"open_line": i + 1, "content": "\n".join(content)})
        i = j + 1 if closed else j
    return blocks


def _parse_text(text: str, line_offset: int, entry_check: str) -> tuple[str, Any, list[_Issue]]:
    """Parse `text` trimmed. Returns (result, value, issues): result is pass, fail or not_run."""
    stripped = text.lstrip()
    lead = text[:len(text) - len(stripped)]
    try:
        return "pass", jsonio.decode_exact(stripped.rstrip()), []
    except JSONSyntax as exc:
        line, column = exc.line + line_offset + lead.count("\n"), exc.column
        if exc.line == 1:
            column += len(lead) - (lead.rfind("\n") + 1)
        return "fail", _UNPARSED, [_line_issue(entry_check, line, column, "the answer is not valid JSON")]
    except ExactLimit as exc:
        message = f"{exc}, so the answer cannot be checked exactly"
        if exc.line is None:
            return "not_run", _UNPARSED, [_answer_issue(entry_check, message)]
        line, column = exc.line + line_offset + lead.count("\n"), exc.column
        if exc.line == 1:
            column += len(lead) - (lead.rfind("\n") + 1)
        return "not_run", _UNPARSED, [_line_issue(entry_check, line, column, message)]
    except ValueError as exc:
        return "fail", _UNPARSED, [_answer_issue(entry_check, f"the answer cannot be read as JSON: {exc}")]


def _parse_answer(contract: Contract, answer: str) -> tuple[str, Any, list[_Issue], list[str]]:
    """The `parse` check: (result, value, issues, notes)."""
    notes: list[str] = []
    blocks = fenced_blocks(answer)
    if len(blocks) > 1:
        return "fail", _UNPARSED, [_line_issue("parse", blocks[1]["open_line"], 1, "multiple code blocks")], notes
    if len(blocks) == 1:
        notes.append("parsed the single fenced code block")
        result, value, issues = _parse_text(blocks[0]["content"], blocks[0]["open_line"], "parse")
        return result, value, issues, notes
    result, value, issues = _parse_text(answer, 0, "parse")
    return result, value, issues, notes


# --- the checks (7.5) --------------------------------------------------------------------------------
def _json_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float, Decimal)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    return "object"


# --- applying the schema ------------------------------------------------------------------------------
def exact_equal(a: Any, b: Any) -> bool:
    """Compare JSON values without conflating booleans and exact numbers.

    Objects ignore key order; arrays do not. No serialization, normalization or
    float conversion is involved in Workshop example expectations.
    """
    pending = [(a, b)]
    while pending:
        left, right = pending.pop()
        kind = _json_kind(left)
        if kind != _json_kind(right):
            return False
        if kind == 'object':
            if left.keys() != right.keys():
                return False
            pending.extend((value, right[key]) for key, value in left.items())
        elif kind == 'array':
            if len(left) != len(right):
                return False
            pending.extend(zip(left, right))
        elif left != right:
            return False
    return True


def _navigate(root: Any, parts) -> Any:
    node = root
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node):
            node = node[part]
        else:
            return _MISSING
    return node


def _original_keyword_value(schema: Any, error: Any) -> Any:
    """The keyword's value as the contract wrote it (the validation copy turns 1.0 into 1), when the
    schema path can be followed from the root without crossing a reference; otherwise the copy's."""
    parts = list(error.absolute_schema_path)
    if error.validator is not None and parts and "$ref" not in parts and "$dynamicRef" not in parts:
        found = _navigate(schema, parts)
        if found is not _MISSING:
            return found
    return error.validator_value


def _schema_message(error: Any, instance: Any, keyword_value: Any, extra_text: str | None = None) -> str:
    keyword = error.validator
    inst, want = _show(instance), _show(keyword_value)
    if keyword is None:
        return f"schema: the value {inst} is not allowed here (the schema is false)"
    if keyword == "type":
        wanted = " or ".join(keyword_value) if isinstance(keyword_value, list) else _bare(keyword_value)
        return f"type: the value must be of type {wanted}; it is {inst}"
    simple = {"maximum": "at most", "minimum": "at least", "exclusiveMaximum": "less than",
              "exclusiveMinimum": "greater than", "multipleOf": "a multiple of", "const": "equal to",
              "maxLength": "at most {} characters long", "minLength": "at least {} characters long",
              "maxItems": "at most {} items", "minItems": "at least {} items",
              "maxProperties": "at most {} properties", "minProperties": "at least {} properties"}
    if keyword in simple:
        phrase = simple[keyword]
        phrase = phrase.format(want) if "{}" in phrase else f"{phrase} {want}"
        return f"{keyword}: the value must be {phrase}; it is {inst}"
    if keyword == "enum":
        return f"enum: the value must be one of {want}; it is {inst}"
    if keyword == "pattern":
        return f"pattern: the string must match the pattern {want}; it is {inst}"
    if keyword == "uniqueItems":
        return f"uniqueItems: the array must not repeat an item; it is {inst}"
    if keyword in ("anyOf", "oneOf"):
        count = len(keyword_value) if isinstance(keyword_value, list) else "the"
        need = "at least one of" if keyword == "anyOf" else "exactly one of"
        return f"{keyword}: the value must match {need} {count} alternatives; it is {inst}"
    return f"{keyword}: the value {inst} does not satisfy {keyword} {want}"


def _schema_errors(contract: Contract, value: Any) -> tuple[str, list[_Issue]]:
    """Validate `value` against the contract's schema. Returns (result, issues); result is pass, fail,
    not_run, or contract_error. Handlers, in order (7.4): a DecimalException depends on the answer, so
    it makes `schema` not_run; any other exception is the contract's, so the result is contract_error."""
    available, detail = schema_support()
    if not available:
        return "not_run", [_answer_issue("schema", f"validation_unavailable: {EXTRA_HINT} ({detail})")]
    try:
        jsonschema, referencing = _libs()
        schema_copy = _validation_copy(contract.schema)
        value_copy = _validation_copy(value)
    except _TooBig as exc:
        return "not_run", [_answer_issue("schema", "a whole number has more than "
                                                   f"{INT_DIGITS_LIMIT:,} digits, so the schema cannot be applied exactly")]
    except Exception:
        return 'contract_error', [_answer_issue('schema', 'the schema validator failed internally')]
    issues: list[_Issue] = []
    with decimal.localcontext(exact_context()):
        try:
            validator = jsonschema.Draft202012Validator(schema_copy, registry=referencing.Registry())
            errors = list(validator.iter_errors(value_copy))
        except decimal.DecimalException:
            return "not_run", [_path_issue("schema", (), "the schema cannot be applied exactly to this value "
                                                         "(a number needs more than 2,100 digits or an exponent beyond the limits)")]
        except RecursionError:
            return "not_run", [_path_issue("schema", (), "the value is nested too deeply for the schema check")]
        except Exception as exc:  # noqa: BLE001 - a reference that cannot be resolved, a validator failure
            return "contract_error", [_answer_issue("schema", f"the schema could not be applied ({type(exc).__name__})")]
        try:
            seen: dict[tuple, int] = {}
            for error in errors:
                parts = list(error.absolute_path)
                original = _navigate(value, parts)
                instance = error.instance if original is _MISSING else original
                key = (tuple(parts), tuple(error.absolute_schema_path))
                index = seen[key] = seen.get(key, -1) + 1
                keyword_value = _original_keyword_value(contract.schema, error)
                if error.validator == "required" and isinstance(error.validator_value, list):
                    missing = [name for name in error.validator_value
                               if isinstance(error.instance, dict) and name not in error.instance]
                    name = missing[index] if index < len(missing) else None
                    text = ("required: the property " + _show(name) + " is missing") if name is not None \
                        else f"required: a required property is missing from {_show(instance)}"
                    issues.append(_path_issue("schema", parts, text))
                elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
                    schema_here = error.schema if isinstance(error.schema, dict) else {}
                    allowed = schema_here.get("properties", {})
                    patterns = schema_here.get("patternProperties", {})
                    extras = [k for k in error.instance
                              if k not in allowed and not any(re.search(p, k) for p in patterns)]
                    for extra in extras or [None]:
                        where = parts + [extra] if extra is not None else parts
                        issues.append(_path_issue("schema", where, "additionalProperties: this property is not allowed"
                                                  if extra is not None else
                                                  _schema_message(error, instance, keyword_value)))
                else:
                    issues.append(_path_issue("schema", parts, _schema_message(error, instance, keyword_value)))
        except decimal.DecimalException:
            return "not_run", [_path_issue("schema", (), "the schema cannot be applied exactly to this value")]
        except RecursionError:
            return "not_run", [_path_issue("schema", (), "the value is nested too deeply for the schema check")]
        except Exception:
            return 'contract_error', [_answer_issue('schema', 'the schema validator failed internally')]
    if contract.policy == 'user':
        issues = [issue._replace(message='value violates schema.' + issue.message.split(':', 1)[0])
                  for issue in issues]
    return ("fail" if issues else "pass"), issues


# --- results (7.6) ------------------------------------------------------------------------------------
def _assemble(entries: list[dict], issues: list[_Issue], notes: list[str], contract_error: bool) -> dict:
    ordered = sorted(issues, key=lambda issue: issue.key)
    if contract_error:
        overall = "contract_error"
    elif any(e["result"] == "fail" for e in entries):
        overall = "fail"
    elif any(e["result"] == "not_run" for e in entries):
        overall = "incomplete"
    else:
        overall = "pass"
    return {"overall": overall, "checks": entries,
            "issues": [{"location": i.location, "message": i.message, "check": i.check}
                       for i in ordered[:ISSUES_SHOWN]],
            "issue_count": len(ordered), "notes": notes}


def _entry(name: str, result: str, count: int, kind: str | None = None) -> dict:
    entry: dict = {"check": name}
    if kind is not None:
        entry["type"] = kind
    entry["result"] = result
    if count:
        entry["issues"] = count
    return entry


def _run(contract: Contract, text: str, parsed: Any, parse_result: str | None,
         parse_issues: list[_Issue], notes: list[str]) -> dict:
    """Run every check. `parse_result` is None for a text contract; otherwise the parse check's result,
    with `parsed` its value (_UNPARSED unless it passed)."""
    entries: list[dict] = []
    issues: list[_Issue] = list(parse_issues)
    contract_error = False
    have_value = parse_result == "pass"
    if parse_result is not None:
        entries.append(_entry("parse", parse_result, len(parse_issues)))
        if contract.schema is not None:
            if not have_value:
                entries.append(_entry("schema", "not_run", 0))
            else:
                result, found = _schema_errors(contract, parsed)
                contract_error = contract_error or result == "contract_error"
                issues.extend(found)
                entries.append(_entry("schema", "not_run" if result == "contract_error" else result, len(found)))
    if contract.format == 'text':
        rules = []
        for key in ('min_length', 'max_length', 'exact'):
            if key in contract.raw:
                passes = (len(text) >= contract.raw[key] if key == 'min_length' else
                          len(text) <= contract.raw[key] if key == 'max_length' else text == contract.raw[key])
                rules.append(('text.' + key, passes))
        for key in ('contains', 'not_contains'):
            for index, literal in enumerate(contract.raw.get(key, [])):
                rules.append((f'text.{key}[{index}]', (literal in text) == (key == 'contains')))
        for name, passes in rules:
            found = [] if passes else [_answer_issue(name, 'value violates ' + name)]
            issues.extend(found)
            entries.append(_entry(name, 'pass' if passes else 'fail', len(found)))
    return _assemble(entries, issues, notes, contract_error)


def check_unparsed_input(contract: Contract, text: str, exc: BaseException) -> dict:
    """The check result of a JSON input whose text did not parse (0.1.4, 9.1): the same shape as an answer's, with
    the `parse` check failed (`not_run` for a number that cannot be kept exactly) and every other check `not_run`,
    because they need the value. `exc` is the decoder's JSONSyntax or ExactLimit."""
    if isinstance(exc, JSONSyntax):
        result, issue = "fail", _line_issue("parse", exc.line, exc.column, "the input is not valid JSON")
    else:
        message = f"{exc}, so the input cannot be checked exactly"
        line = getattr(exc, "line", None)
        issue = (_answer_issue("parse", message) if line is None
                 else _line_issue("parse", line, exc.column, message))
        result = "not_run"
    return _run(contract, text, _UNPARSED, result, [issue], [])


def check_answer_value(contract: Contract, answer: str) -> tuple[dict, Any]:
    """Like check_answer, but also returns the value the answer provides (0.1.4, 9.3): the answer text for a
    text contract; for a JSON contract whose `parse` passed, the parsed value (the single fenced block, or the
    whole trimmed answer); otherwise NO_VALUE. The value is only a candidate: whether it may be used is the
    caller's decision (a block accepts it only from an attempt that ended complete with check pass)."""
    if not isinstance(answer, str):
        raise TypeError("an answer is text")
    if contract.format == "text":
        return _run(contract, answer, _UNPARSED, None, [], []), answer
    result, value, issues, notes = _parse_answer(contract, answer)
    checked = _run(contract, answer, value, result, issues, notes)
    return checked, (value if result == "pass" else NO_VALUE)


def check_answer(contract: Contract, answer: str) -> dict:
    """Check a model's answer text against a contract (7.2, then 7.5)."""
    return check_answer_value(contract, answer)[0]


def check_value(contract: Contract, value: Any) -> dict:
    """Check any value: text (for a text contract), or an already-parsed JSON value (for a JSON
    contract), such as tool outputs. JSON's `parse` check passes with a note."""
    if contract.format == "text":
        if not isinstance(value, str):
            issue = _answer_issue('text.type', 'value must be a string')
            return _assemble([_entry('text.type', 'fail', 1)], [issue], [], False)
        return _run(contract, value, _UNPARSED, None, [], [])
    return _run(contract, encode_exact(value), value, "pass", [], ["value given already parsed"])


# --- the named envelope (0.2.0) ---------------------------------------------------------------------------
ENVELOPE_REPLY = ("as one JSON object whose top-level keys are exactly the declared outputs, "
                  "with each output's complete value under its own key")
_ENVELOPE: Contract | None = None


def envelope_contract() -> Contract:
    """The contract that decides only how an envelope answer is read: one JSON value, non-strict, so a
    single fenced block is accepted as it is for any other JSON contract. It has no schema and no checks:
    each output is checked against its own contract."""
    global _ENVELOPE
    if _ENVELOPE is None:
        _ENVELOPE = parse_contract({"format": "json"})
    return _ENVELOPE


def _envelope_issue(name: str, message: str, check: str = "envelope") -> _Issue:
    return _Issue((2, name), f"outputs.{name}", message, check)


def _moved_issues(name: str, checked: dict) -> list[_Issue]:
    """An output's own issues, located under `outputs.<name>` so a repair names the failing output."""
    moved: list[_Issue] = []
    for issue in checked.get("issues", []):
        location = issue.get("location", "")
        check = issue.get("check") or "checks"
        message = issue.get("message", "")
        if location in ("", "answer"):
            moved.append(_envelope_issue(name, message, check))
        elif location.startswith("$"):
            moved.append(_Issue((2, name), f"outputs.{name}{location[1:]}", message, check))
        else:
            moved.append(_Issue((2, name), f"outputs.{name}: {location}", message, check))
    return moved


def check_named_answer(contracts: Mapping[str, Contract], answer: str) -> tuple[dict, Any]:
    """Check one named-envelope answer (0.2.0): (the aggregate check, {name: value} or NO_VALUE).

    The answer is parsed once, with the parser policy every JSON answer uses. Its top-level keys must be
    exactly the declared output names, in any order, with nothing missing and nothing added. Each value is
    then checked against that output's own contract without being re-serialised, and a text output's value
    must be a JSON string. Values come back only when the aggregate passes, so no partial answer is ever
    published.
    """
    if not isinstance(answer, str):
        raise TypeError("an answer is text")
    names = list(contracts)
    result, value, parse_issues, notes = _parse_answer(envelope_contract(), answer)
    entries: list[dict] = [_entry("parse", result, len(parse_issues))]
    issues: list[_Issue] = list(parse_issues)
    if result != "pass":
        aggregate = _assemble(entries, issues, notes, result == "contract_error")
        aggregate["outputs"] = {}
        return aggregate, NO_VALUE
    if not isinstance(value, Mapping):
        issues.append(_answer_issue("envelope", "the answer is not a JSON object holding the named outputs"))
        entries.append(_entry("envelope", "fail", 1))
        aggregate = _assemble(entries, issues, notes, False)
        aggregate["outputs"] = {}
        return aggregate, NO_VALUE

    def blank(name: str, message: str) -> dict:
        return {"overall": "fail", "checks": [], "issue_count": 1, "notes": [],
                "issues": [{"location": f"outputs.{name}", "message": message, "check": "envelope"}]}

    declared, found = set(names), set(value)
    extra = found - declared
    if extra:
        issues.append(_answer_issue("envelope", "the answer has a top-level key that is not a declared output"))
        entries.append(_entry("envelope", "fail", len(extra)))
    values: dict[str, Any] = {}
    per_output: dict[str, dict] = {}
    contract_error = False
    for name in names:
        if name not in found:
            issues.append(_envelope_issue(name, "the answer has no value for this output"))
            entries.append(_entry(f"outputs.{name}", "fail", 1))
            per_output[name] = blank(name, "the answer has no value for this output")
            continue
        item, contract = value[name], contracts[name]
        if contract.format == "text" and not isinstance(item, str):
            message = "the value must be a JSON string holding this output's text"
            issues.append(_envelope_issue(name, message))
            entries.append(_entry(f"outputs.{name}", "fail", 1))
            per_output[name] = blank(name, message)
            continue
        checked = check_value(contract, item)
        issues.extend(_moved_issues(name, checked))
        state = checked["overall"]
        contract_error = contract_error or state == "contract_error"
        entries.append(_entry(f"outputs.{name}", "not_run" if state == "incomplete" else state,
                              checked.get("issue_count", 0)))
        per_output[name] = checked
        values[name] = item
    aggregate = _assemble(entries, issues, notes, contract_error)
    aggregate["outputs"] = per_output
    return aggregate, (values if aggregate["overall"] == "pass" else NO_VALUE)


# --- the repair message (9.3) ---------------------------------------------------------------------------
def repair_message(contract: Contract | None, result: dict, *, truncated: bool = False,
                   instruction: str | None = None) -> str:
    """The fixed template shown in the repair preview and sent as the repair's user message.
    With `truncated` true (0.1.4, 12.1) the message says the answer was cut off at the output length limit;
    a passing truncated answer has no issues, so no problems paragraph is written. A named envelope passes
    its own reply instruction and no single contract."""
    count = result["issue_count"]
    listed = result["issues"][:ISSUES_SHOWN]
    suffix = f"; the first {ISSUES_SHOWN} are listed" if count > ISSUES_SHOWN else ""
    if instruction is not None:
        pass
    elif contract.format == "text":
        instruction = "as plain text"
    else:
        instruction = "as JSON, either alone or in a single fenced code block"
    if truncated:
        lines = ["The previous answer was cut off at the output length limit, so it is incomplete."]
        if count > 0:
            lines.extend(["", f"Problems found ({count} in total{suffix}):"])
            lines.extend(f"- {issue['location']}: {issue['message']}" for issue in listed)
        lines.extend(["", f"Reply again with the complete required output, {instruction}, and nothing else. "
                          "It must fit within the same length limit."])
        return "\n".join(lines)
    lines = ["The previous answer did not satisfy the required output contract.", "",
             f"Problems found ({count} in total{suffix}):"]
    lines.extend(f"- {issue['location']}: {issue['message']}" for issue in listed)
    lines.extend(["", f"Reply again with the complete corrected output, {instruction}."])
    return "\n".join(lines)
