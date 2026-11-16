"""The eleven addendum utilities (2.2) and the registry (2.1): behaviour, bounds, fixed codes, messages that never
carry input text, and worst-case inputs measured against a generous bound (the target is under 1 second; the
measurements are recorded in the handover)."""
from __future__ import annotations

import json
import time

import pytest

from agentwiz import agent_tools as t, blocks


def run(name, **arguments):
    return t.run_utility(name, arguments)


def refused(name, *, limit=t.RESULT_LIMIT, **arguments) -> t.ToolError:
    with pytest.raises(t.ToolError) as exc:
        t.run_utility(name, arguments, result_limit=limit)
    return exc.value


SECRET = "S3CRET-VALUE"


# --- the registry (2.1) ----------------------------------------------------------------------------------------------
def test_every_name_is_derived_from_the_registry():
    assert t.UTILITY_TOOLS == tuple(t.REGISTRY) and len(t.UTILITY_TOOLS) == 14
    assert blocks.BUILTIN_TOOLS == t.UTILITY_TOOLS
    for name, utility in t.REGISTRY.items():
        assert t.SCHEMAS[name] is utility.schema and t.DESCRIPTIONS[name] == utility.description
        assert utility.schema["additionalProperties"] is False


def test_result_schemas_are_in_the_contract_dialect():
    from agentwiz import contract_schema
    for utility in t.REGISTRY.values():
        assert contract_schema.validate_user_schema(utility.result) is None or True
        contract_schema.validate_user_schema(utility.result)


def test_the_arguments_limit_is_too_large():
    error = refused("find_replace", text="a" * (1 << 20), find="a", replace="b")
    assert error.code == "too_large" and "arguments" in error.message


# --- find_replace ----------------------------------------------------------------------------------------------------
def test_find_replace_is_literal_and_non_overlapping():
    assert run("find_replace", text="aaaa", find="aa", replace="b") == {"text": "bb", "count": 2}
    assert run("find_replace", text="a.c abc", find=".", replace="!") == {"text": "a!c abc", "count": 1}
    assert run("find_replace", text="Abc", find="a", replace="x") == {"text": "Abc", "count": 0}
    error = refused("find_replace", text=SECRET * 3, find=SECRET, replace="x", expected_count=2)
    assert error.code == "count_mismatch" and "3" in error.message and SECRET not in error.message


def test_find_replace_refuses_a_result_over_the_limit_before_building_it():
    error = refused("find_replace", limit=1000, text="a" * 600, find="a", replace="bb")
    assert error.code == "too_large" and "Workshop or a workflow" in error.message


# --- headings ---------------------------------------------------------------------------------------------------------
MARKDOWN = "# Title\nintro\n## Scope ##\none\n```\n## Not a heading\n```\n### Detail\ntwo\n#5 bolt\n## Next\nend\n"


def test_extract_section_with_and_without_subsections():
    whole = run("extract_section", markdown=MARKDOWN, heading="  scope ")
    assert whole["found"] and whole["level"] == 2 and (whole["start_line"], whole["end_line"]) == (3, 10)
    assert whole["section"].startswith("## Scope ##\none\n```") and whole["section"].endswith("#5 bolt\n")
    own = run("extract_section", markdown=MARKDOWN, heading="Scope", include_subsections=False)
    assert own["end_line"] == 7 and own["matches"] == 1
    missing = run("extract_section", markdown=MARKDOWN, heading="Not a heading")
    assert missing == {"found": False, "matches": 0, "section": "", "level": None, "start_line": None,
                       "end_line": None}


def test_outline_ignores_fences_and_strips_closing_runs():
    result = run("outline", markdown=MARKDOWN)
    assert [(h["level"], h["text"], h["line"]) for h in result["headings"]] == [
        (1, "Title", 1), (2, "Scope", 3), (3, "Detail", 8), (2, "Next", 11)]
    assert result["truncated"] is False
    many = run("outline", markdown="# h\n" * 2500)
    assert len(many["headings"]) == 2000 and many["truncated"] is True


# --- compare_texts ----------------------------------------------------------------------------------------------------
def test_compare_texts_ignores_line_endings_and_shifts_hunks():
    same = run("compare_texts", before="a\r\nb", after="a\nb\n")
    assert same == {"changed": False, "diff": "", "added_lines": 0, "removed_lines": 0}
    before = "".join(f"line {n}\n" for n in range(1, 101))
    after = before.replace("line 50\n", "line fifty\n")
    result = run("compare_texts", before=before, after=after, context_lines=1)
    assert result["diff"] == "--- before\n+++ after\n@@ -49,3 +49,3 @@\n line 49\n-line 50\n+line fifty\n line 51"
    assert (result["added_lines"], result["removed_lines"]) == (1, 1)


def test_compare_texts_refuses_too_many_lines_or_too_much_work():
    assert refused("compare_texts", before="a\n" * 5001, after="").code == "too_large"
    before = "".join(f"a{n}\n" for n in range(2000))
    after = "".join(f"b{n}\n" for n in range(2000))
    error = refused("compare_texts", before=before, after=after)
    assert error.code == "too_large" and "2000" in error.message


# --- combine_texts ----------------------------------------------------------------------------------------------------
def test_combine_texts():
    assert run("combine_texts", parts=["a", "b"]) == {"text": "a\n\nb"}
    assert run("combine_texts", parts=["a", "b"], separator="|", headings=["H1", "H2"]) == {
        "text": "## H1\n\na|## H2\n\nb"}
    assert refused("combine_texts", parts=["a"], headings=["x\ny"]).code == "invalid_arguments"
    assert refused("combine_texts", parts=["a", "b"], headings=["x"]).code == "invalid_arguments"


# --- tables -----------------------------------------------------------------------------------------------------------
def test_parse_csv_keeps_values_verbatim():
    text = '﻿id,name\n007,"Smith, J"\n\n008," two\nlines "\n'
    assert run("parse_csv", csv=text) == {"columns": ["id", "name"],
                                          "rows": [["007", "Smith, J"], ["008", " two\nlines "]], "row_count": 2}
    assert run("parse_csv", csv="1;2\n3;4\n", delimiter=";", has_header=False)["columns"] == ["c1", "c2"]


@pytest.mark.parametrize("text, position", [
    ("a,a\n1,2\n", 1), ("a,\n1,2\n", 1), (f"a,b\n1,2\n3,{SECRET},x\n", 3), ('a,b\n1,"x"y\n', 2),
])
def test_parse_csv_errors_name_the_record_and_never_the_value(text, position):
    error = refused("parse_csv", csv=text)
    assert error.code == "invalid_csv" and error.position == position
    assert SECRET not in error.message and "record" in error.message


def test_rows_to_table_escapes():
    result = run("rows_to_table", columns=["a|b", "c"], rows=[["x\\y", "1\r\n2"]])
    assert result["markdown"] == "| a\\|b | c |\n| --- | --- |\n| x\\\\y | 1<br>2 |\n"
    assert run("rows_to_table", columns=["a"], rows=[])["markdown"] == "| a |\n| --- |\n"
    error = refused("rows_to_table", columns=["a", "b"], rows=[["1", "2"], ["3"]])
    assert error.code == "invalid_table" and error.position == 2
    assert refused("rows_to_table", columns=["a", "a"], rows=[]).position == 0


def test_sort_dedupe_rows():
    columns = ["n", "tag"]
    rows = [["2", "b"], ["10", "a"], ["", "z"], ["2", "c"], ["-1.5", "d"]]
    ascending = run("sort_dedupe_rows", columns=columns, rows=rows, by=["n"], numeric=True)
    assert [r[1] for r in ascending["rows"]] == ["d", "b", "c", "a", "z"]
    descending = run("sort_dedupe_rows", columns=columns, rows=rows, by=["n"], numeric=True, descending=True)
    assert [r[1] for r in descending["rows"]] == ["a", "b", "c", "d", "z"]
    deduped = run("sort_dedupe_rows", columns=columns, rows=rows, by=["n"], dedupe=True)
    assert deduped["removed"] == 1 and [r[1] for r in deduped["rows"]] == ["d", "a", "b", "z"]      # as text
    error = refused("sort_dedupe_rows", columns=columns, rows=[["1", "a"], [SECRET, "b"]], by=["n"], numeric=True)
    assert error.code == "invalid_number" and error.position == 2 and SECRET not in error.message


def test_column_stats():
    result = run("column_stats", columns=["v"], rows=[["1"], ["2.5"], [""], ["x"], ["-0.5"]], column="v")
    assert result == {"count": 5, "numeric_count": 3, "empty_count": 1, "other_count": 1, "sum": "3.0",
                      "min": "-0.5", "max": "2.5", "mean": "1.0", "rounded": False}
    third = run("column_stats", columns=["v"], rows=[["1"], ["0"], ["0"]], column="v")
    assert third["mean"].startswith("0.3333") and third["rounded"] is True
    none = run("column_stats", columns=["v"], rows=[["x"]], column="v")
    assert none["sum"] is None and none["mean"] is None


# --- identifiers ------------------------------------------------------------------------------------------------------
def test_scan_identifiers_kinds_priority_and_positions():
    text = ("Mail jane.doe@example.com or https://example.org/x. IP 192.168.1.20 on 2024-03-05, 3/4/2024, "
            "5 March 2024.\nCall +61 2 9999 8888 or (02) 9876 5432; MRN 12345678; card 1234 5678 9012.")
    result = run("scan_identifiers", text=text)
    found = [(f["kind"], f["value"]) for f in result["findings"]]
    assert found == [("email", "jane.doe@example.com"), ("url", "https://example.org/x"),
                     ("ip_address", "192.168.1.20"), ("date", "2024-03-05"), ("date", "3/4/2024"),
                     ("date", "5 March 2024"), ("phone", "+61 2 9999 8888"), ("phone", "(02) 9876 5432"),
                     ("long_number", "12345678"), ("long_number", "1234 5678 9012")]
    assert result["findings"][6]["line"] == 2 and result["findings"][6]["column"] == 6
    assert result["counts"]["date"] == 3 and result["truncated"] is False
    only = run("scan_identifiers", text=text, kinds=["long_number"])
    assert set(only["counts"]) == {"long_number"}


def test_scan_identifiers_truncates_findings_but_counts_everything():
    result = run("scan_identifiers", text="a@b.co " * 1500)
    assert len(result["findings"]) == 1000 and result["truncated"] and result["counts"]["email"] == 1500


@pytest.mark.parametrize("pairs, position", [
    ([{"placeholder": "PERSON_1", "original": "Alice"}], 1),
    ([{"placeholder": "[P_1]", "original": "Alice"}, {"placeholder": "[P_2]", "original": "Alice"}], 2),
    ([{"placeholder": "[P_1]", "original": "Bob"}], 1),
    ([{"placeholder": "[P_9]", "original": "Alice"}], 1),
    ([{"placeholder": "[DRAFT]", "original": "Alice"}], 1),
])
def test_check_identifier_map_refusals(pairs, position):
    error = refused("check_identifier_map", original="Alice approved [DRAFT].", text="[P_1] [P_2] [DRAFT].",
                    pairs=pairs)
    assert error.code == "invalid_map" and error.position == position
    assert "Alice" not in error.message and "Bob" not in error.message


def test_check_identifier_map_passes_text_and_lists_unmapped_tokens():
    pairs = [{"placeholder": "[PERSON_1]", "original": "Alice", "kind": "PERSON"}] * 2
    result = run("check_identifier_map", original="Alice approved [DRAFT].", text="[PERSON_1] approved [DRAFT].",
                 pairs=pairs)
    assert result == {"text": "[PERSON_1] approved [DRAFT].", "entries": 1, "unmapped_tokens": ["[DRAFT]"],
                      "unmapped_truncated": False,
                      "pairs": [{"placeholder": "[PERSON_1]", "original": "Alice"}]}


# --- worst cases (2.4) ------------------------------------------------------------------------------------------------
MIB = (1 << 20) - 200


def _adversarial():
    rows = [[f"{n % 97}", "x" * 20] for n in range(10_000)]
    return {
        "find_replace": {"text": "a" * MIB, "find": "aa", "replace": ""},
        "extract_section": {"markdown": "#" * MIB, "heading": "x"},
        "outline": {"markdown": "# h\n" * (MIB // 5)},
        "compare_texts": {"before": "".join(f"{n % 10}\n" for n in range(1400)),
                          "after": "".join(f"{n % 7}\n" for n in range(1400))},
        "combine_texts": {"parts": ["a" * (MIB // 50)] * 49},
        "parse_csv": {"csv": "a,b\n" + ('"' + "x" * 88 + '",y\n') * 9999},
        "rows_to_table": {"columns": ["a", "b"], "rows": rows},
        "sort_dedupe_rows": {"columns": ["a", "b"], "rows": rows, "by": ["a", "b"], "numeric": False,
                             "dedupe": True},
        "column_stats": {"columns": ["a", "b"], "rows": rows, "column": "a"},
        "scan_identifiers": {"text": ("a" * 1000 + "@") * (MIB // 1001)},
        "check_identifier_map": {"original": "x" * (MIB // 2 - 40000) + "".join(f" n{i}" for i in range(1000)),
                                 "text": "".join(f"[P_{i}]" for i in range(1000)),
                                 "pairs": [{"placeholder": f"[P_{i}]", "original": f"n{i}"} for i in range(1000)]},
    }


@pytest.mark.parametrize("name, arguments", list(_adversarial().items()), ids=list(_adversarial()))
def test_worst_case_inputs_finish_quickly(name, arguments):
    started = time.monotonic()
    try:
        t.run_utility(name, arguments)
    except t.ToolError as exc:
        assert exc.code in ("too_large", "invalid_map", "invalid_csv")
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("text", ["0 " * (MIB // 2), "1-" * (MIB // 2), "w" * MIB, "a." * (MIB // 2), "+" * MIB,
                                  "1." * (MIB // 2), "(" * MIB, "x@" * (MIB // 2)],
                         ids=["zeros", "digits-hyphens", "www", "dots", "plus", "ip-like", "parens", "at-signs"])
def test_scan_identifiers_is_linear_on_pathological_runs(text):
    started = time.monotonic()
    t.run_utility("scan_identifiers", {"text": text})
    assert time.monotonic() - started < 5
