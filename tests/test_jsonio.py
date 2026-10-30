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
