"""Tool argument forms: techspec 0.1.2 test #53. Fixtures are recorded FastMCP schema shapes (Section 3.3),
not a dependency on any server (D47)."""
import pytest

from agentwiz.jsonio import decode_exact
from agentwiz.tool_forms import form_spec

# search_web(query: str, max_passages: int | None = None) and read_url(url: str), as FastMCP 3.x and 4.x
# describe them (both versions give the same shape).
SEARCH_WEB = """{"type": "object", "properties": {
  "query": {"type": "string", "title": "Query"},
  "max_passages": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": null, "title": "Max Passages"}},
  "required": ["query"]}"""
READ_URL = '{"type": "object", "properties": {"url": {"type": "string", "title": "Url"}}, "required": ["url"]}'


def test_tool_forms_fastmcp_shapes():
    form, notes = form_spec(decode_exact(SEARCH_WEB))
    fields = {f["name"]: f for f in form["fields"]}
    assert fields["query"]["required"] is True and fields["query"]["nullable"] is False
    assert fields["max_passages"] == {"name": "max_passages", "title": "Max Passages", "type": "integer",
                                      "required": False, "nullable": True, "description": None, "default": None,
                                      "enum": None}
    assert notes == []
    form, _ = form_spec(decode_exact(READ_URL))
    assert [f["name"] for f in form["fields"]] == ["url"]


def test_tool_forms_required_nullable():
    schema = decode_exact(SEARCH_WEB)
    schema["required"] = ["query", "max_passages"]
    form, notes = form_spec(schema)
    assert form is None and any("max_passages" in n and "null" in n for n in notes)


@pytest.mark.parametrize("prop, word", [
    ({"$ref": "#/$defs/x"}, "$ref"), ({"oneOf": [{"type": "string"}]}, "oneOf"),
    ({"allOf": [{"type": "string"}]}, "allOf"), ({"not": {"type": "string"}}, "not"),
    ({"anyOf": [{"type": "string"}, {"type": "integer"}]}, "anyOf"), ({"type": ["string", "integer"]}, "types"),
    ({"type": "object", "properties": {}}, "properties"), ({"type": "array", "items": {}}, "items"),
    ({"const": "x"}, "const"), ({"type": "integer", "enum": [1, 2]}, "enum"),
])
def test_tool_forms_unsupported(prop, word):
    form, notes = form_spec({"type": "object", "properties": {"p": prop}})
    assert form is None and notes and "p" in notes[0]
    form, notes = form_spec({"type": "array"})
    assert form is None and notes


def test_tool_forms_notes_and_exact_defaults():
    schema = decode_exact('{"type": "object", "properties": {'
                          '"id": {"type": "integer", "default": 9007199254740993, "minimum": 0},'
                          '"ratio": {"type": "number", "default": 0.10000000000000000001},'
                          '"flag": {"type": "boolean"}, "mode": {"type": "string", "enum": ["a", "b"], "maxLength": 3}}}')
    form, notes = form_spec(schema)
    fields = {f["name"]: f for f in form["fields"]}
    assert fields["id"]["default"] == "9007199254740993"
    assert fields["ratio"]["default"] == "0.10000000000000000001"
    assert fields["mode"]["enum"] == ["a", "b"]
    assert any("minimum" in n for n in notes) and any("maxLength" in n for n in notes)
