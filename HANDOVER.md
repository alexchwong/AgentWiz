# AgentWiz 0.1.3 — hand-over

Built from *AgentWiz 0.1.3 — Technical Specification*, version 4 (2 November 2026).
Baseline: AgentWiz `devel` at `fa345d3` ("macos fix"). Nothing was committed or pushed to GitHub; the
working copy's `.git` was left as cloned, and the release smoke ran on a throwaway local clone.

Apply: unzip over a working copy of `devel` at `fa345d3`. The zip holds every new and changed file at
its repository path, plus this file and `DELETIONS.txt`. **`DELETIONS.txt` is empty: no file is deleted.**
`.clinerules` is unchanged.

## What was built

| Phase | Content |
| --- | --- |
| P | Environment: Python 3.13 and 3.10 venvs, modelSpell 0.3.0 wheel (sha256 checked against the pin), `jsonschema`, Playwright 1.63.0 and its Chromium |
| 0 | Pin moved to modelSpell 0.3.0; the 0.1.2 suite on it |
| A | `jsonio` additions, `checks.py`, `contracts.py`, contract state and routes (#60–#65) |
| B | `runs.py`, `model_step.py`, `history.py`, `/api/complete` around `run_attempt`, Stop, polling, repair, history switch and routes, redaction (#66–#79) |
| D | The tabbed workbench, `tests/test_page.py` (#80–#88 and one extra: no script errors) |
| E | Smoke S11 and S12, stub backend models, `dev_setup.sh`, `install.sh --with-checks`, `tests.yml`, README, DEVEL, NEWS, version 0.1.3, two optional live checks |

## Checkpoint log

**Read this first.** This build ran across a context compaction. The verbatim output of Phase P, Phase 0
and the gates of Phases A, B and D was lost with it, so I cannot give their dates, commands and counts
as they were logged, and I will not reconstruct them. What I can give is (a) the state of the environment
re-probed at Phase E, (b) the two figures I still hold from earlier gates, and (c) the Phase E gates and
smoke, which were run after the compaction and are logged exactly. Every earlier phase's tests are in
the Phase E gate, which runs the whole suite, so no row depends on a lost log. This is a gap against the
specification's Section 21 ("the checkpoint log ... per phase"); the owner should decide whether it
matters (reported under E1).

### Phase P — environment, re-probed 2 November 2026 at Phase E

| Item | Result |
| --- | --- |
| Interpreters | 3.13.18 (`venv313`) and 3.10.20 (`venv310`), both from `uv` |
| Installed in both | modelSpell 0.3.0, jsonschema 4.26.0, referencing 0.37.0, Playwright 1.63.0, pytest 9.2.0, hatchling 1.33.0 |
| modelSpell wheel | `modelspell-0.3.0-py3-none-any.whl`, sha256 `0dddccebec3e062871f9ee6c45461415ad18c468a3f43b0bafa1803abfe27b27` (equals the pin), kept outside the working copy |
| Chromium | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers` holds build 1243; Playwright launches Chromium 153.0.8010.12 headless; nothing was downloaded |
| Clipboard probe | A page on `127.0.0.1` wrote `"xé 9007199254740993"` with `navigator.clipboard.writeText`; the context read back exactly that |
| Editable installs | Both venvs still report `AgentWiz 0.1.2` in package metadata (the editable install predates the version bump). Nothing reads it; `agentwiz --version` and the built wheel say 0.1.3 |
| `pip` | Added to `venv313` at Phase E (`uv pip install pip`), because `build_release.py` runs `python -m pip wheel`. Tooling in a scratch venv only; no project dependency changed |

### Gates

| Phase | Result I can still verify |
| --- | --- |
| 0 | Specification records 190 passed, 4 deselected for the unchanged 0.1.2 suite on modelSpell 0.3.0. I no longer hold my own log of this run |
| A, B | Logs lost. Covered by the Phase E gate |
| D | 346 passed on 3.13 before the extra test `test_page_raises_no_script_errors` was added (from my notes) |
| E | `python -m pytest -q -m "not live"` on **3.13.18 with `.[dev,checks]`: 347 passed, 8 deselected in 45.13 s; no failures, errors or skips**. Same command on **3.10.20: 347 passed, 8 deselected in 33.91 s; no skips** |

The 8 deselected are the live checks: `test_live_lmstudio`, `_openrouter`, `_llamaserver`, `_mcp`,
`_stop_openrouter`, `_checked_request_openrouter` and two parametrised "blocked until 0.2.0" skips. They skip without
their variables and are deselected by the gate.

Per-file counts on 3.13: attachments 16, attempts 24, checks 52, contracts 12, history 16, jsonio 6, live
judgement 11, mcp_client 47, mcp_ops 28, model_call 18, model_step 9, page 14, profiles 18, runs 15,
stub_mcp_server 3, task_settings 24, tool_forms 13, ui 13, workspace 8 (the spec's rows are parametrised
or split; none was dropped).

### Release build and smoke (Phase E)

`python scripts/build_release.py --smoke --no-isolation --modelspell-wheel <scratchpad>/wheels/modelspell-0.3.0-py3-none-any.whl`,
Python 3.13.18, on a **throwaway local commit** (a fresh clone of `devel` with the working tree committed;
never pushed; the working copy's `.git` is untouched):

```
building AgentWiz 0.1.3 from HEAD
RC1 ok
RC2 ok
RC3 ok
pip check ok
smoke ok: AgentWiz 0.1.3 (modelSpell 0.3.0) from <venv>/site-packages/agentwiz/__init__.py
sha256 70da75ff8f32d977843e54894f074565972d5620f803f0a0c38cde7f9fe4fe13   agentwiz-0.1.3-py3-none-any.whl
```

S1–S12 all passed (the smoke script exits non-zero on any failure), including S11 (a saved text contract
and a passing Checked request, no extra installed) and S12 (Stop of a request still waiting for response
headers, from the installed wheel: `stopping`, then `stopped` with `stop_mode` `immediate` and `sent` true).
The sha256 above is from a build on this machine and will differ from CI's.

Also run: `bash -n` on `install.sh` and `dev_setup.sh`; `install.sh --wheel <that wheel> --sha256 <above> --with-checks`
into a fresh venv installed AgentWiz 0.1.3, modelSpell 0.3.0 and jsonschema 4.26.0. `dev_setup.sh` itself
was not run end to end (it would recreate the checkout's `.venv`).

Not run here, as the specification expects: the CI jobs on Ubuntu and macOS (R1), the connected
`build_release.py --smoke` (R2), the live checks (H2) and the README tour (H1).

## Departures and choices found during the build (E1)

No scope change, no new runtime or dev dependency beyond the specification (Playwright 1.63.0 in `dev`,
`jsonschema>=4.26,<5` in `checks`). Nothing in Section 22.1 was contradicted. Choices within the
implementer's discretion, all listed in `DEVEL.md` ("Choices recorded for 0.1.3"), are:

- **Strictness beyond the spec's text.** A Chat request with *any* `contract` key (even `null`) is 400. `source` is required.
  A repair while a later attempt of the step is running is `not_latest`. A `strict` flag or a schema on a text contract is refused.
- **Schema and checks.** Schema messages quote the keyword's value as written in the contract. A `RecursionError`
  in a check makes it `not_run` ("nested too deeply"). A boolean schema needs the extra. `length` accepts `null` bounds.
  A contract problem's `file` is an absolute path. `ExactLimit` re-scans for its location.
- **The page.** First poll is immediate, then every 2 s. A refused request leaves the tab's status `idle` with `data-error` set to the code.
  MCP tool status can also be `stale`. The History list refreshes when the tab is shown. A saved contract's editor is replaced
  by the server's stored text. Contract and run deletions confirm inline; the old Model settings dialog still uses
  `confirm()`/`prompt()` for profiles (unchanged from 0.1.2, outside Part E's scope). History's "Full record" Copy puts
  `JSON.stringify` of the parsed run file on the clipboard.
- **Extras.** One extra page test, `test_page_raises_no_script_errors`. Two optional live checks added to `tests/test_live.py`
  (`test_live_stop_openrouter`, `test_live_checked_request_openrouter`) plus a parametrised "blocked until 0.2.0 (D72)" skip.

Two things the owner may want to look at:

1. The checkpoint-log gap above.
2. The README tour now has 29 numbered steps in groups; it replaces the 0.1.2 tour rather than extending it.

## Phase H — owner disposition (updated 2 November 2026)

Objective checks belong in pytest, including browser-scripted checks. The old mandatory 29-step
walkthrough is withdrawn; the tutorial remains optional learning material, not release sign-off.

**Remaining subjective UI review: Completed by owner direction.** This covers presentation,
clarity and usability only. The status records the owner's instruction, not invented individual
observations. No tutorial acknowledgement or manual pass/fail exercise is required before commit.

The offline gate, CI and installed-wheel release smoke are still required. Live backend checks
remain optional. See DEVEL "Checking a release" for the authoritative maintenance rule.

## Release checklist (after Phase H)

1. Commit to `devel` (the owner's commit).
2. CI: four jobs pass — Ubuntu and macOS, Python 3.10 (no `checks` extra; the `needs_checks` tests skip with their reason) and
   3.13 (with it), each with Chromium.
3. `python scripts/build_release.py --smoke` (connected form) passes on the committed tree.
4. Merge to `main`. **`main` is at `v0.1.1` and 0.1.2 was never published (specification Section 21): merging publishes 0.1.3.**
   Whether to publish 0.1.2 first is your choice; nothing here depends on it.
