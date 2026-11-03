"""Template scanning and filling: techspec 0.1.4, test #92 (R6-5, P9, P14)."""
import hashlib
from decimal import Decimal

import pytest

from agentwiz import jsonio, templates
from agentwiz.templates import FillError, TemplateError


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_scan_finds_exact_placeholders():
    found = templates.scan_text("a {{inputs.x}} b {{inputs.x.y}} c", "prompt")
    assert [(p.text, p.path) for p in found] == [("{{inputs.x}}", ("x",)), ("{{inputs.x.y}}", ("x", "y"))]


@pytest.mark.parametrize("text", ["{{ inputs.x }}", "{{inputs.}}", "{{inputs.x[0]}}", "{{inputs.x }}", "{{ inputs.x}}",
                                  "{{inputs}}", "{{inputs.1x}}", "line one\n{{inputs..x}}"])
def test_malformed_placeholders_are_located_load_errors(text):
    with pytest.raises(TemplateError) as err:
        templates.scan_text(text, "prompt")
    assert err.value.location == "prompt" and "malformed placeholder" in err.value.message


def test_steps_placeholders_are_refused_until_later():
    with pytest.raises(TemplateError) as err:
        templates.scan_text("{{steps.a.b}}", "system")
    assert "0.2.1" in err.value.message and err.value.location == "system"


def test_other_braces_are_left_alone():
    text = 'Use {{name}} and {"a": {"b": 1}} and {{ other }}'
    assert templates.scan_text(text, "prompt") == []
    assert templates.fill_text(text, "prompt", {}) == (text, [])


def test_error_lines_are_reported():
    with pytest.raises(TemplateError) as err:
        templates.scan_text("one\ntwo {{ inputs.x }}", "prompt")
    assert "line 2" in err.value.message


def test_scan_arguments_at_any_depth_with_json_paths():
    template = {"a": "{{inputs.x}}", "b": [{"c": "pre {{inputs.y.z}}"}], "k{{inputs.n}}": 1, "n": 5}
    found = templates.scan_arguments(template)
    assert [(w, p.text) for w, p in found] == [("arguments.a", "{{inputs.x}}"),
                                               ("arguments.b[0].c", "{{inputs.y.z}}")]
    with pytest.raises(TemplateError) as err:
        templates.scan_arguments({"a": ["{{ inputs.x }}"]})
    assert err.value.location == "arguments.a[0]"


def test_fill_text_inserts_strings_as_they_are_and_other_values_as_exact_json():
    values = {"s": "plain {{inputs.q}}", "big": jsonio.decode_exact("9007199254740993"),
              "dec": jsonio.decode_exact("0.10000000000000000001"),
              "obj": jsonio.decode_exact('{"a": [1, 2.50], "b": null}')}
    text, fills = templates.fill_text("{{inputs.s}}|{{inputs.big}}|{{inputs.dec}}|{{inputs.obj}}", "prompt", values)
    assert text == 'plain {{inputs.q}}|9007199254740993|0.10000000000000000001|{"a":[1,2.50],"b":null}'
    assert [f["placeholder"] for f in fills] == ["{{inputs.s}}", "{{inputs.big}}", "{{inputs.dec}}", "{{inputs.obj}}"]
    assert fills[1] == {"where": "prompt", "placeholder": "{{inputs.big}}", "source": "inputs.big",
                        "sha256": sha("9007199254740993")}
    assert fills[0]["sha256"] == sha("plain {{inputs.q}}")


def test_a_placeholder_used_twice_gives_two_fills():
    text, fills = templates.fill_text("{{inputs.x}} {{inputs.x}}", "system", {"x": "a"})
    assert text == "a a" and len(fills) == 2 and fills[0] == fills[1]


def test_nested_paths_resolve():
    text, fills = templates.fill_text("{{inputs.o.p}}", "prompt", {"o": {"p": "deep"}})
    assert text == "deep" and fills[0]["source"] == "inputs.o.p"


@pytest.mark.parametrize("values, message", [
    ({}, "the input has no value"),
    ({"o": {"q": "SECRET-VALUE"}}, "the path has a key that the value does not hold"),
    ({"o": "SECRET-VALUE"}, "applied to a value that is not an object"),
])
def test_unfillable_placeholders_name_the_placeholder_never_a_value(values, message):
    with pytest.raises(FillError) as err:
        templates.fill_text("{{inputs.o.p}}", "prompt", values)
    assert err.value.location == "prompt" and message in err.value.message
    assert "{{inputs.o.p}}" in err.value.message and "SECRET-VALUE" not in str(err.value)


def test_fill_arguments_keeps_types_for_whole_string_placeholders():
    values = {"n": jsonio.decode_exact("9007199254740993"), "d": jsonio.decode_exact("1.10"),
              "o": jsonio.decode_exact('{"k": [true]}'), "s": "txt", "t": jsonio.decode_exact("true")}
    template = {"count": "{{inputs.n}}", "dec": "{{inputs.d}}", "obj": "{{inputs.o}}", "flag": "{{inputs.t}}",
                "mixed": "n={{inputs.n}} s={{inputs.s}}", "list": ["{{inputs.s}}", 3, None], "{{inputs.s}}": "key"}
    out, fills = templates.fill_arguments(template, values)
    assert out["count"] == 9007199254740993 and type(out["count"]) is int
    assert out["dec"] == Decimal("1.10") and str(out["dec"]) == "1.10"
    assert out["obj"] == {"k": [True]} and out["flag"] is True
    assert out["mixed"] == "n=9007199254740993 s=txt" and out["list"] == ["txt", 3, None]
    assert "{{inputs.s}}" in out                                    # object keys are never filled
    by_where = {f["where"]: f for f in fills}
    assert by_where["arguments.count"]["sha256"] == sha("9007199254740993")
    assert by_where["arguments.obj"]["sha256"] == sha(jsonio.encode_exact(values["o"]))
    assert by_where["arguments.mixed"]["sha256"] in (sha("9007199254740993"), sha("txt"))
    assert len(fills) == 7                                          # count, dec, obj, flag, 2 in mixed, list[0]
    for fill in fills:
        assert set(fill) == {"where", "placeholder", "source", "sha256"}


def test_fill_arguments_error_is_located_at_the_argument():
    with pytest.raises(FillError) as err:
        templates.fill_arguments({"a": {"b": "{{inputs.missing}}"}}, {})
    assert err.value.location == "arguments.a.b"
