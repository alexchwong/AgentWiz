"""The block-library tutorial topic (starter blocks addendum, 5.4): adds `find_and_replace` and `calculate` to the
tutorial's sandbox workspace through the ordinary installer (plan, then add with the plan's confirmations). Both
are Tool blocks: no model, no destination. Writes only inside the tutorial's temporary workspace."""
from __future__ import annotations

KEYS = ("find_and_replace", "calculate")


def ensure_library_fixture(state) -> None:
    installer = state.block_library
    missing = [key for key in KEYS if not (state.workspace.path / "blocks" / key).exists()]
    if not missing:
        return
    planned = installer.plan({"entries": [{"key": key} for key in missing]})["entries"]
    installer.add({"entries": [{"key": p["key"], "as": p["as"], "expected_plan": p["expected_plan"],
                                "confirmations": p["required_confirmations"], "binding": p["binding"]}
                               for p in planned if p["outcome"] == "planned"]})
