# AgentWiz

## Quick start (AgentWiz 0.2.5)

**Prerequisites:** Python 3.10+, Docker with Docker Compose, OpenSSL, and an internet
connection for first-time setup. On Windows, use WSL2 with Docker available.
Start Docker before using the release installer, which sets up the bundled webSpell service.

**Install:** From a normal terminal, run the AgentWiz release installer:

```bash
curl -fsSL https://github.com/alexchwong/AgentWiz/releases/latest/download/install.sh | bash
```

The installer creates `.venv`, installs AgentWiz and **automatically installs,
configures, starts and checks webSpell** (including SearXNG). No manual webSpell
repository clone, MCP registration or API key is required for public web search.

**Run:** From the same directory where you installed AgentWiz:

```bash
.venv/bin/agentwiz
```

AgentWiz Chat opens in your browser. Choose a Cloud model and use the webSpell
tools for public research. webSpell is available only in Cloud mode. Set your OpenRouter
key using **OpenRouter key** in the Chat toolbar, or configure it as described under
[AgentWiz 0.2.4](#agentwiz-024).

**Manage webSpell (separate background service):**

```bash
.venv/bin/agentwiz-webspell status
.venv/bin/agentwiz-webspell stop
.venv/bin/agentwiz-webspell start
.venv/bin/agentwiz-webspell restart
.venv/bin/agentwiz-webspell logs
```

Stopping AgentWiz does not stop webSpell; stopping webSpell does not prevent
ordinary AgentWiz chat. Docker's `unless-stopped` policy restarts unexpectedly
stopped containers, but respects a deliberate `stop`.

**Change the MCP port:** Edit `mcp_servers/servers.json` alongside your
AgentWiz installation. Change only the `webspell.url` entry, e.g.:

```json
{
  "format_version": 1,
  "servers": {
    "webspell": {"url": "http://127.0.0.1:8766/mcp"}
  }
}
```

Keep all other server entries if the file already has them. Apply the port
change with `.venv/bin/agentwiz-webspell restart`, then restart AgentWiz. The
managed MCP URL must use `127.0.0.1`, port 1024–65535, and path `/mcp`.
To use a custom MCP settings directory, pass `--mcp-servers-dir PATH` to
**both** `agentwiz` and `agentwiz-webspell`, or set
`AGENTWIZ_MCP_SERVERS_DIR` for the lifecycle command.

**Problems:** Ensure Docker is running, then use `agentwiz-webspell status` and
`agentwiz-webspell logs`. A failed initial webSpell setup makes the installer
report failure; fix the prerequisite and rerun the installer. The pinned
webSpell release is verified by SHA-256. To use AgentWiz from the source checkout,
run `scripts/dev_setup.sh`, then `python -m agentwiz.webspell_service install`
from the checkout root (the published installer is for release wheels).

For detailed configuration, privacy, development and troubleshooting guidance,
continue below.

A local browser UI for building and testing LLM steps, built on
[modelSpell](https://github.com/alexchwong/modelSpell).

You choose a model backend (LM Studio, OpenRouter, or any OpenAI-compatible server such as
llama-server), type an optional system prompt and an input, and watch the reasoning and the answer
stream back, followed by token usage, cost and a clear outcome.

AgentWiz 0.2.4 includes:

- **Agentic Chat:** a switch in Chat that lets the model ask AgentWiz to run one approved capability per turn.
  AgentWiz decides what may run, validates every request, asks before any call that may change state, and
  decides where data may go.
- **Built-in tools:** list, search and read staged documents, create, copy into and revise downloadable drafts,
  compare documents, read CSV tables, and fourteen local utilities (date and time, calculator, date arithmetic, find
  and replace, Markdown sections and outlines, text diff and combine, CSV parse, sort and table rendering, column
  statistics, identifier scanning and identifier-map checks).
- **Block library:** 29 ready-made model, Tool and Transform blocks, added from Workshop after reviewing each
  port's Local only label.
- **PDF:** text extraction from PDF files in a short-lived worker process, with page coverage.
- **Python blocks:** restricted Python that runs locally in scriptSpell's runner, standalone, in fixed workflows
  and from agentic Chat; **Describe it** drafts examples and source for you to review.
- **Workshop:** create, understand, save, test and compare one model, tool, Python or transform block at a time.
- **Starters grouped by type** (LLM, Tool, Python, Transform) and blank blocks, exact structured fields, prompt
  preview and a standard description for each saved revision.
- **Saved revisions:** display names, duplicate, restore, explicit import of unmanaged v3 blocks and newest-20 retention. Eligible retained
  revisions can be selected, described, tested and compared without changing the default.
- **Examples and correctness:** exact outputs, field assertions or human review, independently of contract acceptance.
- **Assistance:** create, improve, suggest examples and explain a failure, reviewed before Apply; Describe drafts a saved
  revision's standard description with its own `write_block_description` role, reviewed before Save description.
- **Contracts:** a Workshop section for reusable Text and JSON contracts with append-only revisions, a readable
  editor (advanced source optional), sample checks, Used by and reviewable model-drafted proposals.
- **Workbench:** Chat, Documents and MCP tools remain independent utilities.
- **History:** saved runs, checks, attempts and source attribution, with saving off by default.
- **Workflows:** exact captured block revisions, a palette/canvas/inspector editor and a full-form List view,
  destination trust, built-in transforms and explicit human-reviewed Release Gates.
- **Guided tutorial topics** using isolated local services, with no API keys or live model required.

Model profiles, roles and MCP servers are shared settings. Credentials stay in process memory.
Python blocks run in scriptSpell's restricted runner: a language-level restriction, not an OS sandbox. Host
Python (v3 script) blocks are retired. AgentWiz uses the pinned modelSpell 0.4.0 and scriptSpell 0.3.0 public APIs.

## AgentWiz 0.2.5

Workshop's Component library reviews portable Blocks, Workflows, Agents and inactive Teams.
Keep components in My Workspace or explicitly promote an all-Public revision to the local
Shared Pool. A standalone skill imports as a private draft. A separate reviewed OpenRouter action
can propose an agent from Cloud-eligible text. [Workshop components guide](docs/workshop-components.md)

The retained `privacy_summary_local_v1` and `privacy_summary_cloud_v1` templates display
**Unqualified**. Both are unsuitable for production private summarisation or private Cloud use.

## AgentWiz 0.2.4

Launch `agentwiz` for AgentWiz Chat, `agentwiz --open workshop` for AgentWorkshop, or
`agentwiz --open both` for both. Both listeners bind to separate loopback ports and share one
workspace and lock. The terminal prints their public addresses. Browser access is initialized by the
launcher. For API clients, use `--no-browser --session-file PATH` and send each endpoint’s scoped
`session` value in the `X-Session-Token` header. The launch owns this private file and replaces its
contents when the workspace changes.

Chat starts in Cloud with the General chat task. The composer stays below the scrolling transcript. Expand a
turn's **Activity** bar to see its tool steps. Answers support Markdown, with Copy and Download
available on completed turns. Pending approvals appear beneath the relevant prompt.

Use the top-bar model picker to choose OpenRouter, LM Studio, or llama-server and filter the models
by name or ID. Choosing a model applies to this chat. Use **Settings** to assign a named profile as
the default model for the selected mode. For a Local only run, review and trust the exact destination
in AgentWorkshop. **Chat task** adds instructions for the current turn, such as Executive summary
or Adversarial critic. **Reasoning** beside **Send** lists the supported effort levels. llama-server
uses its server default.

**Use chat context** includes up to the six most recent completed prompt-and-answer pairs, limited
to 64 KiB. Clear it for an independent request. Select any attachments or drafts for each turn.
Uploads default to Local only. A Local only turn makes the conversation private until you close it.
Download anything you want to keep before closing. To pass private material into a reviewed workflow,
upload it inside AgentWorkshop and mark that input Local only.

**History** opens or collapses the left sidebar. Cloud chats save by default after the first finished
turn. Clear **Save this Cloud chat** to opt out. Use the **⋯** menu on a saved chat to rename or
delete it. Saved transcripts retain up to 20 recent turns and activity summaries. Each new response
shows the primary server, model and reported provider, plus the turn's combined model token counts
and reported cost. Conversation totals in the sidebar survive reopening and transcript trimming.
Missing billing figures are shown as unreported or partial. Attachments, draft files, and raw tool payloads belong to the
live chat. A chat that becomes Local only leaves saved history. If an unclean shutdown interrupts a
saved turn, AgentWiz marks the saved chat unavailable because it cannot confirm the final privacy state.
Workspace settings for saved run records are independent of saved Chat history.

Built-ins and eligible bundled starters are offered by default. Chat has a separate Cloud-only
webSpell switch. The release installer automatically installs, configures and starts pinned webSpell
v0.2.1, and registers its MCP endpoint as `webspell`. Cloud Chat checks the server name
and three expected tool schemas when connecting.
Search queries and URL fetches go to the web service. Chat asks for approval before sending
free-form queries influenced by an attachment, draft, earlier answer, or tool result. An eligible
page read uses an exact URL supplied in the prompt or returned by webSpell in the current run.
The service controls are described in its own documentation. [webSpell v0.2.1](https://github.com/alexchwong/web-retrieval-mcp/releases/tag/v0.2.1)

Use **OpenRouter key** in the top bar to set or clear a session key. AgentWiz checks the session key
first, then the process environment, then `~/.config/agentwiz/.env`. Set the file owner to your user and permissions to
`0600`. It accepts a literal `OPENROUTER_API_KEY=` assignment. The file is read at startup and its value
stays in server memory. Restart to reread it. Ordinary new Cloud presets request no data collection.
A custom profile may opt into stricter policy.


## Install

AgentWiz requires Python 3.10 or later.

```bash
curl -fsSL https://github.com/alexchwong/AgentWiz/releases/latest/download/install.sh | bash
```

The script creates `.venv` in the current directory, checks the latest published release wheel against
the SHA-256 GitHub publishes and prints how to launch. Run `.venv/bin/agentwiz --version` to confirm
what was installed. To pin a published release, pass `--version` followed by its version number
(`… | bash -s -- --version 0.1.4` is an example). You can download the script first to inspect it.
For a development checkout, follow [the developer setup](DEVEL.md#environment-and-required-commands).

**Upgrading a development workspace.** Executable format-1 blocks and format-1 saved contract
files report `migration_required`. Recreate or deliberately rewrite them as executable v3 blocks
and current Text/JSON contracts. Reusable contract references need exact revision pins. For model
steps, update the reply instructions to match the [named JSON envelope](#named-outputs).

If you have a local copy of the 0.2.4 wheel, install it with:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./agentwiz-0.2.4-py3-none-any.whl
```

For a published wheel, you can pass the URL with the `#sha256=…` fragment from the release notes
to pip instead of the local filename. Both methods install the pinned modelSpell 0.4.0 and scriptSpell 0.3.0 wheels.

**JSON Schema validation is required and installed automatically** (`jsonschema>=4.26,<5`).
It brings `rpds-py`, a compiled package; a platform without a published wheel needs a Rust
toolchain to install it. If validation support becomes unavailable because the installation is
damaged or unsupported, reinstall AgentWiz before using schema-dependent actions.

## Launch

```bash
agentwiz                            # opens your browser at the printed address
agentwiz --open both                # open both apps
agentwiz --no-browser               # print the two loopback addresses
agentwiz --port 8765                # fixed port (default: any free port)
agentwiz --workspace work           # use the workspace "work" (default: default)
agentwiz --workspaces-dir ~/aw      # folder holding workspaces (default: ./workspaces)
agentwiz --profiles-dir ~/models    # shared default profiles (default: ./model_profiles)
agentwiz --mcp-servers-dir ~/mcp    # shared MCP server list (default: ./mcp_servers)
agentwiz --version                  # AgentWiz and modelSpell versions
python -m agentwiz                  # same as agentwiz
```

The three folders default to paths relative to the directory you launch AgentWiz from.

Both apps listen on 127.0.0.1. The launcher opens the selected browser surface and initializes its
session. For headless access, request a private handoff with `--session-file`. Press Ctrl-C to stop.

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

**Workshop** is the default, with **Blocks** (its compact saved-block selector is above
Design, Test and Compare), **Contracts** and **Workflows**. **Workbench** groups Chat, Documents and MCP tools. **History**
opens saved block runs, workflow runs and comparisons. **Save runs: On/Off** sits beside the workspace name and
shows its state either way. The header activity indicator reopens running work without starting it again.
Settings is a categorised dialog with the configuration summary, model profiles, **Models &
roles**, MCP servers, **Cloud keys** and **Trusted destinations**; the Workspaces control beside the workspace name opens
workspace management. The tutorial opens as a help panel at the side, over the page on narrow screens.

| Location | Address | Purpose |
| --- | --- | --- |
| Workshop: Blocks | `#workshop/<key>/<design\|test\|compare>` | Author and evaluate one block. |
| Workshop: Contracts | `#contracts` | Create, revise, check and reuse saved contracts; see which blocks use them. |
| Workshop: Workflows | `#workflows` | Design and run saved workflows with captured block revisions. |
| Workbench: Chat | `#chat` | Send a system prompt and input; see reasoning and answer stream in. |
| Workbench: Documents | `#documents` | Preview a text document and copy it. |
| Workbench: MCP tools | `#mcp-tools` | Discover and call tools, with labelled arguments or an explicit advanced editor. |
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

## Agentic Chat

Turn on **Agentic** in Workbench → Chat. Off, Chat is exactly as before. On, the panel shows, before Send:

- the model destination (kind, URL, model, provider), whether it is local and trusted for Local only data, and
  modelSpell's offline preflight. Preflight shows that modelSpell accepts the request shape; it does not show that
  a given server, model and chat template produce usable tool calls;
- the conversation's documents and drafts, each with its **Local only** switch (on by default for uploads);
- the capabilities, each with a tick and, for a saved block, its exact revision;
- items excluded for this model, with the reason. Exclusion never changes a label;
- three limits: model turns (default 8, at most 20), actions (6, at most 20) and run time (300 s, at most 1,800);
- **Remember this conversation** (on by default), **Clear memory** and **New conversation**.

During a run the panel streams the model's text and reasoning, lists each proposed action and its outcome, asks
for approval when one is needed, and ends with the answer, the stop reason and the trace. **Stop** is available
throughout. A reload reattaches to the running conversation, its run and any pending approval.

**What can be offered.** The built-in tools (eight document tools and fourteen utilities, below); MCP tools you have
classified; and saved workflow-ready (v4)
model, tool, transform and Python blocks. Every MCP tool is disabled and unknown-effect until you classify it in
Settings → MCP servers → **Agentic Chat: tool classifications**: **Enabled**, **Effects** (read-only, changes state
or unknown), **Cannot reach files on this computer** (your statement; AgentWiz cannot verify it, and a tool
without it is never offered, directly or inside a Tool block) and **Results Local only** (on by default). A tool
appears there once its list has been loaded. Saved workflows, Release Gates and v3 host-script blocks are never
offered.

**Approval.** Built-in tools, read-only MCP tools and model, transform and Python blocks need none. Every call to
an MCP tool classified as changing state or unknown asks first, showing exactly the arguments the server will
receive (for a Tool block, after its template is filled). A denial is final for that call and counts as an
action; an approval is single-use and expires after 120 seconds or at the run's deadline.

**Documents and drafts.** Choose a `.txt`, `.md`, `.csv`, `.tsv`, `.json`, `.log`, `.yaml`, `.yml` or `.pdf` file in
the panel: it is read as on the Documents tab and staged under `<workspace>/agent_documents/`. A PDF shows its
coverage (pages read, pages with text, and any page without a text layer). The model reaches a document only by its
ID, through search (literal, case-insensitive, in document order) and read (by the document's own line numbers; long
lines by column). **Create draft** writes a new Markdown or text draft that you can download. **Copy to draft**
copies a line range of a document or draft into a new draft (or appends it to one), **Revise draft** applies exact
find-and-replace edits to a draft as a new draft, **Compare documents** gives a line diff of two items, and **Read
table** reads a staged CSV or TSV by rows. None of them ever changes a document; reading restricted material for a
draft restricts the draft. Up to 20 documents and drafts (10 drafts) and 64 MiB per conversation. Staged files are removed when the
conversation closes, the workspace changes and AgentWiz exits.

**Privacy.** A document marked Local only, and anything derived from it, is restricted. For a model that is not
a trusted local destination, restricted documents, MCP tools with Local only results and blocks with protected
design or Local only outputs are excluded before the run. A run that reads restricted material becomes
restricted for the rest of the run; if the model is not a trusted local destination, the result is withheld and
the run ends. Tightening a label clears the conversation's memory. Your typed message is never labelled, and
AgentWiz never copies protected material into it.

**Memory.** Each answered run adds its question, answer and the drafts it created; the next run sends them.
Earlier tool calls are never replayed. At most 20 entries; when full, start a new conversation or clear memory.
Memory is kept in process memory only and is lost on restart. After a restricted run on a trusted local model,
sending to any other model is refused until you turn memory off, clear it or start a new conversation.

**Records.** With **Save runs** on, each run saves one record: destination, settings, limits, your message and the
final answer, and one action trace with each action's identity, approval, outcome, byte sizes and digests. It
never holds tool arguments, tool results, excerpts or draft contents. A restricted run is marked Local only in
History.

## Block Workshop

Choose **New block**, a starter or blank block and a stable block key. Set its display name,
purpose, inputs, behaviour and outputs in Design. A model role resolves to a profile; a tool binds to a
server and discovered tool. Insert input controls use the existing `{{inputs.name}}` template syntax.
The block key is an internal identifier, not a display title: use 1–64 ASCII letters, digits, dots,
underscores or hyphens, starting with a letter or digit (for example, `summarise_notes`); no spaces.
**Start with** chooses an initial template, not an output prefix. Model roles are aliases assigned to
saved profiles in Settings → Roles, not chat-message roles. The dialog lists defined roles and marks
unresolved assignments. Fresh workspaces define unconfigured `fast`, `assistant` and `reasoning` roles,
plus the dedicated `write_contract` and `write_block_description` roles;
drafts can be designed before profiles are assigned. Use **Configure roles** before running.
Assistance separately selects an assistant role.
Only fields relevant to the selected block type are shown. Tool blocks list configured MCP servers and
automatically discover the selected server's advertised tool names in a dropdown. **Refresh tools**
retries discovery after configuration or connection changes. AgentWiz does not scan for unconfigured
servers: add their URLs in Settings → MCP servers first. Discovery lists tools without executing them.
Model and tool starters ask for **Port labels**: **Local only**, **No local-only label** or **Decide per
port**. The choice creates a workflow-ready (v4) block; with Decide per port, saving waits until each port has
a label. A label declares only that port; restrictions can still come from connected inputs, examples or
protected design. **Start with** groups starters by type: **LLM** (the five model starters and Blank), **Tool**
(Call an MCP tool, Built-in utility, Blank), **Python** (Describe it, Blank) and **Transform** (Count text, Select or
rename fields, Format report, Restore placeholders). Every block they create is workflow-ready. A Tool block shows its
binding as **Built-in** (one of the fourteen utilities listed under [Block library](#block-library): local,
read-only, no approval) or the MCP server name. **Add from library** adds ready-made blocks; see below.
Creation errors keep the dialog and entered values available for correction. **Create draft** opens
Design without calling a model; **Create with assistance** first creates the same draft, then opens the
separate assistance request/review workflow. Neither button saves a revision automatically.
Design reads in editing order. A bar above the editor holds the display name, **Block revision**, the unsaved
state, the optional revision name and **Save as new revision**; it stays in view while you scroll. Below it, a
model block's ports are on the left, **System prompt**, **User prompt** and block description in the centre, and
role, destination, sampling, protection and documentation on the right. Medium widths put ports above prompts
beside the settings; narrow widths stack all three in the same order. Insert buttons add
`{{inputs.name}}` and `{{output.name}}` placeholders (see [Named outputs](#named-outputs)) and follow port
renames, additions and removals at once. A prompt reference to a port the block no longer declares is flagged
under the prompt; when exactly one port could replace it, **Replace with …?** offers that change for
confirmation. Each input and
output is an editable row with field name and **Declared meaning** (part of the executable block; an output's
declared meaning can contribute to the model request), and its contract summary below it, such as `JSON · list of {placeholder, original}`, with a sentence stating
required, nullable and extra-field rules. **Edit contract** opens the contract in one drawer at the side:
**Inline** uses the same readable contract editor as Workshop → Contracts; **Saved contract** pins one numbered
revision (see [Contracts](#contracts)). **List of records…**, **Single record…** and **Choice…** fill in a common
shape for review. Guided edits reach the block draft at once; **Done** closes the drawer, and the block still
needs saving. The drawer stays open while the block re-renders. A type change that would drop fields, choices or bounds previews the before and
after definitions first; contract edits are unsaved block edits until **Save as new revision**.
**Request preview** shows the exact system and user messages a run would send for the current inputs,
with the generated output section and where each setting came from, each with Copy. On the right,
**Model role** selects a configured role without silently replacing an unavailable saved binding.
Temperature and Reasoning level sit side by side. An empty Temperature inherits the selected role's
default, shown in grey; zero is an explicit value. Reasoning's first option shows the role's value
followed by **(default)** and inherits it; selecting a level explicitly overrides it. If no role default
is declared, the backend default applies and no numeric value is guessed.

**Model advanced settings** holds explicit backend-default bypasses for both settings, the output cap,
budgets and source editors. **Additional repairs** is always visible below Outputs; zero allows no
repair attempts after the initial response. The optional revision name sits above the revision selector and save controls;
the **Block revision** selector sits above the save buttons in Design and above the editor in Test and Compare. **Assist** and **Revision actions** share
the Design/Test/Compare action row in all three modes. Test's **Preview
instructions** shows the actual filled prompt or arguments for the supplied inputs.

Structured fields distinguish missing, null, false, zero and empty text. Numeric values travel as exact
source text. Saved examples and their expected values use the same typed forms as Test. Editing an example
also offers inputs it lacks, and keeps expected values for outputs the block no longer declares until you
remove them. A new group needs at least one field before **Save as new revision** or a run: editing may pass through
an empty group, and a group that was already empty in the saved block is left alone. A definition outside
the supported vocabulary is never projected or overwritten: it stays in the advanced source with its
located problems, and neither the port nor the block can be saved until it is corrected or discarded.
**Make local copy** replaces a pin with the pinned definition for a block-specific edit; **Save inline as
reusable** creates a new saved contract in one dialog and, unless you clear **Use this saved contract for port**,
pins the port to it. The block still needs saving.
Shared edits happen in Workshop → Contracts, which shows the blocks that use each contract. Schema-backed
validation and assistance use the validator included in the standard installation; repair the
installation if it becomes unavailable.

**Save as new revision**, in Design's top bar, previews the effective action and its required
acknowledgements. A new revision becomes the default. **Block revision** opens any retained revision as a
clean saved selection: its design, contracts and standard description are shown, Test and Compare run that
revision, and the default (marked **(current)**) does not change. Unsaved changes offer Save, Discard or
Stay before switching. Editing a selection makes it an unsaved draft, labelled with its base revision and
**Unsaved**, which runs through the draft path. On a clean older selection **Save as new revision** stays disabled
until you edit it or choose **Revision actions → Restore to draft**; saving then publishes it as the new
default. Results and the prepared preview name the revision they ran. A retained revision runs with today's
model roles, MCP settings and exact saved-contract pins: a missing or changed pinned contract blocks the run
with a located error but still lets you view and describe the revision's captured interface. Older-format
revisions are listed and show **Migration required** on selection. Revisions display per-block numbers starting at 1 and an
optional name of up to 120 characters. Enter a name before saving; names need not be unique and do not
rename the block. A new revision's name defaults to blank.

**Overwrite selected revision** is in **Model advanced settings** (or **More settings**). It confirms replacement of the selected revision's contents (and clears its standard description),
keeping its number and position rather than adding another revision. Overwriting an older revision does
not change the current default. The replacement remains selected; existing run evidence is unchanged.
Overwrite is unavailable until the block has a saved revision, for workflow-ready (v4) drafts and targets,
and for revisions with protected design. Those revisions can carry Local-only input labels or protected design
history that Test still honours; replacing one would erase that record, so save a new revision instead. To
retire an earlier revision's label deliberately, use **Review retained privacy labels**.
Concurrent changes require a fresh review.
Numbers survive restart and pruning; restoring an older version and saving creates the next number.
Duplicates start their own sequence. Existing unnumbered histories are numbered oldest first from the
retained history available at upgrade, not from already-pruned lifetime history. Internal IDs remain unchanged.
The first confirmed Workshop Save of a valid unmanaged v3 block captures its original definition
as revision 1 and the edited definition as revision 2 under a managed v2 head. Only the newest
20 revisions are retained.
The Save plan identifies revisions
being pruned. Duplicate keeps the original key intact and offers explicit copying of examples. Restore to
draft loads a retained definition into a draft; a deliberate Save creates a new revision, without rerunning it.
Renaming the display name also requires Save; the stable key remains in Details.

**Python blocks.** A Python block's `scripts/main.py` defines `transform(inputs)` and returns a dictionary with
exactly the declared outputs. It runs in scriptSpell's restricted runner (no files, network, environment or imports
outside its profile; 64 KiB of source, 1 MiB of inputs and of outputs, 1 s of execution, 5 s in all). It is a
language-level restriction, not an OS sandbox. JSON numbers arrive as `int` (whole numbers written without a decimal
point) or `Decimal`; a script that returns a float fails with `float_output`, so divide with `Decimal`.
**Describe it** asks the selected role for three examples (shown as fields; **Edit as YAML** changes them, checked
locally with no model call), then **Generate and check** asks for the source and runs every example exactly as the
saved block will. **Apply** puts the source and the examples, unconfirmed, into the draft; Test and Save stay
separate. The flow sends at most three requests. A block saved with a scriptSpell profile or codec that differs
from the installed one is unusable (`engine_changed`) until it is tested and saved again. v3 host-script blocks load
as unusable (`host_script_retired`).

For a workflow-ready block, the **Protection** card counts port labels, shows **Design protection** (off, set
here, or inherited from protected assistance and not clearable) and notes that run restrictions also depend on
connections, examples and earlier revisions. **Change design protection…** explains the setting before
changing it. Test treats an input as restricted if any retained revision labelled it Local only.
**Review retained privacy labels** lists those revisions and inputs; **Retire these labels** records an
acknowledgement of those exact revision/input pairs, after which Test ignores them. Saved examples keep their
restrictions and captured values stay restricted. **Restore all labels** removes the acknowledgement. A later
revision that labels the input again restricts it again.

Test places inputs on the left and results on the right. **Check readiness** shows where the run connects and
why it is restricted (declared labels, example restrictions, retained labels, design protection). A refusal is
explained in place with its remedy: **Grant** for an eligible destination not yet trusted, the address or proxy
fix for an ineligible one, **Edit port**, or **Review retained privacy labels**. A refused **Run test** shows the
same. The **Examples** section can be collapsed and remembers its state.
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
can compare exact named outputs, assert explicit field paths, run **Text checks** on Text outputs (includes or
excludes literal values, optionally ignoring case, or *No placeholder tokens left*) or ask for human review. Text
check values are stored in plain text with the example and are never sent to a model. Verify an expectation
before using it for automatic correctness. Editing an example invalidates prior verification. An accepted,
well-typed result may still be incorrect. Human ratings bind the exact result and never transfer to a rerun.

Compare selects a saved baseline and either a saved candidate, the selected revision or the current unsaved
draft, using the same ordered examples. The setup collapses after Start; **Stop**, the run state, both arms'
identities and the example being read stay visible above the results. Each example shows baseline and candidate
side by side (stacked on narrow screens), with **Previous example** and **Next example**. A shared example that does not fit one revision's inputs is named,
with its missing or extra input names; run such revisions individually. Review source differences, destinations, trial/repair bounds, effects and saving before Start.
Trials alternate arm order between examples. Results show execution, checks and correctness separately,
with paired answers, trace, denominators, reported usage and timing coverage. Unknown usage is not zero;
with no correct result, time per correct result is undefined. Backend internals and cold starts can vary.

Save runs is off by default and is captured once at Start. Saving off retains active/latest session evidence
until process restart. Saving on writes comparison evidence and child histories; these can be opened from
Recent runs and comparisons and deleted independently. Stop cancels the current child and remaining trials.
Reload reattaches to retained work by ID without dispatching it again. Real tool calls may change
external state, and an unknown tool outcome is never automatically retried.

Assist offers four independent draft actions. Choose the action, role, task, stage or Improve sections, then review
the destination, settings and selected context before sending. Assist's **describe** is a shortcut to
**Describe block** (below) and ignores the role, task and settings in this dialog. Apply changes only the matching draft or
unsaved example suggestions. Discard sends nothing further. Generated custom Python is displayed for source
acknowledgement. A stale or expired proposal requires a deliberate new request. Assistance never publishes
a block or executes its proposal automatically.

### Standard descriptions

Each saved revision has its own standard description: six short sections (use, when not to use, effects,
limitations, missing data, failures) and optional per-port **Documentation**. Design's **Standard
description** panel shows it for the selected revision, with the purpose, each port's exact name and format,
the configured role or server, declared effects and any dependency problems. A port without documentation
shows its declared meaning. Documentation is for readers only: it never changes what the block sends or
checks, and describing a revision never creates, changes or reruns a revision.

**Describe block** asks the `write_block_description` role (unassigned by default; assign it in Settings →
Roles) for a description of the selected saved revision. It first shows the role, backend, model and whether
the endpoint is on this computer; a remote endpoint carries a warning that source excerpts or feedback may be
confidential. The request contains the revision's interface and up to 4,000 characters of its active prompt,
main script or tool binding (argument names and input paths; literal values omitted). Support files,
examples, inputs, history and known credentials are not sent; other confidential text in the source can be.
It never runs the block, calls a tool or saves run evidence, even with Save runs on. **Stop** cancels the call.
Review the proposal, then **Save description**, **Refine** (one feedback note and one further model call;
requests to change behaviour are declined) or **Discard**. **Edit description** writes it by hand without a
model. Saving changes only that revision's description, never the executable block, the default or your
inputs and examples. Both are disabled while the executable form has unsaved changes, and for a block that
has not been saved yet.

A new revision starts without a saved description, even when restored from an older one. Revisions saved
before this release show their stored prose as **Existing description** until you save a new one. A
description that changed elsewhere since you opened it is refused rather than overwritten; reload and
review it again. An unreadable, linked or newer-format sidecar shows **Description unavailable**
and is protected from writes. An invalid or mismatched v1 sidecar can be replaced if AgentWiz
can read its freshness token. The sidecar is independent of execution. Pruning a revision also
removes its description.

## Block library

**Add from library** in Workshop lists 29 ready-made blocks in three groups. Each is an ordinary block of yours
once added: it has revisions, examples and a standard description, and you can edit, test, compare and wire it like
any other. A library block never updates itself; a newer library version is added under a new key.

- **Model blocks** (role `fast` by default): Summarise a document, Extract action items, Extract entities, Classify a
  text, Answer from a source (with verbatim quotes, or "Not found in the source."), Rewrite for an audience,
  Proofread, Translate a text, Draft a reply, Minutes from a transcript, Compare documents by
  meaning, Check against a checklist and Table from text.
- **Tool blocks** (built-in utilities: local, read-only, no model): Find and replace, Extract a Markdown section,
  Document outline, Compare two texts, Combine two texts, CSV to rows, Rows to Markdown table, Sort and remove
  duplicates, Column statistics, Scan for identifiers, Check an identifier map, Current date and time, Calculate, Add
  to a date and Days between dates.
- **Transform blocks**: Restore identifiers.

**Adding.** Tick entries, optionally rename them (**Add as**), and for a model block choose its role. **Review**
shows each port's Local only label, the role and the destination that role resolves to now; **Add** requires that
you have reviewed the labels. If a role, a profile's endpoint or a provider pin changes after the review, Add asks
again and writes nothing. A key already in use is refused, case-insensitively. Each entry's status is one of
**Available**, **Installed**, **Key in use** or **Incomplete**; an addition interrupted after the block was saved
offers **Finish adding**, which writes the missing examples and description unless you have edited the block since,
in which case it reports what was written. Examples or a description you save yourself meanwhile are kept as you
wrote them. Saving an added block yourself makes it an ordinary revision without the library record.

**The utilities** behind the Tool blocks are also built-in tools in agentic Chat and Tool-step bindings:
`current_datetime`, `calculate`, `date_arithmetic`, `find_replace`, `extract_section`, `outline`, `compare_texts`,
`combine_texts`, `parse_csv`, `rows_to_table`, `sort_dedupe_rows`, `column_stats`, `scan_identifiers` and
`check_identifier_map`. Each takes at most 1 MiB of arguments, works out its output size before producing it, and
refuses with `too_large` rather than truncating; only `outline` and `scan_identifiers` stop at a cap and say so.
Table values stay verbatim strings, so IDs such as `007` keep their leading zeros.

**Wiring examples.** A CSV to a sorted Markdown table: `csv_to_rows` → `sort_and_dedupe_rows` → `rows_to_table`,
binding each node's `columns` and `rows` by path from the previous node's `out`, and `sort.by` to a constant such
as `["Order ID"]`:

```json
"sort.columns": {"kind": "node", "node": "parse", "port": "out", "path": ["columns"]},
"sort.rows":    {"kind": "node", "node": "parse", "port": "out", "path": ["rows"]},
"sort.by":      {"kind": "constant", "name": "sort_keys"},
"table.columns":{"kind": "node", "node": "sort",  "port": "out", "path": ["columns"]},
"table.rows":   {"kind": "node", "node": "sort",  "port": "out", "path": ["rows"]}
```

Model rows to a table: bind `table_from_text.rows` to `rows_to_table.rows`, and bind the same constant or input that
fed `table_from_text.columns` to `rows_to_table.columns`.

**De-identification.** Your local de-identification block → `check_identifier_map` → a Release Gate →
the remote step → `restore_identifiers`. The map check runs locally before the Gate: a malformed placeholder, one
placeholder for two originals, one original under two placeholders, or a placeholder that collides with a token
already in the source fails the workflow before anything is sent. Tokens the remote reply adds or keeps that the map
does not know stay as they are and are listed. The Gate review is still yours: neither the model nor the scan can
show that no identifier was missed.

**Quality.** You can use library blocks from Workshop, workflows and Agentic Chat. For 0.2.2, the
shipped model blocks passed live qualification and Agentic Chat passed its live evaluation with
`openai/gpt-4.1-mini` on OpenRouter. Qualification results apply to the tested models and profiles.

## Workflows

In **Workshop → Workflows**, create a named workflow, add inputs and outputs, then add
nodes from exact v4 block revisions. **Design** combines a searchable palette, canvas and
inspector. Connect compatible ports by dragging, tapping or keyboard; use **List view** for
the whole binding form over the same draft. In List view each bound input and Output source has
**Insert Release Gate** and a **Restriction** disclosure explaining why its value is restricted; an
**Add Release Gate** remedy opens the connection a finding names, or asks you to choose when several qualify. A binding can select an object field or an array
index. **Undo** and **Redo** cover graph edits until Save. Validation explains what stays
local and the expected destinations; it does not replace Run's readiness check. Create a
block or Restore placeholders from Design and return without discarding the unsaved graph.

Save before running. In **Run**, the left column reads inputs or saved examples → **Check readiness** → findings
→ **Run**. Each finding names its location and offers a remedy for the object that owns it: **Grant** for an
eligible untrusted destination, the address or proxy fix for an ineligible one, **Add Release Gate** when a
restricted value would reach a destination that cannot take it, **Edit in workflow** for workflow inputs and
outputs, or **Open source revision** for a captured block. From that revision, **Return to workflow** opens the
node's explicit Rebind review; nothing is rebound or cleared automatically. Privacy warnings such as an
unprotected upstream call stay visible; routine notices fold. While running, **Stop**, the current phase
(including Assessing release, Awaiting your review and Submitting decision) and saving state stay at the top of
the left column. Outputs appear above progress, with a restoration summary where applicable.
Progress lists nodes in execution order; **Inspect** opens a node's trace beside it.
Rebind explicitly to update a node's captured revision.
A saved capture remains usable if its original block revision is removed.

Open **Settings → Trusted destinations** to grant or revoke destination trust and choose whether
MCP requires confirmation on every run or only on batches. The table lists destinations from configured
roles, discovered MCP tools, blocks' current default revisions and saved workflows, with where each connects,
local-only eligibility, trust and known uses. A banner lists destinations that **saved workflows** need for
local-only data but are not trusted, for example after a profile's model changed; it does not inspect the
workflow you are editing. If some saved workflows could not be checked, the banner says the list may be incomplete.
Findings explain themselves in plain language; the code stays in Details. A workflow without any labels is treated
according to its labels: it is not checked for private content, and no warning does not mean the data is not private.
**Grant** appears only where a grant can help; manual entry is folded and checks eligibility as you type.
Saved workflow runs appear in **History** with block runs and comparisons. The tutorial walks through a two-branch report
and a protected model stage on an isolated local stand-in.

### Release Gates

Choose **Add Release Gate** in a workflow, or **Insert Release Gate** on a connection.
Insertion preserves the connection through the new Gate; an identifier-map suggestion is never bound automatically.
Bind `value` to the redacted text or JSON you
want to release. Optionally bind `scan` to a local-only list of identifier originals
(strings, or objects with `original` and optional `placeholder`). The Gate has an explicit
contract for the selected value. Connect its `released` output to the next stage.

The raw document, mapping and upstream outputs remain local-only. Ordinary nodes still
inherit restriction from any local-only input. Only the Gate's separately identified output
can cross that boundary, after you review and release its actual value. Setting a downstream
port Local only restricts that output again. Sending a restricted mapping to OpenRouter is
still refused. A later local reinjection stage reading the mapping produces restricted outputs.

Enable **Local AI assessment** to request advisory findings from a separately assigned role.
Set its assignment in Models & roles and grant its exact local destination in Trusted destinations.
Assessment always uses protected local dispatch, including for otherwise unrestricted candidates;
it never falls back to a cloud model. Its findings and mapping are never added to the released value.
Assessment attempts, including one permitted repair, consume normal model/time budgets.

During a run, inspect the entire candidate and downstream destinations. Acknowledge identifier
matches and any AI concerns/unavailability, then choose **Review and release**, **Reject** or
**Stop**. The server enforces acknowledgements. A failed assessment is visibly unavailable,
not a clean result; budget exhaustion and changed destinations stop execution. Literal scanning
and AI findings can miss identifiers and do not certify anonymity.

**Restore placeholders** is a built-in transform that puts originals back after processing de-identified text.
Bind `text` to the processed text and `pairs` to the local-only mapping (a list of
`{placeholder, original}` records whose placeholders look like `[PERSON_1]`). Each placeholder token is replaced
once; inserted text is never rescanned. A token with no mapping fails the step unless **If a placeholder has no
mapping** is set to **Leave it in place**; mapped placeholders the text no longer contains are listed in the trace.
A mapping with a string entry, an invalid placeholder or one placeholder mapped to two originals is refused, even
though the Release Gate's scan accepts such a mapping. The restore step's ports are Local only.

The tutorial's two private-summary topics show this route end to end with synthetic data: all-local
(redact → summarise → restore) and with a Release Gate before a cloud stand-in. Passing their checks says nothing
about real documents, and Gate approval covers the reviewed value at the destinations shown, not forwarding inside a
provider.

Each Gate execution needs a separate decision, including identical batch examples. Reject,
Stop or expiry ends the remaining batch. Human review defaults to 30 minutes and is excluded
from the execution budget. The run holds the shared Workshop slot while waiting. Reopening
the Workflows tab after a browser reload recovers the active review. Restarting AgentWiz
invalidates it; start a new run. There is no automatic approval or execution resume.

Review covers downstream processing of this value at the displayed destinations, including
indirect consumers through transforms. It does not show each eventual HTTP request or a
provider's internal forwarding. Save runs remains opt-in. Gate workflows use bundle format 2;
workflows without Gates stay at format 1. Workshop single-block Test/Compare do not run Gates.

## Workflow-ready blocks

New model and tool blocks are workflow-ready when created with a Port labels choice. For an existing v3 model
or MCP block, the banner's **Upgrade for workflows** creates a v4 draft.
Choose a locality label for each port and save the draft as a new revision.
New count, field-selection, report and restore-placeholders starters are built-in transforms.
**Convert to built-in transform** creates a fresh v4 draft from an intact historical starter.

**Protect design context** applies to the block's design and assets. Port labels apply to the
values they receive or produce. AgentWiz requires an exact workspace trust grant for protected
model and MCP sends to literal loopback addresses such as `127.0.0.1` or `[::1]`.
If your environment proxies HTTP requests, add the exact loopback host to its proxy bypass
list. On macOS, check the bypass entries in System Settings as well. A receiving local
service may relay data elsewhere, so grant trust only to services you control.

Manually authored standalone Python runs with your filesystem and network access.
AgentWiz refuses attributable generated Python, including descendants edited in Workshop.

## Settings

Model-server connections are configured in `./config/model-servers.ini`, relative to the launch directory.
The installer or first launch creates it from the supplied template if absent; it never replaces an existing
file. Edit the URLs and, if needed, local-server API keys there, then restart AgentWiz. Use `--config PATH`
to select another file. Protect this file if it contains local-server credentials. Do not put an OpenRouter
key in it.

Open **Settings → Cloud keys** to enter a write-only OpenRouter session key. A session key
takes precedence over `OPENROUTER_API_KEY`; clearing it exposes that
environment fallback if present. Neither is saved by AgentWiz.

Open **Settings → Model settings** to choose the model:

| Setting | Notes |
| --- | --- |
| Model source | LM Studio, OpenRouter or OpenAI-compatible. |
| Model ID | Type it, or use **Load models**. |
| Routing / Provider | OpenRouter only. Automatic uses modelSpell to probe and select an eligible provider (Balanced, Cheapest or Fastest). Manual pins the selected slug without substitution. OpenRouter-managed is available for text requests only. Automatic selection can make up to four additional billable probes; probe usage is recorded as unreported, not zero. |
| Privacy | Account default retains account restrictions. Require zero data retention or deny data collection to add explicit requirements to every probe and completion; routing never relaxes them. |

Reasoning and max tokens are set in each request panel (see **Task settings**).

**Apply** uses the dialog's settings for this AgentWiz process.

The header shows the workspace (hover for its folder). Settings shows the active profile, current settings and
where the key comes from: `key: session`, `key: env OPENROUTER_API_KEY`, `key: config`, or `key: none`.

### Profiles

A profile stores model source, model ID, provider routing and privacy requirements under a name. It never stores a key or server URL.
Older profile URLs are no longer used: transfer any required endpoint manually to the configuration file.

- **Profile files are format version 4.** Older profiles load, but their server URLs are ignored and
  removed on the next write. Earlier AgentWiz releases cannot read version 4 files. Existing pins remain Manual; empty providers remain OpenRouter-managed. Profiles saved by 0.1.1 load as they are, but
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

**Attach file…** on the **Documents** tab reads `.txt`, `.md`, `.csv`, `.tsv`, `.json`, `.log`, `.yaml`, `.yml` and
`.pdf` files, chosen by the extension in any letter case. Text files are read as below; PDF is described at the end
of this section. Files must be UTF-8 (with or without a byte-order mark) or UTF-16 with
a byte-order mark; anything else, including Latin-1 and UTF-32, is refused with a message asking for
a UTF-8 copy. Line endings become `\n`. The file is kept as it is: CSV is not turned into a table
and JSON is not reformatted.
Copied text carries no privacy label: to use private document text in a block, paste it into a **Local only**
input of a block whose role resolves to a trusted local destination.

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
- **PDF.** A `.pdf` is read on both routes (Documents and agentic Chat) by a short-lived worker process
  (`python -I -m agentwiz.pdf_worker`) using pypdfium2. Text comes page by page in PDFium's order, under
  `--- page N ---` markers, without structure: tables read as runs of text, and scanned pages are reported and
  skipped (no OCR). Limits: 16 MiB per file, 300 pages, 16 MiB of text and 30 seconds. At most two PDFs are
  read at once (one on macOS), four more may wait up to 10 seconds, and further uploads are refused as busy. The
  preview reports coverage: pages read, pages with text, and warnings such as a page without a text layer. A PDF
  with no text at all, an encrypted PDF and a damaged one are refused with a reason.
- **The worker boundary.** A PDFium crash or hang costs one upload, not AgentWiz and the keys and conversation it
  holds. On Linux and WSL2 the worker also runs under a 256 MiB address-space limit: headroom for a valid PDF at
  the limits above, while a runaway allocation still fails and costs one upload. **The macOS worker has no
  address-space limit.** It runs one PDF at a time with the same 30-second deadline. The
  worker is not a sandbox on any platform: it runs with your user's permissions. It receives no credentials (its
  environment is a fixed minimal one).
- The text formats are `.txt`, `.md`, `.csv`, `.tsv`, `.json`, `.log`, `.yaml` and `.yml`. Word and other formats
  need docSpell, which arrives in a later release.

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
  `[tool webspell.web_search_results arguments={"query":"lighthouse"}]`. When a result has both text and structured
  content, **Copy structured content** copies the structured part instead of the text preview. The
  provenance line is copied with the text. Images and other items are listed, not copied.
- Tool results and documents may contain instructions aimed at the model. AgentWiz shows them
  and never sends them by itself; they reach a model only if you paste them into a request. It does
  not sanitise them.

### Example: web search with managed webSpell

1. On the **MCP tools** tab choose `webspell` and press **Load tools**. The installer registers
   the service at `http://127.0.0.1:8765/mcp` by default; no duplicate entry is needed.
2. Choose `web_search_results`, enter a query and press **Run**. Optionally filter by category
   (`general`, `news`, `science` or `it`) and a best-effort recency window such as `7 days`.
3. Use `web_evidence_passages` for passages, or `web_page_text` to read one known URL. When
   supplying URLs directly to `web_evidence_passages`, omit category, recency and domains.
4. To use a result in Chat, review it first, then press **Copy**, paste it into Chat, and add your question.

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

**Editing contracts (Workshop → Contracts).** The left column searches names and descriptions, filters
by family and starts **New contract** (a blank Text draft in family General). The right column edits one
contract: **Format** is Text, Structured JSON or Any JSON. Text has optional length limits, exact text and
repeatable required/forbidden literals (a blank box means the rule does not run). Structured JSON is an
indented field table — Name, Type, Description, Required, Allow null — where **More** shows a field's
bounds, choices, unknown-property policy and Add/Remove property. New groups reject unknown properties;
new properties are required and not nullable. Any JSON is labelled as having no field validation. Missing
descriptions are warnings only. **Advanced source** (collapsed) accepts exact source through the same
rules; invalid source is kept with its problems and cannot be saved. Every edit is applied by the server
to the exact definition, so long integers and decimals keep every digit.
**Revision** views any saved revision; editing it drafts an append, and **Restore as new revision** copies
it as the next revision. **Save** first shows the name, the new revision number, the format and the
changes from the latest revision; an unchanged draft writes nothing. A save based on a file changed in
another tab is refused and the draft is kept: **Refresh from disk**, review and save again. If saving
reports that durability could not be confirmed, do not save again; refresh to confirm. **Save grouping**
changes only the family; **Duplicate** starts a new contract at revision 1. **Delete** lists the blocks
that use the contract and is refused while any do, or while a block could not be inspected. **Check
sample** checks pasted text (literal text for Text, an answer for JSON) without a model call; a pass is a
structure pass, not a correctness check. Leaving an unsaved draft offers Save, Discard or Stay.
**Synthesize** (Structured or Any JSON only) shows the destination of the `write_contract` role before
sending; the proposal appears beside its assumptions, **Apply draft** only replaces the editor's draft,
and **Save** is still a separate step. Model-call failures show the sanitized error in the synthesis pane,
including missing credentials, authentication failures and connection errors, rather than only a failed-proposal message.
Read-only workspaces allow viewing, checking and copying only.

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

## Blocks

A block lives directly under `<workspace>/blocks/<key>/`. An unmanaged block has an executable
v3 `block.json` with a matching `name`. A Workshop-managed root has a v2 `block.json` that points
to its saved generations. Edit managed blocks through Workshop so the captured assets stay intact.
Run a saved block from Workshop (**Test** and **Compare**) or through the headless block API
(`/api/blocks/run`). For a run started through the API, use the headless API to poll or stop it.
If saving was on, its saved record opens in History. An unedited block is tested at
the revision Workshop showed: if its files, or a contract revision it pins, changed on disk since, Test
is refused `stale` with nothing sent, your inputs are kept, and **Reload** reopens it. Workshop shows
why a hand-written block cannot run (its file and the first 20 located issues) and keeps Test disabled;
a Python block whose only problem is a changed scriptSpell engine offers **Open as draft**, which writes
the installed engine into an edited draft and keeps the original as the first saved revision on Save.

The table describes the executable v3 definition. Unknown keys are refused with their locations.

| Key | Meaning |
| --- | --- |
| `format_version` | `3`. Version 1 blocks are refused with `migration_required`; a greater number means a newer AgentWiz. |
| `name` | The folder name (1 to 64 ASCII letters, digits, `.`, `_`, `-`, starting with a letter or digit). |
| `description` | Optional text shown in the list. |
| `inputs` | Object: input name to `{"contract": ..., "description": ...}`. The contract is an inline definition or an exact saved-contract pin `{"name", "revision", "digest"}`. Names follow `[A-Za-z_][A-Za-z0-9_]{0,63}`. May be empty. |
| `outputs` | Same shape, at least one. |
| `steps` | A list of **exactly one** step. |
| `budget` | `{"max_seconds": 1..86400 (default 1800), "max_model_tokens": integer >= 1 or absent}`. |

Save this complete example as `<workspace>/blocks/count_identifiers/block.json` and assign the
`fast` role before running it:

```json
{
  "format_version": 3,
  "name": "count_identifiers",
  "description": "Count synthetic identifier mentions",
  "inputs": {
    "source": {"contract": {"format": "text", "min_length": 1}}
  },
  "outputs": {
    "summary": {
      "description": "Short tally",
      "contract": {"format": "text", "min_length": 1}
    },
    "count": {
      "description": "Number of identifier mentions, including repeats",
      "contract": {"format": "json", "schema": {"type": "integer", "minimum": 0}}
    }
  },
  "steps": [{
    "id": "extract",
    "kind": "model",
    "role": "fast",
    "response_mode": "named_json",
    "prompt": {"text": "Count each mention of a token starting with ID- in the source below. Include a short tally and the total number of mentions.\n\nSource:\n{{inputs.source}}"}
  }]
}
```

Use `ID-ALPHA, ID-BETA, ID-ALPHA` as the plain-text Test input. One passing reply is
`{"summary":"Three mentions","count":3}`. A quoted count, missing key or extra key fails. A count
of 2 is structurally valid. An example expectation on `count` can test correctness separately.

Model steps declare one or more outputs. The optional `system` and required `prompt` each use
`{"file":"prompts/…"}` or `{"text":"…"}`. The optional `task` uses request-panel settings.

For a tool block, replace the `inputs`, `outputs` and `steps` fields with the following fields,
and use a block name matching its folder. This example requires a configured MCP server
named `docs` that advertises a `lookup` tool accepting `id` and `label`:

```json
{
  "inputs": {"id": {"contract": {"format": "text"}}},
  "outputs": {"result": {"contract": {"format": "json"}}},
  "steps": [{
    "id": "lookup", "kind": "tool", "server": "docs", "tool": "lookup",
    "arguments": {"id": "{{inputs.id}}", "label": "n={{inputs.id}}"}, "output": "result"
  }]
}
```

The tool step's `output` binds its one declared output. A host script step (`"kind": "script"`)
is retired: a v3 block that uses one loads as unusable (`host_script_retired`). Use a Python step
(block format 4) instead; see [Python steps](#python-steps).

- **File references** are relative paths inside the block folder (`prompts/` for prompts, `scripts/`
  and ending in `.py` for Python-step sources). A symbolic link, a missing file or an oversized one (4 MiB; 1 MiB
  for `block.json`) is a problem. Only referenced files matter; helper files beside them are ignored.
- **Revisions.** Each block has a revision that changes when its files change, or when a revision it
  pins changes, disappears or becomes unusable (a newer appended revision does not count). A run, from
  Workshop or the headless API, uses only the revision that was shown; a changed block is refused
  `stale` and nothing is sent.
- **Attempts.** Each finished Workshop trial has an **Attempts** list: every attempt's outcome, check
  and repair reason, read only when you open it. An attempt no longer held in memory reads "details no
  longer held".
- **History.** Saved block runs appear in the History tab with `run.json`, each attempt and
  `result.json`. Checked request runs saved by earlier versions still open there.
- **Activity indicator.** While a Workshop Test or Compare runs, the header indicator reopens it from any
  tab, also after a reload, and its **Stop** stops it directly. A run of a saved block reopens read-only
  until it ends; **Open block** then loads the block.

## Roles

Roles select saved model profiles for model blocks and Workshop assistance. Fresh workspaces offer
three unconfigured starter roles: **fast** (routine generation/extraction), **assistant** (Workshop
assistance), and **reasoning** (more demanding tasks). These are suggestions, not model capabilities;
several roles may share a profile. No model, paid service or credentials are selected automatically.

Open **Settings → Models & roles** to browse by **location** (All, Local, Cloud / remote), then by
**family / domain**. The left side lists the matching roles with location, resolved model and status (Ready,
not configured, or ready but not eligible for local-only data); select one there or from the **Role** dropdown.
The editor on the right shows one role at a time. Suggested families are General, Writing & communication, Coding & engineering,
Research & analysis, Data & extraction, and Workflow assistance. Use **Add family** in the editor
to create and assign your own family; custom families persist with their saved roles. Older roles
without a family appear under General. Adding a role uses the active location/family filters.

Edit the selected role's purpose, location rule, family and saved profile. The editor also states where the
saved role connects, whether that endpoint can receive local-only data and whether it is trusted; a `localhost`
endpoint names the `127.0.0.1` address to use in `config/model-servers.ini` (profiles do not own server
addresses). Switching roles or filters retains unsaved edits; **Save role assignments** saves every role,
including roles hidden by filters. Profile choices show backend, endpoint and model. **New or edit profile…**
opens the same profile editor as Model settings over this panel; its **Save profile** is a separate save.
Closing it returns to the role with edits intact. If saving role assignments fails, saved profiles stay saved
and the role edits remain in the editor. **Set up defaults** adds missing starter
roles without replacing existing definitions. A notice lists default roles missing from the saved workspace;
it remains until those roles are added and saved. Workspaces created before `write_block_description` existed
list it there until it is added. Save persists assignments and definitions in
`<workspace>/roles.json`. Saved role names are stable; removing a role warns about references in blocks.

**Local means this machine:** LM Studio or an OpenAI-compatible server (including llama-server)
using `localhost` or a loopback IP address. LAN servers and other remote endpoints are classified as
Cloud / remote; OpenRouter is always Cloud. Profile choices are filtered by location, and the backend
checks compatibility at assignment and role resolution. Neither location silently falls back to the
other. This constrains the endpoint AgentWiz contacts, not what a server might forward elsewhere.

Each role may carry a default **Temperature** and **Reasoning level**; leave either as *Backend default*
to set none. They apply to every model block and Workshop assistance call that uses the role, unless the block
overrides or resets that setting ([Named outputs](#named-outputs)), and never to Chat,
which use the active profile. Zero is a default, not an absent one. An assignment-only save keeps existing
definitions and defaults.

Unconfigured roles can be used while drafting, but cannot run. Missing profiles, blank models and
location mismatches are shown with reasons. Existing version-1 assignments remain usable without
automatic reassignment; saving through the editor adds explicit location definitions. Files written
with definitions use format version 3 (versions 1 and 2 are still read) and require this version of
AgentWiz or newer. **Chat
keeps using the active profile**, not these roles. Role resolution uses saved profile
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

- **Generated output instructions.** AgentWiz renders each output's name, declared meaning,
  contract description, format, Text rules and complete JSON schema where applicable. The
  generated section excludes saved contract identifiers and example data. The checker evaluates
  the result against the captured contracts.
- **How JSON answers are read.** With no fenced block, the parser reads the whole trimmed answer.
  With exactly one, it reads that block and ignores surrounding prose. Multiple fences, duplicate
  keys and invalid JSON are refused. The parser performs no coercion or arbitrary substring extraction.
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

**What is sent.** AgentWiz fills your prompt templates and appends a generated section to your
system instructions. With no authored system instructions, the section is the system message.
**Request preview** shows both messages. The generated section asks the model to:

- Return exactly one JSON object and nothing else: no words before or after it, and no Markdown fence around it.
- Its top-level keys are exactly these output names, in any order, one value each.
- Every key must be present. Do not add keys, rename them, or nest one output inside another, and do not split
  one output across several keys.
- Place each output's complete value directly under its own key. A value may be empty only where its schema
  allows that.
- A text output is a JSON string holding the complete text; quotes, backslashes and line breaks are escaped as
  JSON requires, and any formatting the task asks for goes inside that string.
- A JSON output is the value itself, placed directly under its key, not a string containing JSON.

The section is generated for each request. A Text value inside the JSON envelope may contain
Markdown. The prohibition on fences in the instructions applies to the envelope itself.

**`{{output.name}}`** inserts the named output's declaration in a model prompt. The declaration
may span multiple lines, so place the placeholder on its own line when you use it.
`{{output.a.b}}`, `{{ output.a }}`, `{{output.}}` and undeclared names are refused. Filled text is
never scanned again. Structured outputs require a working JSON Schema validator.

**Older blocks.** Executable v1 definitions report `migration_required` before step keys are
checked. Rewrite the definition and its contracts for v3. If a v3 model step still contains
`output`, remove that key and use `response_mode: "named_json"`. Old saved attempts remain readable.

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

## Python steps

A Python step (`"kind": "python"`, block format 4) names its source file under `scripts/` and the scriptSpell
`engine` it was saved with:

```json
{"id": "run", "kind": "python", "file": "scripts/main.py",
 "engine": {"name": "scriptspell", "profile": "scriptspell-pure-v5", "codec": 2}}
```

- The inputs are the checked values: a Text port's string, a JSON port's exact value (`int`, `Decimal`, `str`,
  `bool`, `None`, lists and objects). Outputs may be the same types; a float at any depth fails the step.
- scriptSpell's limits apply and can only be lowered; the block's `max_seconds` still bounds the run. **Stop**
  ends the step as stopped. At most 4 Python steps run at once. If scriptSpell cannot confirm a worker's cleanup,
  Python blocks are disabled until AgentWiz restarts, and Settings → Overview says so.
- Restricted Python is a language-level restriction, not an OS sandbox. A Python step has no destination and can
  run on Local only data; its outputs are restricted whenever an input is.
- Python steps run on Linux, WSL2 and macOS. Native Windows is not supported.
- A v4 Tool step may carry `"builtin": "<utility>"` (any of the fourteen utilities listed under
  [Block library](#block-library)) in place of `server` and `tool`; such a step runs locally, takes no MCP slot and
  needs no classification or approval.

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

**Stop** (on Chat and MCP tools) asks AgentWiz's server to end the request. For a
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
- **Stop in Workshop Test or Compare** stops the current trial and cancels the remaining trials.
  The activity indicator can stop the same batch from any tab. A model request stops as described
  above. A Python step is cancelled through scriptSpell's runner. A tool call is abandoned and its
  connection closed. Servers using the earlier handshake also receive a cancellation notice.
  **A stopped tool call may still have run**, so its outcome is shown as unknown. An answer already
  accepted by the time Stop arrives stands.

## Run history

Off by default. The **Save runs: On/Off** switch beside the workspace name turns it on and shows its state
either way; while it is on, the header also says "Saving runs". A failed switch is shown as such until the
next change. The switch is saved in `workspace.json`, survives a restart, and cannot be turned on in
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
- **The History tab** lists saved records on the left and opens one on the right. Filters choose all
  records, block runs, workflow runs, comparisons or failed runs. Each kind keeps its own storage and delete
  confirmation. Block runs are the newest 200 (it says "200 of N shown"). Open one to read
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
  summaries of the latest 1,000. Once the summary is evicted, the outcome can no longer be retrieved.

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
- **Define a contract** (updated in 0.2.0): in Workshop → Contracts, check and save reusable structural requirements; a wrong number of the
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
- **Checks are syntactic.** They check structure and declared requirements. Use examples or
  review to judge correctness. User contracts refuse unsupported schema keywords such as
  `$ref`, `pattern`, `format` and `oneOf`. The contract's top-level `format` still selects Text or JSON.
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
- **Documents** are the eight text formats, kept whole, and PDF text without structure; the token count is an
  estimate. The PDF worker is not a sandbox, and on macOS it does not contain a runaway allocation. A workspace
  switch waits for, and does not cancel, a PDF upload in progress. A non-PDF upload cannot be interrupted by
  shutdown; its body read is bounded at 60 seconds.
- **Block library.** A model block tested on one model is not evidence for another; each library block's
  description lists its context requirement. Local de-identification and `scan_for_identifiers` reduce
  the work of a Release Gate review and replace none of it; `check_identifier_map` stops an unusable map before the
  Gate only in a workflow that places it, and cannot tell whether an identifier was missed. The installer holds no
  lock across its three writes: examples or a description you save in between are kept, and an edit to the block in
  between stops the installation, which reports what was written. The Add review binds the destination at
  installation only; a later change to the role changes where the block sends. Workflows have no iteration, so
  `csv_to_rows` output cannot be processed row by row by a model block in a workflow. Table width and header
  uniqueness are checked by the blocks that consume a table, not by contracts.
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
- **Agentic Chat.** A route can pass preflight and still choose the wrong capability; the owner's evaluation
  (`scripts/agent_eval.py`) is the evidence. `Cannot reach files on this computer` and `Effects` are your statements;
  a wrong one defeats the boundary for that tool. Trusted endpoints can relay what they receive. A document or tool
  result can still steer which approved read-only capability the model calls and what it writes in an unrestricted
  answer. A request too large for the model's context is refused by the backend. Token and cost ceilings act only
  after usage is reported. One conversation and one run per process; memory is lost on restart.
- **Python blocks** are not an OS sandbox, and the 1 MiB and 5 second limits rule out whole-document work. Floats are
  rejected, so a script that divides integers with `/` fails until it uses `Decimal`.
- **Blocks.** One step per block: no wiring, routing or nesting yet. Workshop writes managed revisions. The token budget is a soft, reported-usage
  allowance; time budgets are not an exact ceiling. Block runs continue after the page closes, up to
  their time budget, holding their slots; they do not survive a restart. Results are held for 100
  runs and 256 MiB; older ones are available from History only if saving was on. A stopped or
  timed-out tool call may still have run. A truncated attempt cannot publish outputs even when its
  checks pass. A second Ctrl-C exits immediately and skips cleanup. The tutorial doubles process budgets while it is open.
- **The page** is checked in a headless Chromium by the developer tests; other browsers are covered
  only by the tour above.
- **WSL2** is outside hosted CI. PDF extraction was checked manually for 0.2.2.

## License

MIT. See `LICENSE`.

