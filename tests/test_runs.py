"""The cancellation token and the attempt store: techspec 0.1.3 tests #66 and #73 (Part B, 8.3, 8.9)."""
import threading

import pytest

from agentwiz import jsonio, runs
from conftest import wait_until


def record(run, number, *, outcome="complete", check="fail", text="x", size=0, parent=None):
    """A committed record's shape, as much of it as the store reads."""
    return {"id": runs.attempt_id(run.run_id, number), "run_id": run.run_id, "step_id": "request", "attempt": number,
            "parent_id": parent, "outcome": outcome, "cancel_reason": None, "stop_requested_at": None,
            "ended_at": runs.utc_now(), "check": {"overall": check} if check else None,
            "result": {"text": text}, "pad": "p" * size}


def new_run(store, **kw):
    return store.new_run(source="chat", save_runs=kw.get("save_runs", False), system="", prompt="hi", contract=None)


def run_one(store, run, number, **kw):
    """Start and commit one attempt; returns its ID."""
    aid = store.start(run, number, {"id": runs.attempt_id(run.run_id, number)})
    store.end_point(aid)
    store.commit(aid, record(run, number, **{k: v for k, v in kw.items() if k != "saving"}), kw.get("saving", False))
    return aid


# 66 ---------------------------------------------------------------------------------------------
def test_cancel_token():
    token = runs.CancelToken()
    order = []
    token.on_cancel(lambda: order.append("a"))
    token.on_cancel(lambda: order.append("b"))
    assert token.cancelled is False and token.cancelled_at is None
    assert token.cancel() is True
    first = token.cancelled_at
    assert first is not None and token.cancelled is True
    assert token.cancel() is False and token.cancelled_at == first
    assert order == ["a", "b"]                                       # once each, in order
    token.on_cancel(lambda: order.append("late"))                    # after cancellation: runs at once
    assert order == ["a", "b", "late"]
    token.cancel()
    assert order == ["a", "b", "late"]

    # An action that calls cancel() does not deadlock; an action that raises does not stop the next.
    token = runs.CancelToken()
    seen = []
    token.on_cancel(lambda: seen.append(token.cancel()))
    token.on_cancel(lambda: 1 / 0)
    token.on_cancel(lambda: seen.append("after the failure"))
    assert token.cancel() is True
    assert seen == [False, "after the failure"]

    # 16 threads cancelling at once: one true, each action once.
    token = runs.CancelToken()
    ran, results, barrier = [], [], threading.Barrier(16)
    token.on_cancel(lambda: ran.append(1))

    def go():
        barrier.wait()
        results.append(token.cancel())

    threads = [threading.Thread(target=go) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results.count(True) == 1 and len(results) == 16 and ran == [1]


def test_cancel_token_publish_and_dispatch_are_separate():
    token = runs.CancelToken()
    entered, proceed, ran = threading.Event(), threading.Event(), []

    def blocking_action():
        entered.set()
        assert proceed.wait(10)
        ran.append("blocking")

    token.on_cancel(blocking_action)
    token.on_cancel(lambda: ran.append("second"))
    actions = token._publish()
    assert token.cancelled is True and token._publish() is None and ran == []      # published, nothing ran
    seen = {}
    other = threading.Thread(target=lambda: seen.setdefault("cancel", token.cancel()))
    other.start()
    other.join(10)
    assert seen["cancel"] is False                                                  # already cancelled: returns at once
    token.on_cancel(lambda: ran.append("registered meanwhile"))                    # runs now, exactly once
    assert ran == ["registered meanwhile"]
    dispatcher = threading.Thread(target=token._dispatch, args=(actions,))
    dispatcher.start()
    assert entered.wait(10)
    token.on_cancel(lambda: ran.append("while dispatching"))                       # no lock is held by the dispatcher
    assert token.cancelled is True and ran == ["registered meanwhile", "while dispatching"]
    proceed.set()
    dispatcher.join(10)
    assert ran == ["registered meanwhile", "while dispatching", "blocking", "second"]


def test_a_run_has_one_handle_registered_with_its_token():
    store = runs.AttemptStore()
    run = new_run(store)
    assert run.handle.cancelled is False
    assert run.token.cancel() is True
    assert run.handle.cancelled is True                                             # the token's stop action


# 73 ---------------------------------------------------------------------------------------------
@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(runs, "RECORD_LIMIT", 3)
    monkeypatch.setattr(runs, "SUMMARY_LIMIT", 6)


def test_eviction_leaves_summaries_then_nothing(small):
    store = runs.AttemptStore()
    ids = []
    for _ in range(8):
        run = new_run(store)
        ids.append(run_one(store, run, 1))
    held = [store.record_of(i) is not None for i in ids]
    assert held == [False] * 5 + [True] * 3                                         # the latest 3 records
    # Summaries: the latest 6 settled; the two oldest are gone altogether.
    for i in ids[:2]:
        with pytest.raises(runs.NotHeld):
            store.envelope(i)
        with pytest.raises(runs.NoSuchAttempt):
            store.stop(i)
    evicted = store.envelope(ids[3])
    assert evicted["state"] == "committed" and evicted["record_held"] is False and "record" not in evicted
    assert evicted["summary"]["outcome"] == "complete" and evicted["summary"]["record_held"] is False
    assert store.envelope(ids[7])["record_held"] is True
    with pytest.raises(runs.RepairRefused) as caught:                               # repairing an evicted attempt
        store.admit_repair(ids[3])
    assert caught.value.status == 410 and caught.value.code == "not_held"
    with pytest.raises(runs.NoSuchAttempt):
        store.admit_repair(ids[0])


def test_record_over_the_byte_budget_is_not_kept(monkeypatch):
    monkeypatch.setattr(runs, "RECORD_BYTES", 2000)
    store = runs.AttemptStore()
    run = new_run(store)
    aid = run_one(store, run, 1, size=5000)
    assert store.record_of(aid) is None
    assert store.envelope(aid)["record_held"] is False
    small_run = new_run(store)
    kept = run_one(store, small_run, 1)
    assert store.record_of(kept) is not None


def test_byte_budget_evicts_oldest_first(monkeypatch):
    size = len(jsonio.encode(record(type("R", (), {"run_id": "0" * 12}), 1, size=600)))
    monkeypatch.setattr(runs, "RECORD_BYTES", size * 2 + 10)
    store = runs.AttemptStore()
    ids = [run_one(store, new_run(store), 1, size=600) for _ in range(3)]
    assert [store.record_of(i) is not None for i in ids] == [False, True, True]


def test_running_attempts_are_never_evicted(small):
    store = runs.AttemptStore()
    run = new_run(store)
    running = store.start(run, 1, {"id": runs.attempt_id(run.run_id, 1)})
    for _ in range(10):
        run_one(store, new_run(store), 1)
    assert store.envelope(running)["state"] == "running"
    assert store.stop(running) == "stopping"


def test_oversized_record_with_a_pending_save_keeps_run_and_envelope(monkeypatch, small):
    monkeypatch.setattr(runs, "RECORD_BYTES", 2000)
    store = runs.AttemptStore()
    run = new_run(store, save_runs=True)
    aid = run_one(store, run, 1, size=5000, saving=True)
    assert store.run_view(run.run_id) is not None                                   # kept: a save is pending
    env = store.envelope(aid)
    assert env["save_status"] == "pending" and env["record_held"] is False
    with pytest.raises(runs.RunActive):
        store.reserve_delete(run.run_id)                                            # deletion refused meanwhile
    for _ in range(10):                                                             # summary pressure from others
        run_one(store, new_run(store), 1)
    assert store.envelope(aid)["save_status"] == "pending"                          # the envelope survived
    store.settle_save(aid, "saved", file="runs/x/request-1.json")
    store.settle_save(aid, "failed", reason="a second settlement changes nothing")
    env = store.envelope(aid)
    assert env["save_status"] == "saved" and env["file"] == "runs/x/request-1.json"
    assert store.run_view(run.run_id) is None                                       # nothing held: metadata released


def test_evicted_parent_does_not_take_a_running_repair_with_it(small):
    store = runs.AttemptStore()
    run = new_run(store)
    parent = run_one(store, run, 1)
    _run, _rec, number = store.admit_repair(parent)
    repair = store.start(run, number, {"id": runs.attempt_id(run.run_id, number)})
    for _ in range(8):                                                              # evict the parent's record
        run_one(store, new_run(store), 1)
    assert store.record_of(parent) is None
    assert store.stop(repair) == "stopping"                                         # Stop and polling still work
    assert store.envelope(repair)["state"] == "running"
    assert store.run_view(run.run_id)["running"] == number


def test_run_metadata_is_released_when_nothing_needs_it():
    store = runs.AttemptStore()
    run = new_run(store)
    assert store.run_view(run.run_id) is not None
    aid = run_one(store, run, 1)
    assert store.run_view(run.run_id)["held"] == [1]
    assert store.record_of(aid) is not None


def test_a_long_repair_chain_keeps_numbers_monotonic_and_metadata_bounded(monkeypatch):
    monkeypatch.setattr(runs, "RECORD_LIMIT", 3)
    monkeypatch.setattr(runs, "SUMMARY_LIMIT", 5)
    store = runs.AttemptStore()
    run = new_run(store)
    ids, parent = [run_one(store, run, 1)], None
    for expected in range(2, 21):
        _run, rec, number = store.admit_repair(ids[-1])
        assert number == expected                                                    # no gap and no reuse
        ids.append(run_one(store, run, number, parent=rec["id"]))
    for gone in ids[:15]:
        with pytest.raises(runs.NotHeld):
            store.envelope(gone)                                                     # 410 for the early ones
    assert store.record_of(ids[-1]) is not None
    view = store.run_view(run.run_id)
    assert view["next_attempt"] == 21 and view["latest"] == 20 and view["running"] is None
    assert view["attempts"] == [16, 17, 18, 19, 20]                                  # no entry for a forgotten attempt
    assert view["held"] == [18, 19, 20]
    store.admit_repair(ids[-1], reserve=False)                                       # the latest stays repairable


def test_repair_admission_refusals():
    store = runs.AttemptStore()
    run = new_run(store)
    first = run_one(store, run, 1)
    _r, _rec, n = store.admit_repair(first)
    with pytest.raises(runs.RepairRefused) as caught:                                # a repair is already admitted
        store.admit_repair(first)
    assert (caught.value.status, caught.value.code) == (409, "not_latest")
    store.abandon_run(run, n)                                                        # a refused 503 gives its number back
    assert store.run_view(run.run_id)["next_attempt"] == 2
    _r, _rec, again = store.admit_repair(first)
    assert again == 2
    store.abandon_run(run, again)
    for outcome, check in (("complete", "pass"), ("complete", "incomplete"), ("complete", "contract_error"),
                           ("empty", "fail"), ("error", None), ("stopped", None)):
        other = run_one(store, new_run(store), 1, outcome=outcome, check=check)
        with pytest.raises(runs.RepairRefused) as caught:
            store.admit_repair(other)
        assert (caught.value.status, caught.value.code) == (409, "not_repairable"), (outcome, check)
    stopped = new_run(store)
    sid = run_one(store, stopped, 1)
    stopped.token.cancel()
    with pytest.raises(runs.RepairRefused) as caught:
        store.admit_repair(sid)
    assert (caught.value.status, caught.value.code) == (409, "run_stopped")


def test_concurrent_repair_admission_gives_one_winner():
    store = runs.AttemptStore()
    run = new_run(store)
    first = run_one(store, run, 1)
    barrier, outcomes = threading.Barrier(8), []

    def go():
        barrier.wait()
        try:
            outcomes.append(store.admit_repair(first)[2])
        except runs.RepairRefused as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(map(str, outcomes)) == ["2"] + ["not_latest"] * 7


def test_stop_decisions():
    store = runs.AttemptStore()
    run = new_run(store)
    aid = store.start(run, 1, {"id": runs.attempt_id(run.run_id, 1)})
    fired = []
    run.token.on_cancel(lambda: fired.append(1))
    assert store.stop(aid) == "stopping" and run.token.cancelled and run.handle.cancelled and fired == [1]
    first = store.envelope(aid)["stop_requested_at"]
    assert first is not None
    assert store.stop(aid) == "stopping" and store.envelope(aid)["stop_requested_at"] == first and fired == [1]
    end = store.end_point(aid)
    assert end.cancelled is True and end.stop_requested_at == first
    assert store.stop(aid) == "already_finished"
    with pytest.raises(runs.NoSuchAttempt):
        store.stop(runs.attempt_id("f" * 12, 1))


def test_deletion_reservation_and_settlement():
    store = runs.AttemptStore()
    run = new_run(store)
    run_one(store, run, 1)
    assert store.reserve_delete("f" * 12) is None                                    # not in memory
    reserved = store.reserve_delete(run.run_id)
    assert reserved is run and store.history_state(run) == runs.HISTORY_DELETING
    with pytest.raises(runs.RunActive):
        store.reserve_delete(run.run_id)                                             # one reservation at a time
    with pytest.raises(runs.RepairRefused) as caught:
        store.admit_repair(runs.attempt_id(run.run_id, 1))
    assert (caught.value.status, caught.value.code) == (409, "run_active")
    store.settle_delete(run, ok=False, changed=False)
    assert store.history_state(run) == runs.HISTORY_INTACT                           # restored
    assert store.run_view(run.run_id)["prior_history_state"] is None                 # discarded at settlement
    store.reserve_delete(run.run_id)
    store.settle_delete(run, ok=False, changed=True)
    assert store.history_state(run) == runs.HISTORY_INCOMPLETE
    store.reserve_delete(run.run_id)                                                 # an explicit retry is permitted
    store.settle_delete(run, ok=False, changed=False)
    assert store.history_state(run) == runs.HISTORY_INCOMPLETE                       # a failed retry repairs nothing
    store.reserve_delete(run.run_id)
    store.settle_delete(run, ok=True, changed=True)
    assert store.history_state(run) == runs.HISTORY_DELETED
