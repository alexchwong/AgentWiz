# AgentWiz 0.1.4 — hand-over

Built from *AgentWiz 0.1.4 — Technical Specification*, version 5. Baseline: AgentWiz `devel` at `4a41304`
(tree equals the v0.1.3 release). Nothing was committed or pushed to GitHub.

Apply: unzip over a working copy of `devel` at `4a41304`. The zip holds every new and changed file at its
repository path, plus this file and `DELETIONS.txt`. This file replaces the 0.1.3 hand-over.

## Checkpoint log (one row per gate; appended as the build goes)

Gate = `python -m pytest -q -m "not live"`, no failures, errors or skips.

| Date | Phase | Interpreter | Result |
| --- | --- | --- | --- |
| 2026-11-03 | 0 baseline (unchanged devel) | 3.13.18 | 350 passed, 8 deselected, 46.1 s |
| 2026-11-03 | 0 baseline | 3.10.20 | 350 passed, 8 deselected, 37.6 s |
| 2026-11-03 | A definitions (#89-#93, #106, #107 + block route tests) | 3.13.18 | 466 passed, 8 deselected, 47.2 s |
| 2026-11-03 | A definitions | 3.10.20 | 466 passed, 8 deselected, 40.6 s |
| 2026-11-03 | B block runs (#94-#105, #114, #99) | 3.13.18 | 560 passed, 8 deselected, 58.6 s |
| 2026-11-03 | B block runs | 3.10.20 | 560 passed, 8 deselected, 49.7 s |
| 2026-11-03 | C page (#108-#111, #86) | 3.13.18 | 564 passed, 8 deselected, 63.9 s |
| 2026-11-03 | C page | 3.10.20 | 564 passed, 8 deselected, 53.9 s |
| 2026-11-03 | D tutorial (#112, #113) | 3.13.18 | 569 passed, 8 deselected, 72.5 s |
| 2026-11-03 | D tutorial | 3.10.20 | 569 passed, 8 deselected, 63.2 s |
| 2026-11-03 | E finish (version 0.1.4, README, DEVEL, NEWS, smoke S13/S14) | 3.13.18 | 569 passed, 8 deselected, 71.3 s |
| 2026-11-03 | E finish | 3.10.20 | 569 passed, 8 deselected, 62.3 s |

## Smoke result

`python scripts/build_release.py --smoke --no-isolation --modelspell-wheel modelspell-0.3.0-py3-none-any.whl`, run on 3.13.18 in
a throwaway copy of the tree with a throwaway **local** commit (never pushed; nothing touched the real repository):
RC1, RC2 and RC3 ok, `pip check` ok, `smoke ok: AgentWiz 0.1.4 (modelSpell 0.3.0)`, i.e. S1–S14 pass against the installed
wheel. The wheel was `agentwiz-0.1.4-py3-none-any.whl`, sha256 `41467864d301bc129bb3677f39fe075d61019df2782ff0ab1d56da21732cce46`
(a throwaway build; rebuild your own release from your commit). The first attempt failed because a wrapped comment line in
`scripts/smoke_installed.py` had lost its `#`; the shipped file is fixed. The suite does not execute that script, which is
why the gates did not catch it. The suite was not re-run after that one-character fix.

The optional live check (`-m live`) was **not** run: the workspace has no reachable model backend.

## Departures from the specification

1. Spec 20.3 says `test_attempts.py` and `test_model_step.py` stay unchanged, but 9.3 makes the attempt call
   `checks.check_answer_value`. Two existing tests injected failures by patching `checks.check_answer`
   (`test_model_step.py::test_a_failing_check_is_contract_error_never_a_pass`,
   `test_attempts.py::test_stop_between_the_end_point_and_the_commit`). Only the patch target changed to
   `check_answer_value`; assertions are untouched.
2. Fill provenance for a whole-string typed fill in tool arguments hashes `encode_exact(value)`; text fills hash
   the UTF-8 of the inserted text (spec 8.6 does not say which for a string value).
3. A block's revision is kept when only file metadata changed (content hashes and named-contract revisions decide).
4. Problem-code precedence when several kinds occur: `invalid` > `unreadable` > `unusable`; `newer_format` alone.
5. `/api/config` does not register as a workspace writer: `ProcessState.apply_config` never touches the workspace, and
   spec 10.10 exempts a method that does not write. Every other route in the gate table does register.
6. After shutdown begins, a block run's `final` and `save` events are dropped (delivery is interrupted by design,
   10.10 step 2); the run still commits and saves.
7. `UIServer.grace_s` (default 5 s) was added so the tutorial's bounded Exit waits can be lowered in tests (#113); the
   spec's `server_close(*, wait=True)` signature is unchanged and `grace_s` is a plain attribute.
8. Tutorial topics `model-block`, `tool-block` and `script-block` set `seed_contract` (the example blocks name the saved
   `fields` contract); the spec's table does not say.
9. `Tutorial.shutdown_all()` is kept as specified and is built from two halves, `begin_shutdown()` (closing flag, active
   session closed, pending taken) and `finish(pending)` (drain, close, remove), so `UIServer.server_close` can order them
   around its own state's drain (14.5).
10. `build_release.py --no-isolation` needs `hatchling>=1.27` installed in the building interpreter (its own error says so);
    this is environment setup, not a code change.

## Obsolete files

None. `DELETIONS.txt` is empty.

## Next steps

1. Unzip over `devel` at `4a41304`, review `git diff`, commit yourself.
2. Walk the tutorial's three new topics once in a real browser (the headless gate drives them against the stand-ins only).
3. Run `-m live` against a real backend if you want the optional live check.
4. Known small gap: attempt rows that the Blocks tab fills from polling (after Reattach) lack `data-repair-reason`, because the
   polling summary does not carry it; streamed rows have it.
