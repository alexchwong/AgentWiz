# AgentWiz 0.2.0 — cumulative Phase 2 handover

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
