"""The live Stop check's judgement function on hand-built records: techspec 0.1.3 test #69 (m)."""
import pytest

from live_stop import judge_stop


def rec(**kw):
    base = {"outcome": "stopped", "cancel_reason": "user_stop", "stop_mode": "immediate", "sent": True,
            "stop_requested_at": "2026-11-02T00:00:00.000Z", "error": None}
    return {**base, **kw}


@pytest.mark.parametrize("answer, record, seconds, verdict", [
    ("stopping", rec(sent=False), 0.2, "pass_pre_send"),
    ("stopping", rec(sent=True), 0.2, "pass_sent"),
    ("already_finished", rec(outcome="complete", stop_mode=None, cancel_reason=None, sent=None), 0.1, "inconclusive"),
    ("stopping", rec(outcome="complete", stop_mode=None, cancel_reason=None, sent=None), 0.1, "inconclusive"),
    ("stopping", rec(sent=None, error={"description": "BackendError: the backend reported a failure (HTTP 500)."}),
     0.1, "inconclusive"),
    ("stopping", rec(sent=None), 0.1, "fail"),                                   # null without the error rule 3 requires
    ("stopping", rec(), 9.0, "fail"),                                            # later than the bound
    ("stopping", None, 0.0, "fail"),                                             # no final
    ("stopping", {"outcome": "stopped"}, 0.1, "fail"),                           # a missing field
    ("stopping", rec(stop_mode=None), 0.1, "fail"),
    ("nonsense", rec(), 0.1, "fail"),
])
def test_judge_stop(answer, record, seconds, verdict):
    got, report = judge_stop(answer, record, seconds)
    assert got == verdict and report
    if verdict == "pass_pre_send":
        assert "no sent request was interrupted" in report                        # does not claim an interruption
    if verdict == "pass_sent":
        assert "interrupted" in report and "no sent" not in report
