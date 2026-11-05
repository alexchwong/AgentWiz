# AgentWiz contract overhaul — cumulative implementation handover

## Scope and current status — November 5, 2026

Specification: `contract_overhaul_tech_specs.md`, version 1.0, November 5, 2026.
Baseline: `ada01c82b82f1c83ca1ca702c6d15574bc5757fc`; package remains 0.2.0.

This dedicated file is the cumulative progress record; it supersedes the overhaul entry formerly in
`HANDOVER.md`, not the unrelated release/UI handover. Phase 1A was committed as `841844f`. The latest
increment (owner request: **Phases 1B and 1C**) started from that commit with a clean tree.
No commit, push, tag, publication, modelSpell modification or additional dependency was made in this
checkout. (The installed-wheel smoke used a throwaway local clone, as DEVEL requires; see below.)

**Phase 1 (1A, 1B, 1C) is implemented headlessly. The overhaul is not complete: Phase 2 (UI) remains.**
At the Phase 1 gate the offline suite passes except two browser journeys that drive the replaced
Workbench Contracts editor; they are pending Phase 2, not passing and not deleted. This checkout is not
shippable as the finished overhaul: the page's Contracts utility no longer works with the new routes.
Do not reintroduce compatibility or automatic workspace migration.

## November 5, 2026 — readiness review and prerequisite corrective pass

### Review disposition and evidence limits

Reviewed checkout: `42fdb09` (`contract overhaul phase 1 completion`), with a clean working tree.
The implementation record below is historical; this review qualifies its Phase 1 completion claim.
The architecture is substantially ready for Phase 2, but **Phase 1 sign-off is blocked pending the
targeted corrective pass below**. These findings do not justify redesigning the contract system or
adding a compatibility layer. Complete the pass before treating the backend as the accepted foundation
for Phase 2 integration. This section records recommendations, not completed fixes or authority to
implement additional roadmap work.

Evidence: source tracing and in-memory probes using mocked reads on Linux / Python 3.13.13. Exact
numeric projection was also checked. `git diff --check` passed and the review changed no source files.
The review did **not** rerun pytest, installed-wheel smoke, live backends, Python 3.10 or macOS checks.
The earlier 851-passed/2-failed result remains the implementation handover's historical result, not
fresh validation of these findings. No end-to-end destructive deletion was performed by the probes.

### R1 — high: deletion reference scanning does not consistently fail closed

Relevant code: `agentwiz/workshop_assets.py`, `reference_scan` and `_declared_references`; compare
the stricter managed-head validation in `head`. `agentwiz/contracts.py`, `ContractStore.delete`,
trusts the scan's blockers/unreadable result.

The scan accepts an empty retained list without checking that `current` is represented, does not enforce
the ordinary managed-head invariants, and treats successfully decoded but structurally uninspectable
definitions as containing no references. Malformed port collections are skipped. In-memory probes
returned `([], [])` (no blockers and no unreadable paths) for each of:

- A managed head with a nonempty `current` identity but `retained: []`.
- A valid-shaped managed head whose retained generation's `block.json` is a JSON array.
- An executable-shaped object with malformed `inputs` rather than an inspectable port object.

Consequently deletion can be permitted without establishing absence of use. The Phase 2 Used by/Delete
screen must not build a safety claim on this result. This concerns damaged/external files as well as
normal writers; registry serialization alone cannot correct an incomplete scan.

Recommended correction:

1. Reconcile managed-head inspection with the existing `head` invariants: exact supported envelope,
   nonempty bounded unique retained identities, valid identity strings, and `current == retained[0]`.
   Reuse a narrowly shared validation helper if appropriate; do not introduce a second divergent format
   interpretation or require live contract resolution merely to inspect declarations.
2. Validate enough of each current/retained definition and its port/declaration structures to establish
   that references can be enumerated. Unsupported or ambiguous structures must contribute their path
   to `unreadable`, producing `reference_scan_incomplete`, rather than silently implying no use.
   Distinguish a legitimately absent port collection from a malformed one according to the executable
   format. Keep conservative detection of old string references and identifiable pins in broken blocks.
3. Continue scanning current plus retained definitions exactly once, excluding orphan generations and
   run snapshots. Preserve symlink refusal, stable blocker ordering, the registered writer and registry
   reservation, and the final contract file-token check. Do not cascade deletion or rewrite broken files.
4. Add functional regressions in the existing contracts/overhaul suites for malformed heads, omitted
   current generations, empty/duplicate retained lists, non-object retained definitions and malformed
   ports. Exercise the HTTP deletion path: expect 409 `reference_scan_incomplete` and unchanged contract
   bytes/token. Retain positive deletion cases and current/retained pin blockers; check Used by reports
   incomplete inspection instead of an apparently exhaustive empty result.

### R2 — medium: synthesis acceptance omits the complete-definition size gate

Relevant code: `agentwiz/checks.py`, `_synthesis_post_validator`; `agentwiz/workshop_service.py`,
`proposal_definition`, successful synthesis proposal creation, `synthesis_apply` and `synthesis_origin`.

The acceptance post-validator calls `validate_user_schema`, whereas conversion to a usable draft calls
`validate_user_definition`, which additionally enforces the 1 MiB complete-definition limit. A supported
schema probe produced a prospective definition of **1,201,045 bytes**: the synthesis post-validator
returned no issues, but `proposal_definition` refused it with `too_large`. Thus acceptance can declare
success and retain a proposal that Apply cannot use. Apply currently maps that rejection to
`proposal_expired`, obscuring the real reason and missing the acceptance-time repair opportunity.

Recommended correction:

1. Make the server-owned post-validator validate the same prospective user definition that application
   will construct, including the optional description and complete-definition byte limit. Keep wrapper
   validation first and preserve the authoritative subset recognizer. Factor only the small pure
   construction/validation operation needed for consistency; avoid a checks/service import cycle or
   duplicate definition-building rules.
2. Return located, value-redacted acceptance issues through the existing checker so a completed nonblank
   oversized proposal uses the existing single repair allowance. Original and repaired answers must use
   the same captured `contract_synthesis_v1` validation. Do not increase token/time/repair caps, switch
   models, truncate the proposed schema or create another call loop.
3. Keep defensive validation on Apply and Save/provenance derivation. Do not manufacture a usable
   proposal from a failed/truncated run. If an invalid recovered proposal is refused, distinguish that
   defensive refusal in the implementation evidence from genuine expiration; do not change public
   error conventions without reconciling the specification.
4. Add deterministic stand-in tests: supported schema over the complete-definition limit; a schema
   whose description pushes the complete definition over the limit; invalid-first/valid-repair success;
   and invalid-second response yielding no usable proposal. Check that rejection occurs in acceptance,
   not only on Apply, and that an ordinary accepted proposal applies/saves unchanged. Verify no proposal
   or transcript reaches disk with Save runs off and preserve ordinary redacted-history behaviour.

### R3 — medium: snapshot restoration accepts malformed reference identities

Relevant code: `agentwiz/model_step.py`, `ContractCopy.from_record`.

Definition digest and policy are verified, but identity validation only checks that name/revision are
both null or both non-null. A valid captured Text definition was restored after substituting
`name: "../not-a-name"` and `revision: true`. Missing name/revision fields also default to an apparent
inline identity. This is incomplete captured-metadata validation, **not evidence of path traversal or
live-registry resolution**: restoration uses the captured definition, not that name as a file path.

Recommended correction:

1. Require the new snapshot identity members explicitly. Accept inline identities only with both fields
   null; accept referenced identities only with a valid stable name and a positive integer revision,
   excluding booleans. Preserve digest/policy/validator verification and never consult live files.
2. Distinguish genuinely old snapshots lacking the new capture format (`migration_required`) from
   malformed new-format captures (`snapshot_incomplete`) consistently with existing restoration rules.
   Missing identity fields in an otherwise new-format capture must not silently convert a saved pin
   into an inline capture. Keep historical viewing separate from executable restoration.
3. Add focused restoration/repair regressions for invalid names, traversal-like names, zero/negative,
   boolean/string revisions, missing identity members and mixed null identities. Check valid inline and
   saved captures still restore after the live contract disappears. Assert refusals do not dispatch a
   model call and do not repair/recalculate a changed snapshot digest.

### Corrective-pass sequence and acceptance evidence

1. Re-read the specification, DEVEL and relevant architecture contracts; inspect the current tree and
   reconcile any later changes before editing. Fix R1 first, then R2 and R3 using the smallest justified
   changes. Add tests for the defects under T1–T3, not document-content or source-spelling assertions.
2. Run focused checks with the checkout's virtual environment:

   ```bash
   . .venv/bin/activate
   python -m pytest -q tests/test_contracts.py tests/test_contract_overhaul.py
   python -m pytest -q tests/test_checks.py tests/test_model_step.py tests/test_attempts.py tests/test_named_outputs.py tests/test_workshop_replay.py
   python -m pytest -q tests/test_shutdown.py
   ```

   Add directly affected history/Workshop checks as indicated by the actual diff. Preserve cancellation,
   exactly-once admission, immutable capture and registry-race coverage. Use hooks and Events/barriers,
   not sleeps, for any new race regression.
3. Run the complete offline gate, `python -m pytest -q -m "not live"`, without removing or weakening
   tests. Before Phase 2, the two already documented replaced-editor browser journeys may remain
   pending; record their actual results and require no unexplained/new failures. Phase 2C still requires
   zero failures/errors/skips. Run `git diff --check` and inspect the final diff. Report platform coverage
   honestly; Linux/Python 3.13 does not establish macOS/Python 3.10 compatibility.
4. Update this handover with the corrections, their reasons, exact commands/counts, any blocked checks
   and the resulting disposition of R1–R3. Historical results are not replacement evidence. Follow
   DEVEL's throwaway-clone procedure if installed-wheel verification is needed; do not commit the working
   checkout to satisfy a builder. No dependency change, publication or unrelated refactor is needed.

### Phase 2 carry-forward risks (not additional demonstrated Phase 1 blockers)

- **Exact browser data:** use exact source strings/projections and server-targeted edits, never rebuild
  domain numbers through JavaScript Number. Browser coverage must preserve large integers and long
  fractional bounds across edit/save/reload/attachment, not merely initial source display.
- **Single shared editor:** replace legacy page request/response/status assumptions across Workshop,
  inline ports, selectors, Checked request and tutorials. Do not leave a parallel legacy editor active.
  The two known browser failures remain planned UI migration work, not a reason for compatibility APIs.
- **Separate lifecycle actions:** synthesis Apply, contract Save, port attachment and block publication
  remain distinct. A failed block save must not delete a newly saved contract; grouping is separate from
  revision append. Preserve dirty drafts and Save / Discard / Stay on all navigation paths.
- **Pinned selection:** refresh/latest append must not move an existing block pin. Checked request must
  display and dispatch the concrete reviewed revision/digest and exact generated instruction preview.
- **Async ownership:** stale projection/check/synthesis responses must not overwrite newer edits or a
  newly selected item. Retain failed/stale drafts and reviewable assumptions; bind Apply to its source.
- **Authenticated packaged asset:** implement the explicit `/assets/contracts.js` allowlist route,
  existing token authorization and load ordering, then verify the installed wheel serves the script.
  Source-tree success alone is insufficient; do not broaden filesystem serving or add a UI framework.
- **Publication limits:** handle `durability_uncertain` using the returned real identity, without blind
  retries. Registry reservation protects cooperating AgentWiz writers, not arbitrary external editors.
  The current pre-replace hook follows the token recheck; tests must not imply detection of every edit
  after that final observation. This review did not establish an additional race defect beyond R1–R3.

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

### Phase 1B — persistence, references and captures

- `contracts.py` replaced (not adapted): version 2 flat containers per stable name; `family` is grouping
  only; contiguous oldest-first revisions; stored digests recomputed on every load (`digest_mismatch`
  makes a file unusable, never re-blessed); calendar-checked UTC `created_at`; minimal `basis` rules;
  32 MiB container limit checked before parsing. Version 1 files are listed as `migration_required`;
  newer ones as `newer_format`; links and non-regular files as `unusable`. Nothing is overwritten.
- Operations: create (expected token null), append, no-op on equal digest (`unchanged:true`, no
  rewrite), restore-as-new, family-only rewrite, reference-scanned delete. `file_token` is SHA-256 of
  the complete bytes; explicit get/save/delete and Checked request resolution read current bytes;
  stat fingerprints only drive re-scans. Casefold name collisions refused.
- Writes: same-directory temp file, fsync, token recheck immediately before replace, atomic replace,
  parent fsync; private modes 0700/0600; an ambiguous replace is settled by rereading; a failed parent
  fsync returns `publication:"durability_uncertain"` with the real new identity. Hooks
  `before_contract_replace`, `after_contract_replace`, `before_contract_delete` exist for tests.
- Logical registry reservation `Assets.registry()`: a busy flag claimed and released under C6, never
  held as a lock across I/O; contract save/family/restore/delete and Workshop block publication take it
  after writer registration; a competing registry writer gets 409 `busy`. Publication rechecks its pins
  against the live store under the reservation. C1 is held only for the brief cache refresh.
- Executable definitions are `format_version` 3. Version 1 → `migration_required` (never rewritten);
  version 2 in an executable position is a managed head (`invalid`). `workshop_assets.head` classifies
  v3 (and v1) roots as unmanaged. All producers (starters, assistance, synthesis, tutorial, scripts,
  test builders) write v3.
- References are exactly `{name, revision, digest}`; strings, `latest`, partial or mixed shapes are
  `reference_invalid`. `Declared.reference` replaces `contract_name`; `Loaded.contract_pins` replaces
  `contract_names`. Ports resolve from one `ContractStore.snapshot()`. Effective-revision signatures use
  each pin's state, never latest/family/process counters (no name-only fallback).
- `ContractCopy` carries name/revision (null inline), digest, exact text, parsed contract and policy, plus a
  server validator ID for internal synthesis captures. `from_record` verifies policy and digest and never
  resolves live files: old records → `migration_required`; changed/redacted text → `snapshot_incomplete`
  (both refuse repair; viewing is unaffected). `OutputPlan.view/from_record` nest the snapshot and reject
  disagreeing flat copies; plan identity covers canonical digests, policy/validator and rendered text.
- Generation metadata adds `contract_pins` (referenced ports only) and `contracts_digest`, validated with
  the assets for v3 generations (exact port sets, pins equal declarations and captured digests, inline
  snapshots equal the asset definition). The converted original root of a legacy conversion also gets
  its maps. Historical preview uses a private `SnapshotContracts` resolver (user policy, captured texts);
  restored drafts keep pins and publication re-resolves them. The silent `restore_contracts` inlining and
  its confirmation were removed; the explicit `make_local_copy` edit replaces one pin with its captured
  definition after a digest check.
- Used by / deletion: raw scan of current unmanaged definitions and every retained generation (current
  once, orphans and runs ignored); unreadable definitions refuse with `reference_scan_incomplete`; rows
  give block, generation, current flag, side, port, revision and `newer_available`, sorted.

### Phase 1C — services, synthesis and migration

- Version 2 HTTP API per spec 13: list, get (`?revision=` immutable selection), project, edit, save
  (optional `proposal_id`), family, restore, delete, check (`label:"Contract check"`), plus
  synthesis plan/start/apply. Strict members, no boolean integers, bounded issues, value-redacted
  messages; `contracts.StoreError` maps codes/statuses; `model_step.SnapshotUnavailable` maps to 409.
  Read-only workspaces allow list/get/project/check only.
- Checked request takes `contract:{name,revision,digest}`, resolves from current bytes, refuses missing
  revisions (404) and changed digests/unusable files (409) before dispatch; an appended newer revision
  does not invalidate the pin. The page's Checked request tab now sends the concrete latest revision and
  digest it lists (two-line consumer update; selector/preview remain Phase 2B).
- Synthesis: unassigned default role `write_contract` (cloud, family Workshop); packaged prompt
  `agentwiz/prompts/write_contract.md` (`write_contract` v1) with delimited untrusted task/output/schema
  data. Server-built ephemeral internal block (closed wrapper output `proposal`, max_tokens default 4096
  with assistance task controls, one repair, 1800 s) runs through the existing plan/ticket/start/batch/
  Stop machinery. Server-owned post-validator `contract_synthesis_v1` (hard-coded
  `checks.POST_VALIDATORS`, recorded only in internal captures) applies the subset recognizer inside
  acceptance, identically on repair. Proposals use the bounded store with kind `contract` and a
  source-draft digest; apply is idempotent, never writes, and refuses stale sources (`proposal_stale`) and
  block proposals; block apply refuses contract proposals. Save with `proposal_id` derives
  `synthesized`/`derived_from_synthesis` and a minimal server-captured basis. Unassigned role → 409
  `role_unresolved` before admission.
- `agentwiz/contract_examples.py`: five illustrative starters with valid, invalid and
  structurally-valid-but-wrong synthetic values.
- Migration (inventory with `rg`, classified): tutorial `fields` is a v2 container (family Tutorial,
  revision 1; idempotent `tutorial.ensure_contract`); `extract_fields`/`echo_numbers` pin it with a digest
  computed by `contract_digest`; `word_count`/`wait_for_stop` are v3; stand-ins now fail on a missing
  required field / wrong type; contract and repair topics `updated_in` 0.2.0. Starters, assistance,
  `scripts/workshop_experiment.py`, `scripts/smoke_installed.py`, `tests/test_live.py` and every test
  builder use v3 / new definitions. Unrelated `format_version: 1` envelopes and diagnostic `checks`
  result arrays were classified and left unchanged; `docs/decisions.md` history was left as history.
- Docs: README (Contracts rewritten incl. common-task mapping and limits, block keys, Checked request,
  tutorial list, development note), NEWS, `docs/architecture.md` (component map, contract section, routes,
  locking, errors, loader, Workshop history) and a dated `docs/decisions.md` entry recording local choices
  (family-on-append refusal, `role_unresolved`, wrong-kind `invalid`, conservative legacy string scan).

### Tests added or migrated (1B/1C)

`test_contracts.py` rewritten for v2 (C07, C09, C10, C15, C18, C19, C20, C26); new
`test_contract_overhaul.py` (C15/C18 retained-generation deletion and publication race, C16 restore/local
copy/tampered maps, C22–C25 synthesis and starters, C30 headless tutorial); `test_blocks.py` pin cases and
C14; Checked request pin/refusal and repair-capture cases in `test_attempts.py`; shutdown cases for every
contract writer in `test_shutdown.py`; page/tutorial expectation updates for the new default role, v3 builder
and pins. Rejection tests deliberately keep old formats.

## Verification of the 1B/1C increment

Environment: Linux, Python 3.13.13, public modelSpell 0.3.0, Playwright Chromium from `scripts/dev_setup.sh`.

- Baseline at `841844f` before edits: `.venv/bin/python -m pytest -q -m "not live"` → 758 passed, 61 failed,
  2 errors (as recorded for Phase 1A).
- Final complete offline run (after the last code change): `.venv/bin/python -m pytest -q -m "not live"` →
  **851 passed, 2 failed, 10 deselected, 0 errors, 0 skips** in 151.68 s. This is the Phase 1 gate as the
  specification defines it, **not** the final offline gate (Phase 2C requires zero failures).
- Pending Phase 2 (not passing, not deleted): `tests/test_page.py::test_page_contracts` and
  `tests/test_tutorial.py::test_tutorial_topics`, both stopped at the old Workbench Contracts editor
  (`contracts-save` → `data-state="error"`). A scratch probe (not added to the repository) drove every other
  tutorial topic — repair, model/tool/script blocks, Workshop design and all five assistance topics — to its
  expected state in Chromium: 1 passed.
- Race/hook suites (`test_contracts.py`, `test_contract_overhaul.py`, `test_shutdown.py`) rerun three times:
  41 passed each time.
- `git diff --check`, `compileall` (agentwiz, tests, scripts) and `pip check` passed.
- Installed wheel: following DEVEL, the working changes were copied into a throwaway local clone with its
  remote removed and committed only there; `python scripts/build_release.py --smoke` (online pinned
  modelSpell fetch, not the offline wheelhouse procedure) → RC1–RC3 ok, **smoke ok**. The first attempt
  failed on a stale smoke assertion (`contracts.checks` renamed to `validation`); the smoke script was
  migrated and rerun. Wheel SHA-256 `9fe524f1f7d3ab05be8d98b9b54db8e02937b45f942632fd6e42518b107bd5b3`
  contains `agentwiz/prompts/write_contract.md` and `agentwiz/contract_examples.py`. The smoke now also
  checks the packaged synthesis prompt via the expected `role_unresolved` refusal.
- Defects found and fixed during this increment: replay script crashed on the new snapshot exception;
  a converted legacy root's original generation lacked contract maps (history would have been unusable);
  synthesis start rejected its own ticket members; unassigned synthesis role surfaced as 500.
- Not verified locally: Python 3.10 (no interpreter available), macOS, live backends, offline-wheelhouse smoke.

## Verification of the Phase 1A increment (historical)

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

## Remaining authorised project work

Phases 1A–1C are done (above). Remaining, in specification order:

1. **Phase 2A:** packaged shared `contracts.js`, explicit asset route, readable/source editor and
   inline/saved block-port integration, preserving exact numbers and enforcing the same subset.
2. **Phase 2B:** Workshop Blocks/Contracts library, search/families/revisions/restore/Used by,
   dirty/stale-save handling, sample/synthesis workflows, concrete Checked request pin selection,
   tutorials and browser journeys.
3. **Phase 2C:** full acceptance coverage, green offline gate, browser verification, installed prompt/
   JS/dependency smoke, final owning docs/diff review and accurate final handover.

**Version correction:** the previous progress paragraph accidentally swapped executable and metadata
versions. Specification §4 requires saved contracts v2, executable blocks v3, managed heads v2,
generation metadata v1 with added fields, new contract HTTP envelopes v2, existing Workshop
envelopes v1 and renderer v2. Do not mechanically increment unrelated formats.

No owner design decision blocks these phases. The next implementation scope is Phase 2A. Phase 2 must
replace the Workbench Contracts utility (which still posts the removed v1 bodies), add `contracts.js` and
its authenticated asset route, the Workshop Blocks | Contracts library, port Inline | Saved contract
editors with Make local copy / Select newer, the Checked request revision selector and preview, and the
synthesis UI; then migrate `test_page_contracts` and the tutorial contract topic.

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

