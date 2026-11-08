# NEWS

## 0.2.1

- New model and tool blocks ask for port labels and are workflow-ready without an upgrade step.
- Design reads in order: a top bar with revision and Save, then ports, prompts and settings.
- Insert buttons follow port renames; stale prompt references are flagged with a confirmed replacement.
- A Protection card shows port labels, design protection and where run restrictions come from.
- Review retained privacy labels retires an earlier revision's Local-only labels with a recorded acknowledgement.
- Overwrite stays unavailable for workflow-ready revisions, which may hold privacy labels; the button explains why.
- Contracts show a readable summary; Edit opens a side drawer with list, record and choice shortcuts.
- Saving an inline contract as reusable and using it for the port takes one dialog.
- Test shows inputs beside results and checks readiness, with Grant and other remedies in place.
- Compare keeps Stop and both arms visible and shows each example's answers side by side.
- Models & roles lists roles with status and opens the profile editor without losing role edits.
- Trusted destinations lists configured endpoints with eligibility, trust and uses; Grant needs no retyping.
- Workflow Run orders inputs, readiness, findings and Run; findings offer remedies such as Grant or Add Release Gate.
- Workflow progress follows execution order, shows release phases, and opens node traces beside it.
- History lists block runs, workflow runs and comparisons together with filters.
- Save runs shows On or Off beside the workspace name; the tutorial opens as a side panel.

- Release Gates pause workflows for explicit human approval without removing local-only protection from source data.
- Optional trusted local AI assessment and identifier checks assist release review; acknowledgements are enforced by the server.
- Reopen Workflows after a browser reload to recover an active review; restart invalidates pending approvals.

- Create and run workflows from Workshop using forms or a drag-and-connect canvas.
- Review saved examples, workflow traces, and destination trust in the browser.
- Added v4 block ports, built-in transforms and explicit conversion of legacy starters.
- Added headless workflow planning, serial execution, saved examples and private run evidence.
- Protected model and MCP calls require a workspace grant for the exact literal-loopback destination.
- Workshop records draft lineage and refuses attributable generated host Python.
- Stop preserves an unknown MCP outcome when the call may already have been sent.

## 0.2.0
- The contract editor preserves a new choice while an earlier edit finishes.
- Workshop Design places revision selection above Save as new revision and Overwrite selected revision.
- Confirmed overwrite preserves the selected revision number, unrelated default and existing run evidence.
- Overwrite clears the selected revision’s standard description.
- Select, test or compare eligible retained v3 revisions without changing the default. Results identify the revision used.
- Edit a retained selection or use Restore to draft before saving it as a new revision.
- Standard descriptions belong to individual saved revisions and leave the executable block intact.
- Describe block uses the `write_block_description` role and shows the destination before sending.
- Review a proposed description with Save description, Refine or Discard. Edit description supports manual editing.
- A new revision starts without a standard description.
- Assist's describe opens the same Describe flow; Improve and Explain failure no longer edit description sections.
- Workspaces created earlier list `write_block_description` among missing default roles until it is added.
- The port field is now labelled Declared meaning; per-port Documentation in the description is display-only.
- Contract synthesis shows sanitized model-call errors, including missing API keys, instead of only a generic failure.
- Roles flags default roles missing from the saved workspace and explains how to add them without replacing existing roles.
- Contracts support Text literal rules, a restricted JSON Schema vocabulary and Any JSON. JSON numbers are exact.
- Legacy `checks` and `strict` contract definitions are refused.
- Saved contracts keep revisions in one file per name, with families, restore-as-new and stale-edit protection.
- Deletion is refused while blocks use a contract. Version 1 contract files require recreation or rewriting.
- Executable blocks use format 3 and pin saved contracts by name, revision and digest.
- Runs and repairs retain contract snapshots. Executable v1 blocks report `migration_required`.
- Workshop → Contracts replaces the Workbench Contracts tab; `#contracts` opens it.
- The contract library searches, filters by family, shows revisions and restores old ones as new revisions.
- A readable Text/Structured JSON/Any JSON editor; advanced source is optional and follows the same rules.
- Contract saves show a review of changes first; stale saves keep the draft.
- Used by lists blocks pinning a contract; deletion is refused while any do.
- Block ports choose Inline or a pinned saved contract revision; newer revisions are never adopted automatically.
- Ports offer Select newer, Make local copy and Save inline as reusable.
- Checked request selects Latest or a numbered revision and previews the exact generated contract section.
- The new `write_contract` role drafts JSON contracts with stated assumptions; applying and saving stay separate steps.
- JSON Schema validation is now included in every installation; the optional checks extra is removed.
- Workshop Design uses three responsive columns with editable input/output tables and Save in the settings pane.
- Design adds a model-role dropdown and paired temperature and reasoning controls that show inherited defaults.
- Additional repairs sits below Outputs. Revision naming sits near Save.
- Assist and Revision actions share the mode action row.
- Block revisions have stable per-block numbers, optional names and an inline selector; restoring saves a new revision.
- Structured-variable editors appear immediately after format selection; a contract type change that drops content previews it first.
- Model-server connections now come from `config/model-servers.ini`; OpenRouter keys have a separate session-only button and environment fallback.
- Model blocks return one named JSON envelope: several independently checked outputs, published together or not at all.
- Each model step now sends generated output instructions; `{{output.name}}` describes an output inside prompts.
- Design shows system instructions, per-output structured fields and a Request preview with Copy of the exact messages sent.
- Temperature and reasoning can inherit a role's default, be set per block, or be reset to the backend default.
- Roles can save a default temperature and reasoning (roles file format 3); older versions cannot read it.
- Workshop assistance has its own temperature and reasoning controls, separate from the block it proposes.
- Saved examples and expected values use typed forms; a new empty group must gain a field before saving.
- Workshop reports located block validation errors instead of misleading file-permission failures.
- Test retains usable inputs and separates one-off input from pending or saved examples.
- Output renames preserve step bindings.
- Roles show one editor at a time, with location and family filters, suggested families and custom family creation.
- Browsing preserves unsaved edits; Save all changes saves every role.
- Starter roles (`fast`, `assistant`, `reasoning`) have a guided editor with model details and setup links.
- Local/Cloud assignments are enforced. Local roles require loopback model servers; no automatic cloud fallback.
- New-block creation validates keys without losing inputs, lists model roles, and discovers tools from configured MCP servers in dropdowns.
- Design, test and compare individual blocks in Workshop with saved revisions, examples and correctness reviews.
- Review bounded assistance proposals before applying them to a draft.
- Workspaces dialog: switch, create, clone, reset and delete workspaces in the launch folder.
- Switching reloads the page and releases the old workspace lock; active work must be stopped first.
- Clone can include saved runs and comparisons; reset and delete require typing the workspace name.

## 0.1.4
- Blocks tab: run single-step blocks saved as folders in the workspace, with a generated input form and trace.
- Block files are JSON, with prompts in Markdown files; AgentWiz reads them and never writes them.
- Model, tool and script steps run through one executor; each output is checked against its contract.
- Inputs are checked against their contracts before anything runs; a failing input rejects the run.
- Roles dialog maps role names such as fast to profiles; block steps choose their model by role.
- Templates fill {{inputs.name}} placeholders; tool arguments keep each value's JSON type and exact digits.
- Script steps run your own Python files with JSON in and out, a time limit and no credentials.
- Scripts are not sandboxed: they run as you, with your access to files and the network.
- Automatic repair: repair.max_repairs counts model calls after the first; default 2, at most 5.
- A cut-off answer is repaired with a message saying so, and is never accepted as a block output.
- Contracts are never sent to the model; README explains how to state the required output in prompts.
- Block runs reserve their slots up front, have time and token budgets, and Stop ends any step.
- Block runs keep going if the page closes; the Blocks tab finds running ones again.
- Saved block runs include a result file; History shows them with their outputs and step details.
- Repairs record whether they followed a failed check or a cut-off answer.
- Tutorial: three new Blocks topics; every topic shows the version that last updated it.

## 0.1.3
- Tutorial mode prepares an isolated workbench, fills examples and offers optional learning examples without manual service setup.
- The page is now tabs: Chat, Documents, MCP tools, Contracts, Checked request and History.
- Copy buttons replace Insert; copied document text and tool results keep their provenance line.
- Contracts tab: save text or JSON contracts in the workspace and check pasted text against them.
- JSON Schema checks need the optional extra AgentWiz[checks]; without it they report not run, never pass.
- Numbers in contracts and checks stay exact; nothing is rounded or converted to a float.
- Checked request tab: check a model's answer against a contract, then preview and send a repair.
- Stop is handled by the server and ends a request at any stage, even before the first token.
- A stopped request reports whether it may have reached the backend.
- Each request is a run of numbered attempts; a repair is the next attempt of the same run.
- Save runs, off by default, writes each run to a workspace folder, redacting credentials AgentWiz has held.
- History tab: open saved runs read-only, copy any part, or delete them.
- After a lost connection the page asks the server for the committed outcome; nothing is resent.
- Requires modelSpell 0.3.0, pinned by URL and sha256.

## 0.1.2
- Reasoning, temperature and max tokens moved from model settings to the request panel; each request sends its own.
- Advanced panel: top-p, top-k, min-p, repetition, presence and frequency penalties, seed and stop sequences.
- Fields the model source cannot take at the chosen reasoning level are disabled and not sent.
- Invalid or unsupported task settings are refused before anything is sent; blank fields use the backend's default.
- Save task settings stores the panel in the workspace; new pages and restarts start from it.
- Profiles keep model source, base URL, model and provider; saved reasoning and max tokens are dropped.
- Profile files rewritten by 0.1.2 are read-only to AgentWiz 0.1.1.
- With reasoning off, LM Studio models without reasoning configuration now work.
- With task settings on OpenRouter, only providers supporting every setting serve the request.
- Attach .txt, .md, .csv or .json files in UTF-8 or UTF-16; preview, then insert with a provenance line.
- Previews show characters, estimated tokens and request size; uploads are capped at 50 MiB, requests at 32 MiB.
- Tools panel: call tools on local MCP servers, then preview and insert results; nothing is sent automatically.
- Speaks MCP 2026-07-28 and the earlier handshake revisions, detecting which each server uses.
- MCP servers are kept in ./mcp_servers (--mcp-servers-dir); tokens stay in memory or an environment variable.
- Tool arguments use a generated form for simple schemas, otherwise a raw JSON editor; numbers stay exact.
- Tool outcomes: ok, tool error, rejected (never ran) or unknown; nothing that may have run is resent.
- Stop ends a tool call at once and reports whether the server was told to cancel.
- At most 8 tool operations run at once, stopped ones included; further ones are refused, not queued.
- Requires modelSpell 0.2.1, pinned by URL and sha256.

## 0.1.1
- Local server startup no longer waits for reverse-DNS resolution, avoiding delays on macOS.
- Model settings can be saved as named profiles; the active profile is restored when AgentWiz starts.
- API keys are still never saved; re-enter them after a restart, or use OPENROUTER_API_KEY.
- Default profiles are saved in ./model_profiles; new workspaces import independent copies, with explicit updates and exports.
- Only one AgentWiz process can use a workspace; a second one exits with status 3.
- Damaged or newer workspace files are left untouched; profiles become read-only until you repair and restart.
- Base URLs containing a user name, password, query or fragment are refused.
- The settings dialog's Save button is now Apply; Save profile stores settings in the active profile.
## 0.1.0
- First release: local browser UI to send one prompt to LM Studio, OpenRouter or OpenAI-compatible servers.
- Reasoning and answer stream live; each request stands alone and nothing is saved to disk.
- Every result shows an outcome: complete, empty, reasoning only, truncated, error or stopped.
- Settings are shared by all tabs; a tab showing outdated settings must reload before sending.
- At most 8 requests run at once, stopped ones included; further requests are refused until one ends.
- API keys stay in memory, are never shown again, and error messages never include them.
- `agentwiz --version` reports AgentWiz and modelSpell versions; `--port` and `--no-browser` control launch.
- Released as a wheel on GitHub releases; pin it by URL and sha256.

