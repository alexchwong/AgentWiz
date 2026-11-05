# AgentWiz contract overhaul — implementation plan and technical specification

Specification version: 1.0. Date: 5 November 2026.

Status: owner-authorised design for implementation. The owner accepted every recommendation in `AgentWiz_json_contract_overhaul_appraisal_v3.md.txt`. This specification resolves the remaining implementation details. It supersedes contradictory contract recommendations in discussion v2 and the contract-related portions of plan v14. It does not authorise unrelated roadmap work.

Repository: `alexchwong/AgentWiz`, branch `devel`. Inspected baseline: `ada01c82b82f1c83ca1ca702c6d15574bc5757fc` (`Improve Block design UI`). The branch still resolved to that commit when this specification was prepared. Current package version: `0.2.0`.

## 1. Instructions to the implementing coding model

Implement this document in order. MUST and MUST NOT are requirements. Do not replace specified behaviour with a superficially similar alternative. Inspect the checkout and preserve unrelated uncommitted work. If the checkout has advanced, reconcile equivalent functionality with the actual code; do not revert unrelated changes. Record material baseline differences in `HANDOVER.md`.

Do not commit, push, merge, publish, tag, change modelSpell or implement workflows. Do not create a compatibility layer or an automatic user-workspace migration. Every active repository-owned asset and packaged tutorial must use the new formats before completion. Rejection tests may deliberately contain old formats.

Keep this work on the existing 0.2.0 development line; update its NEWS entry and owning documentation. Do not invent a new package release number. If the checkout is already on a later version, preserve that version and record the difference. This instruction is not authority to merge or release.

The approved dependency change is moving `jsonschema>=4.26,<5` into required dependencies. No other new dependency is authorised. Use the existing framework-free UI, pytest, Playwright, public modelSpell API, runner, cancellation, admission, history and writer infrastructure.

Build and test all non-UI functionality in Phase 1. Build the UI in Phase 2. Both phases are part of completion; do not stop after Phase 1. At completion write actual checks, failures, blockers and remaining actions in `HANDOVER.md`. Do not substitute a patch file for changed source files if a changed-file archive is subsequently requested.

## 2. Product behaviour and fixed decisions

1. A contract describes one port value. A block's named JSON reply envelope is a separate transport protocol.
2. User contracts are Text, JSON with a supported schema, or explicitly unconstrained JSON. All three may be inline or saved and reused.
3. Use one small user schema vocabulary, with a mandatory recogniser before value validation. Keep `jsonschema` as the value-validation implementation. Preserve exact numbers.
4. Keep required/forbidden literal text, exact text and text length rules. Regex remains deferred.
5. Saved contracts have globally unique stable names. Family is mutable grouping metadata, not identity. All revisions of one contract live in one flat file.
6. Block references pin name, revision and digest. Checked request defaults to latest, but resolves and captures a numbered revision before dispatch. Running work and repairs never follow later edits.
7. Revision history is append-only through AgentWiz. Contract deletion protects current and retained block definitions that reference it. Historical runs hold independent snapshots.
8. The readable editor is the normal interface. Advanced source editing is optional and passes exactly the same recogniser. Source editing never broadens the subset.
9. Contract synthesis returns a direct subset schema, wrapped with description and notes. It has one repair and produces a reviewable draft. It never saves or attaches itself automatically.
10. No mandatory global status, confidence or evidence wrapper. Domain-specific conventions belong in editable examples and descriptions.
11. Internal assistance schemas and real MCP schemas retain their richer interfaces. A user cannot select an internal policy through JSON, HTTP, metadata or a saved file.
12. A structure pass is labelled as such; it is not a correctness or privacy certificate.

## 3. Current integration points

| Existing file | Required responsibility after change |
| --- | --- |
| `agentwiz/checks.py` | User/internal contract construction, exact value validation, text rules, existing outcome/repair integration |
| New `agentwiz/contract_schema.py` | Authoritative user subset recogniser, schema node limits and canonical contract digest |
| `agentwiz/contracts.py` | Flat revisioned files, resolution, stale tokens, integrity validation and deletion blockers |
| New `agentwiz/contract_service.py` | Headless contract draft operations, editor projection/application, synthesis request/proposal processing |
| `agentwiz/blocks.py` | New executable definition version, pinned reference parsing and dependency signatures |
| `agentwiz/model_step.py` | Captured contract identity/text/digest, snapshot restoration and internal-policy capture |
| `agentwiz/output_protocol.py` | Render schema and Text rules from the captured contract; increment renderer version |
| `agentwiz/workshop_editor.py` | Shared readable schema projection/compiler and exact bounds; no user source-only bypass |
| `agentwiz/workshop_assets.py` | Pinned publication, reference snapshots, historical export and registry-write reservation |
| `agentwiz/workshop_service.py` | Existing assistance integration, exact comparison, synthesis lifecycle through the existing runner |
| `agentwiz/state.py` | State views, contract service entry points, capture and registered writers |
| `agentwiz/ui/server.py` | Strict request validation, route mapping and explicit static-file route |
| New `agentwiz/ui/contracts.js` | Reusable contract editor and contract library screen; no framework/build dependency |
| `agentwiz/ui/index.html` | Workshop navigation, port integration, Checked request integration and shared helpers |
| `agentwiz/roles.py` | Unassigned `write_contract` definition in Workshop family |
| Tutorials, setup scripts, documentation, tests | Migrate all active examples and assumptions |

Do not put HTTP logic in the headless modules. Keep `agentwiz/__init__.py` side-effect-free. Do not extract unrelated parts of the existing page as a prerequisite.

## 4. Versioning boundaries

Use these distinct versions; do not infer one file's version from another:

| Resource | Version |
| --- | --- |
| Saved contract container | `format_version: 2` |
| Inline contract definition | No `format_version` member; enclosing document governs it |
| Executable block definition | `format_version: 3` |
| Workshop managed-head `block.json` | Remains `format_version: 2`, with `current` and `retained` |
| Workshop generation metadata | Remains `format_version: 1`; add the specified contract fields |
| New contract HTTP request/response envelopes | `format_version: 2` |
| Existing Workshop operation envelopes | Remain `format_version: 1` |
| Output protocol renderer | Increment from 1 to 2 |

Executable block version 2 is deliberately not reused: the repository already uses 2 for managed heads. Make `workshop_assets.head` recognise an executable v3 document as an unmanaged root and return the same unmanaged classification formerly used for executable v1. Reject executable v1 for execution/publication with `migration_required`; do not rewrite it on read. Higher unknown versions are `newer_format`.

Update every producer of executable definitions, including assistance-generated ephemeral blocks, tutorial files and test builders, to v3. Head envelopes and unrelated file versions must not be changed mechanically.

## 5. User contract definitions

### 5.1 Text

```json
{
  "format": "text",
  "description": "A short source-grounded reply",
  "min_length": 1,
  "max_length": 2000,
  "exact": "Acknowledged",
  "contains": ["Acknowledged"],
  "not_contains": ["CONFIDENTIAL EXAMPLE"]
}
```

Only `format` is required. `description` is optional text up to 4096 Unicode code points. Length limits are nonnegative integer JSON numbers, not booleans; `min_length <= max_length` when both exist. Bounds must not exceed 4,194,304. This is a contract-definition limit, not permission to enlarge existing answer/body limits.

`exact` is an optional string, including the empty string. `contains` and `not_contains` are optional arrays of at most 100 nonempty, distinct strings each; each literal is at most 4096 code points. Exact text is at most 1,048,576 UTF-8 bytes and remains subject to the total definition limit. Empty arrays are valid. There is no case, whitespace or Unicode normalisation. Compare the decoded output string directly, using code-point length. JSON escaping is transport only.

All supplied rules apply together. Reject inverted bounds, an identical literal in both lists, a required literal containing a forbidden literal, a required literal longer than `max_length`, or an `exact` value that violates another supplied rule. Do not implement a general satisfiability solver for combinations not covered by these checks. Omission of a rule means it does not run.

Reject `schema`, `checks`, `strict`, `pattern`, any rule not listed above and every other unknown top-level key. Retain the result's existing `checks` array as an output diagnostic format; removing a definition key does not remove diagnostic check entries.

### 5.2 JSON

```json
{"format":"json","description":"A classification","schema":{"type":"string","enum":["routine","urgent","needs_review"]}}
```

Allowed keys: `format`, optional `description`, optional `schema`. Omitted schema means any JSON value. Explicit `schema: null`, empty schema `{}`, boolean schemas and unknown top-level keys are rejected for user contracts. Show the unconstrained form as **Any JSON — no field validation**. It is valid to save that form.

Do not interpret Text as a nullable JSON string. JSON string schemas can be nullable; Text contracts always check strings. A non-string value checked against Text is a located value failure, not an uncaught TypeError.

### 5.3 Fixed parser policy

Remove `strict` from user definitions and generated assets. Preserve the current non-strict answer parser behaviour as a single fixed policy: whole JSON or the existing single fenced-block handling. Do not introduce first-object extraction, competing-candidate selection, automatic quote repair, coercion or any new recovery rule in this slice. The generated model protocol still requests a bare object and nothing else. Preserve current raw-answer tracing and parser notes.

For pasted contract **source**, use `jsonio.decode_exact` on the entire source, never the answer parser. Duplicate keys, nonfinite numbers, malformed JSON and unreadable UTF-8 fail.

## 6. Authoritative JSON Schema subset

`contract_schema.py` owns `validate_user_schema(schema)`, `validate_user_definition(obj)` and `contract_digest(definition)`. `checks.parse_contract` calls these rules; loaders and UI compile paths must not maintain independent keyword allowlists.

### 6.1 Nodes and allowed keywords

Every node is an object with `type`. A type is one of `string`, `integer`, `number`, `boolean`, `array`, `object`, or exactly `[T,"null"]` in that order. Reject other unions, null-only nodes and duplicate type members. Advanced authors must use the canonical order. Every node may contain optional `description` up to 4096 code points.

| Base type | Additional allowed keywords |
| --- | --- |
| string | `enum`, `minLength`, `maxLength` |
| integer, number | `minimum`, `maximum` |
| boolean | None |
| array | Required `items`, optional `minItems`, `maxItems` |
| object | Required `properties`, optional `required`, optional boolean `additionalProperties` |

`properties` may be empty. Its keys must match existing `blocks.IDENT`: `[A-Za-z_][A-Za-z0-9_]{0,63}`. Reject other property names; external documents with arbitrary keys use an explicitly designed key/value-list interface. Do not rename keys automatically.

`items` is exactly one node, never a list or boolean. Object `required` is an array of distinct property names all present in `properties`; omitted means none are required. Its order is preserved. `additionalProperties` omitted means **true**, exactly as standard JSON Schema. The editor creates new Groups with explicit `additionalProperties:false` and explicit `required:[]`.

Do not allow `$schema`, `$id`, `$ref`, `$defs`, anchors, `format`, `pattern`, `default`, `examples`, `title`, `const`, `oneOf`, `anyOf`, `allOf`, conditional keywords, `uniqueItems`, typed `additionalProperties`, tuples, exclusive bounds or `multipleOf`. Reject at the offending location, never discard. Format/version indicators live outside the subset schema.

### 6.2 Choices and null

`enum` is allowed only for string nodes. It is a nonempty list of distinct string values; strings may be empty. Maximum 200 string choices, each at most 4096 code points. Every string choice must satisfy that node's declared minLength/maxLength. A nonnullable Choice must contain only strings. A nullable Choice must contain at least one string plus exactly one final `null`. `type:["string","null"]` with a string-only enum is rejected as an internally inconsistent nullable Choice. The editor adds/removes null automatically; null is not an editable choice label.

```json
{"type":["string","null"],"enum":["positive","negative",null]}
```

Both keywords must permit null. Required and nullable remain independent controls. Required is a parent-property membership flag; it is disabled and true for the root and List item row.

### 6.3 Bounds and exact values

String lengths and array counts are nonnegative whole JSON numbers, excluding booleans, no greater than 4,194,304. Bound pairs are inclusive and must be ordered. Numeric minimum/maximum accept exact integers or finite Decimals within existing `jsonio`/checks limits. Reject binary floats at the user-contract API boundary rather than silently round them.

Mathematical integers include `2`, `2.0` and `2e0`; `true` is not a number. Numeric strings are not numbers. Preserve existing `_validation_copy` behaviour that converts whole Decimals to integer validation copies while retaining the original value. Non-whole Decimals remain exact. Do not change Decimal precision/exponent or reader limits as part of this change.

No global coercion, trimming, default filling, unit conversion or deduplication. Values returned to consumers must be the accepted original values.

### 6.4 Structural resource limits

Maximum 100 schema nodes. The root counts as one node. Each property schema and each `items` schema counts as one, including repeated identical definitions. Maximum depth 10: root is depth 0 and each property/item descent adds 1. Data row count does not count as schema nodes. Maximum UTF-8 definition text size is 1 MiB, before parsing. Reject unpaired Unicode surrogate code points in new contract source and literal/description fields. Do not alter unrelated document reader policies.

Report at most the existing `ISSUES_SHOWN` issues while retaining total issue count. Traverse property declarations in stored order and bound issues deterministically. Reject the schema before dispatch rather than proceeding with partial checks.

## 7. Validation architecture and outcomes

### 7.1 Dependency

Move `jsonschema>=4.26,<5` into `project.dependencies`; remove the obsolete `checks` optional extra and `needs_checks` skip paths. Update setup/install commands from `.[dev,checks]` to `.[dev]` and remove user guidance to install a checks extra. Do not change modelSpell's pin.

Keep a defensive `schema_support()` probe for corrupted or unsupported installations. Schema-dependent save/test/dispatch fails before model/tool/script execution with `validation_unavailable`. Text and any-JSON checks that require no schema can still operate. An unexpected validator failure during a run produces `contract_error` or `incomplete` under the existing distinction; never pass, publish values, or repair an infrastructure failure. Show a clear repair-installation message, not an optional-feature message.

### 7.2 Separate internal entry point

Retain rich internal proposal schemas through `checks.parse_internal_contract(obj)`. This accepts the internal JSON-schema constructs currently generated by `workshop_assist.schema`, applies existing Draft 2020-12 schema checks and local-reference/no-network rules, and uses the same exact value validator. It does not accept obsolete user `checks`, `strict` or `json_equals` fields. Internal JSON can be unconstrained only when intentionally generated by server code.

`parse_contract` and `contract_from_text` always mean **user** policy. Add a keyword-only Python flag `internal_contracts=False` to the private block parsing/capture path. Set it true only while constructing a fresh server-generated assistance or synthesis capture from server-owned schemas. Never read it from a draft, block file, metadata, query or body. Public `Assets.validate` and all public block paths stay user-policy by default. In `Service.prepare`, first generate the complete ephemeral assistance definition on the server, then invoke the private internal capture path; do not grant the policy to the caller's proposed block.

Keep the five existing assistance actions and their field-row payloads. When an assistance proposal is applied to a user block, run the resulting user contracts through the subset recogniser. A rich internal wrapper is not permission to save rich user schemas. Actual MCP argument validation remains in its own tool path; do not route MCP schemas through the user subset.

Capture policy in internal run snapshots as `policy:"internal"`; ordinary captures use `policy:"user"`. Restore internal captures only from the program's own run records using that explicit recorded policy and existing record integrity/read rules. This marker must never be a field accepted in a user contract definition or block declaration. It is an internal audit distinction, not protection against an attacker rewriting the entire workspace.

### 7.3 Text execution and exact expectations

Replace the old definition-check loop with the explicit Text rules. Diagnostic names are `text.min_length`, `text.max_length`, `text.exact`, `text.contains[i]`, `text.not_contains[i]`. Issues include locations and the rule, not actual sensitive values. Do not reuse old `_normalise` for exact comparisons.

Remove `json_equals` as a runtime contract feature. Extract the existing boolean-versus-number-aware deep exact equality into `checks.exact_equal(a,b)`: objects compare keys and values without key-order significance; arrays are ordered; bool is distinct from all numbers; int/Decimal compare mathematical values exactly. Do not use Python's unqualified `a == b`, which equates `true` with `1`. Workshop example expectations use this function directly, not a throwaway contract. No JSON serialisation round trip for equality.

### 7.4 Acceptance and repair

Keep `pass`, `fail`, `incomplete`, `contract_error`, existing issue result shape and named-output aggregation. A named output is published only when **every** output passes and the run is complete and not truncated. No partial output publication. Input failures reject before dispatch for model, script and tool blocks.

Original and repaired answers use the exact same captured definition and validator. Keep existing repair limits/settings, Stop and truncation handling. Do not auto-repair missing capabilities, contract errors, unreadable inputs, blank answers or unknown external outcomes. Keep raw-answer records and current retry policy.

## 8. Rendering instructions to models

Increment `output_protocol.RENDERER_VERSION` to 2. Preserve named envelope ownership and existing output placeholders. Each output's value goes directly under its output name; do not double-wrap a List, scalar or Group.

Render each JSON output's exact captured subset schema. Render every supplied Text rule using JSON-escaped literals and explicit counts. Render a Text description from the captured contract as well as the port purpose; do not silently ignore one. Use fixed headings **Purpose**, **Contract description** and **Value requirements**. Omit empty descriptions rather than inventing a purpose.

Include rendered definitions/rules and renderer version in output-plan freshness identity. Repairs use that captured plan, not current files. The model may contradict instructions; rendering only aligns declared structural requirements. Do not add provider `response_format`, constrained decoding, synthetic tools or new modelSpell parameters.

Checked request should append a generated single-value contract instruction section built by the same definition renderer, but it continues to request the value directly, without a named-output envelope. Request preview must show the exact generated section and its captured revision.

## 9. Stable identity, container and revision rules

### 9.1 Identity and paths

File: `<workspace>/contracts/<name>.json`. `name` uses existing workspace `valid_name`, is globally unique within contracts and immutable after creation. Also reject casefold collisions, for Linux/macOS consistency. No family directory, slash syntax or composite identity.

`family` is trimmed, nonblank Unicode text of at most 64 code points without controls; default `General`. Changing family changes grouping and file token only, not name, revision numbers, contract digest or block pins. No family registry file. Available families are distinct labels from loaded contract files plus General; **Add family** sets the draft label and writes nothing separately.

### 9.2 Container example

```json
{
  "format_version": 2,
  "name": "order_line",
  "family": "Commercial",
  "latest": 1,
  "revisions": [
    {
      "revision": 1,
      "label": "Initial definition",
      "created_at": "2026-11-05T05:00:00Z",
      "origin": "manual",
      "digest": "sha256:<64 lowercase hexadecimal characters>",
      "contract": {
        "format": "json",
        "description": "One extracted order line",
        "schema": {
          "type": "object",
          "properties": {
            "product": {"type":"string","minLength":1},
            "quantity": {"type":["integer","null"],"minimum":1}
          },
          "required": ["product","quantity"],
          "additionalProperties": false
        }
      }
    }
  ]
}
```

The digest placeholder above is illustrative, not a valid fixture digest. Compute real digests in fixture builders.

Container keys are exactly those shown. Revisions are oldest first, strictly numbered `1..latest` with no gaps, duplicates or boolean numbers; `latest` equals the final revision. Each revision requires exactly revision/label/created_at/origin/digest/contract, with optional `basis`. Label is at most 120 code points without controls. `created_at` is UTC RFC3339 with whole seconds and `Z`, written by the server; validate calendar correctness on load using the standard library. Origins: `manual`, `synthesized`, `derived_from_synthesis`.

`basis`, when present, has only `prompt_id`, `prompt_version`, `role`, `provider_kind`, `model`, optional `provider`, optional `note`. The first five fields are required. prompt_id is nonblank text of at most 64 code points, prompt_version is a positive integer, role follows valid_name, provider_kind is nonblank text of at most 32 code points, and model is nonblank text of at most 2048 code points. Optional provider is nonblank text of at most 256 code points. Note is author-entered, at most 500 code points. Reject control characters in these string fields. No task text, source records, profile object, endpoint credentials, raw response or automatic assumptions transcript. The server owns generated provenance; the model never authors it.

Maximum complete container size: 32 MiB. Do not prune revisions. If an append would exceed the limit, refuse without modifying the file and explain that the owner must create a new named contract. Contract source per revision remains limited to 1 MiB. Size limits apply before full parsing; oversized files are listed as unusable.

### 9.3 Digest algorithm

Digest only `contract`, excluding family, labels, timestamps, origin and basis. Implement one `contract_digest` function and use it everywhere.

Canonicalisation is AgentWiz-specific, not a claim of RFC canonical JSON:

1. Recursively order object keys by Python Unicode string order; preserve array order.
2. Encode strings with the existing ASCII JSON string escaping, booleans/null normally, no whitespace.
3. Encode every exact numeric value in one mathematical form: zero is `0`; otherwise take its signed coefficient digits, remove trailing zeros while incrementing its base-10 exponent, and emit `[minus]digits` followed by lowercase `e` and the signed exponent in ordinary decimal form. Thus `1`, `1.0` and `1e0` canonicalise to `1e0`; `100` to `1e2`; `0.10` to `1e-1`. No leading plus, exponent plus sign or exponent leading zeros. Do not expand huge powers or convert through float. Use Decimal tuples, not context-rounded `normalize()`.
4. SHA-256 the ASCII bytes and prefix lowercase hex with `sha256:`.

Numeric lexical changes and file whitespace do not change the digest. Array order, descriptions and absent versus explicit optional members may change it; do not attempt schema-equivalence reasoning. Display/storage writers may preserve author order and ordinary numeric spelling; the canonical form is for identity only.

Compare each stored revision digest to recomputation when loading. A mismatch makes that file unusable; never silently recalculate old digests. An independent block pin also detects coordinated changes to a contract and its local digest. This is integrity checking, not cryptographic authentication against a workspace owner editing all copies.

### 9.4 Save, no-op, restore and hand edits

Saving an edited contract appends `latest+1`. Existing revision records remain byte-for-byte equivalent as exact-parsed data, apart from whole-file formatting; never mutate their values. A save whose contract digest equals latest is a no-op: do not append, rewrite labels or update timestamp. Return `unchanged:true`. Family-only saves use the metadata operation in Section 13 and do not append a revision.

**Restore as new revision** copies the selected old contract into a new manual revision. It never moves `latest` backwards or edits an old record. If it equals latest, return unchanged. Pin changes in blocks are explicit separate saves.

Hand-editing a valid new contract or appending a valid revision is allowed on next rescan. Historical mutation is not allowed through AgentWiz. If an external editor changes an old revision, existing independent pins detect disagreement. Never offer an automatic repair that blesses changed history by updating all pins. The UI may explain restoring the original or appending a new revision. Old files and newer files are listed with their problem and never overwritten by the editor.

## 10. References, captures and historical use

### 10.1 Block reference

```json
{
  "format_version": 3,
  "name": "choose_priority",
  "inputs": {"message":{"contract":{"format":"text","min_length":1}}},
  "outputs": {
    "priority": {
      "description": "The selected queue",
      "contract": {"name":"priority_label","revision":2,"digest":"sha256:<64 lowercase hexadecimal characters>"}
    }
  },
  "steps": []
}
```

This is a declaration illustration; a runnable block still requires its valid existing single step. Reference objects have exactly name/revision/digest. They have no `format` or family. Inline objects have `format`. Reject mixed shapes, string references, `latest`, absent digest, null revision and unknown members.

Resolve all ports from one coherent ContractStore snapshot. A missing file/revision, unusable container or digest disagreement makes the block unusable with a located issue. A newly appended revision does not invalidate a pin to an unchanged older revision. Changes to another contract or family metadata do not change a block's effective execution revision. Pinned definition/digest/usability changes do.

Extend `Declared` to hold reference identity instead of just `contract_name`; update `_contract_sig` and all cache signatures to use exact pins, not the container's latest/process counter. Do not leave a name-only fallback.

### 10.2 Captured values

Extend `ContractCopy` to carry `name: str|None`, `revision:int|None`, `digest`, `text`, parsed contract and policy. Ordinary inline copies have null name/revision, a computed digest and user policy. References have the captured stable values. Store contract definitions as exact JSON **strings** in HTTP/run envelopes: do not let Decimal objects pass through ordinary `jsonio.encode`.

`ContractCopy.view` and output plan records include name/revision/digest/text/policy. `OutputPlan.from_record` and repair restoration verify the snapshot digest and apply its recorded policy. Restoration never resolves latest or requires the live contract file. If existing credential redaction changes snapshot text, flag the record incomplete for faithful replay and disable repair; do not silently regenerate a matching digest after redaction. Do not bypass existing credential sanitisation.

Historical run viewing remains possible after a contract is deleted because it uses snapshots. Old records lacking the new snapshot fields may remain viewable through the existing history reader, but cannot be repaired/re-executed; return `migration_required`. No broad history migration or backwards execution compatibility is required.

### 10.3 Managed block revisions

Keep the existing generation metadata's `contracts` exact-text map. Add `contract_pins`:

```json
{"inputs":{},"outputs":{"priority":{"name":"priority_label","revision":2,"digest":"sha256:<64 lowercase hexadecimal characters>"}}}
```

Only referenced ports appear in `contract_pins`; `contracts` contains every port. Validate each pin against its captured text and the declaration. For inline ports, verify the snapshot definition matches the asset's inline definition by canonical digest. Require exactly the declared port names in the snapshot maps, and exactly the referenced ports in the pin maps. Include this metadata in generation validation and freshness identity alongside the existing assets. Do not let an unvalidated metadata edit silently change restore behaviour.

Current block execution still checks the live referenced revision against its independent pin. Historical preview uses its captured definitions for readability even if the live file is damaged: add a private snapshot resolver for that preview, after validating generation assets, declarations and captured maps. Do not first require a successful parse against the live registry. That resolver still applies user-policy validation and is never used for current execution or new publication. Restoring a historical block creates an editable draft preserving its original references; validate that they still resolve before publication. Offer an explicit **Make local copy** action to replace a failed reference with the already captured inline definition. Do not silently inline references during export as the old `restore_contracts` path does. Remove that silent path.

### 10.4 Deletion and Used by

Before deletion, scan current unmanaged v3 definitions and every retained managed generation for reference declarations. Include current generations only once. Return blockers with block key, durable generation ID/current flag, side, port and contract revision. Ignore orphaned generations not in a head's retained list; active runs already have snapshots. Historical runs are not deletion blockers.

If a block head/retained definition cannot be inspected, refuse deletion with `reference_scan_incomplete` and list unreadable paths, because absence of use was not established. Scan raw declarations rather than requiring successful contract resolution. Do not introduce a second persistent dependency index.

The normal Used by display distinguishes current from retained references and sorts by block/side/port/revision. A port pinned to an older revision shows **Newer revision available**. Upgrade requires explicit selection, preview and saving a block revision; never bulk-upgrade automatically.

## 11. Mutation, concurrency and filesystem safety

Reuse the workspace lifetime lock. Do not introduce another lock file or database. Keep registered writers for every mutation and coordinator, and preserve shutdown ownership.

Add a **logical contract-registry write reservation** protected by the existing C6 Workshop leaf lock. It is a busy flag/owner token, not a lock held during I/O. Contract append/create/family edit/delete and block publication/deletion acquire this reservation before they can publish references or remove reference-bearing definitions. A competing registry writer gets `busy` (409) without waiting. Readers use captured snapshots. Block-only operations not affecting saved definitions, model calls and example runs do not take it.

Acquire writer registration first, then claim the logical registry reservation under C6 and release the actual C6 lock. Do checks and filesystem work without holding C6. Use existing per-block logical reservations during publication, claimed separately with actual locks released. Never acquire C1/state or another lock while holding C6. Holding logical reservation tokens across I/O is allowed; holding the underlying C6 mutex is not. Release tokens and writer ownership in `finally`, including all cancellation/failure paths.

Snapshot caches briefly under C1, validate candidate contracts outside C1, and recheck the file token immediately before replacement under the logical registry reservation. Do not hold state/store/step/C6 locks while validating a model answer, waiting, calling a model/tool, or writing run history. Existing short state-cache updates remain under C1.

The stale token is `sha256:` plus SHA-256 of the complete current file bytes. Return it as `file_token`; creation expects null. Unlike contract digest, whitespace/family/history changes affect this token. Keep inode/mtime/size as a rescan optimisation, but explicit get/resolve/save/delete must hash/read current bytes and not rely on stat alone. Check casefold collisions and expected token under the reservation. Register the write before its first possible mutation.

Write via a temporary file in the same directory, file fsync, atomic replace and parent-directory fsync. Reuse/extend existing atomic helpers without changing unrelated writers. Refuse symlinked contract directories/files and nonregular files; never follow an escaping reference. Name validation prevents path traversal. Preserve existing private permissions (directories 0700, files 0600).

If replacement succeeded but parent fsync fails, return the new identity with `publication:"durability_uncertain"`, not a false refusal inviting a duplicate append. On an ambiguous exception, reread and compare the intended complete bytes before settling outcome. Do not blind-retry. A stale tab keeps its draft and must refresh/review; it must not auto-resubmit over the newer file.

External editors that ignore the workspace lock may race the final check. Detect observed changes and refuse, but do not promise atomic multi-editor transactions. Contract append and block publication remain separate actions; orphaned unused contracts are acceptable. No rollback that deletes a previously successful contract save.

## 12. Headless editor and source operations

Implement one headless contract editor used by the library, inline ports and tests. `contract_service.project(definition_text)` validates the entire user definition and returns human fields, exact source, warnings and digest. `contract_service.apply_edit(definition_text, edit)` returns the new validated draft; no filesystem write. Reuse `workshop_editor.field_rows/compile_rows` for schema projection but make the central recogniser authoritative.

Schema rows contain `path`, type label, description, required/nullable, applicable bounds, choices and Group extra-property policy. Paths use existing arrays of property names and `[]` item segments; the root is `[]` as an empty path array, not the string `"[]"`. Bound values on the wire are decimal JSON **text strings**. Preserve explicit `additionalProperties:true`, null flags, required membership, descriptions, Choice ordering and all supported constraints on unrelated edits.

Do not rebuild the entire definition from a lossy projection. Apply targeted edits to a parsed exact copy, then validate. The inverse/compile path for new definitions must preserve all representable fields. The previous `source_only` fallback is removed for valid user contracts; unsupported loaded definitions instead have a clear problem and cannot be overwritten.

Supported `edit.op` values and required fields:

| Operation | Payload after `op` |
| --- | --- |
| `set_description` | `path`, `description`; path null addresses contract description, otherwise schema node |
| `set_text_rules` | `rules` containing only Text constraints from Section 5.1; replaces that rule set |
| `set_bound` | `path`, `keyword`, `value_text` or null to remove; keyword must apply to node type |
| `set_nullable` | `path`, boolean `value`; updates enum null membership |
| `set_required` | property `path`, boolean `value`; root/List item rejected |
| `set_choices` | `path`, `choices` strings; null handled automatically |
| `set_additional_properties` | Group `path`, boolean `value` |
| `add_property` | parent Group `path`, `name`, `node_text`, boolean `required` |
| `rename_property` | property `path`, `name`; updates required membership and preserves position |
| `remove_property` | property `path`; removes its subtree and required membership |
| `set_node` | `path`, `node_text`; replaces exactly that subtree, preserving parent's membership |

Format/root-type changes use `set_node` or a fresh definition created by **Change type**, with an explicit preview if constraints/children would be lost. No hidden conversion or silent dropping. Switching away from schema-backed JSON to Any JSON also requires this preview. Discarding an unsaved draft is a separate user action.

Advanced source application submits `definition_text` to the same project operation. Invalid source remains in the editor buffer with located issues; it cannot be saved or applied to a port. Returning to guided mode while invalid requires correcting or discarding that source edit. Source view is collapsed by default, never mandatory for normal authoring.

## 13. Contract HTTP API

All routes require the existing session token, body/size enforcement and workspace gate. Each new request includes `format_version:2`; reject unknown members and boolean integer substitutes. Return standard `code`, plain message and bounded `issues`/issue_count on failures. Raw exact definitions/schemas are strings, not nested ordinary-number HTTP objects.

Contract identity is always a validated name, never a filesystem path. Existing `/api/contracts/*` routes may be replaced; do not maintain old body shapes.

| Route | Request | Response essentials |
| --- | --- | --- |
| GET `/api/contracts` | None | version 2, items with name/family/latest/latest_digest/problem, read_only, validation support |
| GET `/api/contracts/<name>` | Optional `revision=<positive integer>` | identity, file_token, selected numbered revision, exact `definition_text`, digest, revision summaries, Used by, problem |
| POST `/api/contracts/project` | `definition_text` | projection, exact text, digest, warnings |
| POST `/api/contracts/edit` | `definition_text`, `edit` | same as project |
| POST `/api/contracts/save` | name, family, expected_file_token, definition_text, label; optional proposal_id | captured new/current revision, digest, file_token, unchanged, publication |
| POST `/api/contracts/family` | name, expected_file_token, family | new file_token, unchanged revision identities |
| POST `/api/contracts/restore` | name, expected_file_token, revision, label | append/no-op result |
| POST `/api/contracts/delete` | name, expected_file_token | deleted true, or blockers/refusal |
| POST `/api/contracts/check` | definition_text, value_text | existing check result; no model call, save or automatic acceptance |

All listed fields are required unless labelled optional; an expected creation token is explicit null. `save` creates only when expected token is null and name is absent; an update requires an existing matching token. `revision` on GET defaults to latest **at that read**, and the response always gives its number. Invalid/unusable files return enough metadata/problem for diagnosis but no editable valid projection. Read-only workspaces permit project/check and viewing, but forbid all persistence. A wholly malformed file can be deleted only through the same safe reference scan and file-token check; never overwritten.

`check` treats Text value_text as literal text and JSON value_text as an answer under the fixed parser policy. It labels the result **Contract check**. It does not mark examples human-correct.

Error codes:

| HTTP | Code and condition |
| --- | --- |
| 400 | `invalid`, `contract_invalid`, `unsupported_keyword`, `reference_invalid`, `too_large`, `migration_required` for submitted old executable/source formats |
| 404 | `no_such_contract`, `no_such_revision` |
| 409 | `stale_contract`, `contract_exists`, `contract_unusable`, `digest_mismatch`, `contract_in_use`, `reference_scan_incomplete`, `read_only`, `busy`, `proposal_stale` |
| 410 | `proposal_expired`, existing operation ticket expiry |
| 503 | `validation_unavailable`, shutdown/closed admission using existing code conventions |

List/read problems on existing files remain inspectable; do not turn a directory listing into an HTTP error because one file is invalid. Use value-redacted messages for errors, including missing literals, unless the value is already deliberately shown in the user's source editor.

### 13.1 Checked request selection

The page defaults the revision selector to **Latest** and displays the concrete current number/digest. At send, body contains `contract:{name,revision,digest}` with the concrete values reviewed by the page; no literal `latest` is dispatched. Resolve all three under the ordinary request preparation. Appending a newer revision alone does not invalidate the chosen older pin. A changed selected digest or missing selected revision refuses before dispatch. Capture full text/digest/number into the attempt and generated instruction preview.

## 14. Contract synthesis

### 14.1 Role, prompt and request

Add unassigned default role `write_contract` with description **Create and revise reusable contracts**, location `cloud`, family `Workshop`, and no implicit profile or fallback. This follows the prior proposed role. Role definitions remain editable through existing settings, so the user can deliberately assign a local definition/profile under existing location rules. Show the actual destination and existing cloud warning before dispatch; do not add a blocking approval gate for ordinary model use.

Create packaged prompt `agentwiz/prompts/write_contract.md`, prompt_id `write_contract`, prompt_version 1. It contains the complete subset, required/null distinctions, nullable enum example, explicit extra-property rule, identifier-as-string advice and no-inference advice. Supported substitutions: task_description, output_name, current_schema. Represent task/current schema as clearly delimited untrusted data. No tool calls, scripts, external requests, hidden context or automatic attachment of histories.

Use the existing role task defaults; synthesis's explicit max_tokens defaults to 4096, supports the same visible task-setting controls as assistance, repair.max_repairs is fixed 1, and budget.max_seconds is 1800. Never raise caps or switch models on repair. Missing assignment/credentials is a setup failure, not repairable.

Synthesis is for schema-backed JSON. Text is authored through the simple readable form; selecting Text disables Synthesize with an explanation. Any JSON can be changed into schema-backed JSON through a synthesis draft, but no empty/unknown schema is accepted.

### 14.2 Model response and application validation

One named output `proposal`, whose value has exactly:

```json
{"description":"Purpose of the value","schema":{"type":"string","enum":["yes","no","uncertain"]},"notes":["Assumption to review"]}
```

Description: string up to 4096 code points. Notes: up to 20 strings, each at most 2048 code points. Schema: a valid user-subset node. Use a closed internal wrapper schema for the three fields; validate the nested schema with the central recogniser **inside the same output acceptance path before attempt commit**. Extend the internal Contract value type with a server-supplied, in-memory post-validator for this purpose; it is not a user-defined callable/schema field. The callback returns located issues under `outputs.proposal.schema` and is applied identically to original and repaired answers. It must not run code supplied by the model.

For recorded synthesis repairs, recreate that validator by a server-owned internal validator ID `contract_synthesis_v1`, stored only in internal capture metadata and resolved from a hard-coded mapping; reject unknown IDs. Do not serialise functions or allow HTTP to choose validator IDs. This prevents a wrapper-valid but unsupported nested schema from bypassing repair or becoming a candidate.

Exactly one repair is offered for a completed nonblank invalid proposal, including unsupported schema keywords. Truncated responses follow existing bounded repair behaviour and are never accepted as final. A second failure yields no usable proposal. Validity is structural only; notes must remain reviewable and never become executable constraints.

### 14.3 Reuse execution machinery

Extend Workshop service actions with server-owned `contract_synthesize`; reuse its plan/ticket/start/batch/Stop/runner/attempt machinery. Do not implement a second model-call loop. Use one ephemeral internal-policy model block, one candidate, one example. It is never published as a user block. On success place a proposal in the existing bounded proposal store with a distinct kind and source-draft digest. Preserve existing admission and exactly-once ticket semantics.

New routes:

- POST `/api/contracts/synthesis/plan`: v2 body with task_description (nonblank, max 16384 code points), output_name (IDENT), definition_text or null, role (must be write_contract), settings_text optional and max_tokens optional. Returns the existing v1 Workshop plan structure wrapped as `plan`, destination and source draft digest.
- POST `/api/contracts/synthesis/start`: the same body plus expected_plan, ticket and existing confirmations. Returns operation/batch identity. Use existing POST `/api/workshop/tickets`, GET ticket/batch status and POST batch Stop routes.
- POST `/api/contracts/synthesis/apply`: v2 body with proposal_id and current definition_text or null. Checks the source draft digest, proposal kind and successful validated response; returns a draft definition_text, projection, notes and proposal_id. No write.

An original null definition represents a new empty draft and participates in digest binding. Prompt/settings/destination changes invalidate the plan. A later edit to the source draft causes proposal_stale on apply. Apply is idempotent for the same source; it does not consume the proposal or persist it. Proposal storage/recovery follows existing bounded assistance behaviour, with kind checked before use. Contract APIs cannot apply a block proposal or vice versa.

When Save receives proposal_id, compare the submitted candidate digest with the proposal-derived definition: equal means synthesized, changed means derived_from_synthesis. Build minimal basis from server-captured provenance. Omitting proposal_id creates a manual revision. Never trust origin/basis supplied by HTTP.

### 14.4 Data retention

Synthesis prompts, responses and assumptions remain in session memory unless existing Save runs is enabled. When enabled they use ordinary redacted history; do not invent separate transcript storage in contract files. Minimal basis contains no raw task, example or notes text automatically. Save runs off must leave no synthesis transcript or proposal on disk after cleanup, apart from the contract definition the user explicitly saves.

## 15. UI specification

### 15.1 Placement and layout

Keep top-level Workshop / Workbench / History. Workshop gains **Blocks | Contracts** navigation. The Contracts view supports Text and JSON, so use **Contracts**, not the narrower **JSON Contracts** label. This is a label correction to the agreed placement, not a separate utility tab. Remove the old editable contract utility from Workbench; old in-session links/tutorial targets select Workshop → Contracts. No persistent URL-compatibility layer is required.

On desktop use a left library column approximately 280 px wide and a flexible editor column; below 800 px stack them. Left column: search, family filter, New contract, and contract rows showing name/family/kind/latest. Default family filter All; search matches name and description case-insensitively. Right column: name/family header; revision selector; description; format/root editor; Check sample; Used by; save bar. Use existing design language and accessible labels.

New contract starts as a nonpersisted Text draft named blank, family General, description blank. Format choices: Text / Structured JSON / Any JSON. Structured JSON starts as an empty closed Group with required empty array. Root selector allows Text field, Whole number, Number, Yes/No, Choice, List, Group; the first label is a JSON string field, distinct from the contract-level Text format.

### 15.2 Editor behaviour

- Text: optional min/max, optional exact text switch and area, repeatable required/forbidden literal lists. Blank numeric controls omit a rule. Exact enabled plus empty area means exact empty string.
- Structured JSON: indented field table; Name, Type, Description, Required, Allow null; applicable controls beneath/within a selected row. New Group defaults closed. New properties are required and nonnullable. New List gets one string item node. New Choice defaults to one visible blank choice, which is valid; users may change it.
- All saved historical revisions are viewable. Selecting an older revision does not change live latest. Editing it produces a draft for append and displays that basis. Saving compares against the latest file_token reviewed by the editor; a stale token refuses. Restore as new revision is explicit.
- Missing descriptions produce nonblocking warnings only. For any JSON show the weaker validation label prominently.
- Advanced source is collapsed and editable for every format. Apply source validates; invalid source is kept with issues. Copy source copies the definition, not the entire history container.
- Revision label is optional, defaults blank and is not used as an identifier.
- Save shows name, new revision number, format and changes from latest in readable terms before the final write. Use the existing compact review interaction, not a second approval workflow. A no-op displays unchanged.
- Family changes on existing items have a separate Save grouping action; do not silently bundle them with an append. Name is read-only after creation. Duplicate creates a new named contract starting at revision 1.
- Navigation with dirty edits offers the existing Save / Discard / Stay pattern. Invalid drafts cannot Save. Failed/stale saves preserve the draft. Successful save refreshes the library and consumers.
- Check sample is a local validation call with clear structure result. Do not imply completeness, correctness or human verification.
- Synthesize has task description, output name and existing role/settings/destination preview. Generated assumptions appear alongside the candidate; Apply draft / Discard are explicit. Save is separate.
- Delete shows Used by blockers and never cascades into blocks, history or other contracts. Disable persistent actions in read-only workspaces, while allowing view/check/copy.

### 15.3 Block ports and Checked request

Every port has **Inline | Saved contract**. Inline uses the same editor component. Saved selection shows family/name/revision/description, defaults the revision selector to the current numbered latest when first selected, and stores explicit name/revision/digest. Subsequent refreshes do not move it. Show newer-available status and Open contract / Select newer / Make local copy actions. Shared edits never mutate a block implicitly.

**Save inline as reusable** opens a new contract draft prefilled with its exact definition. After the save succeeds, explicitly offer **Use saved revision for this port**. Declining leaves the port inline. A failed block save does not delete the new saved contract. Replacing a port contract remains an unsaved block edit until ordinary block Save succeeds.

Checked request uses the same shared library and displays selected concrete revision. Do not transfer Workbench prompt contents to synthesis implicitly; the user supplies task context deliberately.

### 15.4 JavaScript and static assets

Add `agentwiz/ui/contracts.js` as one plain script exposing `window.AgentWizContracts.create(dependencies)`. Pass existing request, DOM, exact-text/projection and navigation helpers explicitly. Keep no credentials or durable drafts in localStorage. Do not introduce module bundling, npm, a UI framework or a whole-page rewrite.

Serve this script only through an explicit authenticated `/assets/contracts.js` allowlist entry with JavaScript content type. Load it with the existing session token in the same-origin `t` query parameter, as supported by the current authoriser; await its load before constructing the contract screen or port editor. Do not weaken authentication to make a static script tag work. Do not add arbitrary path-based filesystem serving. Package the file with the existing package-data mechanism and verify an installed wheel can load it. Treat every model/user string as textContent/value, never HTML. Existing `data-test` conventions apply; page tests assert interaction/state, not wording or markup strings.

All numeric editing/transport uses strings. Do not JSON.parse exact schema source in the browser to obtain numeric values and then JSON.stringify it. Use server projections and exact source strings. Browser Number is allowed only for bounded UI counters and indices that cannot contain domain data.

## 16. Common-task fixtures and coverage

Add a headless fixture module `agentwiz/contract_examples.py` containing functions returning fresh exact definitions and synthetic examples. It is the single source for editor starter choices and focused cross-task tests. No real patient, employee or customer data. Examples are marked illustrative, not a regulatory/clinical standard. Include these five selectable starters:

1. Identifier occurrences: root List of Group with required kind Choice, value string minLength 1, source_id string minLength 1, start/end integer minimum 0. Kind choices person_name/record_id/contact/address/date/other. All fields nonnullable, Group closed, list may be empty.
2. Clinical observations: List of closed Group; parameter string minLength1; status Choice present/not_found/uncertain/conflicting/not_applicable; value nullable Number; raw_value nullable string; comparator nullable Choice eq/lt/le/gt/ge; unit/observed_at nullable string; source_id nonblank string. Every field required. No invented clinical reference ranges.
3. Classification: root Choice routine/urgent/needs_review. Demonstrates scalar output under a named envelope.
4. Invoice: closed Group invoice_id nonblank string, currency Choice AUD/NZD/USD/EUR/GBP/other, lines List of closed Group description string/quantity nullable Number/amount Number, total Number. Every field required. Amounts may be negative.
5. Action items: List of closed Group task nonblank string, owner/due_raw/due_date nullable strings, source_id nonblank string, all required.

Include at least one valid value and one structurally invalid value per starter. Also include one structurally valid but semantically wrong value for identifiers, clinical observations and invoice; tests must demonstrate that contract validation passes these while separate example assertions/review can reject them. Do not add semantic validation features to make these artificial contract failures.

The documentation must additionally map these common tasks to the same vocabulary: de-identification, medications, grounded summaries, transaction matching, legal obligations, compliance evidence, security/safety triage, letters, translation, table extraction, variable key/value records, tool-action proposals, search results, artifact references and suggested corrections. They use task-specific Groups/Lists/Text/Choices; no domain engine is added.

Document these limits beside examples: source spans need immutable source versions and code-point half-open offset checks; dates are strings without assertions; arithmetic needs deterministic code; key/value typed slots need consistency checks; real tool arguments retain tool-specific schemas; an empty extraction list must not stand for failed input processing. Before multi-block execution exists, these checks are independent script/example tests, not an automatically composed workflow.

## 17. Required repository migration

No active repository asset may depend on old user definitions. Implement an inventory using `rg` before edits, then classify every occurrence rather than replacing tokens blindly.

1. `tutorial_topics.CONTRACT`: create a real v2 container named `fields`, family Tutorial, revision 1, JSON Group id Whole number / ratio Number, both required, extra properties false. Use server/fixture digest construction, not a guessed literal. Tutorial preparation is idempotent: if correct fields exists, reuse it; if the tutorial needs reset, operate only in its temporary workspace using the existing lifecycle.
2. `extract_fields`, `echo_numbers`: executable v3; fields output uses name/revision/digest object. Preserve named outputs. Text report gets min_length1. Tool inputs that were Any JSON remain so.
3. `word_count`, `wait_for_stop`: executable v3; remove obsolete checks/strict members, preserve behaviour.
4. Rewrite the tutorial's exact-number lesson around structural contracts plus exact Workshop example expectations. A wrong numeric answer with the right type passes its contract and fails its expectation. Adjust tutorial stand-ins so the repair example still initially fails a real structural rule (missing required field or wrong type), not only equality.
5. Workshop starters, `port`, starter runtime captures, generated definitions, `tutorial_support/workshop.py`, assistance proposal builders and test builders use executable v3 and new inline definitions. Old `checks:[]` is not accepted as harmless.
6. Update current five assistance internal schemas only where necessary to construct internal captures and apply valid user contracts. Keep their row format and actions; do not rewrite them to schema-direct proposals in this task.
7. Remove schema-backed UI actions' optional-extra disabling logic; replace it with defensive validation_unavailable handling. Migrate old Text templates and source-only controls.
8. Update `model_step` captures, runner capture consumers, frozen comparisons, repair restoration, saved metadata and snapshot display. Do not lose exact text or provenance.
9. Remove old string contract references from production/tutorial assets and active generated definitions. Old-format data remains only in explicit rejection or historical-view-only fixtures.
10. Update `pyproject.toml`, dev/install scripts, CI install commands, README, DEVEL, architecture, decisions, NEWS and HANDOVER where their owning facts change. Preserve unrelated policy/history. Do not add repository-document-content tests or a second conformance runner.
11. The old generic runtime `json_equals`, contains/exact checks-list parser and user strict option are obsolete. Delete their unused paths after migrating consumers. Preserve helper logic still needed for exact expectations, fixed parsing and internal full-schema validation.

## 18. Detailed acceptance tests

Extend existing suites first; add `tests/test_contract_schema.py` for the new recogniser/digest and `tests/test_contract_overhaul.py` for cross-component cases only where existing homes are unsuitable. Group cases using parametrisation. No test per UI label, no tests that merely mirror helper implementation, no repository source-string tests. The requirements below are behaviour cases, not a request for one file/function per row.

| ID | Functional evidence | Primary home |
| --- | --- | --- |
| C01 | Every allowed node/root, nullable types, List of Group and empty object/List | test_contract_schema |
| C02 | Unknown/misapplied keywords, missing type/items/properties, malformed union, bad names, wrong required references, duplicate enum/required values rejected with locations | test_contract_schema |
| C03 | Required versus absent/null/empty; nullable enum accepts null and editor round trip preserves it | test_checks, test_workshop |
| C04 | AdditionalProperties omission allows extras; explicit false rejects; unrelated edit preserves true | test_checks, test_workshop |
| C05 | 2.0/2e0 integers, boolean rejection, huge identifier string, >2^53 numeric value and exact fractional bounds survive | test_checks, test_jsonio |
| C06 | Text exact/required/forbidden/length, Unicode code points, CRLF/case/whitespace unchanged, contradictory definitions rejected | test_checks |
| C07 | Depth/node/size edges, invalid UTF-8/surrogates, duplicates and nonfinite data fail safely | test_contract_schema, test_contracts |
| C08 | Canonical digest independent of whitespace/key order/equivalent numeric spelling, sensitive to actual values/description/array order; golden numeric examples | test_contract_schema |
| C09 | Every contract kind saved/reloaded; contiguous immutable append, no-op, restore append, family-only save, collision and malformed/newer file refusal | test_contracts |
| C10 | Restart preserves stable revision/digest; file-token stale edits include whitespace/history/family changes; observed external changes refused | test_contracts |
| C11 | Missing validator/unsupported install/corrupt validator never passes or dispatches schema work; internal rich wrapper remains valid | test_checks, test_workshop |
| C12 | User rich schema rejected through save, inline block, advanced source and applied assistance; internal policy cannot be selected through HTTP/file fields | test_blocks, test_workshop, test_block_routes |
| C13 | All input/output kinds use same contracts; invalid model/tool/script inputs cause zero dispatch; named values publish only when all pass | test_blocks, test_named_outputs, test_scripts, test_block_tool |
| C14 | Pin survives unrelated/latest append; changing selected historical contract with recomputed local digest fails independent block pin | test_blocks, test_contracts |
| C15 | Retained generations block deletion; unreadable dependency scan refuses; orphan generation/run snapshots do not block; historical display survives file removal | test_workshop, test_history |
| C16 | Historical block restore preserves references; local-copy conversion explicit; corrupted snapshot disables faithful repair | test_workshop, test_attempts |
| C17 | Plan and in-flight run freeze contracts/renderer; edits after admission cannot change repairs; stale Checked request digest refuses | test_named_outputs, test_attempts, test_ui |
| C18 | Two tabs append without lost update; contract deletion races block publication safely under registry reservation | test_contracts, test_workshop |
| C19 | Fault before replace leaves original; after replace/fsync failure reports actual new revision with uncertain durability; no duplicate append | test_contracts |
| C20 | Shutdown during each new writer/coordinator releases resources without late writes; symlinks/traversal/read-only refused | test_shutdown, test_workspace_lifecycle, test_contracts |
| C21 | Boolean-aware exact examples distinguish true/1, preserve Decimal equality and never invoke runtime json_equals | test_workshop |
| C22 | Synthesis valid direct proposal; wrapper/unsupported nested node repaired once with located errors; second failure no proposal | test_workshop or test_contract_overhaul |
| C23 | Synthesis plan freshness, duplicate ticket exactly once, wrong-kind/stale apply, Stop, missing role/key, no automatic save or port attachment | test_workshop, test_ui |
| C24 | Save-runs off leaves no transcript; enabled history uses existing redaction; revision basis contains only permitted metadata | test_history, test_contracts |
| C25 | All five domain starters load; positive/negative examples; structurally valid semantic errors distinguished | test_contract_overhaul |
| C26 | API request versions, unknown fields, limits, statuses, tokens and immutable numbered selection | test_ui, test_block_routes |
| C27 | Browser creates nested List/Group with nullable Choice, edits exact bounds, advanced edit same subset, saves/reuses pin, appends without moving block pin | test_page |
| C28 | Browser stale save keeps draft; invalid source cannot save; family filter/grouping; historical restore and Used by refusal | test_page |
| C29 | Browser synthesis preview/apply/save and inline-to-saved attachment are separate actions; navigation preserves/discards correctly | test_page |
| C30 | Packaged tutorial main paths and installed static asset work after migration | test_tutorial, installed smoke |

Use deterministic loopback stand-ins for model/tool calls. Do not use a real model or paid API to prove these gates. Use hooks and Events/barriers for races, not sleeps. Do not weaken existing cancellation, shutdown or script-cleanup tests. Run both Python 3.10/3.13 on Linux/macOS through the existing CI matrix; report any environment not locally verified.

## 19. Phased implementation plan

### Phase 0 — inventory and preparation

- Read DEVEL/.clinerules/architecture and inspect working status.
- Inventory contract consumers, generated formats, fixture builders and install commands with rg.
- Confirm baseline versions and record deviations.
- Add/prepare the dependency using normal developer setup. If it cannot be installed, report a blocked gate; do not write a native replacement or skip tests.
- Do not start UI work yet.

### Phase 1A — definitions and validation

Implement contract_schema recogniser/digest; new Text/JSON definitions; mandatory dependency; internal parser; exact expectation helper; fixed parser policy and output renderer. Update central internal captures so current assistance does not become unusable when subset enforcement lands. Add C01–C08/C11/C12/C21 coverage and migrate their test builders.

### Phase 1B — persistence, references and captures

Implement v2 containers, tokens, immutable append, family operation, restoration, source integrity, logical registry reservation and filesystem safety. Implement executable v3, pinned resolution and dependency signatures. Extend copies/output plans/history restoration and managed metadata. Implement Used by/deletion scan and explicit historical local-copy conversion. Add C09/C10/C14–C20.

### Phase 1C — headless services, synthesis and asset migration

Implement project/edit/check/API services, internal synthesis post-validation and existing runner integration. Implement bounded proposals/minimal basis; add write_contract role and packaged prompt. Add domain fixtures. Migrate every shipped/tutorial/generated contract/block definition. Update setup/CI/docs so the whole offline suite can pass with the new contracts. Add C13/C22–C26/C30 headless portions.

**Phase 1 completion gate:** all non-UI requirements operate through headless services and HTTP; all active generated/shipped assets use new contracts; offline suite passes apart from page tests that must be migrated in Phase 2. Existing page tests that depend on the replaced UI must be reported as pending Phase 2, not called passing or deleted. Do not claim the final offline gate until Phase 2 completes. A Phase 1 checkpoint is not final delivery.

### Phase 2A — shared editor and port integration

Build contracts.js, explicit asset route and reusable guided/source editor. Integrate all formats, exact projection and inline/saved ports. Replace legacy source-only paths and optional-extra messages. Add C27 and installed asset smoke.

### Phase 2B — library, revision and synthesis UI

Implement Workshop Blocks/Contracts, families/search, history/restore, Used by, save reviews, dirty navigation, Check sample and synthesis. Integrate Checked request and tutorials. Add C28/C29 and update existing page tests behaviourally.

### Phase 2C — final verification and handover

- Run focused tests for new failures while developing; once stable run `python -m pytest -q -m "not live"` with no failures/errors/skips, following existing developer setup including Playwright Chromium.
- Record executed counts, OS/interpreter and any blocked checks. Tests not run are not passes.
- Use existing packaging/installed smoke procedures to verify dependencies, prompt and contracts.js inclusion. Do not publish or commit the working checkout to run a release builder; follow the existing uncommitted-work procedure if required.
- Update README/NEWS/architecture/decisions and HANDOVER with final behaviour and real evidence. Remove obsolete test helpers only after equivalent tests exist; keep rejection cases.
- Check the final diff for unrelated changes, secrets, temporary inspection files, generated caches and unsupported dependency changes. Do not add this specification's source-inspection workspace to the repository.

## 20. Definition of done

Completion requires all normative behaviours above, all repository-owned assets migrated, the complete offline test gate passing, browser journeys verified, install/package assets checked, owning documentation updated and an accurate handover. Do not declare completion on compilation, unit tests alone, a single successful extraction or a schema-only demo.

No remaining owner design decision is required for ordinary implementation. Routine local coding choices such as helper function names may vary only when observable behaviour, file/API formats, module responsibilities and gates remain exactly as specified. If an actual checkout constraint prevents a requirement, document the precise conflict and implement independent authorised work; do not quietly substitute behaviour.

## 21. Risks and limitations

- Schema validity and a value pass do not establish factual correctness, completeness, privacy, regulatory compliance or permission to act.
- This subset intentionally excludes cross-field invariants, arithmetic, real-date assertions, typed dynamic dictionaries, recursion and branch-dependent schemas. Keep external tool validation intact.
- Contract definitions, source quotations, examples, repair messages and saved runs can contain sensitive text. Minimal provenance reduces duplication but does not make storage a privacy boundary.
- Digests detect divergence relative to independent captures; they do not authenticate a workspace against someone able to rewrite every copy.
- Noncooperating external editors can race file operations. The specified locking/atomic writes cover AgentWiz writers and observed changes, not arbitrary multi-editor transactions.
- This document specifies implementation and acceptance evidence; it does not claim those changes or tests have already been completed.

## 22. Inspected source references

All repository links below are pinned to the inspected baseline:

- [Contracts and storage](https://github.com/alexchwong/AgentWiz/blob/ada01c82b82f1c83ca1ca702c6d15574bc5757fc/agentwiz/contracts.py)
- [Validation and acceptance](https://github.com/alexchwong/AgentWiz/blob/ada01c82b82f1c83ca1ca702c6d15574bc5757fc/agentwiz/checks.py)
- [Executable loader](https://github.com/alexchwong/AgentWiz/blob/ada01c82b82f1c83ca1ca702c6d15574bc5757fc/agentwiz/blocks.py)
- [Output protocol](https://github.com/alexchwong/AgentWiz/blob/ada01c82b82f1c83ca1ca702c6d15574bc5757fc/agentwiz/output_protocol.py)
- [Workshop assets and history](https://github.com/alexchwong/AgentWiz/blob/ada01c82b82f1c83ca1ca702c6d15574bc5757fc/agentwiz/workshop_assets.py)
- [Workshop service](https://github.com/alexchwong/AgentWiz/blob/ada01c82b82f1c83ca1ca702c6d15574bc5757fc/agentwiz/workshop_service.py)
- [Assistance schemas](https://github.com/alexchwong/AgentWiz/blob/ada01c82b82f1c83ca1ca702c6d15574bc5757fc/agentwiz/workshop_assist.py)
- [Developer rules](https://github.com/alexchwong/AgentWiz/blob/ada01c82b82f1c83ca1ca702c6d15574bc5757fc/DEVEL.md)
- [Architecture and locking](https://github.com/alexchwong/AgentWiz/blob/ada01c82b82f1c83ca1ca702c6d15574bc5757fc/docs/architecture.md)
