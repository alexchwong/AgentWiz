"""Contracts and checks without HTTP: techspec 0.1.3 tests #60-#63 (Part A, Section 7).

Contract texts are literal text, so exact numbers reach the reader digit for digit; a Python float in a
test would already have lost them."""
import importlib.util
import json
import socket
from decimal import Decimal

import pytest

from agentwiz import checks, jsonio

needs_checks = pytest.mark.needs_checks


def contract(spec):
    """A Contract from literal JSON text, or from a dict of values that carry no exact numbers."""
    return checks.contract_from_text(spec if isinstance(spec, str) else json.dumps(spec))


def run(spec, answer):
    return checks.check_answer(contract(spec), answer)


def entry(result, name):
    return next(e for e in result["checks"] if e["check"] == name)


def refusal(spec):
    with pytest.raises(checks.ContractError) as refused:
        contract(spec)
    return refused.value


# 60 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("check, text, passes", [
    ({"type": "exact", "value": "Hello\nworld"}, "  Hello\r\nworld \n", True),
    ({"type": "exact", "value": "Hello\nworld"}, "hello\nworld", False),
    ({"type": "exact", "value": ""}, " \n ", True),
    ({"type": "contains", "value": "wor"}, "Hello world", True),
    ({"type": "contains", "value": "WOR"}, "Hello world", False),
    ({"type": "not_contains", "value": "xyz"}, "Hello world", True),
    ({"type": "not_contains", "value": "world"}, "Hello world", False),
    ({"type": "regex", "pattern": "^H.*d$"}, "Hello world", True),
    ({"type": "regex", "pattern": "(?i)HELLO"}, "say hello", True),
    ({"type": "regex", "pattern": "HELLO"}, "say hello", False),
    ({"type": "regex", "pattern": "^world"}, "Hello world", False),       # search, unanchored: ^ is the start
    ({"type": "length", "min": 2, "max": 2}, "  é\U0001f600  ", True),  # two code points, trimmed
    ({"type": "length", "min": 2, "max": 2}, "é", True),              # a combining mark counts
    ({"type": "length", "max": 3}, "abcd", False),
    ({"type": "length", "min": 5}, "abcd", False),
    ({"type": "length", "min": 4, "max": None}, "abcd", True),
])
def test_check_definitions(check, text, passes):
    result = run({"format": "text", "checks": [check]}, text)
    assert result["overall"] == ("pass" if passes else "fail")
    assert entry(result, "checks[0]")["result"] == ("pass" if passes else "fail")
    assert len(result["issues"]) == (0 if passes else 1)
    if not passes:
        assert result["issues"][0]["location"] == "answer" and result["issues"][0]["check"] == "checks[0]"


def test_fenced_blocks_and_strict():
    json_contract = {"format": "json"}
    single = run(json_contract, 'Here you go:\n```json\n{"a": 1}\n```\nDone.')
    assert single["overall"] == "pass" and single["notes"] == ["parsed the single fenced code block"]
    assert entry(single, "parse")["result"] == "pass"
    bare = run(json_contract, ' \n {"a": 1} \n')
    assert bare["overall"] == "pass" and bare["notes"] == []

    two = run(json_contract, 'x\n```\n{"a":1}\n```\ny\n~~~\n{"a":2}\n~~~')
    assert two["overall"] == "fail" and entry(two, "parse")["result"] == "fail"
    assert two["issues"][0]["location"] == "line 6, column 1"
    assert two["issues"][0]["message"] == "multiple code blocks"

    strict = run({"format": "json", "strict": True}, '```json\n{"a":1}\n```')
    assert strict["overall"] == "fail" and strict["issues"][0]["location"] == "line 1, column 1"
    assert run({"format": "json", "strict": True}, ' {"a":1}\n')["overall"] == "pass"

    # CommonMark's rules: an unclosed block runs to the end; a longer fence is not closed by a shorter
    # one; four spaces of indentation make no fence; a backtick fence's info string has no backtick.
    assert [b["open_line"] for b in checks.fenced_blocks('```json\n{"a": 1}')] == [1]
    assert run(json_contract, '```json\n{"a": 1}')["overall"] == "pass"
    nested = checks.fenced_blocks("````\n```\n{}\n```\n````")
    assert len(nested) == 1 and nested[0]["content"] == "```\n{}\n```"
    assert checks.fenced_blocks('    ```\n{"a": 1}\n    ```') == []
    assert checks.fenced_blocks("```a`b\n{}") == []
    assert [b["open_line"] for b in checks.fenced_blocks("```a`b\n{}\n```")] == [3]    # the last line opens one
    assert len(checks.fenced_blocks("~~~ a`b\n{}\n~~~")) == 1
    assert checks.fenced_blocks("   ```\n   {}\n   ```")[0]["content"] == "{}"          # indentation removed
    assert run(json_contract, "```\r\n{\"a\": 1}\r\n```")["overall"] == "pass"           # CRLF lines

    # Text checks on a JSON contract run on the whole answer, not on the extracted block.
    whole = run({"format": "json", "checks": [{"type": "contains", "value": "Here you go"}]},
                'Here you go:\n```json\n{"a": 1}\n```')
    assert whole["overall"] == "pass"
    # Parse errors are located in the answer's own lines, also inside a block.
    bad = run(json_contract, 'intro\n```json\n{"a": 1,\n"b": }\n```')
    assert bad["issues"][0]["location"] == "line 4, column 6"


def test_issues_are_sorted_and_counted():
    expected = "[" + ",".join(["0"] * 25) + "]"
    spec = '{"format": "json", "checks": [{"type": "json_equals", "value": %s}]}' % expected
    result = run(spec, "[" + ",".join(["1"] * 25) + "]")
    assert result["issue_count"] == 25 and len(result["issues"]) == checks.ISSUES_SHOWN == 20
    assert [i["location"] for i in result["issues"]] == [f"$[{n}]" for n in range(20)]   # numeric order

    spec = ('{"format": "json", "checks": [{"type": "contains", "value": "zzz"},'
            ' {"type": "json_equals", "value": {"b": 1, "a": [%s], "c": {"x": 1}}}]}' % ",".join(["0"] * 12))
    found = '{"a": [0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0], "c": {"x": 1, "y": 2}}'
    locations = [i["location"] for i in run(spec, found)["issues"]]
    assert locations == ["answer", "$.a[2]", "$.a[10]", "$.b", "$.c.y"]

    # `answer` sorts before a located parse error.
    mixed = run('{"format": "json", "checks": [{"type": "contains", "value": "zzz"}]}', "not json")
    assert [i["location"] for i in mixed["issues"]] == ["answer", "line 1, column 1"]


@pytest.mark.parametrize("spec, location", [
    ({"format": "text", "checks": [{"type": "nope", "value": "x"}]}, "checks[0].type"),
    ({"format": "text", "checks": [{"type": "contains", "value": "x", "extra": 1}]}, "checks[0].extra"),
    ({"format": "text", "checks": [{"type": "contains", "value": ""}]}, "checks[0].value"),
    ({"format": "text", "checks": [{"type": "regex", "pattern": "("}]}, "checks[0].pattern"),
    ({"format": "text", "checks": [{"type": "length"}]}, "checks[0]"),
    ({"format": "text", "checks": [{"type": "length", "min": 5, "max": 2}]}, "checks[0]"),
    ({"format": "text", "checks": [{"type": "length", "min": -1}]}, "checks[0].min"),
    ({"format": "text", "checks": [{"type": "length", "max": True}]}, "checks[0].max"),
    ({"format": "text", "strict": False}, "strict"),
    ({"format": "text", "schema": None}, "schema"),
    ({"format": "text", "checks": [{"type": "json_equals", "value": 1}]}, "checks[0]"),
    ({"format": "yaml"}, "format"),
    ({}, "format"),
    ({"format": "json", "strict": "yes"}, "strict"),
    ({"format": "json", "checks": {}}, "checks"),
    ({"format": "json", "checks": [{"type": "json_equals"}]}, "checks[0].value"),
    ({"format": "json", "checks": ["x"]}, "checks[0]"),
    ({"format": "json", "checks": [{"type": ["x"]}]}, "checks[0].type"),
    ({"format": "json", "format_version": 2}, "format_version"),
    ({"format": "json", "format_version": True}, "format_version"),
    ({"format": "json", "description": 5}, "description"),
])
def test_contract_refusals_are_located(spec, location):
    error = refusal(spec)
    assert location in [issue["location"] for issue in error.issues]
    assert all(isinstance(issue["message"], str) and issue["message"] for issue in error.issues)


def test_contract_refusal_lists_every_issue_without_values():
    error = refusal({"format": "text", "strict": True,
                     "checks": [{"type": "contains", "value": "SECRET-VALUE", "bogus": "SECRET-VALUE"},
                                {"type": "regex", "pattern": "SECRET-(("}]})
    assert {i["location"] for i in error.issues} == {"strict", "checks[0].bogus", "checks[1].pattern"}
    assert "SECRET" not in json.dumps(error.issues) and "SECRET" not in str(error)
    assert refusal("not json").issues[0]["message"] == "the contract is not valid JSON"
    assert refusal("[1]").issues[0]["location"] == "contract"
    assert refusal('{"format": "text", "format": "json"}').issues[0]["location"] == "contract"
    assert refusal('{"format": "json",\n "checks": NaN}').issues[0]["location"] == "contract"
    assert refusal('{"format": "json",\n "checks": ').issues[0]["location"].startswith("line 2")


def test_contract_defaults_and_unknown_fields_are_kept():
    c = contract('{"format": "json", "future": [1.50], "checks": []}')
    assert (c.strict, c.description, c.schema, c.checks) == (False, "", None, ())
    assert c.raw["future"] == [Decimal("1.50")] and list(c.raw) == ["format", "future", "checks"]


def test_repair_message_template():
    c = contract({"format": "json", "checks": [{"type": "contains", "value": "zzz"}]})
    result = checks.check_answer(c, "nope")
    text = checks.repair_message(c, result)
    assert text.splitlines()[0] == "The previous answer did not satisfy the required output contract."
    assert "Problems found (2 in total):" in text
    assert "- answer: contains: the answer does not contain \"zzz\"" in text
    assert text.endswith("Reply again with the complete corrected output, "
                         "as JSON, either alone or in a single fenced code block.")
    strict = contract({"format": "json", "strict": True, "checks": [{"type": "contains", "value": "zzz"}]})
    assert checks.repair_message(strict, checks.check_answer(strict, "{}")).endswith(
        "as JSON only, with no other text.")
    plain = contract({"format": "text", "checks": [{"type": "contains", "value": "zzz"}]})
    assert checks.repair_message(plain, checks.check_answer(plain, "x")).endswith("as plain text.")
    many = contract('{"format": "json", "checks": [{"type": "json_equals", "value": [%s]}]}' % ",".join(["0"] * 25))
    text = checks.repair_message(many, checks.check_answer(many, "[" + ",".join(["1"] * 25) + "]"))
    assert "Problems found (25 in total; the first 20 are listed):" in text
    assert text.count("\n- ") + text.startswith("- ") == 20


def test_check_value_accepts_parsed_values():
    c = contract('{"format": "json", "checks": [{"type": "contains", "value": "9007199254740993"},'
                 ' {"type": "json_equals", "value": {"n": 9007199254740993}}]}')
    value = jsonio.decode_exact('{"n": 9007199254740993}')
    result = checks.check_value(c, value)
    assert result["overall"] == "pass" and result["notes"] == ["value given already parsed"]
    assert checks.check_value(c, {"n": 9007199254740992})["overall"] == "fail"
    with pytest.raises(TypeError):
        checks.check_value(contract({"format": "text"}), {"n": 1})


# 61 ---------------------------------------------------------------------------------------------
def equals(expected_text, answer_text):
    spec = '{"format": "json", "checks": [{"type": "json_equals", "value": %s}]}' % expected_text
    return run(spec, answer_text)["overall"]


def test_check_numbers():
    assert equals("1000000000000000128", "1.0000000000000001e18") == "fail"
    assert equals("1000000000000000100", "1.0000000000000001e18") == "pass"
    assert equals("100", "1e2") == "pass"
    assert equals("1", "1.0") == "pass"
    assert equals("9007199254740992.0", "9007199254740993.0") == "fail"
    assert equals("9007199254740993", "9007199254740992") == "fail"
    assert equals("0.10000000000000000001", "0.1") == "fail"
    assert equals("true", "1") == "fail" and equals("1", "true") == "fail"
    assert equals("0", "false") == "fail" and equals("null", "0") == "fail" and equals("null", "null") == "pass"
    assert equals('{"a": 1, "b": [1.0, 2]}', '{"b": [1, 2.00], "a": 1.0}') == "pass"      # key order is free
    assert equals("[1, 2]", "[2, 1]") == "fail"                                          # array order is not
    assert equals('{"a": 1}', '{"a": 1, "b": 2}') == "fail"
    assert equals('"x"', '"x "') == "fail"

    # Too many digits: the answer cannot be checked exactly, which is not a failure and not a pass.
    huge = run('{"format": "json", "checks": [{"type": "contains", "value": "1"}]}', "1" * 5000)
    assert entry(huge, "parse")["result"] == "not_run" and huge["overall"] == "incomplete"
    assert entry(huge, "checks[0]")["result"] == "pass"                                  # text checks still run
    assert huge["issues"][0]["location"] == "line 1, column 1"
    prefix = '{"format": "json", "checks": [{"type": "json_equals", "value": '
    with pytest.raises(checks.ContractError) as refused:
        contract(prefix + "1" * 5000 + "}]}")
    assert refused.value.issues[0]["location"] == f"line 1, column {len(prefix) + 1}"

    # A number Decimal cannot construct is the same limit, never an internal error or the contract's fault.
    for text in ("1e9223372036854775808", '{"x": [1e9223372036854775808]}'):
        result = run('{"format": "json", "checks": [{"type": "contains", "value": "e"}]}', text)
        assert entry(result, "parse")["result"] == "not_run" and result["overall"] == "incomplete"
        assert "contract_error" != result["overall"]
    with pytest.raises(checks.ContractError) as refused:
        contract('{"format": "json", "checks": [{"type": "json_equals", "value": 1e9223372036854775808}]}')
    assert refused.value.issues[0]["location"].startswith("line 1, column ")

    # NaN fails parse; malformed JSON fails parse at the position the reader reports.
    nan = run({"format": "json"}, '{"a": NaN}')
    assert entry(nan, "parse")["result"] == "fail" and nan["overall"] == "fail"
    for bad in ('{"a": }', '[1,\n 2,, 3]', '{"a" 1}'):
        with pytest.raises(json.JSONDecodeError) as syntax:
            json.loads(bad)
        located = run({"format": "json"}, bad)
        assert entry(located, "parse")["result"] == "fail"
        assert located["issues"][0]["location"] == f"line {syntax.value.lineno}, column {syntax.value.colno}"

    # Results are plain, strict JSON: no NaN, no Infinity, no Decimal.
    for result in (nan, huge, run({"format": "json"}, '{"a": 1.50}')):
        json.loads(jsonio.encode({"check": result}), parse_constant=lambda name: pytest.fail(name))


def test_decode_exact_error_contract():
    for text, kind in [("{", jsonio.JSONSyntax), ("1e9223372036854775808", jsonio.ExactLimit),
                       ("1" * 4301, jsonio.ExactLimit), ("NaN", ValueError), ('{"a":1,"a":2}', ValueError),
                       ("[" * 100000 + "1", ValueError)]:
        with pytest.raises(kind) as caught:
            jsonio.decode_exact(text)
        assert type(caught.value) is not Exception and isinstance(caught.value, ValueError)
        assert not isinstance(caught.value, ArithmeticError) and "999" not in str(caught.value)
    with pytest.raises(jsonio.JSONSyntax) as caught:
        jsonio.decode_exact('{\n  "a": ,}')
    assert (caught.value.line, caught.value.column) == (2, 8)
    with pytest.raises(jsonio.ExactLimit) as caught:
        jsonio.decode_exact('{"s": "' + "7" * 5000 + '",\n  "n": ' + "7" * 4301 + "}")      # digits in a string are fine
    assert (caught.value.line, caught.value.column) == (2, 8)
    assert jsonio.decode_exact("1" * 4300) == int("1" * 4300)                              # the limit is exclusive


# 62 ---------------------------------------------------------------------------------------------
def schema_contract(schema_text, extra_checks="[]"):
    return '{"format": "json", "schema": %s, "checks": %s}' % (schema_text, extra_checks)


RECURSIVE = ('{"$defs": {"node": {"type": "object", "properties": {"v": {"type": "integer"},'
             ' "next": {"$ref": "#/$defs/node"}}}}, "$ref": "#/$defs/node"}')


@needs_checks
def test_schema():
    # Through the validation copy, 1 and 1.0 are alike everywhere, including check_schema and $ref.
    assert contract(schema_contract('{"type": "array", "minItems": 1.0}')).schema["minItems"] == 1
    c = contract(schema_contract(RECURSIVE))
    assert checks.check_answer(c, '{"v": 1, "next": {"v": 1.0}}')["overall"] == "pass"
    nested = checks.check_answer(c, '{"v": 1, "next": {"v": 1.5}}')
    assert nested["overall"] == "fail" and nested["issues"][0]["location"] == "$.next.v"
    assert "type: the value must be of type integer; it is 1.5" == nested["issues"][0]["message"]
    defs = contract(schema_contract('{"$defs": {"n": {"type": "integer"}}, "properties": {"a": {"$ref": "#/$defs/n"}}}'))
    assert checks.check_answer(defs, '{"a": 2.0}')["overall"] == "pass"
    assert checks.check_answer(defs, '{"a": 2.5}')["issues"][0]["location"] == "$.a"
    anchor = contract(schema_contract('{"$defs": {"n": {"$anchor": "num", "type": "integer"}},'
                                      ' "properties": {"a": {"$ref": "#num"}}}'))
    assert checks.check_answer(anchor, '{"a": 3.0}')["overall"] == "pass"
    assert checks.check_answer(anchor, '{"a": 3.5}')["overall"] == "fail"

    assert checks.check_answer(contract(schema_contract('{"multipleOf": 0.1}')), "0.3")["overall"] == "pass"
    assert checks.check_answer(contract(schema_contract('{"uniqueItems": true}')), "[1, 1.0]")["overall"] == "fail"
    assert checks.check_answer(contract(schema_contract('{"type": "integer"}')), "1.0")["overall"] == "pass"
    assert checks.check_answer(contract(schema_contract('{"const": 1}')), "true")["overall"] == "fail"
    assert checks.check_answer(contract(schema_contract('{"const": 1}')), "1.0")["overall"] == "pass"

    # Numbers the exact context cannot handle make `schema` not_run, never a pass and never the
    # contract's fault; an integral 1e5000 in a schema is a contract error.
    huge = checks.check_answer(contract(schema_contract('{"multipleOf": 7}', '[{"type": "contains", "value": "e"}]')), "1e5000")
    assert entry(huge, "schema")["result"] == "not_run" and entry(huge, "checks[0]")["result"] == "pass"
    assert huge["overall"] == "incomplete"
    with pytest.raises(checks.ContractError) as refused:
        contract(schema_contract('{"minimum": 1e5000}'))
    assert refused.value.issues[0]["location"] == "schema.minimum"

    # After validation the saved contract still reads 1.0.
    kept = contract(schema_contract('{"minItems": 1.0}'))
    checks.check_answer(kept, "[1.0]")
    assert str(kept.schema["minItems"]) == "1.0" and isinstance(kept.schema["minItems"], Decimal)


@needs_checks
def test_schema_numeric_controls():
    """Fractional values, so the validation copy leaves them alone (review v2 R1)."""
    multiple = contract(schema_contract('{"multipleOf": 0.1}', '[{"type": "contains", "value": "0.1"}]'))
    assert checks.check_answer(multiple, "0.3")["overall"] == "fail"       # no "0.1" in the text: the contains fails
    assert entry(checks.check_answer(multiple, "0.3"), "schema")["result"] == "pass"
    tiny = checks.check_answer(multiple, "1e-1005000")
    assert entry(tiny, "schema")["result"] == "fail"                         # a nonzero remainder, never a pass
    schema_issue = next(i for i in tiny["issues"] if i["check"] == "schema")
    assert schema_issue["location"] == "$" and schema_issue["message"].startswith("multipleOf:")
    wide = checks.check_answer(contract(schema_contract('{"multipleOf": 0.2}', '[{"type": "contains", "value": "0.11"}]')),
                               "0." + "1" * 2101)
    assert entry(wide, "schema")["result"] == "not_run"                      # Inexact: the quotient needs > 2,100 digits
    assert entry(wide, "checks[0]")["result"] == "pass"                      # an independent check still runs
    assert wide["overall"] == "incomplete"


@needs_checks
def test_schema_rules_at_save():
    refused_schemas = {
        "another draft at the root": '{"$schema": "http://json-schema.org/draft-07/schema#", "type": "integer"}',
        "a remote reference": '{"$ref": "http://example.com/schema.json"}',
        "a relative reference": '{"properties": {"a": {"$ref": "other.json"}}}',
        "a dynamic reference": '{"$dynamicRef": "https://example.com/x#node"}',
        "a reference that is not a string": '{"$ref": 5}',
        "a Draft 7 resource in $defs": '{"$ref": "#/$defs/x", "$defs": {"x": {"$schema": '
                                       '"http://json-schema.org/draft-07/schema#", "dependencies": {"a": ["b"]}}}}',
        "a Draft 7 resource nested in a keyword": '{"properties": {"a": {"allOf": [{"$schema": '
                                                  '"http://json-schema.org/draft-04/schema#"}]}}}',
        "an object under const that a $ref targets": '{"const": {"$schema": "http://json-schema.org/draft-07/schema#"},'
                                                      ' "$ref": "#/const"}',
        "an anchored target in another draft": '{"$ref": "#lib", "$defs": {"x": {"$anchor": "lib", "$schema": '
                                                '"http://json-schema.org/draft-07/schema#"}}}',
        "an invalid schema": '{"type": "nonsense"}',
        "an invalid keyword value": '{"minItems": "many"}',
        "a pattern that is not a regular expression": '{"pattern": "(("}',
    }
    for why, schema in refused_schemas.items():
        with pytest.raises(checks.ContractError, match="schema"):
            contract(schema_contract(schema))
        assert why

    accepted = {
        "2020-12 at the root": '{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "integer"}',
        "2020-12 with a trailing #": '{"$schema": "https://json-schema.org/draft/2020-12/schema#", "type": "integer"}',
        "a 2020-12 resource in $defs": '{"$ref": "#/$defs/x", "$defs": {"x": {"$schema": '
                                       '"https://json-schema.org/draft/2020-12/schema", "type": "integer"}}}',
        "a $schema key inside const": '{"const": {"$schema": "http://json-schema.org/draft-07/schema#"}}',
        "a $schema key inside enum, default and examples":
            '{"enum": [{"$schema": "x"}], "default": {"$schema": "x"}, "examples": [{"$schema": "x"}]}',
        "a self reference": '{"$ref": "#"}',
        "a reference cycle": '{"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"}',
        "a local pointer with escapes": '{"$defs": {"a/b": {"type": "integer"}}, "$ref": "#/$defs/a~1b"}',
        "a boolean schema": "true",
        "an embedded resource": '{"$defs": {"x": {"$id": "https://example.com/x", "$defs": {"y": {"type": "integer"}},'
                                ' "properties": {"a": {"$ref": "#/$defs/y"}}}}}',
        "a reference nowhere": '{"$ref": "#/$defs/missing"}',
        "an anchor nowhere": '{"$ref": "#nowhere"}',
    }
    for why, schema in accepted.items():
        assert contract(schema_contract(schema)).schema is not None, why


@needs_checks
def test_unresolved_local_reference_is_the_contracts_error(monkeypatch):
    attempts = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, address: attempts.append(address) or (_ for _ in ()).throw(OSError("blocked")))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: attempts.append(a) or (_ for _ in ()).throw(OSError("blocked")))
    c = contract(schema_contract('{"$ref": "#/$defs/missing"}', '[{"type": "contains", "value": "1"}]'))   # it saves...
    result = checks.check_answer(c, "1")
    assert result["overall"] == "contract_error"                                        # ...and fails when applied
    assert entry(result, "schema")["result"] == "not_run" and entry(result, "checks[0]")["result"] == "pass"
    assert result["issues"][0]["check"] == "schema" and "could not be applied" in result["issues"][0]["message"]
    remote = checks.check_answer(contract(schema_contract('{"properties": {"a": {"$ref": "#nowhere"}}}')), '{"a": 1}')
    assert remote["overall"] == "contract_error"
    assert attempts == []                                                               # nothing was fetched


@needs_checks
def test_schema_messages():
    c = contract(schema_contract(
        '{"type": "object", "required": ["a", "b"], "additionalProperties": false,'
        ' "properties": {"a": {"type": "string", "maxLength": 2}, "n": {"minimum": 1.0}, "e": {"enum": ["x", "y"]}}}'))
    result = checks.check_answer(c, '{"a": "toolong", "n": 0.5, "e": "z", "extra": 1}')
    messages = {i["location"]: i["message"] for i in result["issues"]}
    assert messages["$.a"] == 'maxLength: the value must be at most 2 characters long; it is "toolong"'
    assert messages["$.n"] == "minimum: the value must be at least 1.0; it is 0.5"          # original form: 1.0
    assert messages["$.e"] == 'enum: the value must be one of ["x","y"]; it is "z"'
    assert messages["$.extra"] == "additionalProperties: this property is not allowed"
    missing = checks.check_answer(c, "{}")
    assert [i["message"] for i in missing["issues"]] == ['required: the property "a" is missing',
                                                         'required: the property "b" is missing']
    shown = checks.check_answer(contract(schema_contract('{"const": 1.0}')), "2.50")["issues"][0]["message"]
    assert shown == "const: the value must be equal to 1.0; it is 2.50"                      # exact text, as written
    long = checks.check_answer(contract(schema_contract('{"const": "a"}')), json.dumps("x" * 500))["issues"][0]["message"]
    assert len(long) < 300 and "cut at 200 characters" in long
    false = checks.check_answer(contract(schema_contract("false")), "1")
    assert false["overall"] == "fail" and "the schema is false" in false["issues"][0]["message"]
    deep = checks.check_answer(contract(schema_contract('{"properties": {"a": {"items": {"type": "integer"}}}}')),
                               '{"a": [1, 2, "x"]}')
    assert deep["issues"][0]["location"] == "$.a[2]"


# 63 ---------------------------------------------------------------------------------------------
def test_schema_without_extra(monkeypatch):
    monkeypatch.setattr(checks, "schema_support", lambda: (False, "jsonschema is not installed"))
    c = contract(schema_contract('{"type": "object", "required": ["x"]}', '[{"type": "contains", "value": "ok"}]'))
    assert c.schema == {"type": "object", "required": ["x"]}                       # a schema contract still loads
    result = checks.check_answer(c, '{"ok": 1}')
    assert entry(result, "schema")["result"] == "not_run" and entry(result, "parse")["result"] == "pass"
    assert result["overall"] == "incomplete"                                         # never pass
    assert "pip install 'AgentWiz[checks]'" in result["issues"][0]["message"]
    other = checks.check_answer(c, '{"nope": 1}')                                    # another check fails: fail
    assert other["overall"] == "fail" and entry(other, "schema")["result"] == "not_run"
    assert checks.check_answer(c, "not json")["overall"] == "fail"
    # No schema rule runs at load: even a schema that would be refused loads without the extra.
    assert contract(schema_contract('{"$ref": "http://example.com/x"}')).schema is not None
    assert checks.schema_support() == (False, "jsonschema is not installed")


def test_unsupported_version_counts_as_missing(monkeypatch):
    for version, usable in [("4.26.0", True), ("4.99.1", True), ("4.25.9", False), ("5.0.0", False),
                            ("3.2.0", False), ("not-a-version", False)]:
        monkeypatch.setattr(checks, "_jsonschema_version", lambda version=version: version)
        available, detail = checks.schema_support()
        assert available is (usable and importlib.util.find_spec("jsonschema") is not None), version
        if not usable:
            assert version in detail and ("outside the supported range" in detail), detail
    monkeypatch.setattr(checks, "_jsonschema_version", lambda: None)
    assert checks.schema_support() == (False, "jsonschema is not installed")
    result = checks.check_answer(contract(schema_contract('{"type": "object"}')), "{}")
    assert entry(result, "schema")["result"] == "not_run" and result["overall"] == "incomplete"


# 93 (0.1.4) -------------------------------------------------------------------------------------
def test_answer_value_and_truncation_message():
    text_contract = contract({"format": "text"})
    result, value = checks.check_answer_value(text_contract, "hello")
    assert value == "hello" and result == checks.check_answer(text_contract, "hello")

    json_contract = contract('{"format": "json"}')
    fenced = 'Here you go:\n```json\n{"n": 9007199254740993, "x": 0.10000000000000000001}\n```\n'
    result, value = checks.check_answer_value(json_contract, fenced)
    assert value == {"n": 9007199254740993, "x": Decimal("0.10000000000000000001")}
    assert type(value["n"]) is int and result == checks.check_answer(json_contract, fenced)
    whole = ' {"a": [1, 2]} \n'
    result, value = checks.check_answer_value(json_contract, whole)
    assert value == {"a": [1, 2]} and result == checks.check_answer(json_contract, whole)
    result, value = checks.check_answer_value(json_contract, "null")
    assert value is None and value is not checks.NO_VALUE                 # a parsed null is a value

    for bad in ("not json", '{"a": ', "```json\n{}\n```\n```json\n{}\n```"):
        result, value = checks.check_answer_value(json_contract, bad)
        assert value is checks.NO_VALUE and result["overall"] != "pass"
        assert result == checks.check_answer(json_contract, bad)

    failing = contract({"format": "json", "checks": [{"type": "contains", "value": "zzz"}]})
    result = checks.check_answer(failing, '{"a": 1}')
    plain = checks.repair_message(failing, result)
    cut = checks.repair_message(failing, result, truncated=True)
    assert plain != cut
    assert cut.startswith("The previous answer was cut off at the output length limit")
    for issue in result["issues"]:
        assert f"- {issue['location']}: {issue['message']}" in cut
    assert cut.endswith("It must fit within the same length limit.")

    passing = contract({"format": "json"})
    ok = checks.check_answer(passing, '{"a": 1}')
    assert ok["issue_count"] == 0
    cut_ok = checks.repair_message(passing, ok, truncated=True)
    assert "Problems found" not in cut_ok and "\n- " not in cut_ok
    assert cut_ok != checks.repair_message(passing, ok)
