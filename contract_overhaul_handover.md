# AgentWiz contract overhaul — cumulative implementation handover

## Scope and current status — November 5, 2026

Specification: `contract_overhaul_tech_specs.md`, version 1.0, November 5, 2026.
Baseline: `ada01c82b82f1c83ca1ca702c6d15574bc5757fc`; package remains 0.2.0.

The owner requested completion of **Phase 1A only** in this increment, and this dedicated file
as the cumulative progress record. It supersedes the overhaul entry formerly in `HANDOVER.md`,
not the unrelated release/UI handover. Existing uncommitted foundation work was preserved.
No commit, push, tag, publication, modelSpell modification or additional dependency was made.

**Phase 1A implementation is complete. Phase 1 as a whole and the overhaul are not complete.**
The focused backend checks pass. The complete offline gate is not green because remaining
storage/fixture/tutorial/browser consumers still depend on rejected legacy definitions. This
intermediate checkout is not shippable. Do not reintroduce compatibility or automatic workspace
migration to hide those failures.

## Cumulative implementation

### Phase 0 / pre-existing foundation

- Baseline and required dependency prepared; `jsonschema>=4.26,<5` is required, with obsolete
  optional-extra setup/CI/skip paths removed. modelSpell stays pinned to its public 0.3.0 API.
- `contract_schema.py` owns bounded subset recognition and canonical exact-number SHA-256 identity.
- `contract_service.py` provides pure project/edit/sample-check operations and strict v2 draft
  endpoints; exact siblings, nullable Choices and Group extra-property policies survive edits.
- A private server-owned rich-schema capture path exists for the five assistance actions.
- Packaging/smoke tooling was prepared for the required validator and offline dependency wheels.

### Phase 1A — definitions and validation

- Public `checks.parse_contract` and `contract_from_text` now always recognise the user subset.
  Legacy definition `checks`, `strict`, inline `format_version`, user policy selectors, arbitrary
  members, unconstrained empty/null/boolean schemas and rich keywords are rejected with locations.
- Contract source uses the entire exact JSON document, with a 1 MiB UTF-8 pre-decode bound.
  Duplicate/nonfinite/malformed source and invalid Unicode are refused, not recovered.
- Answer parsing has one fixed policy: whole JSON or the existing single fenced block. No object
  extraction, quote repair, coercion or competing-candidate selection was introduced.
- Text checks use explicit rules, decoded-string equality and code-point lengths without trimming,
  newline/case/Unicode normalization. Diagnostics use `text.*`; nonstrings fail at `text.type`.
  Legacy regex/definition-check execution and runtime `json_equals` were removed.
- `checks.exact_equal` is used directly for Workshop expectations, preserving exact numbers,
  object key-order independence, array order and bool/number distinction.
- Rich schemas remain limited to `parse_internal_contract` and server-generated assistance captures.
  All new user/internal snapshots explicitly record policy. Internal local-reference/no-network
  and exact Draft 2020-12 validation remain intact. Applied user contracts pass subset recognition.
- Schema-dependent saves, block runs (model/tool/script), Checked requests and repair preparation
  refuse missing validation before dispatch. Unexpected validator failures never pass or publish
  values. Text/Any JSON work without schema validation remains available.
- Renderer v2 includes captured descriptions, schemas and Text requirements. Checked request
  preparation now appends direct-value instructions from the same renderer; records and repairs
  retain the exact generated messages and captured revision. The future pin-selector/preview UI
  is still Phase 1B/2 work.
- Workshop port/starter constructors no longer inject obsolete empty `checks` fields. This small
  producer correction keeps existing assistance usable; full asset/executable migration is not
  claimed. Relevant exact-editor tests now preserve supported bounds and reject unsupported source.
- User schema value messages redact actual values and enum choices while retaining rule/location.

## Verification of this increment

Environment: Linux, Python 3.13.13, public modelSpell 0.3.0.

- Final focused recognizer/value/draft-service/named-output/model-step/block/replay/Workshop run:
  **296 passed in 6.77 s**, including the final defensive error-reporting and empty-description
  rendering corrections. Command: `.venv/bin/python -m pytest -q tests/test_checks.py
  tests/test_contract_schema.py tests/test_contract_service.py tests/test_named_outputs.py
  tests/test_model_step.py tests/test_blocks.py tests/test_workshop_replay.py tests/test_workshop.py`.
- `git diff --check`, `.venv/bin/python -m compileall -q agentwiz tests` and checkout
  `.venv/bin/python -m pip check` passed after those final code changes.
- An early patch application left a fragment in the parser and compilation caught it; repaired
  the source, then reran the focused suite. Other intermediate failures caught legacy test data
  and an invalid assistance-test payload; corrected the cases to exercise the specified policy.
- First diagnostic full offline run: **741 passed, 63 failed, 10 deselected, 2 errors**, 124.02 s.
  It ran before the final focused test migrations and additions; it is not final evidence.
- Subsequent complete offline run: **758 passed, 61 failed, 10 deselected, 2 errors**, 124.01 s,
  no skips. Command: `.venv/bin/python -m pytest -q -m "not live" --tb=short -r fE`.
  This is a **failed gate**, not Phase 1/full-overhaul sign-off. The run preceded the final
  defensive diagnostic guard and empty-description renderer adjustment; the focused suite was
  rerun afterward as recorded above. Full failures span `test_attempts`, `test_contracts`,
  `test_history`, `test_page`, `test_runner`, `test_server_config` and `test_tutorial`; both errors
  are tutorial setup/cleanup cases. Legacy shared fixtures submit `checks`/`strict` or v1 saved
  definitions; runner cases also rely on obsolete contract checks/rich user schemas. The tutorial
  bootstrap loads old contracts, preventing its dependent journeys from starting. Preserve these
  lifecycle and browser checks and migrate their assets/assertions in Phases 1B/1C/2 rather than
  skipping/deleting them. Full diagnostic log: `/tmp/agentwiz-phase1a-final-offline.log` (local,
  temporary, not a delivery artifact).
- No installed-wheel, live-provider, macOS or Python 3.10 validation was performed in this increment.
  Earlier wheel evidence below applies only to that earlier foundation snapshot.

## Remaining authorised project work (not performed in this increment)

1. **Phase 1B:** v2 contract containers, stable identities, immutable contiguous append/no-op/restore,
   family updates, exact file tokens, registry reservations, safe atomic/fsynced publication and
   symlink protection. Replace—not adapt as compatibility—the legacy store and routes.
2. **Phase 1B:** executable definitions **v3**, exact name/revision/digest references and dependency
   signatures, coherent store resolution, immutable full captures/history/repair integration,
   Used by and retained-definition deletion protection, historical local-copy conversion.
3. **Phase 1C:** complete headless HTTP services; `write_contract` role/prompt; synthesis through
   existing runner/ticket/admission/Stop machinery; bounded draft-bound proposals and minimal
   provenance; domain fixtures; migrate every active packaged/generated/test/tutorial asset.
4. **Phase 2A:** packaged shared `contracts.js`, explicit asset route, readable/source editor and
   inline/saved block-port integration, preserving exact numbers and enforcing the same subset.
5. **Phase 2B:** Workshop Blocks/Contracts library, search/families/revisions/restore/Used by,
   dirty/stale-save handling, sample/synthesis workflows, concrete Checked request pin selection,
   tutorials and browser journeys.
6. **Phase 2C:** full acceptance coverage, green offline gate, browser verification, installed prompt/
   JS/dependency smoke, final owning docs/diff review and accurate final handover.

**Version correction:** the previous progress paragraph accidentally swapped executable and metadata
versions. Specification §4 requires saved contracts v2, executable blocks v3, managed heads v2,
generation metadata v1 with added fields, new contract HTTP envelopes v2, existing Workshop
envelopes v1 and renderer v2. Do not mechanically increment unrelated formats.

No owner design decision blocks these phases. The next implementation scope is Phase 1B, not UI.

## Earlier foundation report — preserved historical evidence

The report below is moved from the release handover. Its completion/check claims describe the
earlier snapshot and were not independently rerun as installed-wheel evidence in this increment.
Its remaining-work paragraph is superseded by the phase status and version correction above.
## November 5, 2026 — contract-overhaul foundation; NOT COMPLETE

The owner requested the complete implementation of `contract_overhaul_tech_specs.md` version 1.0.
This work is an incomplete foundation, not Phase 1 sign-off or completion of that request. Baseline
was `ada01c8`, matching the specification; the specification was the only initially untracked file.
No working-checkout commit, push, tag, release or modelSpell change was made. Version stays 0.2.0.

Implemented and inspected:

- New `agentwiz/contract_schema.py`: bounded user-vocabulary recognition, exact canonical SHA-256
  digest, Text-rule consistency, nullable Choices, Group/List structure, resource limits, unknown
  keyword rejection, Python boundary checks against floats/cycles/surrogates/oversized integers.
- New `agentwiz/contract_service.py`: pure source projection, targeted exact-number edits and
  sample checks. Existing-group extra-property policy and unrelated schema content survive edits.
  Strict version-2 `/api/contracts/project`, `/edit`, and sample `/check` request envelopes use the
  existing session/workspace protections. No model or storage write is involved in these operations.
- Required `jsonschema>=4.26,<5`; removed the optional extra, install flag, optional CI job split
  and schema-test skip markers/hook. UI messages direct repair rather than extra installation;
  schema-dependent Workshop actions return `validation_unavailable` (503). The remaining legacy
  registry/routes still have their old error/envelope behaviour and must be replaced, not retained
  as compatibility support in the final implementation.
- Explicit rich internal parser and server-only assistance capture path, protected from caller
  edit/duplicate options. Internal capture policy survives record restoration. Direct exact
  equality replaces the synthetic `json_equals` contract used for Workshop correctness expectations.
- New Text rules are accepted by the runtime checker when passed a recognized capture; nonstring
  values fail at `text.type`. Renderer v2 includes captured descriptions/schema/Text rules; replay
  uses the declared renderer version. The direct-value rendering helper is not yet wired into
  Checked request dispatch. Existing public parsing/persistence is still legacy.
- Release metadata checks require the validator; smoke tooling accepts `--dependency-wheels` for
  offline required-validator/transitive wheels. Installed smoke exercises new draft routes with
  exact values above JavaScript's safe-integer range. Setup/README/NEWS/architecture/release docs
  updated for required dependency; architecture explicitly distinguishes unfinished legacy paths.

Verification on Linux / Python 3.13.13 with public modelSpell 0.3.0:

- Focused contracts/rendering/capture/replay suite: **192 passed** in 5.99 s. Subsequent focused
  recognizer/service/checks/registry/replay run after stronger repair-message assertions:
  **164 passed** in 2.29 s.
- Final full offline gate: **781 passed, 10 deselected**, 108.86 s, no skips.
  The subsequent focused 164-test run also passed after strengthening repair-message assertions;
  later edits only recorded verification evidence in this handover.
- Installed-wheel RC1–RC3, `pip check` and smoke **passed**, including version-2 project/edit/check
  and exact numeric schema validation. Used an isolated throwaway clone containing the working
  changes, committed only there as required by DEVEL; prepared hash-checked modelSpell plus
  required-validator wheelhouse. Final validation wheel SHA-256:
  `20f23a4f89cd6abd952725fa7f07393929040fc99ca63dbf247ac9b9ee7cb953`.
- `git diff --check`, Python compilation, shell syntax and checkout `pip check` passed.
- Intermediate checks caught a misplaced indentation and a hard-coded replay renderer version;
  corrected both rather than weakening assertions. A Text TypeError assertion was updated to the
  specified located failure; installation assertions now require repair diagnostics, not extra hints.
- No live backend, macOS or Python 3.10 validation. Existing browser journeys run in the full
  offline gate; new overhaul journeys do not exist yet and are not claimed as verified.

Remaining work is substantial and has no owner design blocker: finish Phase 1 in specification
order, then all of Phase 2. In particular: replace the general user parser and reject legacy
definitions/formats; append-only revision-2 registry with exact file tokens and symlink-safe atomic
storage; pinned name/revision/digest resolution and deletion usage protection; executable format 2
and generation metadata 3 with immutable captured snapshots; state/dispatch/history/repair wiring;
role-backed contract synthesis with bounded admission and minimal provenance; migrate every active
repository-owned fixture/starter/example/tutorial; shared packaged `contracts.js`, Workshop
Contracts library/editor, block-port and Checked request selectors, synthesis/sample UI; complete
new backend/browser journeys and final installed-asset verification. The current draft endpoints
and passing existing suite do not satisfy sections 19–20. Do not ship this intermediate state as
the completed overhaul or add an automatic user migration/compatibility layer to finish it.

