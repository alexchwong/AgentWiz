"""The live Stop check's judgement, as a pure function (techspec 0.1.3, 18.5; offline test #69 m).

Given the Stop route's answer, the committed record and the seconds from the answer to the `final`
event, it says what the run proved. No model, no delay, no network."""
from __future__ import annotations

BOUND_SECONDS = 5.0
REQUIRED = ("outcome", "cancel_reason", "stop_mode", "sent", "stop_requested_at", "error")


def judge_stop(stop_answer: str, record: dict | None, seconds: float) -> tuple[str, str]:
    """Returns (verdict, report). Verdicts: "pass_pre_send" and "pass_sent" (cancellation observed; reported
    differently), "inconclusive" (cancellation of an active call was not observed), "fail" (a protocol or
    cancellation fault)."""
    if record is None:
        return "fail", "no final event arrived"
    missing = [k for k in REQUIRED if k not in record]
    if missing:
        return "fail", "the record lacks " + ", ".join(missing)
    if stop_answer == "already_finished":
        return "inconclusive", "the attempt had already finished when Stop arrived"
    if stop_answer != "stopping":
        return "fail", f"unexpected Stop answer {stop_answer!r}"
    if record["outcome"] != "stopped":
        return "inconclusive", f"the request committed {record['outcome']!r} before Stop took effect"
    if record["stop_mode"] != "immediate" or record["cancel_reason"] != "user_stop":
        return "fail", "a stopped record without stop_mode 'immediate' and cancel_reason 'user_stop'"
    if seconds > BOUND_SECONDS:
        return "fail", f"the stopped outcome arrived {seconds:.1f}s after the Stop answer (bound {BOUND_SECONDS:.0f}s)"
    if record["sent"] is None:
        if record["error"] is not None:
            return "inconclusive", ("an error came first and Stop landed before the end point: "
                                    f"{record['error'].get('description', 'an error')}")
        return "fail", "sent is null without the error that rule 3 requires"
    if record["sent"] is False:
        return "pass_pre_send", "cancelled before anything was sent; no sent request was interrupted"
    return "pass_sent", "a sent request was interrupted"
