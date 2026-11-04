# Proposal: block authoring, typed I/O and LLM output improvements

Date: November 4, 2026  
Status: **partly implemented, not completed and not a release claim.** Section 12 records
what this session built, the defects found while building it and what remains. Sections
1–11 keep the original agreed text with per-item state flags, so no approved item is
deleted.  
Repository: `/home/alex-wong/vscode_projects/AgentWiz`

**State flags.** `[DONE]` implemented and exercised by an offline check; `[PARTIAL]`
implemented for part of the described behaviour; `[TODO]` not implemented; `[DROPPED]`
superseded by an owner decision, with the original text kept for the record. Flags record
implementation state only; they are not validation of correctness beyond the checks in
section 12.5. Nothing here is committed, pushed or published.

## 1. Goal and scope

Make LLM blocks straightforward to author and test with visible system instructions,
multiple independently named outputs, schema-driven forms, per-block sampling overrides,
and role-level sampling defaults. Keep the framework-free UI, existing modelSpell
transport, exact-number pipeline, Workshop revision/publication workflow and deterministic
contract checker. No new dependency is proposed.

This is a handoff specification. Implement only this scope after owner approval; do not
interpret it as authorization to publish, change modelSpell, implement multi-step blocks,
or redesign the rest of AgentWiz.

> **Scope changes approved during the session:** the named envelope became the *only*
> model mode (no legacy mode, no conversion), reverse compatibility was waived and the
> existing workspace data was reset, the default workspace's `roles.json` and profiles were
> kept, and the Workshop assistance pane gained its own Temperature/Reasoning controls.
> Everything else here remains the agreed scope.

### Requirement mapping

| Request | Proposed delivery | State |
| --- | --- | --- |
| System prompt field | Promote the existing field into the main LLM Design pane; preserve inline/file-backed prompts. | `[DONE]` field moved into the main form with `data-test="ws-system"`; file-backed prompts still edited through the existing projection. No browser check yet asserts the placement. |
| Multiple output fields | Opt-in named JSON envelope; exact parse once, validate each field, publish all outputs atomically. | `[DONE]` named envelope is now the only model mode and is mandatory (see §3.3/§4.2); atomic publication in `executor._publish`. |
| `{{output.text}}`, `{{output.structured_table}}` in user prompt | Output-description placeholders, rendered from declared output definitions before dispatch, not references to future model answers. | `[DONE]` `templates.scan_model_text` / `fill_model_text`, model contexts only. |
| Expected output formats in system prompt | Deterministically generated, read-only output-instructions section appended to author system instructions. | `[DONE]` `output_protocol.instructions`; never persisted into the block. |
| Structured I/O as one or more typed elements | Ordered field-list editor for object/group schemas, plus list-of-values controls; support text, integer, number, boolean, choices, nested objects and arrays. | `[PARTIAL]` definition editor unchanged from its pre-existing form; value panes reused and extended. See §12.3 R1–R3. |
| Compatible forms throughout Workshop | Shared schema-driven value editor for Test, example editing and expected values; shared typed output viewer for Test/Compare. | `[PARTIAL]` example inputs and expected values now use the shared typed form; per-output panes and a raw-answer pane added. Definition-side forms remain. |
| LLM temperature/reasoning overrides | Visible validated controls using existing task settings, with explicit inheritance/reset semantics. | `[DONE]` model settings fieldset with Inherit / Custom / Backend default; `task_resets` compiled by `design_form`. |
| Model-role defaults | Role editor fields, persisted defaults, shared resolution and frozen trial settings. | `[DONE]` roles format 3, `task_settings.resolve_effective`, `state.role_capture`, frozen batch capture. |
| *(added during implementation)* Workshop assistance sampling | Assistance-pane Temperature and Reasoning controls, separate from settings the assistant proposes for the authored block. | `[DONE]` owner-requested addition; `settings_text`/`task_resets` on the assistance request. |

## 2. Repository findings and constraints

The following findings describe the inspected working tree, including pre-existing edits:

- `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/ui/index.html`: `wsDesign`
  already exposes system instructions and temperature/reasoning under **More settings**.
  Design already adds/removes ports and edits structured properties. Improve these
  controls rather than building another editor.
- `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/workshop_editor.py`:
  `TYPES`, `field_rows`, `compile_rows`, `typed`, `input_value`, `port`, and the
  `design_form`/`property_form` operations provide the starting typed representation.
  The existing types cover all the inexpensive types proposed here.
- `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/blocks.py`:
  executable format 1, model/tool `Step.output`, and `_parse_model` enforce exactly
  one model output. Scripts already support multiple declared outputs. A UI-only
  change cannot implement the requested LLM behavior.
- `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/executor.py`:
  `_execute_model` checks one contract and publishes one value.
- `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/runner.py`:
  `_prepare_model` fills prompts and sends `step.task`; frozen Workshop execution
  substitutes a captured role snapshot. Role defaults must reach both paths.
- `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/workshop_service.py`:
  preview separately prepares prompts/settings, and Start captures destinations.
  Centralize preparation so preview, planning and execution cannot diverge.
- `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/templates.py` recognizes only
  input-value placeholders; output-description placeholders do not yet exist.
- `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/roles.py` uses roles format 2
  with definitions, family, location and separate profile assignments. Defaults do
  not yet exist. Preserve the recent single-role browser and its hidden drafts.
- `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/task_settings.py` already owns
  validation and reasoning levels: backend default, off, low, medium, high. Reuse
  these; do not invent backend-specific levels or silently downgrade settings.
- `/home/alex-wong/vscode_projects/AgentWiz/docs/architecture.md` explicitly says
  contracts are never sent to models, and output-format instructions are authored
  manually. Generated output instructions are a deliberate specification change.
  Approval of this proposal must include that departure; update the architecture
  contract and record the disposition in the owning documents during implementation.
- The parser accepts the whole trimmed JSON answer, or the currently allowed single
  fenced block when non-strict. Preserve that policy. This proposal does not authorize
  substring extraction, delimiter guessing, JSON repair or additional recovery rules.
- The `checks` extra remains optional. Schema-requiring execution must retain the
  existing explicit unavailable-checks refusal; never silently skip validation.

The checkout already has uncommitted changes in Workshop, UI, tests and documentation.
The implementing agent must inspect the then-current diff and preserve those changes,
especially invalid-draft projections, output-rename bindings, retained Test inputs,
save-first example handling and unsaved-example guards. Handover test counts are not
proof that the implementing agent's tree passes.

> **Correction established at the start of the session:** the "pre-existing uncommitted
> changes" described above were already committed in `HEAD` (`devel` @ `7a11f7a`,
> "workspace management"); the only untracked file was this proposal. The behaviours named
> here therefore had to be *preserved* through the change, which the passing suite confirms:
> invalid-draft projections (`test_page.py::test_workshop_invalid_design_keeps_inputs_and_located_save_error`),
> retained Test inputs, save-first example handling and the unsaved-example guards.

## 3. Decisions to approve before implementation

1. **Meaning of output placeholders:** `{{output.NAME}}` means the named output's
   description/type/schema in a prompt. It cannot mean its not-yet-generated value.
   Post-run references and inter-step references are out of scope.
   **Resolved by the owner: agreed.** Implemented as described; `{{output.x.y}}`,
   `{{ output.x }}` and `{{output.}}` are located load errors.
2. **Meaning of structured “list”:** a structured object is authored through a list
   of named field definitions, minimum one field for new guided object definitions.
   It is not mandatory to turn every object into a runtime array. A runtime List is
   an explicit separate type. Preserve legacy empty objects, empty arrays and zero-input
   blocks; array minimum length is configurable, not universally forced to one.
   **Owner direction: reverse compatibility is not required — delete the default
   workspace's blocks and the other workspaces instead.** Performed (section 12.2).
   The object/List distinction and the configurable array minimum stand; the "preserve
   legacy empty objects" clause is moot for the deleted data but still governs blocks
   authored from now on.
3. **Output transport:** new guided LLM blocks use a named JSON envelope, even for
   one output. Existing blocks retain their legacy raw single-output behavior until
   explicitly converted. Conversion requires normal revision review.
   **Owner direction: no legacy mode and no conversion workflow.** `[DROPPED]` the
   legacy branch: `response_mode: "named_json"` is required on every model step and
   `step.output` is refused there. Tool steps keep `output`.
4. **Generated instructions:** send declared output names, descriptions, formats,
   and required schemas, but not arbitrary contract check definitions, example data,
   or private metadata. Local acceptance remains authoritative.
   **Resolved by the owner: agreed.** Covered by
   `tests/test_named_outputs.py::test_plan_roundtrip_and_instructions_exclude_check_definitions`.
5. **Role defaults:** apply to all role-based model calls, including Workshop
   assistance, not direct-profile Workbench calls. There is no implicit fallback to
   the mutable Workbench task-settings file for block execution.
   **Resolved by the owner: agreed, plus the assistance pane must be able to set its
   own temperature and reasoning.** Both done; the assistance call and the block it
   proposes carry separate settings.

If the owner intends a different meaning for items 1 or 2, resolve it before coding;
do not silently ship a different output syntax or wrap all structured values in arrays.
*(Items 1 and 2 are resolved; this caveat is retained for the next change.)*

## 4. Data model and compatibility

### 4.1 Keep existing port contracts

Keep `inputs` and `outputs` as named declaration maps in executable source. Do not
introduce a second schema language. UI rows compile to the existing JSON Schema
Draft 2020-12 subset. Keep source strings and `jsonio` exact values at API boundaries.

Guided types:

| UI type | Contract representation | Value control |
| --- | --- | --- |
| Text | text contract or JSON string property | multiline text |
| Integer / Whole number | JSON `integer` | exact numeric source input |
| Number / Decimal | JSON `number` | exact numeric source input |
| Yes/No | JSON `boolean` | boolean selector |
| Choice | JSON string `enum` | choice selector |
| Group / Object | JSON object with properties and required list | nested labeled field pane |
| List | JSON array with one item schema | repeatable item panes |

Use Number/Decimal rather than promising binary floating-point semantics. Keep exact
decimal values, including integers beyond JavaScript's safe range. Required/optional
and nullable are separate controls. Arbitrary JSON and schemas outside the supported
form subset remain source-only, with no destructive round-trip through guided fields.

### 4.2 Add an explicit model response mode

Proposed additive executable-format-1 model-step fields:

```json
{
  "id": "request",
  "kind": "model",
  "role": "fast",
  "system": {"text": "Extract facts without inventing missing values."},
  "prompt": {"text": "Read {{inputs.source}}. Return {{output.text}} and {{output.structured_table}}."},
  "response_mode": "named_json",
  "task": {"temperature": 0.2, "reasoning": "low"},
  "repair": {"max_repairs": 2}
}
```

`outputs` remains the block's declaration map and is the complete binding set.
In `named_json` mode omit `step.output`; require 1–50 declared outputs, matching
the existing output limit. Missing `response_mode` preserves the legacy exactly-one
output rule and mandatory `step.output`. Reject ambiguous combinations and unknown
mode values. Tool and script semantics do not change.

> **State: `[DONE]` for the mode, `[DROPPED]` for its legacy branch.** Owner direction:
> "no-one is using 0.1.4, disregard reverse incompatibility concerns". `_STEP_KEYS["model"]`
> is `id, kind, role, system, prompt, response_mode, task, task_resets, repair`;
> `response_mode` is required and only `"named_json"` is accepted; `output` on a model
> step is an unknown-key issue; 1–50 outputs are declared from `outputs` alone. Tool
> steps keep `output`. Because of the dropped legacy branch, an existing block with
> `step.output` now loads as `invalid` rather than running in a single-output mode — see
> section 12.3 R6 for the migration prose this still needs.

This deliberately uses an additive executable-v1 extension: old strict readers reject
the unfamiliar step keys instead of misexecuting the new mode. Do not repurpose format
2, which already identifies Workshop managed heads. Document the extension and old-client
refusal. If project versioning policy requires a new executable version, obtain approval
and update head dispatch, asset validation and imports together rather than merely
incrementing `blocks.FORMAT_VERSION`.

> **State: `[DONE]` additive keys, `[TODO]` documentation.** `FORMAT_VERSION` is still 1
> and no head dispatch, asset validation or import path was changed on that account.
> Confirmed working through `parse_block`: `workshop_assets`, starter generation, export
> and duplicate all accept the new keys because they go through the loader. The
> old-client refusal and the extension itself are not yet described in README or
> `docs/architecture.md` (section 12.3 R5).

Optional new `task_resets` is a bounded list containing only `temperature` and/or
`reasoning`. It explicitly requests backend default instead of a role default. A key
must not be present in both `task` and `task_resets`. Missing keys mean inherit.
Existing explicit `null` temperature / empty reasoning should retain their historical
backend-default meaning; retain raw key presence when resolving legacy blocks.

> **State: `[DONE]`.** `blocks._task_resets` bounds the list, refuses duplicates and
> unknown names, and `_parse_model` normalises an explicit `null` temperature or blank
> reasoning into a reset while refusing a name present in both `task` and `task_resets`.
> `task_settings.resolve_effective` treats a reset as "do not inherit". One UI gap is
> recorded as section 12.3 R4: a Custom control left blank currently behaves as inherit,
> not as reset.

### 4.3 Role defaults

Write roles format **3** when defaults are persisted; read formats 1 and 2 unchanged:

```json
{
  "description": "Routine extraction",
  "location": "local",
  "family": "Data & extraction",
  "task_defaults": {"temperature": 0.2, "reasoning": "low"}
}
```

Only temperature/reasoning are allowed in `task_defaults` for this increment. Reuse
the existing validators, reject unsupported keys and non-finite/invalid values, and
keep temperature zero distinct from absence. Missing defaults mean backend defaults.
Old readers refuse newer roles files rather than dropping behavior. Preserve unknown
top-level fields and legacy assignment-save protections. A legacy save request that
omits definitions must retain defaults on retained roles, just as it retains location
policies; it must not erase them or downgrade the file version.

Resolve each field independently:

1. Explicit block override wins.
2. Explicit block reset selects backend default and prevents inheritance.
3. Otherwise use that role's saved default.
4. Otherwise omit the argument and use backend default.

Other existing task settings remain exactly as configured on the block. Resolve once
for each prepared execution/batch capture and preserve the effective settings for every
repair. Never mutate a shared role definition or `TaskSettings` instance.

> **State: `[DONE]`, including two defects found and fixed while implementing it.**
> `roles.FORMAT_VERSION = 3`; `parse_definitions` accepts only `task_defaults` with
> `temperature`/`reasoning` and validates through `task_settings.parse_defaults` (zero kept,
> blank reasoning dropped, `max_tokens` refused). Read tolerance for formats 1 and 2 is
> kept, per the owner's adopted recommendation; this checkout writes format 3 and
> `workspaces/default/roles.json` was kept rather than deleted.
>
> **Defect 1 — assignment-only saves downgraded the file.** `state.save_roles`' legacy
> branch reused `roles.build_raw(..., definitions=None)`, which wrote
> `format_version: 1` and dropped `definitions`, silently erasing every saved default and
> every unassigned role. Fixed: `build_raw` always writes format 3 and retains existing
> definitions, and the legacy branch now copies all current definitions instead of only
> assigned ones. Covered by `tests/test_roles.py` and
> `test_named_outputs.py::test_roles_v3_keeps_defaults_and_unassigned_roles`.
>
> **Defect 2 — resolution was duplicated.** Preview recomputed sources separately from
> dispatch. Fixed by a single `runner.model_messages` path plus `state.role_capture`, which
> returns the destination snapshot and a detached copy of the role's defaults under one
> lock acquisition. `Effective.sources` is recorded per attempt
> (`model_step` `settings_sources`), so a repair keeps the settings it was reviewed with.
> Not verified: concurrent modification of `roles.json` during Start (section 12.3 R8).

## 5. Deterministic output protocol and checking

For outputs named `text` (text contract) and `structured_table` (JSON contract), the
model returns exactly one JSON object:

```json
{
  "text": "Two items were found.",
  "structured_table": {
    "rows": [
      {"label": "notebook", "quantity": 3},
      {"label": "pen", "quantity": 2}
    ]
  }
}
```

The keys must exactly match declared output names: no missing or additional keys.
A text output must be a JSON string in the envelope, not a free-text section. A JSON
output is the actual value, not a string containing serialized JSON. Structured table
is an ordinary user-defined object/array schema, not a new special data type.

Implement one shared model-response checking abstraction used by first attempts,
repairs, preview/replay and final output publication:

1. Parse the envelope once through the existing exact JSON-answer parser, preserving
   duplicate-key refusal and strict/fenced behavior. Specify a fixed named-envelope
   policy consistent with the current non-strict parser; do not infer it inconsistently
   from whichever output contract happens to be first.
2. Require the exact key set and string values for text outputs.
3. Check each already parsed field against its own captured contract. Extract/reuse
   value-level checker logic: do not serialize and parse the whole answer again for
   every output. Text checks receive the decoded text; JSON checks receive typed values.
4. Build one aggregate attempt check, preserving existing overall statuses and bounded,
   located per-output issues. Keep per-output results in an additive machine-readable
   field for result panes and repairs. Do not use a synthetic schema alone as a
   substitute for existing text/content checks.
5. Only `complete` plus aggregate `pass` publishes outputs. Stage all values and commit
   them together; no partial output survives another field's failure, truncation or Stop.
6. Repair the entire envelope, using existing repair budgets and captured settings.
   Include failed output paths/rules, not interpolated untrusted values. Retain the raw
   answer and original messages as immutable attempt evidence.

An internal checking-plan type may carry legacy-single versus named-envelope behavior
through `PreparedAttempt`. Preserve old attempt records and `ContractCopy.from_record`
by reading absent new fields as legacy. New repairs must reconstruct the captured plan,
not re-resolve current contracts. Do not apply new validation to old historical records.
Update replay to understand the named plan, or explicitly refuse unsupported evidence;
never incorrectly report a legacy check as validating a multi-output response.

> **State: `[DONE]` items 1–6, `[DROPPED]` the legacy-single branch, `[PARTIAL]` replay.**
> `checks.check_named_answer` parses once through `envelope_contract()` (non-strict, one
> JSON value, duplicate-key refusal unchanged), requires the exact key set in any order,
> requires a JSON string for a text output, then checks each already-parsed value with
> `check_value` and returns `NO_VALUE` unless the aggregate passes. The aggregate keeps
> `_assemble`/`_entry`/`_Issue` bounds and exposes per-output results under
> `check["outputs"]`. `executor._publish` stages every output and commits them together;
> `model_step.OutputPlan` is captured on the attempt and `prepare_repair` rebuilds it from
> the parent record. Items 1–6 are covered by
> `tests/test_named_outputs.py::test_named_outputs_exact_values_and_atomic_failure` and the
> existing attempt/repair scenarios.
>
> Replay now executes the captured plan (`scripts/replay_output.py`) instead of refusing
> named evidence outright, and returns `unsupported_evidence` for an unreadable, incomplete
> or unknown-renderer plan. **No test exercises that path yet** (section 12.3 R7). The
> single-contract path (`checks.check_answer_value`) is unchanged and still serves Workbench
> attempts and contract-only replay; what was dropped is the block-level single-output mode.

## 6. Prompt composition and output placeholders

Create one pure, deterministic output-instructions renderer and a shared model
preparation path. Suggested location: a narrowly scoped new module
`/home/alex-wong/vscode_projects/AgentWiz/agentwiz/output_protocol.py` if the existing
modules would otherwise become coupled. It owns no HTTP, storage or transport.

For a named block, the effective system prompt consists of:

1. The author's system instructions, with normal input placeholders filled.
2. A clearly delimited generated section requiring a JSON envelope, exact output keys,
   JSON-string encoding for text values, and no commentary outside the envelope.
3. Per-output description and expected format; include the complete declared JSON
   schema for structured outputs, including required properties, nesting, enum,
   nullable/array rules and additional-property policy. Escape it with `jsonio`.

Schema references must remain self-contained: preserve local definitions and do not
relocate `$ref` targets into a combined schema where their meaning changes. External
schema fetches remain forbidden. Do not claim that generated instructions or model
compliance replace local checks.

Extend model prompt scanning to validate `{{output.NAME}}` in user/system prompts.
Render it as a stable description such as the output key, value format and exact schema,
not a sample answer and not a value lookup. Reject missing names, malformed reserved
output placeholders and nested output paths in this increment. Keep unrelated literal
double-brace text and existing input behavior unchanged. Do not enable these markers
in script templates or MCP arguments accidentally; those contexts remain input-only.

Tokenize the original template once and substitute once, so braces inside inserted
input text, descriptions or schemas are never recursively interpreted. Add output
definition provenance/digests to fills without mislabeling them as input values.
Renaming/removing a referenced output gives a located validation issue, not a blind
text replacement. Update actual bindings automatically where unambiguous, preserving
the recent output-rename fix.

Generate system output instructions automatically for new named blocks. For legacy
blocks, expose an explicit upgrade action rather than changing their messages on load.
Both modes must show what will actually be sent. Repeated previews never append the
generated section to saved author text or duplicate it.

> **State: `[DONE]` for generation and preview, `[DROPPED]` for the upgrade action.**
> `output_protocol.instructions()` is pure and deterministic; the generated section is
> computed in `runner.model_messages` and never written to `block.json` or to
> `prompts/*.md`, so repeated previews cannot append or duplicate it. `describe()` renders
> `{{output.NAME}}` from the same declaration, so the user prompt and the system section
> cannot disagree. Filled text is never rescanned (`fill_model_text` walks markers of the
> original text), so a value or description containing `{{…}}` stays literal. The
> repository now has no legacy blocks to upgrade — the default workspace's block was
> deleted — so no conversion action was built.
>
> `model_messages` raises a located `FillError` when the encoded request would exceed
> `insertion.REQUEST_LIMIT`, which also covers the generated section's token/byte cost.
> `workshop_service.preview` returns `prepared.system`, `prepared.generated`,
> `prepared.system_sent`, `prepared.prompt`, `response_mode`, `task`, `sources`,
> `role_defaults` and per-output `name/description/format/schema`.
>
> **Decision recorded during implementation:** the instructions ask for one JSON object with
> the exact key set *in any order*, rather than demanding a key order, and they forbid
> commentary or fences only *outside* the envelope, so a text output may legitimately contain
> Markdown. A pseudo-JSON skeleton with `<text>`/`<object>` placeholders was rejected because
> a model can copy it literally. See section 12.3 R9 for the wording still to be reflected in
> README.

Preview must expose author system text, generated instructions, effective system/user
messages, response mode, effective task values and their sources. Account for generated
instructions in the existing request-size limit. Freeze protocol renderer identity,
resolved contracts, role defaults and effective task at Start; include them in the
reviewed configuration/freshness identity. A configuration change between review and
Start follows existing stale-plan behavior; edits after Start cannot change a batch.

> **State: `[PARTIAL]`.** Preview, sources and the request-size limit are done
> (`runner.model_messages`, `workshop_service.preview`). Freshness covers the executable
> definition, captured ports, `role_defaults`, effective `task` and `task_resets`, so a
> content change invalidates review, and a started batch freezes its capture. Two gaps
> remain: the review `basis` digest does not carry the plan identity, so a change to
> `output_protocol.RENDERER_VERSION` alone would not invalidate a reviewed plan, and no
> test asserts that a role-default edit between review and Start is refused (section 12.3
> R10). The *attempt* record does carry it: `OutputPlan.view()` stores
> `mode`, `renderer` and the plan `digest`, and a repair rebuilds the plan from that record.

## 7. UI specification

### 7.1 LLM Design

Use the existing Design view, with the following main sections:

1. **Inputs:** named port rows with type, description, required structure and Edit fields.
2. **Instructions:** visible System prompt and User prompt textareas, each with input
   token insertion; user prompt also offers output-description tokens.
3. **Outputs:** named output cards with descriptions, type/schema and add/remove actions.
   Show legacy versus named-envelope mode and a deliberate legacy conversion action.
4. **Model:** selected role, role setup link, Temperature and Reasoning controls.
5. **Request preview:** generated output instructions and effective messages/settings,
   read-only, with Copy buttons and refresh driven by the common preparation path.

Keep advanced task JSON, budgets, repair counts and raw contracts available without
requiring them for common authoring. Reasoning is a select populated from validated
levels, not an unconstrained text box. Temperature reuses existing validation/ranges.

Each override has Inherit role default / Custom / Backend default modes. Show the
inherited value/source adjacent to the control, preserve zero, and do not serialize an
inherited displayed value as a custom override. A role edit/selection change updates
inherited previews, not explicit custom values. Invalid values stay editable and block
Save/Start with located diagnostics rather than disappearing during rerender.

> **State: `[PARTIAL]`.** Done in `wsDesign`: the System textarea was moved out of *More
> settings* into the main form (`data-test="ws-system"`, placed before the instructions);
> a *Model settings* fieldset gives Temperature and Reasoning a
> `Inherit role default | Custom | Backend default` selector each, hides the value control
> unless Custom is chosen, and prints the role's saved default beside it; `Insert output …`
> buttons insert `{{output.NAME}}`; a static line states the response mode; *Request preview*
> calls the shared preview. `design_form` now compiles `resets` into `task_resets` and
> removes those keys from `task`, so an explicit Backend default survives a save.
>
> Not done: output cards with add/remove/type-schema editing in this pane (ports are still
> plain rows in the Outputs fieldset), a copy button on the request preview, and a browser
> check for the three override modes or the field placement — the page suite passes with the
> controls present but nothing asserts their behaviour (section 12.3 R1, R4, R11).

### 7.2 Structured definition editor

Present named field rows with name, type, description, required and nullable controls.
Objects recursively contain field rows; Lists contain one editable item definition.
Provide add/remove controls and numeric/string/list bounds already supported by
`compile_rows`. Newly created guided groups require at least one named child before
Save/Start; allow the intermediate empty editing state and preserve legacy empty groups.
Do not impose minimum-one on all input ports or runtime lists.

Use current depth/row limits and expose server validation failures. Refuse duplicate
names. A type change that would discard children requires explicit confirmation or
retains the current `field_has_children` refusal. Advanced schemas remain source-only;
shared contracts remain references unless explicitly copied inline. Do not edit a
shared definition globally as a side effect of editing one block.

> **State: `[TODO]`.** `wsFieldEditor` still implements the pre-existing shape: one
> dialog per field with name/type/description/required/nullable/bounds/choices plus
> add/remove buttons, server-side duplicate-name and `field_has_children` refusals, and a
> `source_only` fallback for schemas outside the guided subset. Not implemented: the
> minimum-one-child rule for a newly guided group, an explicit confirmation for a type
> change that would discard children, and a stable ordering control. See section 12.3
> R2 and R3.

### 7.3 Shared schema-driven value panes

Extend/reuse current typed forms rather than deriving a form from the current value's
shape alone. An initially empty object must still show the fields declared by its schema.
Use this editor for one-off Test inputs, saved-example add/edit, and applicable exact
expected-value/assertion editors. Reuse the read-only rendering in Test and Compare.

- Text uses text boxes; numbers use source-string fields; booleans and choices use
  selects; objects use multiple labeled child boxes; arrays use repeatable item panes.
- Optional fields have explicit Include/Omit state. Nullable fields have explicit Null
  state. Missing, empty text, null, false and zero must remain distinguishable.
- Coerce only control text at the form boundary: valid exact JSON numeric spelling to
  integer/decimal as appropriate; explicit selections to booleans/null; no truthiness,
  locale-dependent parsing, `parseFloat`, or implicit string-to-object conversion.
- Reject invalid/incomplete numbers with a field path and preserve entered text.
  Integer validation follows the existing checker, including mathematically whole
  exact decimals; do not create inconsistent browser/server integer rules.
- Preserve raw API strictness. Do not coerce malformed model outputs into a passing
  contract or change the type of API-supplied inputs behind the caller's back.
- Source-only schemas keep an expanded exact JSON editor and current diagnostics.
- Preserve unsaved inputs, optional states, example selection and pending example
  changes across Design/Test/Compare, preview, save-first cancellation and write refusal.
  Schema changes keep compatible values; incompatible/orphaned values require an
  explicit correction/reset, never silent deletion or accidental example rewriting.

Show each accepted output in its own pane with type-aware contents, exact value view
and Copy. Show raw model answer separately from accepted outputs. Failed answers may
show candidate evidence but must not appear as accepted output panes. Every output panel
has Copy, including raw answer and effective prompt panels. Use safe DOM text APIs.

> **State: `[PARTIAL]`.** The shared typed form (`wsInput`, built from the contract's
> `fields` with Include/Omit and Null states) already existed for one-off Test inputs; it
> is now also used for saved-example inputs and for exact expected values, with an opt-out
> so those panes do not overwrite `ws.inputs`. Example editing validates through the dialog's
> own async confirm and keeps the dialog open with the located message on a validation
> failure instead of closing and losing state. Each declared output already renders its own
> pane; per-output checks and a separate *Raw model answer (not accepted output)* pane with
> Copy were added to the trial result, and the raw answer is bounded by
> `workshop_service.PREVIEW_LIMIT`.
>
> Remaining: the form is still derived from the schema rows only when they exist, so a
> schema-driven empty object shows no fields until it has one child; there is no explicit
> orphan/incompatible-value handling when a schema changes; Copy on the effective-prompt
> panel is missing (the request preview has no copy control yet). Covered by
> `test_page.py::test_workshop_structured_inputs_keep_exact_literals_across_views_and_reload`
> and `test_workshop_one_off_and_pending_examples_survive_cancel_and_write_failure`; the new
> example-form path has no dedicated assertion (section 12.3 R11).

### 7.4 Role dialog

Add Default temperature and Default reasoning to the selected role editor. Backend
default is an explicit empty/unset choice; use the same validation as block controls.
Preserve values across family/location filters, selecting another role, profile-dialog
round trips and Save all changes. Save every role draft, including hidden ones.
Do not change profile capabilities or the local/cloud enforcement policy.

> **State: `[DONE]`.** `roleRow` gained `Default temperature`
> (`data-test="roles-default-temperature"`, blank = backend default) and
> `Default reasoning` (`data-test="roles-default-reasoning"`, a select whose first option is
> *Backend default*). `rememberRole` reads them into the draft's `task_defaults`, and
> `save-roles` posts every draft's `task_defaults`, refusing a non-numeric temperature
> before the request. Because drafts, not rows, hold the values, filtering, selecting another
> role and hidden drafts all keep them. Covered by
> `test_page.py::test_roles_single_editor_filters_families_and_retains_other_edits`
> (temperature `0` plus `low` persisted), `test_page.py::test_page_roles` and
> `tests/test_roles.py`. Not covered: a template/ceilings check that a *cleared* temperature
> removes a previously saved default through the UI (the HTTP path is covered by
> `test_named_outputs.py`).

## 8. Implementation work packages

All paths below are absolute. Follow existing patterns and public service boundaries.

> **Overall package state:** A `[DONE]` (except `workshop_assets.py`, verified only through
> the loader — no edit was needed), B `[DONE]` with replay unproven by test, C `[PARTIAL]`,
> D `[PARTIAL]`, E `[TODO]`.

### A. Contracts, schema/version decisions and typed defaults

- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/roles.py`: format-3 read/write,
  bounded defaults validation, old-format compatibility.
- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/task_settings.py`: shared pure
  effective-setting resolver and provenance; existing validators remain authoritative.
- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/state.py`: atomic role/default
  resolution under existing locking; preserve legacy saves and role status projections.
- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/blocks.py`: model mode/reset
  validation, multi-output binding representation and unchanged legacy/tool rules.
- `[DONE by inspection]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/workshop_assets.py`:
  confirm validation, export/import, reference collection and revision digests accept the new
  fields without bypassing executable-v1 validation or confusing managed-head format 2.
  No edit was needed; `parse_block` is the only admission path. **No test names this
  specifically** — it is covered indirectly by every Workshop save/duplicate test.

Deliver a working non-UI model with compatibility fixtures before UI changes.
*Superseded: the non-UI model was delivered, but no compatibility fixtures were written
because the legacy mode was dropped.*

### B. Shared output protocol, prompt preparation and acceptance

- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/output_protocol.py` (if needed):
  deterministic instructions, checking-plan representation and envelope validation.
  Created as specified: `Spec`, `specs_for`, `instructions`, `describe`, `identity`,
  `digest`, `MODE`, `RENDERER_VERSION`. Envelope *acceptance* lives in
  `checks.check_named_answer`, not here, to keep `checks` the single owner of what an
  answer means.
- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/templates.py`: context-scoped
  output-description tokens, scanning, single-pass substitution and provenance.
- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/checks.py`: reuse parsed-value
  validation and aggregate statuses/issues, retaining exact arithmetic and parser policy.
- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/model_step.py`: captured check
  plan, aggregate attempt checks and backward-compatible repair reconstruction.
- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/executor.py`: atomic multi-output
  publication and existing budgets/Stop/repair behavior.
- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/runner.py`: common prepared
  messages/effective settings; extend `FrozenSource` deliberately for captured defaults.
- `[PARTIAL]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/history.py` and
  `/home/alex-wong/vscode_projects/AgentWiz/scripts/replay_output.py`: inspect readers,
  replay/redaction assumptions and add compatibility handling where required.
  `history.py` was **not edited**: it reads records by fixed keys and ignores additive
  fields, which the passing suite confirms. `replay_output.py` now replays a captured plan
  and refuses an unreadable/incomplete one, but no test covers it (R7).

Do not use a second LLM to split outputs. Do not introduce provider-native response
format requirements in this increment; the existing transport plus local checking is
the portable path. Any capability refusal from modelSpell stays visible.
*Honoured: no transport change, no provider `response_format`, no modelSpell change.*

### C. Workshop service and assisted authoring

- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/workshop_service.py`: shared
  preparation in preview/Start, sanitized effective settings/source projections, frozen
  capture identity, structured input normalization and aggregate result projection.
  `preview` now builds messages through `runner.model_messages` (the duplicated `fill_text`
  calls are gone), exposes `prepared` details, `design.role_defaults`, per-row
  `output_checks` and a bounded `raw_answer`.
- `[PARTIAL]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/workshop_editor.py`: starter
  mode, structured row operations, output rename/remove behavior, typed form projections
  and non-destructive source-only fallback.
  Starters emit `response_mode` and no `output`; `design_form` compiles `resets`, forces
  `named_json`, and no longer rewrites `step.output` for a model step. `property_form` was
  **not** changed (its `field_has_children`, bounds and type handling are pre-existing).
  **Remaining:** the structured row operations still do not enforce the minimum-one-child
  rule for a new guided group (R2), and there is no schema-change orphan handling (R3).
- `[DONE]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/workshop_assist.py`: inspect the
  closed proposal schemas/compiler; permit system instructions, named outputs and
  supported settings in generated blocks without losing them on Improve/Apply.
  User-approved task scope does not authorize unrelated assistance changes.
  The proposal step now uses `named_json`; `_assist_task` accepts owner-approved
  `settings_text`/`task_resets` for the *assistance* call, and the instructions tell the
  model that the `proposal` output's value carries the schema, leaving envelope wording to
  the shared renderer.
- `[DONE by no change]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/ui/server.py`: thin
  translation and existing error envelopes only; no second preparation implementation in
  handlers. Handlers were not touched; the new fields reach them through the existing
  projections.

Keep POST/PUT envelope version 1 unless an actual wire incompatibility requires a
reviewed change. Add projections rather than replacing exact source-string fields.
Preserve ticket idempotency, revision review, source acknowledgement and lock ordering.
*Honoured: envelope version is still 1 and projections were added, not substituted.*

### D. Browser controls and tutorial

- `[PARTIAL]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/ui/index.html`: visible prompts,
  outputs, shared structured forms/viewers, inheritance-aware sampling and role defaults.
  Continue the framework-free implementation and behavioral `data-test` selectors.
  Done: visible System field, three-mode model settings with the role default shown,
  output-token insertion, Request preview, role-dialog defaults, typed example inputs and
  expected values, per-output checks and a bounded raw-answer pane, and an assistance
  dialog with its own Temperature/Reasoning modes. Remaining: output cards and their
  editing, the definition-editor rules, copy controls, and browser assertions for the new
  controls (R1–R4, R11).
- `[PARTIAL]` `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/tutorial_topics.py`,
  `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/tutorial_support/workshop.py`, and
  `/home/alex-wong/vscode_projects/AgentWiz/agentwiz/tutorial_support/backend.py`:
  update existing authoring/model-block stages and packaged deterministic answers.
  Demonstrate system instructions, two outputs, structured entry and role defaults in
  the isolated tutorial. Set affected topics' `updated_in` to the actual release version.
  Done: the packaged `extract_fields` block, its prompts and the stand-in answers now use
  the envelope; the tutorial role's stand-in model is `block-fields-repair`; the Workshop
  proposal payload no longer carries `output`. **Remaining:** no topic demonstrates system
  instructions, two named outputs, structured entry or role defaults, and no `updated_in`
  was bumped — it must change in the same release that changes what the topic shows.

### E. Documentation and delivery

- `[TODO]` `/home/alex-wong/vscode_projects/AgentWiz/README.md`: authoring workflow, output
  syntax/protocol, structured forms, override precedence, legacy conversion and limits.
- `[TODO]` `/home/alex-wong/vscode_projects/AgentWiz/docs/architecture.md`: executable/roles
  extensions, generated instructions departure, checking-plan/record contract, capture
  lifecycle and UI/API projections.
- `[TODO]` `/home/alex-wong/vscode_projects/AgentWiz/docs/decisions.md`: owner-approved
  consequential departures; do not record approval that has not occurred.
- `[TODO]` `/home/alex-wong/vscode_projects/AgentWiz/NEWS.md` and
  `/home/alex-wong/vscode_projects/AgentWiz/HANDOVER.md`: concise user-visible changes,
  actual checks, blockers and known limitations. Do not duplicate standing policy.

> **Delivery state:** no document was edited. `docs/architecture.md` still states "A contract
> is never sent to the model", which the generated section now contradicts, and the version
> policy in `DEVEL.md` (any `agentwiz/**` change requires a bump and a NEWS entry) is not yet
> satisfied. Section 12.3 R5/R12 record the exact passages and entries to write.

## 9. Functional validation plan

Follow T1–T3 in `/home/alex-wong/vscode_projects/AgentWiz/DEVEL.md`. Extend existing
functional scenarios rather than adding tests merely for file contents, helper internals
or wording. Each new regression must name its functional reason. Use scripted local
backends and temporary workspaces; never require a hosted model for the offline gate.

| Area / existing test files | Required functional evidence |
| --- | --- |
| `test_blocks.py`, `test_block_routes.py` | Legacy raw output unchanged; named mode accepts 1/multiple outputs; ambiguous bindings, zero outputs, bad names/modes and removed-token references refused with located issues. |
| `test_roles.py`, `test_task_settings.py` | Read v1/v2, persist/reload v3, zero preserved, invalid defaults refused, legacy assignment save retains defaults; override/reset/inherit fieldwise behavior and no Workbench-default leakage. |
| `test_templates.py` through allowed public behavior, `test_block_routes.py` | Output tokens expand descriptions/schemas, unknown tokens refuse, inputs containing token-like text do not recurse, tool/script contexts stay input-only, file-backed prompts persist correctly. |
| `test_attempts.py`, `test_model_step.py`, `test_blocks.py` | Fake backend receives actual generated system section and effective arguments; multiple values pass; malformed envelope, duplicate/missing/extra keys, wrong text type and per-field checks fail; no partial publication. |
| Existing repair/Stop scenarios | One field failure repairs entire envelope using frozen settings/contracts; truncation cannot publish, Stop/budget gates stay authoritative; missing checks extra is not a pass. |
| `test_workshop.py` | Preview equals dispatch preparation; plan identities change for role defaults/protocol/schema changes; stale review refuses; started batches and repairs keep captured settings after role changes. Save/reload/export/duplicate retain system, defaults and named outputs. |
| `test_workshop.py`, `test_workshop_replay.py` | Typed examples and exact expected values round-trip; old evidence stays readable; replay checks the captured named plan or explicitly refuses unsupported evidence; raw answer remains distinguishable from accepted values. |
| `test_page.py` | One end-to-end two-output author/test/example/compare scenario: structured boxes, nested/list edits, integer/decimal exactness, omit/null/false/zero, visible system field, inheritance controls and retained draft/example state. Extend existing role browser regression for hidden default drafts. |
| `test_tutorial.py` | Updated topics drive their real main action against packaged stand-ins, without user workspace/configuration/credentials. |

Include focused coverage for malicious descriptions/output text rendered as text, bounded
schema/message/issue sizes and unsupported source-only schemas not being rewritten.
Use current hooks/barriers for ordering, not sleeps. Do not duplicate every HTTP case
in a separate browser test. Inspect current tests before assigning assertions to files.

> **Coverage state per row.**
>
> 1. `test_blocks.py`, `test_block_routes.py` — `[DONE]` for the dropped-legacy reality:
>    the legacy row no longer applies; named mode, refusals and removed-token references
>    are covered. One row is now **inverted**: a model step carrying `output` is refused.
> 2. `test_roles.py`, `test_task_settings.py` — `[DONE]` v1/v2 read and v3 save, zero kept,
>    invalid defaults refused, assignment-only save retains defaults and does not downgrade.
>    `resolve_effective` precedence is covered by `test_named_outputs.py` rather than
>    `test_task_settings.py`.
> 3. `test_templates.py`, `test_block_routes.py` — `[PARTIAL]` tokens, non-recursion and
>    context isolation are covered by `test_named_outputs.py`; **no assertion proves a
>    file-backed prompt keeps the token after save/reload** (R13).
> 4. `test_attempts.py`, `test_model_step.py`, `test_blocks.py` — `[DONE]` for acceptance,
>    atomicity and refusals at the checker level; the "fake backend received the generated
>    section" assertion exists in `test_page.py`/`test_blocks.py` only indirectly (R13).
> 5. Repair/Stop/truncation — `[DONE]`, existing scenarios pass unchanged.
> 6. `test_workshop.py` — `[PARTIAL]` save/reload/duplicate and preview-equals-dispatch
>    pass; **no test yet asserts that a role-default edit between review and Start is
>    refused** (R10).
> 7. `test_workshop_replay.py` — `[TODO]`: not updated, and `scripts/replay_output.py`'
>    named-plan branch is unproven (R7).
> 8. `test_page.py` — `[PARTIAL]` the existing journeys pass and the role browser now
>    asserts persisted defaults, but the requested two-output author/test/example/compare
>    journey and the override-mode controls are not asserted (R11).
> 9. `test_tutorial.py` — `[DONE]`: 9 passed, including the updated packaged block.

## 10. Execution and completion gates for the implementing agent

1. Read project guidance, the live working diff, and relevant architecture contracts.
   Confirm owner decisions in section 3 and the generated-instructions departure.
2. Implement A–C with focused functional checks, then D, then affected documentation.
   Keep each intermediate stage compatible with legacy blocks and evidence.
3. Work from `/home/alex-wong/vscode_projects/AgentWiz`, activate its `.venv`, and run
   focused tests for changed behavior. Run the full gate before reporting completion:

   ```bash
   cd /home/alex-wong/vscode_projects/AgentWiz
   . /home/alex-wong/vscode_projects/AgentWiz/.venv/bin/activate
   python -m pytest -q -m "not live"
   python -m compileall -q /home/alex-wong/vscode_projects/AgentWiz/agentwiz
   git diff --check
   ```

4. Missing Playwright Chromium or checks dependencies blocks the relevant check; do
   not skip or weaken it. Use the documented setup procedure if needed. Report actual
   local Python/OS, pass/fail/skip counts and unverified CI platforms.
5. Inspect the final diff and all edited files. Confirm no incidental data migration,
   user workspace writes, credentials, dependency changes or unrelated rewrites.
6. Installed-wheel smoke is separate and must use the release procedure if required;
   the builder reads committed HEAD. Never commit this checkout just to run smoke.
   Live provider tests are optional, require configured backends, and cannot be
   described as passed when skipped or unrun.
7. Report implementation, compatibility decisions, actual validation and blockers.
   Do not commit, push, publish or silently continue a larger build.

### Acceptance checklist

- **[PARTIAL]** A user creates an LLM block, enters system/user instructions, and defines text plus
  structured outputs without writing raw contract JSON. — Creating a block, editing both
  prompts and adding outputs works; the output card/schema editing polish is missing (R1).
- **[DONE]** The effective system prompt includes both output formats and the complete required
  structured schema; output-description markers work in the user prompt.
- **[DONE]** One deterministic model answer becomes independently named, validated outputs; a
  malformed/failed/truncated answer never exposes a partial accepted output.
- **[PARTIAL]** Test and saved examples use compatible multi-box forms, keep exact numbers and
  distinguish omitted, null, false, zero and empty text. Compare renders each output. —
  Examples and expected values now use the shared form; Compare still renders accepted
  outputs from the existing structured viewer.
- **[DONE]** Role defaults persist and apply; custom block overrides and backend resets behave
  predictably, appear in preview and remain frozen for trials/repairs. — Frozen for a
  started batch and for repairs; the "edit between review and Start is refused" case is not
  asserted (R10).
- **[DROPPED/PARTIAL]** Legacy blocks, role files, historical attempts and existing Workshop unsaved-work
  protections remain usable. Guided conversion is explicit and revision-reviewed. — Legacy
  *blocks* are deliberately unsupported (owner decision) and the workspace data was deleted;
  role files v1/v2 still read; historical attempts still read; unsaved-work protections pass.
- **[PARTIAL]** Offline gate passes, tutorials work, and documentation accurately identifies the
  new behavior and remaining backend/validation limitations. — Gate and tutorials pass;
  documentation is unwritten (E / R5).

## 11. Explicit non-goals and remaining risks

No multi-step execution, future-output value interpolation, arbitrary formatting language,
provider-specific structured-output transport, new parser recovery, automatic schema
inference, new validation dependency, date/file/binary editor, interactive table-grid
library, batch default migration or automatic model-output coercion is included.

Generated schemas consume prompt tokens and may contain sensitive field descriptions;
show the exact sent content before running. Local checks establish declared structural
and configured content rules, not factual correctness. Unsupported backend sampling
settings remain explicit pre-send errors. Preserve cancellation, writer ownership,
redaction and frozen-source lifecycle rather than treating this as only a form change.

## 12. Implementation status after this session

Written at the end of the implementation session. Flags in sections 1–11 are derived from
this record; where they disagree, this section is authoritative.

### 12.1 Implemented

| Area | Change |
| --- | --- |
| `output_protocol.py` *(new)* | `MODE`, `RENDERER_VERSION`, `Spec` (label/purpose/schema text), `specs_for`, `instructions`, `describe`, `identity`, `digest`. Pure; no HTTP, storage or transport. |
| `templates.py` | `OutputRef`, `OUTPUT_PLACEHOLDER`, `scan_model_text`, `fill_model_text`, `_model_markers`; located errors for malformed/nested output markers. Inputs-only paths (`scan_text`, `fill_text`, `fill_arguments`) unchanged, so script and MCP contexts cannot use output markers. |
| `blocks.py` | `_STEP_KEYS["model"]` is now `id, kind, role, system, prompt, response_mode, task, task_resets, repair`; required `response_mode`; model `output` refused; 1–50 declared outputs; `_task_resets` bounds/duplicate/overlap validation and `null`/blank normalisation to a reset; `Step.response_mode`/`task_resets` exposed in views. |
| `checks.py` | `ENVELOPE_REPLY`, `envelope_contract`, `check_named_answer` (single parse, exact key set in any order, text-output string rule, per-output `check_value`, `NO_VALUE` unless the aggregate passes, per-output results under `outputs`), `repair_message(instruction=…)`. |
| `model_step.py` | `OutputPlan` (capture, `view`, `from_record`, `digest`, `specs`), named plan on `PreparedAttempt`, `settings_sources` on the record, plan rebuilt for a repair from the parent record. |
| `executor.py` | `_publish` stages all outputs then commits; per-step `output_checks`; resolved `settings_sources` passed through. |
| `runner.py` | `ModelMessages`, one `model_messages` preparation used by preview and dispatch, generated section composed there, request-size check covering it. |
| `task_settings.py` | `OVERRIDABLE`, `parse_defaults`, `Effective`, `resolve_effective` (fieldwise override → reset → role default → backend default, zero preserved). |
| `roles.py` | `FORMAT_VERSION = 3`, `task_defaults` in `DEFINITION_KEYS`, validation through `parse_defaults`, tolerant read of 1/2, `build_raw` always writes 3 and retains existing definitions. |
| `state.py` | `RoleCapture` (`defaults`, `revision`), `role_capture()` under one lock, assignment-only saves retain all definitions and never downgrade. |
| `workshop_service.py` | Preview through `model_messages`, `prepared` details, `design.role_defaults`, per-row `output_checks`, bounded `raw_answer`. |
| `workshop_editor.py` | Starters emit `named_json`; `design_form` compiles `resets`; model `step.output` no longer rewritten. |
| `workshop_assist.py` | Envelope proposal step, `_assist_task` with `settings_text`/`task_resets`, `output` removed from the model behaviour schema. |
| `ui/index.html` | System field in the main Design form; three-mode Temperature/Reasoning with the role default shown; output-token inserters; Request preview; role-dialog defaults; typed example/expected-value forms with an opt-out; per-output checks and a raw-answer pane with Copy; assistance Temperature/Reasoning modes. |
| Tutorial support | `extract_fields` block and prompts converted to the envelope; stand-in answers return `{"out": …}`; tutorial role model is `block-fields-repair`; the Workshop proposal payload no longer sets `output`. |
| `scripts/` | `replay_output.py` replays a captured plan and refuses an invalid one; `workshop_experiment.py` fixture emits `named_json`. |
| Tests | New `tests/test_named_outputs.py` (7 tests); updated `test_blocks.py`, `test_block_routes.py`, `test_runner.py`, `test_workshop.py`, `test_roles.py`, `test_page.py`, `conftest.py`. |

### 12.2 Authorized workspace reset (performed once, by hand)

Under `workspace.acquire_existing_lock("default")`, then
`workspace_admin.delete_workspace(..., current="default")` for each other workspace:

- removed `workspaces/default/blocks/local_summarize/` (the one pre-existing block);
- deleted the `alex` workspace (`{"deleted": "alex", "cleanup": "complete"}`);
- kept `workspaces/default/roles.json`, `model-profiles/` and `workspace.json`.

No automatic cleanup was added to startup or upgrade, and `workspaces/` is git-ignored, so
this touched local data only.

### 12.3 Remaining work, with proposals

| ID | Remaining work | Proposal |
| --- | --- | --- |
| R1 | Design has no output *cards*: outputs are plain name/format rows and cannot be edited inline in the pane. | Render one card per output from `design.outputs` plus `contracts.outputs[name]`, reusing `wsFieldEditor`; keep the contract source `<details>` as the advanced path. |
| R2 | A guided object can still be saved with zero children; minimum one was wanted for a *newly created* group. | Enforce in `property_form` when the parent is the schema root of a guided object created in the same operation, and surface it as a located issue; allow an empty intermediate browser state. Avoid a metadata flag: keep it structural. |
| R3 | No explicit handling for values orphaned by a schema change. | Reuse the existing `ws-unmapped-fields` warning in `wsInput`, requiring an explicit *Remove* or *Keep as source*; never drop silently in saved examples. |
| R4 | In Design model settings, a *Custom* control left blank compiles as "inherit", not "backend default", because `design_form` pops a blank value. | Either add an explicit *Use backend default* action to the control, or send `resets` when the field is blank **and** Custom is selected. Needs an owner decision on the spelling before editing. |
| R5 | Documentation: `README.md`, `docs/architecture.md` ("A contract is never sent to the model"), `docs/decisions.md` (approved departure, dropped compatibility), `NEWS.md`, `HANDOVER.md`. | One documentation pass in that order. Keep architecture text a contract, record decisions as dated dispositions, and put check results only in handover. |
| R6 | An old block carrying `step.output` now loads as `invalid`, and nothing tells the author how to fix it. | One short README/HANDOVER paragraph with the exact rewrite (`"output": "x"` → `"response_mode": "named_json"`, keep `outputs`), and a located issue message that names the rewrite. No converter. |
| R7 | `replay_output.py`'s named-plan replay is unproven. | Add cases to `tests/test_workshop_replay.py`: passing plan, failing field, and an unreadable/incomplete plan returning `unsupported_evidence`. |
| R8 | A `roles.json` change between review and Start is untested. | One HTTP regression: change a role default after `plans` and assert Start is refused as stale. |
| R9 | README does not carry the instruction wording (any key order; fences only outside the envelope; Markdown allowed inside a text value; no pseudo-JSON skeleton). | Copy the rules verbatim from `output_protocol.instructions` so prompt and docs cannot drift. |
| R10 | The review `basis` digest omits the plan identity, so a `RENDERER_VERSION` bump alone would not invalidate a reviewed plan. | Add `output_protocol.digest(plan.specs())` to the `workshop_service` basis, then assert in `test_workshop.py` that a declaration or renderer change invalidates review. |
| R11 | No browser assertions for the three override modes, the System-field placement, Request preview, the assistance modes, or the new example-form path. | One focused `test_page.py` journey: set Temperature to Backend default, assert the saved `task_resets` and the hidden value field; open Request preview and assert the generated section appears; exercise the assistance dialog once. |
| R12 | Version/NEWS/handover requirements are unsatisfied and no tutorial `updated_in` was bumped. | Confirm the target release with the owner (`_version.py` is `0.2.0`, unreleased), then update `_version.py` if required, add NEWS bullets, set `updated_in` on topics whose demos change, and record actual results in `HANDOVER.md`. |
| R13 | Two evidence gaps: no assertion that a file-backed prompt keeps `{{output.NAME}}` through save/reload, and none that a block run sends the generated section to the backend. | Extend `test_blocks.py` with a file-backed prompt containing a token, and assert the fake backend's `messages[0]["content"]` ends with the generated section. |

### 12.4 Defects found during this session

Defects in the work itself or in the earlier plan, all found by inspection or by a failing
check, with the fix applied or the proposal that replaces it.

| ID | Defect | Fix / proposal |
| --- | --- | --- |
| I1 | **Plan flaw:** requiring envelope keys "in this order" would reject a correct answer, since acceptance is an exact key *set*, and a pseudo-JSON skeleton (`<text>`, `<object>`) invites literal copying. | Instructions ask for the exact set in any order and give no fake JSON; acceptance never checks order. |
| I2 | **Plan flaw:** "no Markdown" as a blanket rule would forbid a legitimate Markdown text output. | The prohibition now applies only outside the envelope. |
| I3 | **Plan flaw:** forcing every model attempt through a named plan would also have changed Workbench attempts, which share `model_step`. | The named plan is attached only for block model steps; `check_answer_value` and its page path are untouched. |
| I4 | `state.save_roles` downgraded a roles file to `format_version: 1` and dropped `definitions` on an assignment-only save, silently deleting every saved default and every unassigned role. | `roles.build_raw` always writes 3 and retains definitions; the legacy branch copies all current definitions. Tests added. |
| I5 | Preview recomputed prompt filling and settings sources separately from dispatch, so the two could disagree (pre-existing duplication). | One `runner.model_messages`; `workshop_service.preview` calls it and returns its result. |
| I6 | The first tutorial run failed because the packaged `extract_fields` stand-in still answered one bare object and the tutorial role still used `json-repair`; page tests failed for the same reason (bare object vs envelope, and a role dropdown that preselects an unassigned role). | Packaged answers wrapped, stand-in model switched, page fixtures wrapped, role chooser prefers an assigned role. `test_tutorial.py` 9 passed and `test_page.py` 30 passed afterwards. |
| I7 | Example/expected-value editing read structured inputs through a plain text field, which would have coerced booleans and numbers; and an early edit closed the dialog on a validation failure, losing state. | `wsInput` is used with `remember=false` for those panes, and the dialog's async confirm keeps itself open with the located error. |
| I8 | `runner.model_messages` composed the generated section without counting it against `insertion.REQUEST_LIMIT`. | The prepared request is size-checked before dispatch and raises a located `FillError`. |

### 12.5 Checks run (Linux, Python 3.13.13, local `.venv`)

| Check | Result |
| --- | --- |
| Baseline before any change (`pytest -q -m "not live"`) | **645 passed, 10 deselected** (94.43 s) |
| Final offline gate (`pytest -q -m "not live"`) | **659 passed, 10 deselected** (94.47 s) |
| `tests/test_page.py` | **30 passed** (21.64 s) |
| `tests/test_tutorial.py` | **9 passed** (28.27 s) |
| `tests/test_named_outputs.py` + `tests/test_roles.py` | **49 passed** |
| `tests/test_attempts.py` + `test_model_step.py` + `test_named_outputs.py` + `test_workshop.py` | **59 passed** |
| `python -m compileall -q agentwiz scripts tests` | passed |
| `git diff --check` | passed |

Not run, and therefore not claimed: live backend checks (need a configured
LM Studio/OpenRouter/llama-server), installed-wheel smoke (reads committed `HEAD`, and this
checkout must not be committed to run it), macOS and Python 3.10 execution. CI remains the
release criterion for those.

### 12.6 Suggested order for the next session

1. R5/R6/R9/R12 — decide the release and write the documents, because the version and NEWS
   rules gate a merge and the architecture text currently contradicts the code.
2. R10/R8/R7 — close the freshness and evidence gaps that could otherwise let a reviewed
   plan differ from what runs.
3. R11/R13 — add the missing browser and backend assertions for what is already built.
4. R1/R2/R3/R4 — finish the authoring controls, after the R4 decision on blank-custom
   semantics.