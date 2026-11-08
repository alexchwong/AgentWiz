# UI-CD v3 implementation — 8 November 2026

Implemented `AgentWiz_proposal_ui_CD_v3.md` against AgentWiz devel `3bf61c6` ("core (non-UI) fixes"), still devel
HEAD when the work started. Version remains the unreleased 0.2.1. No commit, push, merge, tag or publication was made.

**Nothing in this delivery was executed.** The implementing session could not install or run repository code
(`scripts/dev_setup.sh` and `pip install -e .` were refused by the session's execution policy), so no pytest, page,
tutorial or smoke check ran. Only syntax checks ran (below). Treat every behavioural claim here as unverified until
the offline gate passes.

## Apply this delivery

Extract the ZIP over the matching checkout, preserving paths. It contains complete changed files, not a patch.
No files need deletion (`DELETIONS.txt` is empty). No dependencies were added.

## Behaviour

- **Server (two read-only additions).** CD1.14: `WorkflowService.block_ports()` adds `contract` (exact captured
  contract text) and `identifier_map` (conservative records-form predicate, `workflow_service.identifier_map`); a
  Gate's `scan` keeps a fixed entry with `contract: null`. CD6: the `assist` plan descriptor carries
  `authoring_protected`.
- **CD1 Workflow Design** (`workflows.js`): palette (search, Create block…, Workflow I/O, Release Gate, Restore
  placeholders, blocks with description and folded ports; v3 and script blocks disabled with reasons; drag, click,
  tap or Enter to add), canvas (port rows with handles, edges from measured handle centres, I/O and constants cards
  at derived positions, pan, Ctrl+wheel/pinch zoom 25–200%, Fit ≥ 60%, click/tap/drag/keyboard wiring, refused
  connections explain themselves), inspector (workflow, node, Gate, edge, I/O views; Why this stays local; own label
  folded), List view (whole form, shared selection and inspector), single validation client with a never-resetting
  generation, response states per CD1.8, issue list, Undo/Redo with Save as checkpoint, Insert Release Gate,
  identifier-map suggestion, open-draft destination remedies (also in Trusted destinations), detours that keep
  unsaved work, Run →. `restrictedSources()` is removed.
- **CD3**: release panel highlighting and Previous/Next finding, Find, pagination above 200 KB, batch position,
  destination facts (trust preview), decision consequences, Reject and revise; outputs above progress with a
  restoration summary; restore details in node detail; Revise on rejected/expired outcomes.
- **CD4**: `ResultView` and an exact JSON re-indenter in the page; adopted for Run node detail and History workflow
  records (`traceView`).
- **CD5**: History reads workflow-run and comparison manifests once per refresh (outcome in rows, Failed filter);
  node-structured workflow detail with record files under Troubleshooting.
- **CD6**: Assist is action-first with per-action fields, folded Assistant settings, explicit evidence, an
  Includes list built from the plan and the protection consequence from `authoring_protected`.
- **CD7**: Revision actions show consequence lines.
- **CD8**: one Settings dialog with categories; the existing editors open over it in one click; `openSettings()`.
- **CD9**: header activity indicator (Workshop follower, workflow poller, recovery from `workflow-active` and
  `batches`) and header order. **CD10**: Chat/Checked request destination line; reasoning and the raw final event
  folded under Troubleshooting.
- Tutorial: `private-summary-release` text gains the page assembly steps (`updated_in` stays 0.2.1).

## Departures and interpretations (for owner review)

1. **CD2 spike not run.** The canvas uses option (a), the vanilla engine, because the spike could not be executed
   here and (b) needs a new dependency. The owner decision on the engine remains open; CD1 is engine-neutral.
2. Adding a block from the palette uses a small modal dialog (revision + identifier, Enter accepts) rather than an
   anchored popover.
3. Bands add a fourth state, `stacked`: when the rail, canvas minimum and inspector minimum still don't fit
   (Medium below ≈ 940 px), the inspector moves below the canvas instead of either pane shrinking.
4. While a newer check is pending, the last accepted states stay visible with the design surface dimmed and the
   status "Checking…"; error states clear to "not checked".
5. Assessment findings are "locatable" when their free-text `location` occurs in the payload (case-insensitive).
6. The restoration summary is shown for outputs fed by a transform node with `text` and `pairs` inputs (the
   restore shape); the block view has no operation field and none was added.
7. **CD4 partial**: Test, Compare and Saved block runs keep their own markup (they already put outputs before raw
   panels); `ResultView` is not yet adopted there.
8. **CD8**: Models & roles, Trusted destinations, MCP servers and Cloud keys open their existing dialogs over
   Settings (their buttons moved into the category list with hooks unchanged) instead of being re-hosted inside
   it. MCP confirmation also remains in Trusted destinations.
9. **CD5.1**: manifests carry no date, so rows show a date only when a manifest or batch provides one.
10. Duplicate's consequence line names what is open (the existing action duplicates the open draft), not the
    revision chosen in the picker.
11. The Narrow inspector is stacked below the list rather than a modal sheet.

## Obsolete (removed or renamed in place; no files deleted)

- `restrictedSources()` and the old `canvas()` in `workflows.js`; hooks `wf-canvas-node-*`,
  `wf-canvas-input-*`, `wf-canvas-output-*`; the top-level `wf-delete` button (Delete workflow is folded in the
  workflow inspector, `wf-delete-workflow`); the Add Release Gate identifier dialog.
- The header Settings disclosure (`details#ws-settings`); `ws-settings` is now the Settings dialog button.
- `ws-choice-assistant-role` is now `ws-choice-assistant-role-writes-this-proposal`; the Assist Action select is
  replaced by `ws-assist-action` cards.

## Tests changed (none run)

- `tests/test_core_v3.py`: port projection contract and `identifier_map` (CD1.14).
- `tests/test_workshop.py`: `authoring_protected` on assist plans (CD6).
- `tests/test_page.py`: adapted to List view, the prefilled Gate identifier, folded Assist settings, Settings
  holding Cloud keys and the folded Chat final event; the old canvas drag test replaced; new checks for tap
  wiring/geometry/undo/layout (C-F3/4/5/13/16/17/19), Insert Gate and sibling map (C-F6/8/9/15/19), validation
  races and failures (C-F7/18), bands (C-F21, C-F11 layout), the activity indicator (D-F19), Assist fields
  (D-F12/13/26) and result-first output (D-F24).
- Not covered by tests: C-F1/E-F1 full page journey, C-F10, C-F12, C-F20, C-F22, C-F23, D-F1–D-F5, D-F7–D-F11,
  D-F14, D-F15, D-F16 beyond the adapted tests, D-F17, D-F18, D-F20–D-F23, D-F25, D-F27.

## Actual verification

Environment: Linux x86_64, system Python 3.13.18 and Node 22 (no `.venv`; modelSpell unavailable).

- `node --check` on `workflows.js` and the page's inline script: passed.
- Shipped and changed Python parsed with the Python 3.10 grammar: passed. `git diff --check`: clean.
- Not run: `python -m pytest -q -m "not live"`, page and tutorial tests, `scripts/build_release.py --smoke`, CI.

## Next actions

Run `scripts/dev_setup.sh`, then the offline gate and `tests/test_page.py`; fix failures (the new page tests were
written without a browser). Decide the CD2 engine. Add the uncovered checks above, starting with E-F1.

---

Earlier handovers below are historical; the entry above describes this delivery.

# Core v3 implementation — 8 November 2026

Implemented `AgentWiz_proposal_core_combined_v3.md` (Checkpoints B and U, with the owner decisions of
8 November 2026) against AgentWiz devel `f197a52`, still devel HEAD when the work started. Version remains the
unreleased 0.2.1. No commit, push, merge, tag or publication was made.

## Apply this delivery

Extract the ZIP over the matching checkout, preserving paths. It contains complete changed and added files, not a
patch. No files need deletion (`DELETIONS.txt` is empty). No dependencies were added.

## Behaviour

- **§2 Restore.** `identifier_pairs.py` (scan and restore profiles; the Gate's scan now calls it with unchanged
  codes and messages). `restore_placeholders` transform with streaming tokenisation, no rescanning, chunked
  escaped-length enforcement and a bounded trace. v4-only starter (all ports Local only, two passing examples),
  assistance schema, `GET starters` version 2, page control *If a placeholder has no mapping*.
- **§3.1** `roles.profile_location` keeps its kind guard and empty-URL fallback and delegates the host test to
  `outbound_policy.locality`.
- **§3.2–3.4** `State.destination_config()`; `outbound_policy.saved_uses`, merged into
  `destination-trust-candidates` (`requires_trust`, `trust_gaps`, `scan`, `observed`, `remedy`); remedies on
  Workshop/assistance protected refusals, workflow readiness issues (with `facts`) and run-time
  `destination_changed`.
- **§4** `Graph.explain` (values and nodes, per basis), `effective` labels in the editor's `node_ports`, per-example
  `restriction` in readiness; `messages.py` catalogue and `GET messages`; the page renders catalogue text with the
  code in a details line, a remedy attribute on findings and the saved-use banner in Trusted destinations.
- **§6** `Workflows.candidate` (extracted from `edit`, Save/Rebind schemas unchanged), `Workflows.open_saved`,
  lenient planning for validation only, `POST workflow-validate`.
- **§7** `text` expectations (validation, judgement, assistance schema, block and workflow editors that preserve
  untouched assertions); `tutorial_support/private_summary.py` reference fixtures with stand-in models
  `private-deid`/`private-summary`; tutorial topics `private-summary-local` and `private-summary-release`
  (`updated_in` 0.2.1).

## Departures and interpretations (for owner review)

1. `declared_label` versus `consumer_label`: a label on a receiving port is reported as `declared_label` when every
   receiver of the value is labelled, and as `consumer_label` when unlabelled siblings also receive it (fan-out).
   A labelled producing output is `declared_label`; workflow inputs, constants and outputs are `workflow_io`.
2. Extra node cause `release_assessment` for a Gate whose local assessment makes it protected.
3. Validation reports Gate findings inside `structural` (the response shape has no separate list).
4. The restore trace is one object in the transform's existing `fills` list (the page expects a list).
5. A Text assertion on a JSON output is refused with `example_incompatible` (409), also for blocks, where other
   example-shape errors are `invalid` (400).
6. `GET messages` added so the page can render catalogue text without duplicating it.
7. `protected_host_script` now carries one located issue with `remedy: explain`; the code is unchanged.
8. The tutorial's release topic uses an OpenRouter-kind profile whose configured server is the local stand-in,
   with a placeholder key held only in the tutorial session's memory; no live provider is contacted.
9. Remedies on Trusted-destination rows suppress `choose_local_role` for destinations no protected use requires.
10. S2/S2a page journeys author the reference workflows through the shared fixture (HTTP/service), then check
    readiness, review, approval and completion in the page. Assembling the blocks and wiring through the page
    (no Upgrade or Refresh design fields click, mapping built with List of records) is not covered: that is
    canvas assembly, which UI-C owns (S10/S11 are deferred there too).

## Tests changed

- Added `tests/test_core_v3.py` (27 functions, 35 cases): restore semantics, locations, encoded limit with `tracemalloc`, profile
  regression, assistance schema, starter standalone and in a workflow, text validation/judgement, explanation
  (fan-out, upstream, released, example basis, agreement), validation (collected issues, cycle and unknown
  source, fan-out under breakage, advisory Rebind and snapshot reuse, stale token, no file writes,
  configuration-only with credential paths patched to raise, 64-node cost, seeded agreement with readiness),
  candidates/remedies (S1, S6), refusal actions, catalogue, reference workflows (10 examples correct locally; release
  sends one approved request), S3, S7, S8, S9.
- `tests/test_page.py`: §5 Create-dialog guard (8 starters), S2, S2a, S3 rendering, S6 banner, restore parameter
  control, Text-check preservation in block and workflow editors.
- `tests/test_tutorial.py`: drivers and the registry's pinned ID/`updated_in` lists for the two new topics.

## Actual verification

Environment: Linux x86_64, Python 3.13 (the checkout's `.venv` from `scripts/dev_setup.sh`), modelSpell 0.3.0,
Playwright 1.63.0 with its Chromium.

- Baseline at `f197a52` before changes: `python -m pytest -q -m "not live"`: **1010 passed, 10 deselected**.
- Final source: `python -m pytest -q -m "not live"`: **1060 passed, 10 deselected**; no failures, errors or skips. An intermediate run failed only `test_tutorial_topics` while the tutorial drivers were being edited.
- Shipped Python parsed with the Python 3.10 grammar: passed. `node --check` on `workflows.js` and the page
  script: passed. `git diff --check`: clean.
- Not run: `scripts/build_release.py --smoke`, page/tutorial checks against an installed wheel, the CI matrix.

## Remaining limits

Only Linux and Python 3.13 were executed; macOS, WSL2 and Python 3.10 remain unverified except by grammar parsing.
Open-draft trust gaps, the validation client and canvas display of validation remain with UI-C. The 150 ms
validation target is recorded here and was not used as a CI bound (the test bound is 2 s).

---

Earlier handovers below are historical; the entry above describes this delivery.

# UI-AB v2 implementation — 8 November 2026

Implemented `AgentWiz_proposal_ui_AB_walkthrough_space_v2.md` with the owner decisions of 8 November 2026
against AgentWiz devel `31cdec822635b491f98acda4c09729f46c8adb60`, which was still devel HEAD at the end of the
work. Version remains the unreleased 0.2.1. No commit, push, merge, tag or publication was made.

## Apply this delivery

Extract the ZIP over the matching checkout, preserving paths. It contains complete changed and added files, not a
patch. No files need deletion (`DELETIONS.txt` is empty). No dependencies were added.

## Owner decisions applied

- `core_03` §3.4 adopted: **Review retained privacy labels** records an acknowledgement of exact
  `(revision, input)` pairs. Saved-example and captured-value restrictions stay intact.
- Overwrite stays disabled for workflow-ready revisions; the button title is the owner's text and
  `docs/architecture.md` records the privacy-history rationale.
- `core_06` S2 rewritten through the complete Gate route (project document `core_06` v2; not a repository file).

## Behaviour

- **Backend.** Model/tool starters accept `data_handling` and become v4 with the chosen labels. Retained-label
  acknowledgement record and `GET/PUT blocks/<key>/retained-labels`. `GET destination-trust-candidates`.
  Trust preview adds `detail` and `locality`; the design projection adds `destination_facts`; Test/Compare plans and
  protected refusals add `restriction_sources` and destination facts. Overwrite is now also refused when the
  *target* is v4 (previously only a v4 draft was refused, so an API v3 draft could replace a labelled v4 entry).
  Enforcement is otherwise unchanged.
- **Page.** Create dialog port labels; Design identity bar and ports → prompts → settings order, upgrade banner,
  live Insert buttons and confirmed reference replacement, destination line, Protection card, retained-label
  review; contract chip and side drawer with Done, shape shortcuts and one-dialog publish; Test readiness with
  in-place remedies; Compare pinned status and aligned pairs; Models & roles two-pane panel; Trusted destinations
  table; Workflow Run readiness findings with owner-routed remedies, pinned Stop/phase, ordered progress and inline
  node detail, Open source revision → Return to workflow → explicit Rebind review; combined History with filters;
  Save runs On/Off in the header; tutorial as a side panel; Workspaces Switch per row and More actions.

## Departures from the proposal (recorded for owner review)

1. `data_handling` is optional on the API. Absent, model/tool starters stay v3 as before (existing API callers and
   tutorial fixtures); the page always sends it. `core_04` §4.6 proposed refusing a missing value with 400.
2. Content-width bands use viewport media queries with `body.help-open` offsets. Container queries would establish
   a containing block that traps the fixed contract drawer.
3. Compare renders every example as an aligned baseline/candidate pair with Previous/Next navigation, instead of
   one example at a time; both arms remain visible together.
4. Test's Examples "drawer" is a collapsible section that remembers its state; Recent remains a dialog.
5. Workflow I/O and Gate contracts keep their disclosure with the readable chip summary and shortcuts, not a side
   drawer. Inline storage plus Copy saved contract is unchanged (R4).
6. Models & roles opens the existing Model settings dialog over the panel (the same editor and validation)
   rather than expanding it inline.
7. **Add Release Gate** opens Workflow Design with guidance; it does not preselect the edge.
8. Not implemented (dependencies absent): `core_02` `trust_gaps` banner and full `used_by` scan of saved blocks
   and workflows (uses are roles, servers and the current workflow's nodes); `core_03` message catalogue (the page
   supplies plain text with the code in a details line); `core_01` unmatched-pair display; UI-D D1–D8, including
   the release panel move and shared `ResultView` (the release panel stays in the progress pane).
9. History lists workflow runs and comparisons by ID; their outcomes appear when opened.
10. Header: Settings remains a disclosure holding the configuration summary; no separate activity indicator.

## Tests changed

- Added `tests/test_label_review.py` (4 functional HTTP tests: creation labels, acknowledgement with
  example restriction kept, overwrite of a labelled target, trust candidates).
- `tests/test_page.py`: helpers `pick_labels`, `open_port`, `close_port`, `open_more`; existing tests adapted to the
  new structure (label choice before Create draft, drawer open/close, one-dialog publish, overwrite seeded as a v3
  block via the API, inline node detail, History workflow row, Trusted destinations dialog and its state attributes
  instead of wording); five new page tests for creation labels and reference replacement, the upgrade banner, Test
  readiness with Grant and label retirement, the contract chip/shortcut/Done, and a workflow finding's Grant.

## Actual verification

Environment: Linux x86_64, Python 3.13.18, modelSpell 0.3.0, Playwright 1.63.0 with its Chromium, installed by
`scripts/dev_setup.sh`.

- Baseline at `31cdec8` before changes: `python -m pytest -q -m "not live"`: **1001 passed, 10 deselected**.
- Final source: `python -m pytest -q -m "not live"`: **1010 passed, 10 deselected**; no failures, errors or skips.
  The one intermediate failure was the tutorial registry's pinned `updated_in` list, updated for `model-block`
  moving to 0.2.1 (DEVEL tutorial rule 3).
- `python scripts/build_release.py --smoke` from a disposable commit in a throwaway clone (this checkout has no
  commit): **RC1, RC2, RC3 and installed-wheel smoke passed**. That commit's tree equals the delivery except this
  verification section of `HANDOVER.md` (not shipped).
- Shipped Python parsed with the Python 3.10 grammar (`ast.parse(..., feature_version=(3, 10))`): passed.
  `node --check` on `contracts.js`, `workflows.js` and the page script: passed. `git diff --check`: clean.
- Not run: page and tutorial tests against the installed wheel in an isolated interpreter.

## Remaining limits

The Linux/macOS × Python 3.10/3.13 CI matrix was not run. Only Python 3.13 was executed locally; Python 3.10
compatibility of shipped modules was checked by grammar parsing only. Layout and fit at 1280×800 were not checked
visually; DEVEL rule 8 page checks assert states, not layout. macOS and WSL2 remain unverified locally.

---

Earlier handovers below are historical; the entry above describes this delivery.

# Release Gate implementation — 7 November 2026

Implemented `AgentWiz_proposal_release_gate_v2.md.txt` against AgentWiz devel
`a6f6458320d246caba59866357922db0ac3ef665`. Devel still pointed to that commit at
final verification. Version remains the existing unreleased 0.2.1. No commit, push,
merge, tag or publication was made.

## Apply this delivery

Extract this ZIP over the matching checkout, preserving paths. It contains complete
changed/added files, not a patch. No obsolete files need deletion. No new runtime or
development dependencies were added to the project.

## Behaviour

- Built-in Release Gate nodes use bundle format 2 without Workshop block snapshots.
  Workflows without Gates continue to save as format 1.
- Raw sources and sibling outputs retain restriction. A Gate exposes only its reviewed
  `released` value after an explicit, single-use decision. Existing labels can restrict
  that output again; restricted mappings still cannot enter cloud blocks.
- Optional local assessment uses a separately assigned role, fixed versioned instructions,
  structured findings, one bounded repair, existing model budgets and protected dispatch.
  It cannot use cloud/ungranted destinations, approve a release, or modify the candidate.
- Deterministic identifier checking handles text/JSON and Unicode offsets. Findings and
  unavailable assessments require server-enforced acknowledgement. Invalid scans/limits
  fail visibly; no failure is reported as a clean check.
- Reviews show indirect downstream destinations, and another Gate requires another decision.
  Earlier authorised cloud processing is permitted, including additional public inputs;
  separate unreviewed model ancestors are still checked.
- Approval is bound to run/example/node, immutable payload, executable, scope and check
  identities. Conflicting/stale decisions are refused; exact retries are idempotent.
- Stop wakes paused/assessing jobs. Reject/expiry stop later batch examples. Only human
  wait is excluded from the execution budget; the shared Workshop slot remains held.
- The browser recovers an active Gate after reload. Restart invalidates pending approvals.
  There is no production auto-approval, including in the synthetic guided tutorial.
- Save runs remains opt-in. Accepted decisions are persisted before downstream execution
  when saving is on. Required evidence failure prevents release. Bounded session node
  traces retain the candidate, decision and local assessment for later inspection.

## Actual verification

Environment: Linux x86_64, Python 3.12.14, modelSpell 0.3.0, Playwright 1.63.0,
Chrome Headless Shell 153.0.8010.12. The Playwright CDN returned an unusable download;
the exact matching browser was obtained from the official Chrome for Testing distribution.

- Final source: `python -m pytest -q -m "not live"`: **1001 passed, 10 deselected**;
  no failures, errors or skips.
- Clean installed wheel: isolated interpreter, copied tests outside the source tree:
  `python -I -m pytest -q tests/test_release_gate.py tests/test_page.py tests/test_tutorial.py`:
  **96 passed**, no failures/errors/skips.
- Built the final source wheel and ran the existing RC1, RC2 and RC3 release checks:
  **all passed**. Used the release-check functions on the built wheel; did not create
  a commit to run the HEAD-export build wrapper.
- Installed-wheel smoke S1–S17: **passed** in a fresh scratch directory. S17 submits
  an explicit Gate decision through the ordinary API. An earlier attempt reused a
  prior smoke workspace and failed its existing MCP-library precondition; the clean
  rerun used the same final wheel and unchanged smoke checks.
- Python 3.10 syntax parsing for shipped Python modules: passed. JavaScript syntax
  check and added-line whitespace review: passed.

Functional coverage includes local → Gate → cloud → local dispatch, no cloud sends
before approval, source/mapping restriction, stale/cross-run/cross-example tokens,
concurrent/conflicting decisions, Reject/Stop/expiry, indirect destinations, sequential
Gates and unsafe branches, acknowledgement enforcement, malformed/limited scans,
JSON precision/Unicode offsets, assessment failure/Stop, changed destinations,
evidence-write failure, browser authoring/reconnection and the explicit tutorial decision.

## Remaining limits

The existing Linux/macOS × Python 3.10/3.13 CI matrix remains unrun here. Python 3.10
syntax parsing is not interpreter/platform execution. Real model quality and live provider
calls were not tested; offline scripted backends establish dispatch/control behaviour.
Identifier scanning and local AI findings do not certify anonymity. Gate approval covers
processing of the reviewed value at the displayed destinations, not exact future HTTP
bodies or provider-internal forwarding. API use cannot prove a person read the candidate.

No extra manual regression checklist is required. Subjective UI review is optional;
run the existing candidate CI when authorised to push.

# AgentWiz 0.2.0 close-out

Documentation cleanup against `devel` at `e937d268069a0c7815fc525f16e7c37ae6c0bb16`, following close-out specification v2. The worktree was clean at the start. Keep the release version at 0.2.0. At preflight, `main` and the latest published release, `v0.1.4`, pointed to `e0b91d26b72d2e96d85828356e4c88cf05eb2f18`. The exact `v0.2.0` tag was absent and the authenticated release list showed no matching draft, prerelease or published release.

## Delivery

- `README.md` and `docs/architecture.md` now describe executable v3 blocks, managed heads, exact contracts, named model outputs and revision descriptions. README includes a runnable synthetic example and separates checkout installation from published releases.
- `DEVEL.md` and `docs/releases.md` clarify version policy, wheel inputs and release procedure. `docs/decisions.md` preserves the historical decisions and links to their pinned sources.
- `NEWS.md` corrects and shortens the affected 0.2.0 bullets. `HANDOVER.md` now covers the current close-out.
- Delete `MANIFEST_phase2.txt`, `contract_overhaul_handover.md` and `contract_overhaul_tech_specs.md`. The archive's `DELETIONS.txt` lists these paths for application and is outside the repository changes.

The owner previously confirmed block design and testing, model-assisted custom JSON contract synthesis and a working identifier-tally block using that contract. The checks below used synthetic data and local stubs.

## Verification

Checks ran on 6 November 2026 on Linux x86_64 with Python 3.12.14, Playwright 1.63.0 and Chrome Headless Shell 153.0.8010.12. The Playwright CDN download failed. The exact pinned browser build was recovered from the Chrome for Testing distribution and its version and launch were verified.

| Gate | Result |
| --- | --- |
| G1 · Scope and examples | Passed. Clean diff check, expected paths only and no active links to obsolete working-tree files. Model, tool and script definitions loaded. Contract examples, multiline Workshop Request preview and positive and negative named-envelope cases passed. All 57 unreleased NEWS bullets meet the whole-bullet limit (maximum 19 words). Released NEWS sections match the baseline byte for byte. |
| G2 · Offline checkout | Passed. `python -m pytest -q -m "not live"` reported 935 passed, 10 deselected, zero failures, errors or skips. |
| G3 · Release build and smoke | Passed. `python scripts/build_release.py --smoke` passed RC1–RC3 and S1–S15 from disposable local commit `13fbc09f403424e1f94ada94b17fb69d45b93c10`. |
| G4 · Installed page and tutorial | Passed. 55 page and tutorial tests, zero failures, errors or skips. The isolated interpreter imported AgentWiz from the installed wheel in site-packages. |
| G5 · Candidate CI and release workflow | Pending authorised push and merge. The Linux/macOS × Python 3.10/3.13 matrix remains required. |

The implementation checkout has no new commit. The sole commit used for smoke is in the disposable clone. A Git tree comparison of that commit and the delivery, excluding only this handover, returned 0. The common tree with the handover excluded is `cf78345b70889461472f0a6b60cf2aa9372d8c54`.

## Release follow-ups

Apply the three deletions with the delivered files. Repeat the exact-tag and draft-aware release preflight before the authorised merge and require candidate CI.

The v2 specification leaves three release tooling fixes separate. The shipped-file guard omits the source template, RC1 ignores words on NEWS continuation lines and manual release dispatch can publish from a non-main ref. The documentation records the corresponding review and operator rules.

## 0.2.1 candidate

Cumulative changes are based on `devel` at `cc44074bbe01b99478566a37fea9a19aa3bd8552`.
The package version is 0.2.1. The checkout is uncommitted.

### Delivered by phase

1. V4 ports, transform starters, workspace-scoped destination trust, protected dispatch,
   draft lineage, and reviewed block upgrades.
2. Exact workflow bundles and immutable captures, graph planning, serial execution,
   example batches, MCP confirmation, and workflow evidence.
3. Workflow forms, run preflight and traces, examples, trust settings, History, and the
   isolated tutorial. The form-only journey passed before the canvas was added.
4. Canvas dragging and connections using the same form state and reviewed save,
   keyboard positioning, release version and notes, and installed workflow smoke S16.

### Verification

- Phase 3 workflow and tutorial checkpoint passed 14 tests on Linux with Chromium.
- Final focused workflow page tests passed five tests, including drag, form-only save
  and execution, exact values, destination trust, and rebind against a missing source.
- The Linux Python 3.12 offline suite passed 963 tests with ten live tests deselected.
- A wheel built from an isolated uncommitted source snapshot passed RC1–RC3,
  `pip check`, and installed smoke S1–S16. This used the release builder's existing
  build and smoke functions and the hash-checked pinned modelSpell wheel.
- Six focused browser and tutorial tests passed against the installed package in an
  isolated Python 3.12 environment outside the checkout.
- The Linux Python 3.10.22 and 3.13.17 offline suites each passed 963 tests with
  ten live tests deselected. The supplementary browser test for both MCP
  confirmation modes and Stop passed separately on all three Python versions.

### Release follow-up

Run the macOS Python 3.10 and 3.13 CI lanes and the WSL2 verification before sign-off.
Use the normal release command on the intended committed candidate.

### Obsolete files

No deletions are required for this delivery. The deletion list in the historical
0.2.0 close-out above is unrelated to this archive.

### 0.2.1 implementation audit

Reviewed the four-phase candidate against the v3 specification. The audit fixes
cover workflow Start replay after a dropped response, cancellation before example
output publication, reasons for skipped nodes and examples, and saved evidence.
Start recovery reuses the reviewed request and ticket while the page is open.
Saved runs recheck captured source bytes for newly known credentials and make the
executable capture available from History.

The Linux offline matrix passed 968 tests per interpreter on Python 3.10.22,
3.12.14 and 3.13.17, with ten live tests deselected. After exposing the capture
in History, the 15 workflow tests passed again on all three interpreters.
The final wheel passed RC1–RC3, `pip check`, smoke S1–S16 and nine focused
browser and tutorial tests. The macOS CI lanes and WSL2 verification remain
pending.

Apply the audit ZIP over the phases 1–4 ZIP. The audit requires no deletions.
The implementation checkout remains at the original devel commit.

