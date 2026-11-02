"""The one JSON writer: techspec 0.1.1 13.3 test #15; the exact-number reader and writer: 0.1.2 #37."""
import copy
import json
import math

from agentwiz.jsonio import encode, encode_line


def _strict(data: bytes):
    def reject(name):
        raise AssertionError(f"non-standard constant {name}")
    return json.loads(data, parse_constant=reject)


def test_encode_replaces_nonfinite_and_lists_locations():
    payload = {
        "a": math.nan,
        "nested": {"list": [1, math.inf, {"deep": -math.inf}], "tuple": (0.5, math.nan)},
        "odd key": {"x": math.inf},
        "big": 2 ** 70,
        "small": 0.1,
    }
    before = copy.deepcopy(payload)
    data = encode_line(payload)
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    out = _strict(data)
    assert out["a"] is None
    assert out["nested"]["list"] == [1, None, {"deep": None}]
    assert out["nested"]["tuple"] == [0.5, None]
    assert out["odd key"]["x"] is None
    assert out["nonfinite_replaced"] == ["a", "nested.list[1]", "nested.list[2].deep", "nested.tuple[1]",
                                         '["odd key"].x']
    assert out["big"] == 2 ** 70 and out["small"] == 0.1
    assert repr(payload) == repr(before)  # not mutated (repr, because nan != nan)
    assert "nonfinite_replaced" not in _strict(encode({"x": 1.5, "y": [1, "s", None, True]}))


# 37 (techspec 0.1.2) ---------------------------------------------------------------------------
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from agentwiz.jsonio import decode_exact, encode_exact  # noqa: E402


def test_exact_numbers():
    for text in ("9007199254740993", "0.10000000000000000001", "1e400", "-0.0"):
        value = decode_exact(text)
        assert not isinstance(value, float)
        again = decode_exact(encode_exact(value))
        assert again == value and str(again).startswith("-") == text.startswith("-")
    assert decode_exact("0.10000000000000000001") != Decimal("0.1")
    assert decode_exact('{"n": 9007199254740993}')["n"] == 2 ** 53 + 1
    for bad in ("NaN", "[Infinity]", "-Infinity", '{"a": 1, "a": 2}', "1" * 5000):
        with pytest.raises(ValueError):
            decode_exact(bad)
    for value in (Decimal("NaN"), Decimal("Infinity"), math.inf, math.nan):
        with pytest.raises(ValueError):
            encode_exact(value)
    with pytest.raises(TypeError):
        encode({"x": Decimal("1.5")})


# 61 (techspec 0.1.3, R4-31): the error contract of decode_exact and the file helpers -------------
import os  # noqa: E402
import stat  # noqa: E402

from agentwiz import jsonio  # noqa: E402
from agentwiz.workspace import write_atomic_bytes  # noqa: E402


def test_decode_exact_raises_only_value_errors_with_locations():
    with pytest.raises(jsonio.JSONSyntax) as caught:
        decode_exact('{"a": 1,\n  "b": }')
    assert (caught.value.line, caught.value.column) == (2, 8)
    assert isinstance(caught.value, ValueError) and "line 2, column 8" in str(caught.value)

    # A number that cannot be held is ExactLimit, located by re-scanning; the message never holds the digits.
    with pytest.raises(jsonio.ExactLimit) as caught:
        decode_exact('{"a": 1,\n "b": [2, ' + "9" * 5000 + "]}")
    assert (caught.value.line, caught.value.column) == (2, 11)
    assert "99999" not in str(caught.value)
    with pytest.raises(jsonio.ExactLimit) as caught:
        decode_exact("[1e9223372036854775808]")
    assert (caught.value.line, caught.value.column) == (1, 2) and "9223372036854775808" not in str(caught.value)
    assert decode_exact("[" + "9" * 4300 + "]")[0] == int("9" * 4300)              # exactly at the limit is fine
    with pytest.raises(jsonio.ExactLimit):
        decode_exact("[" + "9" * 4301 + "]")

    # Everything else is a plain ValueError naming a rule: constants, repeated keys, depth, non-text.
    for text in ("NaN", "[Infinity]", '{"a": 1, "a": 2}', "[" * 100000 + "]" * 100000):
        with pytest.raises(ValueError) as caught:
            decode_exact(text)
        assert type(caught.value) is ValueError, text
    with pytest.raises(ValueError):
        decode_exact(b"{}")                                                      # type: ignore[arg-type]


def test_decode_file_exact():
    assert decode_file_exact_ok(b'\xef\xbb\xbf{"n": 1.50}') == {"n": Decimal("1.50")}      # a byte-order mark is allowed
    for data, message in ((b"\xff\xfe{}", "the file is not UTF-8 text"), (b"[1]", "the top level must be a JSON object"),
                          (b'"x"', "the top level must be a JSON object")):
        with pytest.raises(ValueError, match=message):
            jsonio.decode_file_exact(data)
    with pytest.raises(jsonio.JSONSyntax):
        jsonio.decode_file_exact(b"{ nope")


def decode_file_exact_ok(data):
    return jsonio.decode_file_exact(data)


def test_encode_file_exact_and_record_file():
    payload = {"format_version": 1, "n": Decimal("0.10000000000000000001"), "big": 2 ** 70, "s": "é\n", "z": Decimal("-0.0")}
    data = jsonio.encode_file_exact(payload)
    assert data.isascii() and data.endswith(b"}\n") and data.startswith(b'{\n  "format_version": 1,')
    assert b"0.10000000000000000001" in data and b'"\\u00e9\\n"' in data
    assert jsonio.decode_file_exact(data) == payload and str(jsonio.decode_file_exact(data)["z"]) == "-0.0"
    for bad in (Decimal("NaN"), math.inf):
        with pytest.raises(ValueError):
            jsonio.encode_file_exact({"x": bad})
    with pytest.raises(TypeError):
        jsonio.encode_file_exact([1])                                            # type: ignore[arg-type]

    record = jsonio.encode_record_file({"a": 1, "b": math.nan, "c": {"d": [math.inf]}})
    assert record.isascii() and b'"b": null' in record
    assert json.loads(record)[jsonio.RESERVED] == ["b", "c.d[0]"]
    with pytest.raises(TypeError):
        jsonio.encode_record_file({"x": Decimal("1.5")})
    with pytest.raises(TypeError):
        jsonio.encode_record_file({jsonio.RESERVED: []})


def test_write_atomic_bytes(tmp_path):
    write_atomic_bytes(tmp_path, "one.json", b'{"a": 1}\n')
    target = tmp_path / "one.json"
    assert target.read_bytes() == b'{"a": 1}\n' and stat.S_IMODE(os.stat(target).st_mode) == 0o600
    write_atomic_bytes(tmp_path, "one.json", b"{}\n")                             # replaces whole, never appends
    assert target.read_bytes() == b"{}\n"
    assert [p.name for p in tmp_path.iterdir()] == ["one.json"]                   # no temporary file is left behind
    with pytest.raises(OSError):
        write_atomic_bytes(tmp_path / "missing", "x.json", b"{}")
    assert [p.name for p in tmp_path.iterdir()] == ["one.json"]
