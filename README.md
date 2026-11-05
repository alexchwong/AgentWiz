# AgentWiz

A local browser UI for building and testing LLM steps, built on
[modelSpell](https://github.com/alexchwong/modelSpell).

You choose a model backend (LM Studio, OpenRouter, or any OpenAI-compatible server such as
llama-server), type an optional system prompt and an input, and watch the reasoning and the answer
stream back, followed by token usage, cost and a clear outcome.

What 0.2.0 adds:

- **Workshop:** create, understand, save, test and compare one model, tool or Python block at a time.
- **Nine starters** and blank blocks, exact structured fields, prompt preview and editable standard descriptions.
- **Saved revisions:** display names, duplicate, restore, explicit legacy conversion and newest-20 retention.
- **Examples and correctness:** exact outputs, field assertions or human review, independently of contract acceptance.
- **Five assistance actions:** create, improve, describe, suggest examples and explain a failure. Review proposals before Apply.
- **Workbench:** Chat, Documents, MCP tools, Contracts and Checked request remain independent utilities.
- **History:** saved runs, checks, attempts and source attribution, with saving off by default.
- **Fourteen tutorial topics** using isolated local services, with no API keys or live model required.

Model profiles, roles and MCP servers are shared settings. Credentials stay in process memory.
Scripts run with your filesystem and network access. AgentWiz uses the pinned modelSpell 0.3.0 public API.

## Install

Requires Python 3.10 or later.

```bash
curl -fsSL https://github.com/alexchwong/AgentWiz/releases/latest/download/install.sh | bash
```

The script creates `.venv` in the current directory, checks the newest release wheel against the
sha256 GitHub publishes, installs it, and prints how to launch. Pin a release with
`… | bash -s -- --version 0.2.0`; download the script first if you want to read it before running it.
JSON Schema validation is included in the standard installation.

Working from a clone? See DEVEL.md for the developer setup.

**Development checkout:** the contract overhaul has completed its headless phases (new definitions,
revisioned saved contracts, pinned references, synthesis and migrated packaged assets). The browser
editor (Workshop → Contracts, port editors and synthesis UI) is not built yet, and the old Workbench
Contracts editor in this checkout no longer works with the new routes. Old version 1 contract files and
block files are refused with `migration_required`; nothing migrates a workspace automatically. See
`contract_overhaul_handover.md` for status and validation evidence.

Prefer to do it by hand? Download the wheel from the release page (the wheel, not the source zip),
then run:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./agentwiz-0.2.0-py3-none-any.whl
```

For checksum verification, use `python -m pip install` with the wheel URL and `#sha256=…` fragment
from the release notes instead of the local filename. Both methods also install modelSpell 0.3.0,
pinned by URL and checksum.

**JSON Schema validation is required and installed automatically** (`jsonschema>=4.26,<5`).
It brings `rpds-py`, a compiled package; a platform without a published wheel needs a Rust
toolchain to install it. If validation support becomes unavailable because the installation is
damaged or unsupported, reinstall AgentWiz before using schema-dependent actions.

## Launch

```bash
agentwiz                            # opens your browser at the printed address
agentwiz --no-browser               # just print the address
agentwiz --port 8765                # fixed port (default: any free port)
agentwiz --workspace work           # use the workspace "work" (default: default)
agentwiz --workspaces-dir ~/aw      # folder holding workspaces (default: ./workspaces)
agentwiz --profiles-dir ~/models    # shared default profiles (default: ./model_profiles)
agentwiz --mcp-servers-dir ~/mcp    # shared MCP server list (default: ./mcp_servers)
agentwiz --version                  # AgentWiz and modelSpell versions
python -m agentwiz                  # same as agentwiz
```

The three folders default to paths relative to the directory you launch AgentWiz from.

AgentWiz listens on 127.0.0.1 only and prints an address such as
`http://127.0.0.1:8765/?t=<token>`. The token changes every launch, and every request must carry it,
so other web pages in your browser cannot use AgentWiz. Anyone who can read the terminal output can
use it while it runs. Press Ctrl-C to stop.

### Workspaces

Profiles and saved task settings are kept in a **workspace**: the folder `<workspaces-dir>/<name>/`,
by default `./workspaces/default`. The default is relative to the directory you launch AgentWiz from,
so launching from a different directory uses a different workspace. A workspace that does not exist
is created with copies of the default profiles, and its terminal line ends in `(created)`; if you did
not expect that, check the name you typed. Names are 1 to 64 letters, digits, `.`, `_` or `-`,
starting with a letter or digit.

- **One AgentWiz process owns one workspace at a time.** A second AgentWiz on the same workspace
  prints which process holds it and exits with status 3. Use **Workspaces** in the header to switch
  to another workspace in the same `--workspaces-dir`: AgentWiz releases the current lock, opens the
  selected workspace and reloads the page. The `--workspace` option still chooses the initial
  workspace. To use two workspaces at once, launch a separate process for the other one.
- The folder holds `workspace.json`, `model-profiles/profiles.json`, `task_settings.json` (once
  saved), `roles.json`, `contracts/`, `blocks/`, `workshop/` (saved examples and comparisons),
  `runs/` (when saving is on) and `.agentwiz.lock`. If you keep workspaces inside a git repository,
  add `.agentwiz.lock` to that repository's `.gitignore`.
- **Supported locations:** local disks on Linux and macOS, and WSL2's own filesystem. Shared,
  network and cloud-synced folders are not supported and not detected: the workspace lock may not
  be visible from another machine. Under WSL2, workspaces on `/mnt/c` (the Windows drive) have not
  been tested; prefer a folder in WSL2's own filesystem, such as `~/agentwiz`.

## Navigation

**Workshop** is the default. Its compact saved-block selector is above Design, Test and Compare.
**Workbench** groups the five independent utilities below. **History**
opens saved runs. Settings in the header groups model profiles, roles, MCP servers and Save runs; the
Workspaces control beside the workspace name opens workspace management.

| Location | Address | Purpose |
| --- | --- | --- |
| Workshop | `#workshop/<key>/<design\|test\|compare>` | Author and evaluate one block. |
| Workbench: Chat | `#chat` | Send a system prompt and input; see reasoning and answer stream in. |
| Workbench: Documents | `#documents` | Preview a text document and copy it. |
| Workbench: MCP tools | `#mcp-tools` | Discover and call tools, with labelled arguments or an explicit advanced editor. |
| Workbench: Contracts | `#contracts` | Edit supported structured fields or exact source and check pasted text. |
| Workbench: Checked request | `#checked-request` | Check an answer and explicitly send a repair. |
| History | `#history` | Read saved runs and attempts, copy evidence or delete a run. |

A reload preserves validated navigation identifiers. Unknown fragments open Workshop. Keyboard arrows,
Home and End move between tabs. Ctrl/Cmd+Enter submits the active Workbench function. Output panels have
Copy; independent Workbench utilities exchange content through the clipboard. Hidden requests keep running.

Typed inputs and unsaved drafts are held in page memory. A dirty draft survives Design/Test/Compare
switches; replacing it offers Save, Discard or Stay. Reload can lose it, with a browser unload warning
where supported. Saved definitions and examples survive restart; session-only results do not.

**Workspaces** opens the shared workspace dialog. It lists the current workspace and the other
workspaces in the launch folder, and provides:

- **Switch** — open an existing workspace and reload the page. Save or discard a dirty Workshop
  draft first. Running requests and Workshop batches must be stopped; AgentWiz refuses to switch
  while work is in progress.
- **Create** — make a new workspace and switch to it. A new workspace receives copies of the shared
  default profiles, with no active profile.
- **Clone** — copy the selected source into a new workspace. Configuration, profiles, roles,
  contracts, blocks and saved examples are copied. Saved runs and comparisons are excluded unless
  **Include saved runs and comparisons** is checked. The source and clone are independent; the lock
  file is never copied.
- **Reset** — clear a workspace back to a fresh state, keeping its name and folder. The workspace
  receives fresh profile copies and no saved data. This cannot be undone.
- **Delete** — permanently remove a non-current workspace. The current workspace must be switched
  away first. Delete and reset require typing the workspace name exactly.
- **Launch in another process** — show a shell-quoted command for a separate AgentWiz process. Only
  one process can own a workspace.

Only names directly under the launch folder can be managed. Shared defaults and the MCP server
library must remain outside managed workspaces; reset and delete refuse a workspace that contains
either one. Session API keys and MCP tokens are memory-only: switching or resetting clears them, so
re-enter them when needed. Workspace administration is disabled inside the tutorial's temporary
workspace.

## Block Workshop

Choose **New block**, a starter or blank model/tool/script and a stable block key. Set its display name,
purpose, inputs, behaviour and outputs in Design. A model role resolves to a profile; a tool binds to a
server and discovered tool. Insert input controls use the existing `{{inputs.name}}` template syntax.
The block key is an internal identifier, not a display title: use 1–64 ASCII letters, digits, dots,
underscores or hyphens, starting with a letter or digit (for example, `summarise_notes`); no spaces.
**Start with** chooses an initial template, not an output prefix. Model roles are aliases assigned to
saved profiles in Settings → Roles, not chat-message roles. The dialog lists defined roles and marks
unresolved assignments. Fresh workspaces define unconfigured `fast`, `assistant` and `reasoning` roles;
drafts can be designed before profiles are assigned. Use **Configure roles** before running.
Assistance separately selects an assistant role.
Only fields relevant to the selected block type are shown. Tool blocks list configured MCP servers and
automatically discover the selected server's advertised tool names in a dropdown. **Refresh tools**
retries discovery after configuration or connection changes. AgentWiz does not scan for unconfigured
servers: add their URLs in Settings → MCP servers first. Discovery lists tools without executing them.
Creation errors keep the dialog and entered values available for correction. **Create draft** opens
Design without calling a model; **Create with assistance** first creates the same draft, then opens the
separate assistance request/review workflow. Neither button saves a revision automatically.
A model block's Design form has three unlabelled columns on wide screens: block description, **System prompt**
and **User prompt** on the left; variable tables in the middle; identity, saving and settings on the right.
The form stacks on narrow screens. Insert buttons add
`{{inputs.name}}` and `{{output.name}}` placeholders (see [Named outputs](#named-outputs)). Each input and
output is an editable row with field name, format dropdown and meaning. Each row retains its own expandable
contract source, shared-contract selector or **Structured fields** editor. Choosing Structured value exposes
its field editor without a manual refresh. Destructive format changes ask before replacing a contract.
**Request preview** shows the exact system and user messages a run would send for the current inputs,
with the generated output section and where each setting came from, each with Copy. On the right,
**Model role** selects a configured role without silently replacing an unavailable saved binding.
Temperature and Reasoning level sit side by side. An empty Temperature inherits the selected role's
default, shown in grey; zero is an explicit value. Reasoning's first option shows the role's value
followed by **(default)** and inherits it; selecting a level explicitly overrides it. If no role default
is declared, the backend default applies and no numeric value is guessed.

**Model advanced settings** holds explicit backend-default bypasses for both settings, the output cap,
budgets and source editors. **Additional repairs** is always visible below Outputs; zero allows no
repair attempts after the initial response. The optional revision name sits beside **Save revision**;
the historical Block revision selector sits above the editor. **Assist** and **Revision actions** share
the Design/Test/Compare action row in all three modes. Test's **Preview
instructions** shows the actual filled prompt or arguments for the supplied inputs.

Structured fields distinguish missing, null, false, zero and empty text. Numeric values travel as exact
source text. Saved examples and their expected values use the same typed forms as Test. Editing an example
also offers inputs it lacks, and keeps expected values for outputs the block no longer declares until you
remove them. A new group needs at least one field before **Save revision** or a run: editing may pass through
an empty group, and a group that was already empty in the saved block is left alone. Unsupported schema structures use the labelled contract-source editor; unknown rules are
preserved. A shared contract can be copied inline for a block-specific edit. Deliberate shared edits
remain in Workbench Contracts and show dependent blocks. Schema-backed validation and assistance use
the validator included in the standard installation; repair the installation if it becomes unavailable.

**Save revision**, in Design's right-hand controls, previews the effective action and its required
acknowledgements. **Block revision** selects a retained revision into the draft, not the saved head;
unsaved changes offer Save, Discard or Stay. Revisions display per-block numbers starting at 1 and an
optional name of up to 120 characters. Enter a name before saving; names need not be unique and do not
rename the block. A new revision's name defaults to blank. Historical names are not edited in place.
Numbers survive restart and pruning; restoring an older version and saving creates the next number.
Duplicates start their own sequence. Existing unnumbered histories are numbered oldest first from the
retained history available at upgrade, not from already-pruned lifetime history. Internal IDs remain unchanged.
Opening a legacy v1
block never converts it. Its first Save captures the original and edited definitions in a managed v2
head, numbered 1 (original) and 2 (edited). Only the newest 20 revisions are retained, including that original.
The Save plan identifies revisions
being pruned. Duplicate keeps the original key intact and offers explicit copying of examples. Restore
loads a retained definition into a draft; a deliberate Save creates a new revision, without rerunning it.
Renaming the display name also requires Save; the stable key remains in Details.

Python starters expose validated parameters and captured runtime identity. Actual source/support edits
become custom Python and disable those parameter controls. Update support files retains captured copies
or previews root replacements/additions/removals. Root files selected for replacement are rechecked at
publication. Old revisions keep their captured helpers and data. Scripts are not sandboxed; source
review does not establish safety, and undeclared dependencies or absolute-path assumptions cannot be inferred.

Test's **Inputs** section accepts new input or a selected example. Leave **Example** at **New input**,
enter values and choose **Run test** for a one-off run; neither a saved block nor a saved example is
required. Run-history persistence still follows **Save runs**. Advanced JSON values start expanded.
Definition problems appear in Design and Test with their locations; usable input fields remain
available, but running is disabled until those problems are corrected. Input renames/removals can
require updating prompt or tool references; output renames update the step's output binding.

**Add example** captures the current input in memory. **Edit selected example** edits its expectation
and values. **Saved examples** shows whether changes are pending; **Save example collection** persists
them separately from blocks and run history. If the block is new or edited, saving the collection
offers to save a block revision first, with the normal review. Cancelling or a failed save keeps
pending examples; the selected example and entered inputs survive Design/Test navigation. Expectations
can compare exact named outputs, assert explicit field paths or ask for human review. Verify an expectation
before using it for automatic correctness. Editing an example invalidates prior verification. An accepted,
well-typed result may still be incorrect. Human ratings bind the exact result and never transfer to a rerun.

Compare selects a saved baseline and either a saved candidate or the current draft, using the same ordered
examples. Review source differences, destinations, trial/repair bounds, effects and saving before Start.
Trials alternate arm order between examples. Results show execution, checks and correctness separately,
with paired answers, trace, denominators, reported usage and timing coverage. Unknown usage is not zero;
with no correct result, time per correct result is undefined. Backend internals and cold starts can vary.

Save runs is off by default and is captured once at Start. Saving off retains active/latest session evidence
until process restart. Saving on writes comparison evidence and child histories; these can be opened from
Recent runs and comparisons and deleted independently. Stop cancels the current child and remaining trials.
Reload reattaches to retained work by ID without dispatching it again. Real tool/script calls may change
external state, and an unknown tool outcome is never automatically retried.

Assist offers five independent actions. Choose the action, role, task, stage or Improve sections, then review
the destination, settings and selected context before sending. Apply changes only the matching draft or
unsaved example suggestions. Discard sends nothing further. Generated custom Python is displayed for source
acknowledgement. A stale or expired proposal requires a deliberate new request. Assistance never publishes
a block or executes its proposal automatically.

## Settings

Model-server connections are configured in `./config/model-servers.ini`, relative to the launch directory.
The installer or first launch creates it from the supplied template if absent; it never replaces an existing
file. Edit the URLs and, if needed, local-server API keys there, then restart AgentWiz. Use `--config PATH`
to select another file. Protect this file if it contains local-server credentials. Do not put an OpenRouter
key in it.

Use the standalone **OpenRouter key** button to enter a write-only session key. It is highlighted when
no key is available. A session key takes precedence over `OPENROUTER_API_KEY`; clearing it exposes that
environment fallback if present. Neither is saved by AgentWiz.

Open **Model settings** in the header to choose the model:

| Setting | Notes |
| --- | --- |
| Model source | LM Studio, OpenRouter or OpenAI-compatible. |
| Model ID | Type it, or use **Load models**. |
| Provider | OpenRouter only. **Load providers** lists the model's providers; choosing one pins the request to it with no fallback. |

Reasoning and max tokens are set in each request panel (see **Task settings**).

**Apply** uses the dialog's settings for this AgentWiz process.

The header shows the workspace (hover for its folder), the active profile, the current settings and
where the key comes from: `key: session`, `key: env OPENROUTER_API_KEY`, `key: config`, or `key: none`.

### Profiles

A profile stores model source, model ID and provider under a name. It never stores a key or server URL.
Older profile URLs are no longer used: transfer any required endpoint manually to the configuration file.

- **Profile files are format version 3.** Older profiles load, but their server URLs are ignored and
  removed on the next write. Earlier AgentWiz releases cannot read version 3 files. Profiles saved by 0.1.1 load as they are, but
  their reasoning and max tokens values are dropped the next time the file is written: set them in
  the request panel and press **Save task settings** instead.

Shared **defaults** live in `./model_profiles/profiles.json`, relative to the launch directory.
Use `--profiles-dir /path/to/another/project/model_profiles` to use defaults kept in another project.
Each new workspace copies all defaults into its own `model-profiles/profiles.json`; none is selected
initially. Existing workspaces are never automatically updated, including on restart.

- **Update profiles from defaults…** adds missing profiles and asks whether to overwrite each
  conflicting workspace profile. Cancel on a conflict keeps that copy; identical copies are unchanged.
- A profile whose settings differ from a same-named default is displayed as `name-(workspace-name)`.
  This is only a label, not a renamed profile. Hover over the header for local and default file paths.
- Check **Export as default when saving** to also publish the saved profile to the shared library.
  Replacing a different default requires confirmation. The option is unchecked whenever the dialog opens.
- Overwriting the active profile during an update applies the imported settings. Default-library
  changes alone never change workspace settings. Deleting a workspace profile never deletes a default.
- Local save and export are separate writes. If exporting fails, the workspace save remains successful;
  the error identifies the file that failed. No API key is included in either library.

- **Save as profile…** asks for a name and saves the dialog's settings as that profile (asking
  before overwriting one), and makes it the active profile.
- **Save profile** saves the dialog's settings into the active profile.
- The **Profile** list applies the chosen profile at once, replacing anything typed but not applied.
  **(no profile)** detaches: the settings stay as they are, and nothing is marked as modified.
- **Delete** removes the active profile after asking; the settings stay as they are.
- The header shows `profile: <name>`, with `(modified)` after you Apply settings that differ from
  the saved profile, or `no profile`.
- The active profile and fixed server configuration are restored when AgentWiz starts. OpenRouter
  session keys are not: enter one again or use `OPENROUTER_API_KEY`.

### Read-only workspaces

AgentWiz never rewrites a file it cannot fully understand. If `workspace.json` is damaged or comes
from a newer AgentWiz, the whole workspace is read-only (the header says so); if `profiles.json` is,
the profiles are; if `task_settings.json` is, saving task settings is disabled. "Damaged" includes
invalid JSON, a missing or wrongly typed field, a repeated key, a number AgentWiz could not write
back exactly, or a base URL breaking the rule above. The file is left untouched, a warning names the
file and the problem (in the terminal and in a banner on the page), and the matching controls are
disabled. Apply and Send keep working.

After repairing or restoring a workspace file, **restart AgentWiz**: it stays read-only for the rest
of the process even once the file is fixed. The shared defaults and the MCP server list are
different: they are read again as soon as they are repaired. If you edit `profiles.json` by hand
while AgentWiz is running, AgentWiz picks it up at the next profile change, and fields it manages may
be overwritten; fields it does not know are kept.

## Task settings

The request panel, under the input, holds the settings of the request itself:

- **Reasoning** (backend default, off, low, medium, high; hidden for OpenAI-compatible servers, which
  have no reasoning control), **Temperature** and **Max tokens**.
- **Advanced**, collapsed: top-p, top-k, min-p, repetition penalty, presence penalty, frequency
  penalty, seed, and stop sequences (one per line). Its heading counts the fields set, for example
  `Advanced (2 set)`, and says how many of them are not sent.

A blank field uses the backend's default. Each request sends the panel as it is. Chat and Checked
request each have their own panel, so they can send different settings at the same time.

**Which settings are sent.** AgentWiz sends a setting only on a route that takes it:

| Model source | Sent |
| --- | --- |
| LM Studio, reasoning backend default, low, medium or high | temperature, max tokens, top-p |
| LM Studio, reasoning off | also top-k, min-p and repetition penalty |
| OpenRouter | all of them |
| OpenAI-compatible servers | all except repetition penalty; no reasoning control |

A field the current source cannot take at the chosen reasoning level is greyed out with a note. It
keeps its value but is not sent. Values are checked before anything is sent: an invalid or
unsupported setting is refused with a message, and the backend is not contacted.

Sent means **accepted by the backend; whether it is applied is not verified.** modelSpell checked
that each backend accepts these settings, not that every model honours them.

- **OpenRouter.** When any setting other than max tokens is set, AgentWiz asks OpenRouter to use
  only providers that support every setting sent (`require_parameters`), so a setting is never
  silently ignored. If no provider qualifies, OpenRouter answers HTTP 404; the error then suggests
  checking the provider pinned in the profile, your OpenRouter privacy settings, and whether any
  provider supports every setting sent.
- **LM Studio with reasoning off.** Some models expose no reasoning configuration, and LM Studio
  refuses `reasoning: off` for them. AgentWiz asks LM Studio for the model's details first and, for
  such a model, leaves reasoning out of the request. If that lookup fails, the request is sent as
  before and the result or error says the lookup did not apply.

**Save task settings** stores the panel in the workspace (`task_settings.json`), replacing the one
saved set; new pages and restarts start from it, and each panel starts from it. `(modified)` beside the heading means the panel differs from the saved set.
**Reset** clears the panel without saving. If another page saved in the meantime, the save is refused
and your values are kept, so you can save again to replace them. Sending never uses the saved set:
it sends what the panel shows.

## Attachments

**Attach file…** on the **Documents** tab reads `.txt`, `.md`, `.csv` and `.json` files, chosen by the
extension in any letter case. Files must be UTF-8 (with or without a byte-order mark) or UTF-16 with
a byte-order mark; anything else, including Latin-1 and UTF-32, is refused with a message asking for
a UTF-8 copy. Line endings become `\n`. The file is kept as it is: CSV is not turned into a table
and JSON is not reformatted.

The preview shows exactly what **Copy** will put on the clipboard: a provenance line such as
`[file "report.md"]`, then the file's text. Beside it are its characters, an estimated token count
(characters ÷ 4; compared with the model's context length when **Load models** reported one), and its
size in a request. **Discard** clears the preview. Paste the text into a request yourself; nothing is
sent until you press **Send**.

- Uploads are limited to 50 MiB and requests to 32 MiB. A file whose text is larger than a request
  may be is still shown and can be copied, with a note that it cannot be sent as it is: copy a
  smaller part of the file instead.
- Nothing uploaded is stored: AgentWiz reads the file in memory, and the file name is used only for
  display and the provenance line.
- Earlier versions had **Insert into Input**, **Insert into System prompt** and an **Insertions**
  list. They are gone; copy and paste instead.
- PDF and Word files need docSpell, which arrives in a later release.

## MCP tools

The Model Context Protocol (MCP) lets a program offer **tools**, such as a web search, to AI
applications. AgentWiz connects to MCP servers that you run on your own machine and reach over HTTP
(MCP's Streamable HTTP transport), lists their tools, and calls the one you choose with the arguments
you give. The result is shown on the **MCP tools** tab; you decide whether to copy it, and nothing is
sent to the model until you paste it into a request and press **Send**.

- **Both protocol eras.** AgentWiz speaks MCP 2026-07-28 and the earlier handshake revisions
  (2025-03-26 to 2025-11-25), and detects which one each server uses. The era and version appear
  after **Load tools**.
- **MCP servers** in the header opens the shared server list (`./mcp_servers/servers.json`, or `--mcp-servers-dir`),
  used by every workspace. Each server has a name, a URL, an idle timeout, a call deadline and a
  discovery deadline, and optionally a token. URLs must not contain a user name, password, query or
  fragment. A **token** is either typed (held in memory, bound to the server's URL, never saved) or
  read from the environment variable named in **Token variable**. Only the variable's name is saved.
- **Arguments.** Simple tools get a form: text, numbers, true/false and choices, with optional
  fields left out when blank. Anything the form cannot express opens a raw JSON editor, with notes
  saying what AgentWiz does not check. **Edit as JSON** switches to the editor at any time. Numbers
  are sent exactly as typed, however long or precise.
- **Outcomes.** `ok`; `tool error` (the tool ran and reported a failure); `rejected` (the call did
  not run); `unknown` (the call may have run, for example when the connection was lost or you
  stopped it after it was sent). AgentWiz never resends a call whose outcome is unknown: check the
  tool's effect another way before running it again.
- **Stop** ends the call at once and says what happened to the server. For 2026-07-28 servers,
  closing the connection is the cancellation; for older servers, AgentWiz also sends a cancellation
  notice. Whether the tool actually stops is up to the server.
- At most 8 tool operations run at once, stopped ones included until they finish; further ones are
  refused, not queued. One tool call runs per page at a time; a model request can run alongside it.
- Results and their sizes are previewed like documents, with a provenance line such as
  `[tool web.search_web arguments={"query":"lighthouse"}]`. When a result has both text and structured
  content, **Copy structured content** copies the structured part instead of the text preview. The
  provenance line is copied with the text. Images and other items are listed, not copied.
- Tool results and documents may contain instructions aimed at the model. AgentWiz shows them
  and never sends them by itself; they reach a model only if you paste them into a request. It does
  not sanitise them.

### Example: web search with web-retrieval-mcp

[web-retrieval-mcp](https://github.com/alexchwong/web-retrieval-mcp) is one MCP server you can try;
any Streamable HTTP MCP server is added the same way, and AgentWiz needs none of it.

1. Prerequisites: Docker with Docker Compose, and `openssl`.
2. Clone and start it:
   ```bash
   git clone https://github.com/alexchwong/web-retrieval-mcp.git
   cd web-retrieval-mcp
   ./setup.sh
   ```
   `setup.sh` creates `.env` with a fresh SearxNG secret and starts SearxNG and the MCP server, both
   listening on this machine only.
3. Check with `docker compose ps` that both services are up. The MCP endpoint is
   `http://localhost:8765/mcp`.
4. In AgentWiz press **MCP servers** in the header, then **Add**: name `web`, URL
   `http://127.0.0.1:8765/mcp`, no token (the server listens on this machine only and has none).
   Press **Save**.
5. On the **MCP tools** tab choose `web` and press **Load tools**: `search_web` and `read_url` appear, with the era. An image
   built before FastMCP 4 shows 2025-11-25; a fresh build shows 2026-07-28. Both work.
6. Choose `search_web`, type a query, press **Run**. Read the passages, press **Copy**, paste them
   into the input of **Chat**, add a question, and press **Send**.
7. Try `read_url` on one of the cited URLs, and press **Stop** while it runs.
8. Afterwards: `docker compose down`.

Notes: web-retrieval-mcp fetches the open web from the machine it runs on; its `settings.toml` tunes
how much it returns; under WSL2, use the address where Docker publishes port 8765.

## Contracts

A contract describes one value: the text or JSON a port carries. Checks compare a value with it
deterministically and report every problem with its location. They check **structure** — types,
required fields, allowed choices, lengths and bounds — not whether a value is correct, complete or safe
to act on. A pass is labelled as a structure pass, never as a correctness or privacy certificate.

There are three kinds, and each can be written inline in a block or saved and reused:

```json
{"format": "text", "description": "A short reply", "min_length": 1, "max_length": 2000,
 "contains": ["Acknowledged"], "not_contains": ["CONFIDENTIAL"]}
```

```json
{"format": "json", "description": "A classification",
 "schema": {"type": ["string", "null"], "enum": ["routine", "urgent", null]}}
```

```json
{"format": "json", "description": "Anything"}
```

- **Text** rules (all optional, all applied together): `min_length`, `max_length` (code points),
  `exact` (including the empty string), `contains`, `not_contains` (up to 100 distinct literals each).
  Comparison uses the decoded text exactly: no trimming, case folding or newline/Unicode normalisation.
  A non-string value fails `text.type`. Contradictory rules (inverted bounds, a literal both required and
  forbidden, an `exact` value another rule rejects) are refused.
- **JSON with a schema** uses a small JSON Schema vocabulary: every node has `type` — `string`,
  `integer`, `number`, `boolean`, `array`, `object`, or `[T, "null"]` in that order — and may have a
  `description`. Strings may have `enum`, `minLength`, `maxLength`; numbers `minimum`, `maximum`;
  arrays a required `items` plus `minItems`, `maxItems`; objects a required `properties`, optional
  `required` and a boolean `additionalProperties` (omitted means extra properties are allowed).
  Property names are identifiers. At most 100 nodes and depth 10. Everything else (`$ref`, `pattern`,
  `format`, `oneOf`, `const`, `default`, …) is refused at its location rather than ignored.
- **Any JSON** (no `schema`) accepts any JSON value and is shown as **Any JSON — no field validation**.

Required and nullable are independent: `required` says a property must be present; `[T, "null"]` says
it may be an explicit `null`. A nullable choice lists its strings and then one final `null`.

**Numbers are exact.** Integers and decimals are kept digit for digit; `2`, `2.0` and `2e0` are the same
integer, `true` is not a number, and no value is converted to a float. Identifiers such as record numbers
belong in strings.

**Reading an answer.** A JSON value is parsed from the whole answer or from its single fenced code block;
there is no other recovery. Text is checked as written.

**Saved contracts.** A saved contract is `<workspace>/contracts/<name>.json`: a stable name, a family for
grouping, and every revision oldest first. Saving an edit appends a new revision; saving an unchanged
definition does nothing; **Restore** appends a copy of an older revision; changing the family does not
create a revision. Revisions are never rewritten, and each has a digest of its definition that AgentWiz
checks every time it reads the file — a file whose history was changed by hand is listed as unusable.
Every change states the file token it was based on, so an edit from a stale tab is refused rather than
overwriting another. A contract that blocks still use (current or retained Workshop revisions) cannot be
deleted; the refusal lists them. Old version 1 contract files are listed as `migration_required`: recreate
them as new contracts.

**Pinned references.** A block refers to a saved contract by `{"name", "revision", "digest"}`, never by
name alone or "latest". Appending a newer revision does not change any block; switching a block to it is
an explicit block edit. Runs and repairs keep their own copy of every contract they used, so a later edit
or deletion never changes them.

**Contract synthesis** asks the role `write_contract` (unassigned by default; assign it in Roles) for a
proposed JSON contract with its assumptions listed separately. The proposal is a draft to review: it is
never saved or attached automatically, it gets one repair if its schema is outside the vocabulary, and
saving it records only which prompt, role and model produced it.

**Results.** Each check gives `pass`, `fail` or `not_run`; overall `pass`, `fail`, `incomplete` (a check
could not run, for example a number beyond the exact limits) or `contract_error` (the contract itself
could not be applied — never the model's fault, and never repaired). Issues are listed by location; the
first 20 are shown with the total count, and value messages name the rule, not your data.

### Common tasks

Five illustrative starters (`agentwiz/contract_examples.py`; synthetic data, not a regulatory or clinical
standard) show the vocabulary: **Identifier occurrences** (a list of kind/value/source/offsets),
**Clinical observations** (status, nullable value/unit/comparator, as reported), **Classification** (one
choice), **Invoice** (header, lines, total) and **Action items** (task, nullable owner and due fields).
The same building blocks cover de-identification, medications, grounded summaries, transaction matching,
legal obligations, compliance evidence, security or safety triage, letters, translation, table
extraction, variable key/value records (a list of `{key, value}` groups), tool-action proposals, search
results, artifact references and suggested corrections — as task-specific groups, lists, text and
choices.

Limits to plan for: source spans need an immutable source and a separate half-open code-point offset
check; dates are strings with no date assertion; arithmetic (totals, reconciliation) needs deterministic
code; key/value slots need their own consistency checks; real tool arguments keep their tool's own
schema; and an empty extraction list must not stand for an input that failed to process. Until multi-block
workflows exist, run those checks as separate script blocks or example assertions — a structurally valid
but wrong value passes its contract and only an example expectation or review rejects it.

## Checked requests and repair

The **Checked request** tab is Chat with a contract. Choose a saved contract (only usable ones are
listed, with their latest revision number; Send stays disabled, with the reason, until one is chosen),
type a system prompt and an input, and press **Send**. The request carries the exact revision and digest
shown; if that revision has since been changed or removed the request is refused before sending, while a
newer appended revision does not affect it. When the answer completes, it is checked, and the tab shows the overall result, each
check, and the first 20 issues.

- **What is checked.** Only answers whose outcome is *Complete* or *Truncated answer*. Blank,
  stopped and failed requests are not checked; the tab says why.
- **Runs and attempts.** Each Send starts a **run**; the first send and each repair are numbered
  **attempts** of it. The tab lists them with their outcomes and check results.
- **Repair** is offered when the latest attempt's check *failed*. It is not offered after a pass,
  an incomplete or `contract_error` result (neither is the model's fault), a blank or failed
  answer (send again, usually with a higher token cap), for an attempt that is not the latest, or
  once the run has been stopped (send again). The button says which, and an attempt that has dropped
  out of memory cannot be repaired.
- **Preview repair** shows exactly what **Send repair** will send: the original messages, the failed
  answer as an assistant message, then a fixed request listing the problems and asking for the whole
  corrected output. Only the latest answer and repair message are sent, not earlier failed attempts.
  The preview and the send cannot differ.
- **Settings are the captured ones.** A repair uses the model, endpoint, provider pin, task settings
  and contract copy of the attempt it repairs, not what the screen shows now. Changing the input,
  profile or contract means starting a new run with **Send**. The API key is looked up again when you
  send the repair; if you cleared it, the backend's refusal is an ordinary error.
  If its captured endpoint differs from the configured server, repair is refused before sending;
  start a new request instead. Historical records are not rewritten.
- **LM Studio with reasoning off** uses LM Studio's native route, which flattens a conversation into
  one text. Repair still works, but the roles are lost.
- A repair counts toward the limit of 8 model requests in progress.

## Blocks

A block is a folder directly under `<workspace>/blocks/`; its name is the folder name and the `name` in
its `block.json`. Legacy format-1 folders can still be edited externally and run in place. The saved-block inspector
inside Workshop retains Reload and the original run/Stop/trace controls. Workshop Save creates the
managed format-2 head described above; use Workshop to edit managed revisions rather than changing
their immutable captured assets.

```
<workspace>/blocks/extract_fields/
    block.json
    prompts/system.md
    prompts/user.md
    scripts/clean.py        (script steps only)
```

`block.json` (format version 1). Every key not listed here is refused, with its location, so a
mistyped key never silently does nothing.

| Key | Meaning |
| --- | --- |
| `format_version` | `3`. Version 1 blocks are refused with `migration_required`; a greater number means a newer AgentWiz. |
| `name` | The folder name (1 to 64 letters, digits, `.`, `_`, `-`). |
| `description` | Optional text shown in the list. |
| `inputs` | Object: input name to `{"contract": ..., "description": ...}`. The contract is an inline definition or an exact saved-contract pin `{"name", "revision", "digest"}`. Names follow `[A-Za-z_][A-Za-z0-9_]{0,63}`. May be empty. |
| `outputs` | Same shape, at least one. |
| `steps` | A list of **exactly one** step. |
| `budget` | `{"max_seconds": 1..86400 (default 1800), "max_model_tokens": integer >= 1 or absent}`. |

A model step:

```json
{"id": "extract", "kind": "model", "role": "fast",
 "system": {"file": "prompts/system.md"},
 "prompt": {"file": "prompts/user.md"},
 "task": {"temperature": 0, "max_tokens": 1024},
 "output": "fields",
 "repair": {"max_repairs": 2}}
```

`system` (optional) and `prompt` are `{"file": "prompts/..."}` or `{"text": "..."}`. `task` takes the
same settings as a request panel. `output` names the one declared output. A model or tool block
declares exactly one output.

A tool step calls a tool on a server from the MCP servers dialog:

```json
{"id": "lookup", "kind": "tool", "server": "docs", "tool": "lookup",
 "arguments": {"id": "{{inputs.id}}", "label": "n={{inputs.id}}"}, "output": "result"}
```

A script step runs a Python file (see **Script steps**):

```json
{"id": "clean", "kind": "script", "file": "scripts/clean.py", "timeout_s": 60}
```

- **File references** are relative paths inside the block folder (`prompts/` for prompts, `scripts/`
  and ending in `.py` for scripts). A symbolic link, a missing file or an oversized one (4 MiB; 1 MiB
  for `block.json`) is a problem. Only referenced files matter; helper files beside them are ignored.
- **The Blocks tab** lists each block with its step kind and revision, or its problem (file, code and
  located issues). A block with a problem can be opened and read, but not run. Opening a block shows
  `block.json` and each referenced file as exact text with its SHA-256 and a **Copy** button, and the
  contracts it uses as exact text with their names and revisions.
- **The input form** comes from the input contracts: a text area for a `text` input; for a `json`
  input, a generated form when its schema is a simple object schema (numbers are kept exactly as you
  type them), otherwise a JSON editor. Values travel as text.
- **Revisions.** Each block has a revision that changes when its files change, or when a revision it
  pins changes, disappears or becomes unusable (a newer appended revision does not count). The page runs a block only at the revision it showed. If the block changed since, the run is
  refused (`stale`), the tab reloads the block and keeps the input texts whose names still exist, and
  nothing is sent. Run again.
- **The trace** shows the run's state and outcome, the step, each attempt with its outcome, check and
  repair reason, the fills with their provenance, the tool call or script operation, truncated
  attempts and the token cap, reported usage against the allowance, the input and output check
  results, and the outputs, each with a **Copy** button.
- **History.** Saved block runs appear in the History tab with `run.json`, each attempt and
  `result.json`. A block run is never loaded back into the Blocks tab.

## Roles

Roles select saved model profiles for model blocks and Workshop assistance. Fresh workspaces offer
three unconfigured starter roles: **fast** (routine generation/extraction), **assistant** (Workshop
assistance), and **reasoning** (more demanding tasks). These are suggestions, not model capabilities;
several roles may share a profile. No model, paid service or credentials are selected automatically.

Open **Settings → Roles** to browse by **location** (All, Local, Cloud / remote), then by
**family / domain**, and select a role from the matching-role dropdown. Only one role editor is
displayed at a time. Suggested families are General, Writing & communication, Coding & engineering,
Research & analysis, Data & extraction, and Workflow assistance. Use **Add family** in the editor
to create and assign your own family; custom families persist with their saved roles. Older roles
without a family appear under General. Adding a role uses the active location/family filters.

Edit the selected role's purpose, location, family and saved profile. Switching roles or filters
retains unsaved edits; **Save all changes** saves every role, including roles hidden by filters.
Profile choices show backend, endpoint and model. **Create / edit model profiles**
opens model settings and returns without losing role edits. **Set up defaults** adds missing starter
roles without replacing existing definitions. Save persists assignments and definitions in
`<workspace>/roles.json`. Saved role names are stable; removing a role warns about references in blocks.

**Local means this machine:** LM Studio or an OpenAI-compatible server (including llama-server)
using `localhost` or a loopback IP address. LAN servers and other remote endpoints are classified as
Cloud / remote; OpenRouter is always Cloud. Profile choices are filtered by location, and the backend
checks compatibility at assignment and role resolution. Neither location silently falls back to the
other. This constrains the endpoint AgentWiz contacts, not what a server might forward elsewhere.

Each role may carry a default **Temperature** and **Reasoning level**; leave either as *Backend default*
to set none. They apply to every model block and Workshop assistance call that uses the role, unless the block
overrides or resets that setting ([Named outputs](#named-outputs)), and never to Chat or Checked request,
which use the active profile. Zero is a default, not an absent one. An assignment-only save keeps existing
definitions and defaults.

Unconfigured roles can be used while drafting, but cannot run. Missing profiles, blank models and
location mismatches are shown with reasons. Existing version-1 assignments remain usable without
automatic reassignment; saving through the editor adds explicit location definitions. Files written
with definitions use format version 3 (versions 1 and 2 are still read) and require this version of
AgentWiz or newer. **Chat and
Checked request keep using the active profile**, not these roles. Role resolution uses saved profile
settings when preparing a run; already captured Workshop trials retain their reviewed snapshots.

## Templates

A model step's `system` and `prompt`, and every string inside a tool step's `arguments`, may contain
placeholders written exactly as `{{inputs.name}}` or with a dotted path into a JSON input,
`{{inputs.report.title}}`, with no spaces.

- In **text**, a string is inserted as it is and any other value as its exact JSON text.
- In **tool arguments**, a string that is exactly one placeholder is replaced by the value itself with
  its own JSON type: a number stays a number, digit for digit, and an object stays an object. Text
  around a placeholder makes it a text fill.
- A missing key, or a path into something that is not an object, rejects the run before anything is
  sent; the issue names the placeholder, never the value.
- Any other `{{ ... }}`, such as braces in an example or in JSON, is left alone. A malformed
  placeholder (spaces, `{{inputs.}}`, an index) or a `{{steps...}}` placeholder is refused when the
  block is loaded.
- The trace lists every fill with its location, placeholder, source and the SHA-256 of what was
  inserted. In a model step's system and user prompts, `{{output.name}}` inserts that output's description,
  format and schema (see [Named outputs](#named-outputs)); it is not a value, and script and tool
  templates never accept it.

## Contracts in blocks

- **A model step sends the output format, not the checks.** AgentWiz adds a generated section to the
  system message naming each declared output, its description, its format and, for a structured output,
  its complete schema ([Named outputs](#named-outputs)). Check rules (`contains`, `length` and so on),
  examples and saved contract names are never sent. The contract still alone decides whether the answer is
  accepted, and the generated section does not replace it.
- **How answers are read.** In strict mode the whole trimmed answer is parsed. Otherwise a single
  fenced code block is used, with any prose around it ignored, or else the whole answer.
- **The truncation rule.** A block never accepts a cut-off answer, even one whose check passes: a
  truncated attempt is repaired (with a message saying it was cut off) or, if repair is exhausted,
  fails the step. The step result shows the truncated attempts and the cap that applied, so you can
  raise `task.max_tokens`.
- **Inputs have contracts too.** Each input's text is parsed and checked before the run is admitted.
  A failure rejects the run (outcome **rejected**), takes no slot, and names the input and the rule.
  A JSON input is parsed whole: a fenced block in an input field is a parse error.

## Named outputs

A model step has `"response_mode": "named_json"` and no `output` key. The model returns **one JSON object
whose top-level keys are exactly the names in the block's `outputs`**, for one output or many. Each key's value
is checked against that output's own contract, and the outputs are published together or not at all: a failed,
truncated or stopped answer exposes no partial output. A repair asks for the whole object again.

```json
{"id": "request", "kind": "model", "role": "fast", "response_mode": "named_json",
 "system": {"text": "Extract facts without inventing missing values."},
 "prompt": {"text": "Read {{inputs.source}}. Return {{output.text}} and {{output.structured_table}}."},
 "task": {"temperature": 0.2, "reasoning": "low"}, "repair": {"max_repairs": 2}}
```

**What is sent.** The system message is your system text with its input placeholders filled, then a section
that begins and ends with marker lines and states these rules (the wording comes from
`agentwiz/output_protocol.py`; Request preview shows the real text):

- Return exactly one JSON object and nothing else: no words before or after it, and no Markdown fence around it.
- Its top-level keys are exactly these output names, in any order, one value each.
- Every key must be present. Do not add keys, rename them, or nest one output inside another, and do not split
  one output across several keys.
- Place each output's complete value directly under its own key. A value may be empty only where its schema
  allows that.
- A text output is a JSON string holding the complete text; quotes, backslashes and line breaks are escaped as
  JSON requires, and any formatting the task asks for goes inside that string.
- A JSON output is the value itself, placed directly under its key, not a string containing JSON.

Each output is then described with its purpose and, when structured, its complete JSON Schema. The section is
generated for every run and is never written into your block files, so repeated previews cannot duplicate it.
It asks for key order of no kind, because acceptance checks the key *set*; the prohibition on prose and fences
applies outside the object, so a text value may contain Markdown.

**`{{output.name}}`** in a model step's system or user prompt inserts that output's description, format and
schema on one line. It describes the output; it can never refer to the answer the model has not yet given.
`{{output.a.b}}`, `{{ output.a }}` and `{{output.}}` are refused when the block loads, and so is a name the block
does not declare. Text inserted into a prompt is never scanned again, so braces inside an input value, a
description or a schema stay literal.

**Parsing is unchanged.** The whole trimmed answer is parsed, or a single fenced block when the contract is not
strict; duplicate keys are refused. Nothing is repaired, guessed or extracted from surrounding prose. Output
checks for structured values need a working validator installation; otherwise a block that needs them refuses to run
instead of skipping the check. Checks establish structure and the rules you configured, not that an answer is true.

**Older blocks.** A 0.1.x model block named its single output on the step. Such a block now loads as invalid
with the location `steps[0].output`; AgentWiz does not convert it. Edit `block.json`: delete the step's
`"output": "name"` line, add `"response_mode": "named_json"`, and keep `outputs` as it is. Update the prompt
if it described the old reply, since the model must now answer with the object above. Old saved attempts remain
readable. A block carrying the new key is refused by older AgentWiz versions rather than run wrongly.

**Sampling precedence.** Temperature and reasoning are resolved separately for each setting: a value the step
sets wins; `task_resets` naming the setting asks for the backend's default and stops the role's default being
inherited; otherwise the role's saved default; otherwise the backend's default. Other task settings stay as the
step configures them. The result is resolved once when a run is prepared, so every repair of that run keeps it,
and a started comparison keeps the settings it was reviewed with.

## Automatic repair

`repair.max_repairs` on a model step counts model calls **after the first**: 0 to 5, default 2; 0
switches repair off. When an attempt completes but fails its check, or is cut off, the next call
sends the first request, the failed answer and a message listing the findings (or saying the answer
was cut off). Only a **complete** answer whose check **passes** is accepted. No setting changes on
repair and the token cap is never raised. No repair is attempted after a stop, a time or token
budget stop, a model error, an empty answer, an incomplete check or a contract error; the trace says
which applied. Each repair is a full request, which on a local model without prompt caching is a full
prompt evaluation.

## Script steps

A script step runs one of your Python files under `scripts/`. The block's inputs arrive on standard
input as one JSON object; the script prints one JSON object on standard output whose keys are the
block's output names (every declared output must be present).

- The script runs with an allow-listed environment (`PATH`, `HOME`, `LANG`, `TMPDIR`), so it holds no
  credentials, in its own process group, with a time limit (`timeout_s`, default 60, at most 3,600).
  Its working folder is a scratch folder; with Save runs on it is kept as `scratch/<step id>/` in the
  run's folder.
- **Stop** (and the time budget) sends the process group SIGTERM, then SIGKILL after 2 seconds.
- AgentWiz hashes the file when the block loads and checks the hash again just before Python opens
  it; a file that changed in between is not run. An edit in that last instant is not detected.
- Output is limited (32 MiB; the last 1 MiB of standard error is kept).
- **A script is not sandboxed.** It runs as you, with your access to files and the network, and can
  read any file you can, including files that hold keys. Run only scripts you wrote or read.
- Script steps need POSIX process groups: Linux, macOS and WSL2. Native Windows is not supported.

## Runs and budgets

Pressing **Run** checks the inputs, then admits the run, which reserves what it needs up front: one
model request, one MCP worker or one script process. If the budget for that is used up, the request
gets a 503 `busy` that names it; nothing queues.

- **Outcomes.** `complete`, `rejected` (inputs, a fill failure, an unresolved role or server: nothing
  ran), `failed`, `stopped`, `budget_exceeded` and `unknown` (a tool call that may have run).
- **`max_seconds`** is a time budget for the whole run. When it expires the run is cancelled and ends
  `budget_exceeded`; it is not an exact wall-clock ceiling (a script's teardown can add up to two
  escalation intervals, and a blocking local operation finishes first).
- **`max_model_tokens`** is a **soft allowance of reported usage**, checked before each model call.
  One attempt can take a run past it; the overshoot is shown ("over by N"), and attempts whose usage
  the backend did not report are listed, never estimated.
- **`result.json`.** Every run ends with a result: outcome, inputs and input checks, the captured
  contracts, outputs (only for `complete`), the step result, budget and usage, versions. With Save
  runs on it is written to the run's folder; otherwise the latest 100 results (256 MiB) are held in
  memory.
- **The page is optional.** A block run continues if the page closes or loses its connection, until
  it ends or its time budget does. When the Blocks tab loads, running block runs are listed and can
  be followed (the tab then polls every 2 seconds) or stopped. Reattachment works within the running
  server only: runs end when AgentWiz exits normally, and an abrupt kill (SIGKILL, a crash) leaves
  no record and can leave a script running.
- **On exit,** Ctrl-C or SIGTERM stops running blocks, ends script groups and waits for writers to
  finish; a second signal exits at once without finishing, printing nothing.

## Stop

**Stop** (on Chat, Checked request and MCP tools) asks AgentWiz's server to end the request. For a
model request this works at any stage: while connecting, while waiting for the first token, or in the
middle of an answer. The request normally ends within about a second, the outcome is **Stopped**, and
the page keeps showing the text that had arrived.

- **"The request may have been sent"** means that some of it was written to the connection, so the
  backend may have seen, and may charge for, the request. "Not sent" means nothing was written.
- **Stopping is local.** AgentWiz closes its connection. Whether the backend stops generating, and
  stops charging, is up to the backend.
- **Two exceptions.** While the host name is being looked up, Stop takes effect when the lookup
  returns, before anything is sent. And with LM Studio and reasoning off, AgentWiz first asks LM
  Studio about the model; Stop waits for that lookup (at most 10 seconds).
- **If Stop arrives just after the answer completes**, the answer is kept, the page says "Stop
  arrived after the answer finished", and the run takes no repairs (send again).
- **A request that is stopped, or whose page is closed**, is still recorded, with its partial text.
- **Stop on the Blocks tab** ends any step of a block run: a model request as above; a script by its
  process group (SIGTERM, then SIGKILL); a tool call by abandoning it, which closes its connection
  and, on servers that use the earlier handshake, sends a cancellation notice. **A stopped tool call
  may still have run**, so its outcome is shown as unknown. An answer already accepted by the time
  Stop arrives stands.

## Run history

Off by default. The **Save runs** switch in the header turns it on; while it is on, the header says
"Saving runs". The switch is saved in `workspace.json`, survives a restart, and cannot be turned on in
a read-only workspace.

- **What is saved.** Prompts, answers, reasoning, check results and settings, as the record of each
  attempt, so text you paste into a request, including documents and tool results, is saved with it.
  Keys are never part of a record.
- **Credentials.** Every API key and MCP token AgentWiz has held during the process (including ones
  you replaced or cleared, and the `OPENROUTER_API_KEY` and token variables) of at least 8
  characters is replaced with `***` in the saved files, which then say `"redacted": true`. Shorter
  credentials cannot be replaced without corrupting ordinary text and are saved as given; while that
  could happen, the header says so. Secrets you type into a prompt, or a model returns, are not
  recognised.
- **When it is read.** A run uses the switch as it was when the run was created, repairs included.
  Turning it on later does not save a running request; turning it off later does not stop a run that
  was already being saved. Turning it off never deletes anything.
- **Where.** `<workspace>/runs/<YYYYMMDD-HHMMSS>-<run id>/` holds `run.json`, written when the run
  starts, and `request-1.json`, `request-2.json`, … one per attempt, each written once after the
  attempt ends and never rewritten. Folders are private to you (mode 0700, files 0600). The
  attempt's save status is shown: saved (with the file), or failed with the reason. A failed write
  is reported, not retried.
- **The History tab** lists the newest 200 saved runs (and says "200 of N shown"). Open one to read
  it, read-only, with a **Copy** button on every message, answer, reasoning, check result and
  record. A run is never loaded back into another tab.
- **Delete this run** removes its folder after an inline confirmation. A run with an attempt in
  progress or a save pending is refused. A deletion that fails part-way leaves the run marked as
  incompletely deleted; no further attempts are written into it, and you can retry. Deletion never
  follows a symbolic link.
- **After a lost connection**, a page that did not receive a final result asks the server for the
  committed outcome; nothing is ever resent. If the connection dies before the request is
  acknowledged, the page says "Outcome unknown: AgentWiz may have accepted this request" and, with
  saving on, points you at the History tab.
- Without saving, the latest 100 finished attempts (256 MiB at most) stay in memory, with
  summaries of the latest 1,000; older ones cannot be repaired, and eventually their outcomes cannot
  be retrieved.

## Outcomes

When a request ends, the status line starts with its outcome:

| Outcome | Meaning |
| --- | --- |
| Complete | An answer arrived and was not cut off. |
| Empty answer | The model finished without any answer text. |
| Reasoning only, no answer | The model produced reasoning but no answer. |
| Truncated answer | The answer was cut off by the token limit. |
| Truncated before any answer | The token limit was reached before any answer; "(token cap reached during reasoning)" is added when there was reasoning. |
| Error text | The request failed, was refused, or the connection ended before a final result. |
| Stopped | You pressed Stop, or the page was closed. See **Stop**. |

The rest of the line gives the time, transport, finish reason, token counts and cost. A missing
value is shown as `?`, never as zero. **Final event** shows the final event exactly as received:
the committed record, with the identity of the run and attempt, the task settings sent and, for a
stopped request, whether it may have been sent.

## Backends

| Backend | Choose | Notes |
| --- | --- | --- |
| LM Studio | LM Studio | modelSpell needs LM Studio 0.3.29 or later, and 0.4.0 or later for reasoning "off" and Load models. |
| OpenRouter | OpenRouter | Key from the settings dialog or `OPENROUTER_API_KEY`. Provider discovery and pinning. |
| llama-server | OpenAI-compatible | Base URL such as `http://127.0.0.1:8080/v1`. Reasoning text is displayed. |
| Other OpenAI-compatible servers | OpenAI-compatible | Any server with `/v1/chat/completions` streaming. |

## Platforms

AgentWiz runs on Linux, macOS and WSL2 with any of the backends above, for example:

- Linux or macOS, with LM Studio or llama-server on the same machine
- WSL2, with LM Studio on Windows (mirrored or NAT networking) or LM Studio headless inside WSL2
- any platform, with OpenRouter

Native Windows is not supported.

### WSL2 setup

- **A. LM Studio desktop on Windows, AgentWiz inside WSL2.**
  - With **mirrored networking** (Windows 11 22H2 or later; `networkingMode=mirrored` under
    `[wsl2]` in `.wslconfig`), WSL2 reaches Windows services on `127.0.0.1`, so the default
    `http://localhost:1234/v1` works. Mirrored mode does not support the IPv6 loopback `::1`, so if
    `localhost` misbehaves, use `http://127.0.0.1:1234/v1`.
  - With the **default NAT networking**, find the Windows host's address with
    `ip route show | grep -i default | awk '{print $3}'` and turn on LM Studio's "Serve on Local
    Network". That exposes LM Studio to your local network, so enable LM Studio's authentication
    first and enter its token as the API key. A Windows firewall rule may be needed.
- **B. LM Studio headless (`llmster`) inside WSL2.** Install with
  `curl -fsSL https://lmstudio.ai/install.sh | bash` and start it with `lms server start`.
  `localhost` works directly. GPU access depends on WSL2's GPU passthrough, and models are stored
  separately from any Windows install.

In both setups a Windows browser can open the address AgentWiz prints, because WSL forwards
localhost from Windows. Opening the browser automatically from WSL2 often does nothing; copy the
printed address instead.

## Trying it out

Click **Tutorial** in the header to open an isolated workbench with local scripted services,
auto-filled inputs and a persistent tips box. No API keys, models or terminal setup are needed.
The tutorial works in the installed application too.

Fourteen short feature introductions show how to (each is annotated with the AgentWiz version that last
changed what it shows):

- **Chat** (updated in 0.1.3): send a message and reuse an answer.
- **Use a document** (updated in 0.1.3): preview an example and copy it into a conversation.
- **Call a tool** (updated in 0.1.3): run a prepared local tool and use its result.
- **Define a contract** (updated in 0.2.0): save reusable structural requirements; a wrong number of the
  right type still passes — exact values belong in Workshop example expectations.
- **Repair a response** (updated in 0.2.0): an answer missing a required field fails; preview and send a
  follow-up using contract feedback.
- **Run a model block** (updated in 0.2.0): see the role `fast` mapped to a profile, then run a block
  whose first answer fails its contract and whose repair passes.
- **Run a tool block** (updated in 0.2.0): run a block that sends two exact numbers to a tool and
  gets them back digit for digit.
- **Run and stop a script block** (updated in 0.2.0): count words with a script, then start a script
  that waits and Stop it.

- **Design a block** (updated in 0.2.0): save a starter, test it and compare revisions with examples.
- **Create, improve, describe, suggest examples and explain a failure** (five topics, updated in 0.2.0):
  prepare assistance against the isolated model, inspect a proposal and Apply or Discard it.

These are learning workflows, not correctness checks or owner assignments. Move freely with
Next, Back, Reset step or the feature selector. No acknowledgement or successful outcome is
required. Regression scenarios and expected pass/fail assertions live in pytest, not this tour.

Exit returns to your original workspace and removes the sandbox and its services. Normal profiles,
documents and history remain unchanged. Closing the tab alone does not stop the session immediately:
abandoned sessions expire after 30 minutes without a page heartbeat (checked every 30 seconds).
Stopping the original AgentWiz process also closes its tutorial.

## Limitations

- **Stop.** Stop ends a model request within about a second at every stage, except while a host name
  is being looked up (it takes effect when the lookup returns) and during LM Studio's model lookup
  for reasoning "off" (at most 10 seconds). Stopping is local: whether the backend stops generating,
  and charging, is up to it. A text event already being written to the page finishes first. At most
  8 model requests can be in progress, stopped ones included until they end; further ones are refused.
- **Stop after the answer.** A Stop that arrives just after the answer completes keeps the answer, but
  closes the run to repairs; send again.
- **Backend error details are not shown.** Failures after a request is sent appear as a fixed
  description with the error class, HTTP status and base URL, because backend error text can
  contain your API key in forms AgentWiz cannot reliably remove. MCP servers' error text is shown
  only when no token is held for that server, labelled "the server says:".
- **Accepted is not applied.** A task setting accepted by a backend may still be ignored by it.
  OpenRouter's provider restriction can refuse requests that would otherwise have succeeded with a
  setting quietly ignored. On LM Studio, seed, stop sequences and the presence and frequency
  penalties are never sent; top-k, min-p and repetition penalty need reasoning off.
- **Settings and keys.** Model settings are shared by all pages of one AgentWiz process; only
  profiles, the saved task settings, the MCP server list, contracts, saved blocks/examples, the Save runs switch
  and (if on) saved runs/comparisons survive a restart. Keys and typed MCP tokens are held in memory only and must be
  re-entered after a restart.
- **Inputs are not kept.** Each tab's fields are lost when the page reloads.
- **Checks are syntactic.** They check format, structure and stated values, not whether an answer is
  right. Regular expressions use Python's dialect, in `regex` checks and in a schema's `pattern`; a
  pathological expression can take a long time on a long answer.
- **Number limits.** Exact checks stop at 4,300-digit integers and a 2,100-digit decimal precision;
  beyond them a check reports not run, never an approximation.
- **JSON Schema** is included in the standard installation and brings the compiled package `rpds-py`.
- **A contract edited on disk while a request uses it** does not affect that request, which uses the
  copy taken when it was accepted. A repair uses the contract of the attempt it repairs.
- **Memory.** At most 100 finished attempts and 256 MiB are held, plus 1,000 summaries. Older
  attempts cannot be repaired, and eventually their outcomes are no longer retrievable, unless the
  run was saved.
- **Redaction** covers only credentials AgentWiz has held, and only those of at least 8 characters.
  Shorter ones, and secrets typed into prompts or returned by models, are saved as given when
  history is on.
- **History** has no search, filters or export, lists the newest 200 runs, and never loads a run back
  into a tab.
- **The final event repeats the request's messages**, since it carries the whole record; a prompt near
  the 32 MiB request limit makes it two to three times that size.
- **MCP.** Local servers over Streamable HTTP only; tools only (no resources, prompts, sampling,
  elicitation, roots, logging, tasks or subscriptions); no stdio servers; no servers that speak only
  the 2024-11-05 HTTP+SSE transport; no stream resumption; tool results are not checked against the
  tool's output schema, nor against a contract. On a 2026-07-28 server, only tools that the server
  lists can be called. A tool argument that is required but may be `null` needs the JSON editor. At
  most 8 tool operations and 4 cancellation or session-ending tasks run at once; a server whose name
  never resolves holds its slot until the resolver gives up.
- **Unknown tool outcomes.** A lost connection, a server failure, a deadline or Stop after a call
  was sent leaves its outcome unknown; AgentWiz never resends it.
- **Documents** are the four text formats, kept whole; the token count is an estimate.
- **Workspaces.** One AgentWiz process owns one workspace at a time; the Workspaces dialog can switch
  within the launch folder. The default folders are relative to where AgentWiz is launched. Shared,
  network and cloud-synced folders are unsupported and not detected. Clone excludes saved runs and
  comparisons by default, reset cannot be undone, and only a non-current workspace can be deleted.
- **Damaged or newer local files** make the workspace, its profiles or its task settings read-only
  until AgentWiz restarts. Damaged defaults disable import/export only, and a damaged MCP server list
  disables MCP; both are detected again as soon as they are repaired, as are contract files. Numbers
  with more precision than AgentWiz can keep exactly count as damage in those files.
- **Profile files rewritten by 0.1.2 or later** are read-only to AgentWiz 0.1.1.
- **Files.** Files AgentWiz rewrites become readable by their owner only (mode 0600). A crash during
  a write can leave a `.<file>.<random>.tmp` file beside an intact original; after a power cut, the
  most recent change may be lost.
- **Backends.** OpenAI-compatible servers (including llama-server) have no reasoning control from
  AgentWiz; their reasoning text is still displayed. On LM Studio's native route (reasoning "off"),
  truncation is modelSpell's estimate. Stop, contracts and repair on LM Studio and llama-server are
  covered by optional live checks; results require an available configured backend.
- **Blocks.** One step per block: no wiring, routing or nesting yet. Workshop writes managed revisions; externally edited v1 blocks remain supported. Scripts are not sandboxed and need POSIX process groups; a script that
  starts its own session escapes group termination. The token budget is a soft, reported-usage
  allowance; time budgets are not an exact ceiling. Block runs continue after the page closes, up to
  their time budget, holding their slots; they do not survive a restart. Results are held for 100
  runs and 256 MiB; older ones are available from History only if saving was on. A stopped or
  timed-out tool call may still have run. The parser stays strict, and the truncation rule fails
  steps whose check passed. Graceful exit can wait for a script group that will not die; a second
  Ctrl-C exits immediately and skips cleanup. The tutorial doubles process budgets while it is open.
- **The page** is checked in a headless Chromium by the developer tests; other browsers are covered
  only by the tour above.
- **WSL2** is not checked by the automated tests.

## License

MIT. See `LICENSE`.
