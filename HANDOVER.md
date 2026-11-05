# AgentWiz 0.2.0 — cumulative Phase 2 handover

## Contract overhaul progress

Cumulative implementation progress for `contract_overhaul_tech_specs.md` now lives in
`contract_overhaul_handover.md`, as requested by the owner on November 5, 2026.
That file records every overhaul phase, including Phase 2 (the UI) and its final verification; this
release/UI handover is not the overhaul completion record.

## November 5, 2026 — Design settings and action-row follow-up

- Preserved the existing uncommitted three-column/revision work. Design now builds its columns
  explicitly: the RHS orders display name, revision name beside Save revision, model-role dropdown,
  paired temperature/reasoning, model advanced settings and Standard description. The historical
  revision selector is beside block selection. Additional repairs is visible below Outputs.
- Blank temperature inherits the role's default, shown as a muted placeholder; reasoning's inherited
  option names the default. Explicit values (including zero), missing role bindings and backend resets
  are preserved. Role changes refresh defaults without rebuilding or losing unsaved prompt edits.
  Backend-reset controls remain in advanced settings; backend settings/revision formats are unchanged.
- Assist and Revision actions share Design/Test/Compare's action row in every mode. Save locks that
  row and the relocated revision picker until review/publication/reload completes.
- Updated existing browser journeys and added functional coverage for role/default intent, missing
  bindings, shared actions and save locking. README, architecture and NEWS describe the controls.
- Final validation on Linux / Python 3.13.13: `python -m pytest -q -m "not live"`:
  **685 passed, 10 deselected in 108.90 s**. Earlier page/Workshop/tutorial run: **71 passed**;
  corrected focused settings/save-lock checks: **2 passed**. An intermediate route-callback test
  stalled while awaiting browser assertions; replaced it with assertions during the save-review
  dialog, then reran the full gate successfully. Compilation and `git diff --check` passed.
- Inspected rendered desktop/tablet views and checked widths 1440, 1280, 900 and 390: no horizontal
  overflow. No live-provider, installed-wheel, macOS or Python 3.10 validation was performed.
  No dependencies, commits, pushes or publication were added.

## November 5, 2026 — Workshop Design layout and numbered revisions

- Implemented the owner's approved three-column Design rework: unlabelled prompt, variable and
  miscellaneous columns, responsive stacking, compact block selection above the editor, and the primary
  Save in miscellaneous rather than navigation. Inputs/outputs are editable tables with field name,
  format and meaning; per-variable schema/shared/source controls remain available. Model prompt labels
  are Block description, System prompt and User prompt. Tool/script behaviour remains type-specific.
- Structured format selection exposes its editor immediately. Replacing a meaningful contract asks
  for confirmation; empty text contracts need no destructive-change acknowledgement. Saving makes the
  editor inert until its reviewed publication/reload finishes, preventing edits to an outgoing form.
- Revisions expose stable per-block numbers and optional names, separate from block names, opaque IDs
  and effective freshness tokens. Numbers are assigned under the existing publication reservation and
  persisted in generation metadata; the strict head format is unchanged. Reads project old unnumbered
  retained history without rewriting it; the next save anchors that baseline through pruning. Earlier
  pruned lifetime history cannot be reconstructed. Legacy import is original=1, edited=2; duplicates
  start at 1. Retention stays 20. Historical restore appends a new revision and keeps its contract
  acknowledgement. Labels are at most 120 characters without controls, trimmed on publication;
  new UI labels default blank and historical labels are not renamed in place.
- Inline revision selection uses Save/Discard/Stay, preserves same-block examples and test inputs,
  and distinguishes selected/current IDs from example-collection revisions. Compare and revision
  actions show numeric/name labels. Late load responses are guarded. No dependency or execution
  transport changed; no commit, push or publication was made.
- Functional regression reasons: stable numbering through retention/restart/restore/duplication and
  unnumbered-history upgrade; legacy conversion and stale-save continuity; optional label validation;
  dirty revision-switch choices; immediate schema entry and cancelling destructive format changes.
  Existing browser save helpers now wait for the new revision's form to finish loading, and schema
  tests wait for the applied field rather than racing the asynchronous preview. The Test-tab save
  journey returns to Design, matching the intentional button relocation. README, architecture and
  unreleased 0.2.0 NEWS were updated; no release/version bump was inferred.
- Validation on Linux / Python 3.13.13: focused Workshop/replay/page suite **63 passed** (30.66 s);
  final desktop/mobile and revision-switch/format checks **4 passed** (5.44 s); targeted revision
  persistence/legacy/stale-save checks **4 passed** (1.12 s). Final full offline gate
  `python -m pytest -q -m "not live"`: **684 passed, 10 deselected** (106.67 s), no skips.
  Separate final tutorial run: **10 passed** (30.74 s).
  Python compilation and `git diff --check` passed. Earlier runs exposed the intentional Save relocation,
  asynchronous form/schema test races, and an over-eager confirmation for an empty text contract; these
  were corrected and covered in the final passing runs. Live providers, installed-wheel smoke, macOS
  and Python 3.10 were not checked locally.

## November 4, 2026 — block authoring, named outputs and role defaults: completing the proposal

- Completed the remaining work R1–R13 of `proposal_block_ui_improvement.md` on `devel` @ `9fe6e7f`
  ("partial implementation"). Nothing was committed, pushed or published. This entry replaces that
  proposal's section 12 as the record of state; the proposal file itself is now obsolete (see below).
- **Done.** R1: each input/output is a card holding its own Structured fields editor. R2: Save and
  Start refuse a new guided group with no field (editing may pass through one). R3: the example dialog
  offers inputs an example lacks and keeps stale expected values until explicitly removed. R4: blank
  Custom is refused. R5/R9: README, architecture, decisions and NEWS describe the envelope, generated
  instructions (rules copied from `output_protocol.py`), role defaults and precedence. R6: the located
  issue for a 0.1.x `step.output` names the rewrite. R7/R8/R10/R13: functional tests added (below).
  R11: one browser journey and one example-dialog check; Request preview has Copy. R12: see below.
- **Implementer choices that need the owner's confirmation** (none is recorded as an owner decision):
  1. *R4 spelling.* The proposal asked the owner to choose between an explicit control and sending a
     reset for blank Custom. Neither was assumed: `design_form` takes `customs`, and a Custom control with
     no value is refused as `block_invalid` at `steps[0].task.<name>`. Inherit and Backend default
     already say what a blank might have meant. Either original option can still be layered on.
  2. *R2 rule.* "New" cannot be told from "legacy" by schema alone, so the rule is relative to the saved
     block: `check_new_groups` refuses a guided group (object closed to extra properties) that is empty
     in an inline draft and was not empty in the saved head. Not judged: `key`/revision candidates,
     schemas outside the guided subset, contracts held by reference.
  3. *R10 premise.* Plan identities are hashed with a per-process salt, so a `RENDERER_VERSION` change
     could never leave a reviewed plan accepted within a process; the gap was that the renderer identity
     was not frozen or recorded. Each model candidate now carries `output_plan` (`mode`, `renderer`,
     `digest`), included in `expected_plan` and in the persisted batch. The review `basis` digest
     (description freshness) was left alone.
  4. *R12.* `_version.py` is `0.2.0` and the 0.2.0 NEWS section is treated as unreleased, so bullets were
     added to it and no bump was made; the model-block topic already carried `updated_in: 0.2.0`. If 0.2.0
     has shipped, a bump and a new NEWS section are required before merge.
  5. *R3 scope.* No "Keep as source" gate was added: unmapped values were already kept by default and
     removed only by an explicit button, so a gate would add friction without adding safety.
- **Also changed.** The Workshop status element now clears `data-code` on a successful save or batch update;
  it used to read `saved` while still carrying the previous error's code. The tutorial's packaged
  `extract_fields` block now declares two outputs (`fields`, `note`), inherits temperature from a seeded
  role default, and its topic text mentions system instructions, Request preview and role defaults.
- **Not done.** No tutorial topic demonstrates typed structured entry. The 7.2 confirmation for a type change
  that would discard children (the `field_has_children` refusal is retained) and a field-ordering control are
  not built. Request preview shows the sent messages, settings and their sources; the author/generated split
  stays available in the API (`prepared.system`, `prepared.generated`) but is not drawn separately. After
  switching a port to Structured value, **Refresh design fields** is still needed before its field editor
  appears. `scripts/replay_output.py` hard-codes the one known renderer (`1`).
- **Tests added** (each with its functional reason): replay of a captured named plan, and refusal of
  unreadable/mismatched plans (R7); review freshness after a role-default, declaration or renderer change (R8,
  R10); a file-backed `{{output.NAME}}` surviving save and the backend receiving exactly the previewed messages
  (R13); new empty group refused at save/Start and a saved one kept (R2); the three override modes with a
  blank Custom refused (R4); a Design journey covering field placement, override modes, Request preview with
  Copy, output cards, the empty-group refusal and the assistance request's sampling fields; the example dialog
  offering a newly added input; the tutorial model block inheriting its role default with two outputs.
  Each new browser assertion was checked to fail when its behaviour was removed; the first draft of two of them
  passed on stale state, which is why the status code is now cleared.
- **Checks (Linux, Python 3.13.18, Playwright 1.63.0 Chromium, local `.venv`).** Baseline before any change:
  `python -m pytest -q -m "not live"` **659 passed, 10 deselected** (148 s). Final: **668 passed, 10 deselected**
  (155 s), including `test_page.py` and `test_tutorial.py` (10 passed). `python -m compileall -q agentwiz scripts
  tests` and `git diff --check` passed. **Not run:** live backends, installed-wheel smoke, macOS, Python 3.10, CI.
- **Obsolete:** `proposal_block_ui_improvement.md` — it states "partly implemented", carries stale absolute
  paths, and its content now lives in this entry, `docs/decisions.md`, README and the architecture reference.

## November 4, 2026 — Workspaces dialog: switch, create, clone, reset and delete

- Added live in-process workspace switching within the launch `--workspaces-dir`. A destination
  `ProcessState` is prepared before the current one is shut down; the old lock is released only after
  a drained shutdown. `UIServer` now owns whichever workspace is current and exposes
  `close_workspace()`; `__main__.py` closes the current workspace after its final drain.
- Added top-level `GET /api/workspaces`, `GET /api/workspaces/launch` and POST
  `switch|create|clone|reset|delete` routes. The old Workshop `workspace-open` route was removed.
- Added `agentwiz/workspace_admin.py`: bounded listing, staged clone (configuration, profiles,
  roles, contracts, blocks, examples; optional saved runs/comparisons), in-place reset with profile
  reseeding, and lock-held tombstone deletion. No-follow paths, clone limits, shared-defaults/MCP
  guards and typed-name confirmation are enforced.
- Added a header **Workspaces** dialog. Switch/create and current reset run the Workshop
  Save/Discard/Stay guard then reload; clone/delete/reset-other refresh only. Active work returns
  `workspace_busy`; another process returns `workspace_in_use` with holder details. Workspace
  administration is hidden/refused in the tutorial child and refused while a tutorial is active.
- Session API keys and MCP tokens are not carried across switch/reset; they remain memory-only.
- Updated README, architecture, decisions, NEWS and the proposal. Added filesystem, HTTP lifecycle,
  CLI lock-release, tutorial isolation and browser workflow regressions.
- Validation on Linux / Python 3.13.13: `test_workspace_admin.py` **10 passed**,
  `test_workspace_lifecycle.py` **12 passed**, `test_workspace.py` **9 passed**, focused page
  **30 passed**, focused tutorial **9 passed**. The full offline gate passed
  **645 passed, 10 live tests deselected** (95.47 s). Python compilation and `git diff --check` passed.
  These counts are from the implementation checkout; no live backend or installed-wheel smoke was run.
- No dependencies, commits, pushes or publication. Live backends, installed-wheel smoke, macOS and
  Python 3.10 were not checked. No user workspace data outside test temporary folders was changed.


## November 4, 2026 — Workshop save diagnostics and Test/example workflow

- Save/Start planning returns bounded located `block_invalid` issues for invalid definitions,
  rather than reporting all parser failures as unreadable files. Actual unreadable-source
  failures retain `unusable`. Refused invalid captures are explicitly closed.
- Preview projects independently usable contracts on invalid drafts. Design/Test show definition
  issues; Test keeps usable inputs visible and disables execution until correction. Advanced JSON
  inputs start expanded; zero-input blocks have an explicit state. Output renames update step
  bindings; input reference changes require deliberate correction. Shared contracts use saved-name
  choices and cannot become inline `format: shared` contracts.
- Test groups one-off inputs and saved examples. Add/edit keeps pending examples in memory until
  Save example collection succeeds; saving a new/edited block's examples offers the normal revision
  review first. Cancellation/write refusal retains pending changes, inputs and selection. Replacement
  and unload guards include pending examples; revision saves preserve inputs and example state.
- Updated user/architecture guidance, NEWS and the existing authoring tutorial. Functional regressions
  cover definition diagnostics/bindings, retained Test inputs, unsaved-block one-off execution,
  selection across views, save-first cancellation and example-write refusal/retry.
- Validation on Linux / Python 3.13.13: focused Workshop/page/tutorial run **56 passed**;
  initial full offline gate **620 passed, 10 live tests deselected** (92.66 s). After the final
  unreadable-category adjustment, the three new regressions passed again; the final full gate
  **620 passed, 10 live tests deselected** (91.19 s).
  Python compilation and `git diff --check` passed. Two initial new-test setup mistakes (wrong
  contract-save route and a misplaced reload assertion) were corrected, without weakening checks.
- No dependencies, commits, pushes or publication. Live backends, installed-wheel smoke, macOS and
  Python 3.10 were not checked. No user workspace data was changed.

## November 4, 2026 — single-role browser and families

- Replaced stacked Roles editors with location (All/Local/Cloud), family/domain and matching-role
  dropdowns. Only the selected editor is mounted, even with many roles. Empty matches are explicit.
- Drafts remain in memory across selections, filters and model-settings visits. Save all changes
  submits every role, including hidden roles; new roles inherit active filters. Existing read-only,
  stale-revision, removal-warning, default-setup and backend location-policy behavior is retained.
- Suggested families: General, Writing & communication, Coding & engineering, Research & analysis,
  Data & extraction, Workflow assistance. Add family creates and assigns a custom family. Families
  persist on saved role definitions, not in a separate empty-family registry. Existing definitions
  without family remain valid and appear under General. Optional family text is validated on save/read
  (trimmed, nonblank, at most 64 characters); role format remains 2.
- Updated user/architecture reference and model-block tutorial guidance. No dependencies, commits,
  pushes or publication; existing uncommitted work was preserved.
- Focused roles/editor/tutorial checks: **53 passed** on Linux / Python 3.13.13. Final full offline
  gate `python -m pytest -q -m "not live"`: **617 passed, 10 deselected** (90.31 s), including
  the many-role browser regression and tutorials. Python compilation and `git diff --check` passed.
  A polling command timed out while the background gate continued normally; the final gate result
  was retrieved successfully. Live backends, installed-wheel smoke, macOS and Python 3.10 were not
  checked for this change.

## November 4, 2026 — role setup and location policy

- Missing role files now expose unconfigured `fast`, `assistant` and `reasoning` definitions without
  writing or selecting model profiles automatically. Existing assignments are preserved.
- The Roles editor adds purpose, Local/Cloud selection, filtered profile details, readiness states,
  additive default setup and a model-settings round trip preserving unsaved role edits. Saved names
  are stable; removal warns without rewriting block references. Workshop selectors link to setup.
- Role format 2 keeps the assignment map and adds definitions; version 1 remains readable with its
  original unconstrained assignments until configured. Older AgentWiz versions cannot edit v2 files.
  Legacy assignment-only API saves retain v2 policies for retained roles.
- Local means this machine: LM Studio or OpenAI-compatible endpoints on loopback IPs/localhost.
  Remote/LAN endpoints are Cloud; OpenRouter is always Cloud. Save and role resolution reject
  mismatches, including after profile edits, without fallback. The policy controls the contacted
  endpoint, not server-side forwarding. Reviewed frozen Workshop snapshots retain their existing
  capture semantics; this change does not revoke already prepared runs.
- Updated user/architecture documentation and the existing model-block tutorial guidance. No new
  dependency, model transport or publication action. Existing uncommitted work was preserved.
- Validation on Linux / Python 3.13.13: focused roles/page/tutorial run **69 passed**; final
  focused roles/editor run **40 passed**, including no-dispatch rejection after a profile changes
  location. An initial full gate had **1 failed, 609 passed, 10 deselected**: a state-shape assertion
  still expected no starter roles. It was updated to assert the new definitions and unresolved state.
  Final full offline gate `python -m pytest -q -m "not live"`: **613 passed, 10 deselected**
  (90.48 s), including page/tutorial coverage. `git diff --check` and Python compilation passed.
  Live providers, installed-wheel smoke, macOS and Python 3.10 were not checked for this task.
  No commit, push or release was made.

## November 4, 2026 — new-block dialog correction

- New-block creation now explains and validates the stable key before sending preview requests.
  Both create actions retain the dialog and entered values after validation or preview refusal;
  common Workshop errors include safe guidance and their machine-readable code.
- Bindings are type-specific. Configured model/assistant roles use dropdowns with unresolved
  assignments marked. With no configured role, an unassigned default still permits drafting.
- Tool blocks select configured MCP servers and automatically discover advertised tool names
  using the existing bounded discovery endpoint. Empty/failed discovery prevents creation;
  refresh retries discovery, changing servers clears the selection, and late replies are ignored.
  Unconfigured servers are not scanned for. Discovery does not execute tools.
- Functional browser coverage checks both create paths, correction after invalid keys/refused
  previews, role selection, server/tool binding and empty/failed discovery without tool dispatch.
- Validation on Linux / Python 3.13.13: focused new-dialog checks **3 passed**; full offline gate
  `python -m pytest -q -m "not live"` **595 passed, 10 deselected** (87.26 s), including page/tutorial
  checks. An initial focused run exposed the fake's empty-list default to an echo tool; the fixture
  was corrected to advertise no tools and rerun successfully. `git diff --check` passed.
- No dependencies, backend protocol or publication behavior changed. No commit, push or release
  was made. Live backends, installed-wheel smoke, macOS and Python 3.10 were not checked for this
  correction. The owner's original failing request was not captured; invalid keys are a verified
  cause of the former generic refusal, not proof of the exact input used in that report.

Baseline: devel `130eaf006c6e8af6930d1feea380299f5d97d8e6`, version 0.1.4 (the implemented baseline).
The delivery completes Phase 2 and includes the cumulative Phase 0/1 implementation and the ticket
checkpoint. The implementation checkout remains uncommitted and unpublished: no commit, push, tag,
merge or GitHub publication was made in it. Only a disposable local verification checkout receives a
snapshot commit, because the release builder archives committed `HEAD`.

This document merges the former root `0.2.0 handover.md` precommit note into the technical
`HANDOVER.md`; no delivery fact of either document was changed.

## TL;DR — what to test locally

Run this once from the repository root with the checkout's `.venv` active:

```bash
. .venv/bin/activate
python -m pytest -q -m "not live"   # offline gate
```

Expect **592 passed, 10 deselected**, with no failures, skips or errors. That is the only required
local test, and no other local testing is a precommit condition.

Optional, only when preparing or re-checking a release:

| Step | Command | Expect |
| --- | --- | --- |
| Release checks and installed-wheel smoke, on a throwaway clone that has a snapshot commit (the builder reads committed `HEAD`, never these uncommitted edits) | `python scripts/build_release.py --smoke` | `RC1 ok`, `RC2 ok`, `RC3 ok`; installed-wheel smoke S1–S15 pass |
| Installed-wheel UI, from outside the checkout, against an environment where the built wheel replaces the editable package | `python -I -m pytest tests/test_page.py tests/test_tutorial.py` | 30 passed; confirm AgentWiz imports from that environment's site-packages |
| Subjective presentation review through the isolated tutorial | launch and click through | no pass/fail; optional |

Not tested locally, and not a local task:

- **macOS and Python 3.10/3.13** — unverified in this environment. The existing Ubuntu/macOS ×
  Python 3.10/3.13 CI matrix remains the release criterion and runs after an authorized push.
- **Live D72, V-4–V-8/MS-21 and R7-10/R7-11 measurements** — blocked by missing configured backends
  and credentials. They are optional experiments, never part of the offline gate.
- **No manual tutorial walk, screenshot collection, repeated setup, dependency installation,
  credential entry, data migration or obsolete-file deletion is required**, and no deterministic
  regression checklist is assigned to the owner.

## Contents

1. [Owner actions before commit](#1-owner-actions-before-commit)
2. [Delivered behaviour](#2-delivered-behaviour)
3. [Integration corrections](#3-integration-corrections)
4. [Validation and environment](#4-validation-and-environment)
5. [Blocked experiments and dispositions](#5-blocked-experiments-and-dispositions)
6. [Delivery payload and publication state](#6-delivery-payload-and-publication-state)
7. [Risks, limits and security notes](#7-risks-limits-and-security-notes)
8. [Remaining owner actions](#8-remaining-owner-actions)

## 1. Owner actions before commit

1. Extract the delivery ZIP into the repository root at the stated baseline, preserving its directory
   structure and replacing the corresponding files. The ZIP contains only the changed/new repository
   files, cumulatively through Phase 2: 24 modified and 12 new files, 36 in total.
2. Delete the superseded root `0.2.0 handover.md` if the ZIP re-creates it; this merged `HANDOVER.md`
   replaces it, so a ZIP rebuilt from this merge carries 35 files. That is the only file to drop:
   **no repository file needs deleting for behaviour reasons.**
3. Review the cumulative diff and choose when to commit. No manual tutorial walk, screenshot
   collection, repeated setup or deterministic regression checklist is required.

No dependency installation, credential entry or data migration is required before commit. Existing v1
blocks convert only on their first deliberate Workshop Save.

## 2. Delivered behaviour

Phase 2 completes the Workshop UI over the headless coordinator delivered earlier in the same payload.

### 2.1 Workshop, Workbench, History and Settings

Workshop is the default, with Design/Test/Compare, a searchable block selector, dirty draft handling
and validated fragment restoration. Workbench retains the five independent utilities; History
presents named evidence before raw records. Shared Settings and a shell-quoted, absolute-root
workspace launch command remain in the header. The old saved-block inspector remains available
within Workshop; its Start now uses the same ticketed coordinator. Compatibility HTTP routes remain.

### 2.2 Authoring, forms and save plans

Nine starters and blank model/tool/script creation, exact structured field and input forms, explicit
source-only schema fallback, inline copies of shared contracts, real prompt preview,
bindings/settings, standard-description freshness, save/rename/duplicate/restore and script
source/support review are usable manually. No browser Number conversion carries structured data
values or expected literals.

### 2.3 Managed heads and legacy conversion

Managed v2 heads publish complete v1 executable generations, retain newest 20, pin readers and
protect source integrity. Conversion captures a legacy original; pruning and restoration are
disclosed in the bound Save plan. Script starters classify against their recorded wrapper/runtime,
retain attribution and regenerate only on an actual parameter edit. Old revisions retain their own
helper/data bytes.

### 2.4 Test, Compare and evidence

Test and Compare use serial, frozen normal runner calls. Examples carry explicit versions,
expectations and verification. Accepted output, checks, correctness and human reviews remain
separate. Save preference is captured at Start. Active/latest session work reattaches after reload;
saved comparisons and child histories open and delete independently. Metrics expose denominators and
unknown/partial coverage.

### 2.5 Assistance actions

Five assistance actions use the existing runner and bounded checked model calls. The UI previews
destination/settings/context, displays assumptions, requirements and diffs, and explicitly Applies or
Discards. Apply changes only matching drafts or unsaved example suggestions. Generated custom Python
is displayed for source acknowledgement; it is never executed during proposal validation or
publication.

### 2.6 Coordinator safety and run ownership

The final review's durable `dispatch_pending` marker precedes each external effect, and DELETE refuses
while a coordinator can still write. Tickets resolve accepted duplicate Starts without a second
dispatch. Existing Stop, repair, history, cancellation and script cleanup ownership remain the
execution machinery.

### 2.7 Tutorials and documentation

Fourteen isolated tutorial topics cover every retained function, migrated block runs,
authoring/testing/comparison and all five assistance actions. Deterministic packaged services require
no owner keys or live models. Version and NEWS are 0.2.0; README, architecture, release guidance and
decision records describe final behaviour.

## 3. Integration corrections

Support previews now accept the current draft, preserving simultaneous entry/support edits. Selected
root replacements carry draft-only source digests checked under the publisher reservation before
staging and before head publication. A changed root file refuses Save without changing the current
head. Duplicate and starter regeneration clear those preconditions; they are never persisted as future
live dependencies.

The persisted coordinator record reader now uses the existing strict record decoder, matching
history's treatment of finite runtime timings. Authored numbers remain exact source strings. This
fixes HTTP reads of comparisons after their session entry is evicted; the record reader refuses a
number it cannot preserve. The regression opens persisted evidence after another batch, then deletes
the comparison without deleting its child run.

Machine expectation judgement is retained separately when a human rating overrides reported
correctness, so conflicting evidence stays visible.

Description freshness hashes normalized executable facts and captured asset bytes, so renaming a
display or normalizing a wire representation keeps reviewed prose current. An actual source change
still marks it stale. Structured example inputs retain fields outside the current form until the user
explicitly removes them, avoiding silent data loss.

Exact presentation adapters share the server's authoritative contract/value codecs. Shared Contracts
edits preserve unknown local constraints; field deletion updates required membership. Checked-answer
previews use the unchanged checker/parser. No new runtime dependency, framework, model transport or
acceptance/recovery rule was added.

## 4. Validation and environment

### 4.1 Checks run and results

The final implementation passed **592 offline tests, 10 optional live tests deselected**, with no
failures, skips or errors. Release checks RC1–RC3 and installed-wheel smoke S1–S15 passed. All **30
installed browser/tutorial tests** passed against AgentWiz imported from the installed wheel's
site-packages. The cumulative implementation was verified on a fresh exact-baseline checkout. The
files-only delivery preserves those application/test bytes; this repackage changes the handover
delivery instructions only, by merging the two notes into this document.

Preparing this documentation merge re-ran the offline gate on the working tree with the checkout's
Python 3.13.13: `python -m pytest -q -m "not live"` gave **592 passed, 10 deselected in 88.95 s**
(Linux x86_64). The release checks, installed-wheel smoke and installed browser/tutorial results
above were not re-run for the merge and remain the implementation's results.

### 4.2 Local environment

Linux x86_64, Python 3.12.14, released pinned modelSpell 0.3.0, Playwright 1.63.0, jsonschema 4.26.0.
The matching Chrome for Testing headless shell 153.0.8010.12 / Playwright 1243 was obtained from
Google's official distribution after the Playwright cache was lost. No browser pin or check was
weakened. Desktop and 390-pixel Workshop views were inspected; neither overflowed the viewport.

### 4.3 Platform coverage and gaps

macOS and Python 3.10/3.13 remain unverified locally; existing CI is the release criterion. Let
normal CI perform that check after an authorized push; it is not a manual precommit task. Reproduce
the local gate and the optional release checks with the commands in
[the TL;DR](#tldr--what-to-test-locally).

## 5. Blocked experiments and dispositions

| Track | Disposition |
| --- | --- |
| D72 | LM Studio/llama-server Stop and checked-request variants use existing judgement. Live execution is blocked by absent backend configuration/credentials. |
| V-4–V-7 | Earlier Phase 1 modelSpell probe regressions passed against its released API. No new real-route result is claimed for this UI increment. |
| V-8 / MS-21 | Schema keyword, streaming/reasoning, latency and accuracy measurements remain blocked. Provider fixtures/results belong in modelSpell. |
| R7-10 / R7-11 | `scripts/workshop_experiment.py` prepares label and nested/exact extraction comparisons and submits to the actual coordinator. Live measurements remain blocked. |
| R7-8 | `scripts/replay_output.py` compares explicit saved spans/replacements through the unchanged checker, records discarded text and refuses incomplete/redacted evidence. Offline CLI regression is included. |

For offline experiment fixtures use `--task extract --print-fixture` or
`--task label --print-fixture`. Sending requires an existing server, assigned role, `--url` and
`AGENTWIZ_SESSION_TOKEN`. No key file is created. No experimental result promotes a parser,
schema-generation or routing change without a separate owner decision.

## 6. Delivery payload and publication state

- **Obsolete files: none** for behaviour. The superseded root `0.2.0 handover.md` is the single
  documentation file folded into this document; drop it if the ZIP re-creates it.
- Extract the changed files at the repository root. Generated virtual environments, browser
  downloads, scratch screenshots and build products are excluded from the changed-file payload.
- No publication was performed in the implementation checkout: no commit, push, tag, merge or GitHub
  release. The snapshot commit exists only in the disposable verification checkout the release
  builder needs.

## 7. Risks, limits and security notes

- Saved examples, source and comparison evidence can retain sensitive content.
- Scripts run as the user and are not sandboxed.
- Save-plan/source acknowledgements identify the reviewed bytes; they do not establish safety or
  freeze remote backend behaviour.
- No new runtime dependency, framework, model transport or acceptance/recovery rule was added.

## 8. Remaining owner actions

1. Extract the ZIP at the stated baseline and drop the superseded `0.2.0 handover.md`.
2. Review the cumulative diff and choose when to commit; no commit, push or publication was made for
   you.
3. Run the offline gate locally if you want a precommit result, and let the Ubuntu/macOS × Python
   3.10/3.13 CI matrix confirm platform coverage after an authorized push.
4. Optionally run the release checks/smoke and the installed-wheel UI checks; subjective review
   through the isolated tutorial is optional.
5. Live D72, V-4–V-8/MS-21 and R7-10/R7-11 measurements stay blocked until configured backends and
   credentials exist; fixture results authorize no parser, schema-generation or routing promotion.
