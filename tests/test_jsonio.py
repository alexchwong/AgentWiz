"""The one JSON writer: techspec 13.3 test #15."""
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
