# AgentWiz

A local browser UI for building and testing LLM steps, built on
[modelSpell](https://github.com/alexchwong/modelSpell).

You choose a model backend (LM Studio, OpenRouter, or any OpenAI-compatible server such as
llama-server), type an optional system prompt and an input, and watch the reasoning and the answer
stream back, followed by token usage, cost and a clear outcome.

What 0.1.2 does:

- **Each request stands alone.** There is no chat history; prompts, inputs and outputs are not saved.
- **Task settings belong to the request.** Reasoning level, temperature, max tokens and eight
  advanced sampling settings are set in the request panel, and one set can be saved per workspace.
- **Attachments.** `.txt`, `.md`, `.csv` and `.json` files are previewed and inserted into the input
  or system prompt, with a line saying where the text came from.
- **MCP tools.** AgentWiz calls tools on MCP servers you run locally, shows the result, and inserts
  it only when you ask. The model never decides when a tool runs.
- **Model settings can be saved as named profiles** in a workspace folder. Profiles, the saved task
  settings and the list of MCP servers are saved; nothing else is.
- **Settings are shared by all tabs of one AgentWiz process.** If another tab changes them, a tab
  that is still showing the old settings is asked to check them before sending, so a request never
  goes to a backend the tab is not showing.
- **API keys and MCP tokens stay in memory.** They are never shown again, never written to disk
  (profiles included) and are lost when AgentWiz stops.

## Install

Requires Python 3.10 or later.

```bash
curl -fsSL https://github.com/alexchwong/AgentWiz/releases/latest/download/install.sh | bash
```

The script creates `.venv` in the current directory, checks the newest release wheel against the
sha256 GitHub publishes, installs it, and prints how to launch. Pin a release with
`… | bash -s -- --version 0.1.2`; download the script first if you want to read it before running it.

Working from a clone? See DEVEL.md for the developer setup.

Prefer to do it by hand? Download the wheel from the release page (the wheel, not the source zip),
then run:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./agentwiz-0.1.2-py3-none-any.whl
```

For checksum verification, use `python -m pip install` with the wheel URL and `#sha256=…` fragment
from the release notes instead of the local filename. Both methods also install modelSpell 0.2.1,
pinned by URL and checksum.

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

- **One AgentWiz process per workspace.** A second AgentWiz on the same workspace prints which
  process holds it and exits with status 3. To switch workspace, stop AgentWiz and start it again
  with another `--workspace`.
- The folder holds `workspace.json`, `model-profiles/profiles.json`, `task_settings.json` (once
  saved) and `.agentwiz.lock`. If you keep workspaces inside a git repository, add `.agentwiz.lock`
  to that repository's `.gitignore`.
- **Supported locations:** local disks on Linux and macOS, and WSL2's own filesystem. Shared,
  network and cloud-synced folders are not supported and not detected: the workspace lock may not
  be visible from another machine. Under WSL2, workspaces on `/mnt/c` (the Windows drive) have not
  been tested; prefer a folder in WSL2's own filesystem, such as `~/agentwiz`.

## Settings

Open **Model settings** to choose where requests go:

| Setting | Notes |
| --- | --- |
| Model source | LM Studio, OpenRouter or OpenAI-compatible. |
| Base URL | Including `/v1`. Blank uses the default (`http://localhost:1234/v1` for LM Studio, `https://openrouter.ai/api/v1` for OpenRouter). Required for OpenAI-compatible. Must not contain a user name, password, query or fragment (`@`, `?`, `#`): put credentials in the API key. |
| API key | Write-only. Bound to the model source and base URL it was saved for, so it is never sent anywhere else. **Clear** removes it. For OpenRouter, `OPENROUTER_API_KEY` is used when no key is entered. Keys must be visible ASCII characters without spaces. |
| Model ID | Type it, or use **Load models**. |
| Provider | OpenRouter only. **Load providers** lists the model's providers; choosing one pins the request to it with no fallback. |

Reasoning and max tokens moved to the request panel in 0.1.2 (see **Task settings**).

**Apply** uses the dialog's settings for this AgentWiz process.

The header shows the workspace (hover for its folder), the active profile, the current settings and
where the key comes from: `key: session` (entered here), `key: env OPENROUTER_API_KEY`, or
`key: none`.

### Profiles

A profile stores four settings under a name: model source, base URL, model ID and provider. It never
stores a key.

- **From 0.1.2, profile files are format version 2.** Profiles saved by 0.1.1 load as they are, but
  their reasoning and max tokens values are dropped the next time the file is written: set them in
  the request panel and press **Save task settings** instead.
- **Once 0.1.2 has rewritten a profile file, AgentWiz 0.1.1 treats it as read-only.** In particular,
  after an **Export as default** from 0.1.2, a 0.1.1 process sharing those defaults can no longer
  import or export, though its own workspace profiles keep working.

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
- The active profile is restored when AgentWiz starts. Keys are not: enter them again (each key is
  remembered for its model source and base URL), or use `OPENROUTER_API_KEY` for OpenRouter.

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

A blank field uses the backend's default. Each request sends the panel as it is; two tabs can send
different settings at the same time.

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

**Save task settings** stores the panel in the workspace (`task_settings.json`); new pages and
restarts start from it. `(modified)` beside the heading means the panel differs from the saved set.
**Reset** clears the panel without saving. If another tab saved in the meantime, the save is refused
and your values are kept, so you can save again to replace them. Sending never uses the saved set:
it sends what the panel shows.

## Attachments

**Attach file…** beside the input reads `.txt`, `.md`, `.csv` and `.json` files, chosen by the
extension in any letter case. Files must be UTF-8 (with or without a byte-order mark) or UTF-16 with
a byte-order mark; anything else, including Latin-1 and UTF-32, is refused with a message asking for
a UTF-8 copy. Line endings become `\n`. The file is inserted as it is: CSV is not turned into a table
and JSON is not reformatted.

The preview shows exactly what will be inserted: a provenance line such as `[file "report.md"]`,
then the file's text. Beside it are its characters, an estimated token count (characters ÷ 4; compared
with the model's context length when **Load models** reported one), and its size in a request.
**Insert into Input** or **Insert into System prompt** appends it to that field, after a blank line
if the field is not empty. Nothing is sent until you press **Send**.

- Uploads are limited to 50 MiB and requests to 32 MiB. A file whose text is larger than a request
  may be is still shown and can be inserted, with a note that it cannot be sent as it is: insert a
  smaller part of the file instead.
- Nothing uploaded is stored: AgentWiz reads the file in memory, and the file name is used only for
  display and the provenance line.
- The **Insertions** list under the input records each insertion (its provenance line, target and
  size) until the page is reloaded. **Clear list** empties it.
- PDF and Word files need docSpell, which arrives in a later release.

## MCP tools

The Model Context Protocol (MCP) lets a program offer **tools**, such as a web search, to AI
applications. AgentWiz connects to MCP servers that you run on your own machine and reach over HTTP
(MCP's Streamable HTTP transport), lists their tools, and calls the one you choose with the arguments
you give. The result is shown; you decide whether to insert it, and nothing is sent to the model
until you insert it and press **Send**.

- **Both protocol eras.** AgentWiz speaks MCP 2026-07-28 and the earlier handshake revisions
  (2025-03-26 to 2025-11-25), and detects which one each server uses. The era and version appear
  after **Load tools**.
- **Servers…** opens the shared server list (`./mcp_servers/servers.json`, or `--mcp-servers-dir`),
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
- Results and their sizes are previewed like attachments, with a provenance line such as
  `[tool web.search_web arguments={"query":"lighthouse"}]`. When a result has both text and structured
  content, **Insert structured content instead** switches the preview. Images and other items are
  listed, not inserted.
- Tool results and attachments may contain instructions aimed at the model. AgentWiz shows them
  before insertion and never sends them by itself; it does not sanitise them.

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
4. In AgentWiz open **Tools**, press **Servers…**, then **Add**: name `web`, URL
   `http://127.0.0.1:8765/mcp`, no token (the server listens on this machine only and has none).
   Press **Save**.
5. Choose `web` and press **Load tools**: `search_web` and `read_url` appear, with the era. An image
   built before FastMCP 4 shows 2025-11-25; a fresh build shows 2026-07-28. Both work.
6. Choose `search_web`, type a query, press **Run**. Read the passages, press **Insert into Input**,
   add a question, and press **Send**.
7. Try `read_url` on one of the cited URLs, and press **Stop** while it runs.
8. Afterwards: `docker compose down`.

Notes: web-retrieval-mcp fetches the open web from the machine it runs on; its `settings.toml` tunes
how much it returns; under WSL2, use the address where Docker publishes port 8765.

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
| Stopped | You pressed Stop. |

The rest of the line gives the time, transport, finish reason, token counts and cost. A missing
value is shown as `?`, never as zero. **Result event** shows the final event exactly as received,
including the task settings sent.

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

A tour of what AgentWiz does, through the page. It is the check of the page itself, which no
automated test reaches. Use any backend you have. With no model running, a clone of this repository
has scripted stand-ins: run `python scripts/stub_backend.py`, then in **Model settings** choose
OpenAI-compatible, base URL `http://127.0.0.1:8099/v1`, model `complete`; and run
`python scripts/stub_mcp_server.py` for the MCP steps.

**Basics (from 0.1.1)**

1. **Workspace.** In an empty folder run `agentwiz`. The terminal's third line names the workspace
   and ends in `(created)`; the fourth names the MCP server folder. Stop with Ctrl-C and start
   again: no `(created)` this time.
2. **Send.** In **Model settings**, set the backend and model and press **Apply**. Type an input
   and press **Send**: the answer streams in and the status line shows the outcome.
3. **Profiles.** Press **Save as profile…** and name it `a`; change the model, **Apply**, and see
   `(modified)`; **Save profile** clears it. Save a second profile `b`, switch between them with the
   **Profile** list, choose **(no profile)**, then delete `b`. The dialog has no reasoning or max
   tokens rows. A profile file written by 0.1.1 still loads.
4. **Two tabs.** Change the model in a second tab and **Apply**; **Send** in the first tab is refused
   with a message to check the settings.
5. **Damaged file.** Stop AgentWiz. In `workspaces/default/model-profiles/profiles.json`, change a
   `model` value to `64` (no quotes), and start again. A banner names the file and the problem; the
   profile controls are disabled, but **Send** still works. Undo the edit and restart.

**Task settings**

6. Set temperature and max tokens, open **Advanced**, set top-k, close it and see `Advanced (1 set)`.
7. With LM Studio, switch reasoning between off and low: top-k is greyed out at low and enabled at
   off. With `stub_backend.py`, its log shows the fields each request carried.
8. **Reset** clears the panel. Set values, **Save task settings**, restart AgentWiz: they are restored.

**Attachments**

9. Attach a Markdown file: the preview shows `[file "name.md"]` and the text. **Insert into Input**,
   then **Send**. The **Insertions** list shows it.
10. Attach a Latin-1 text file: it is refused with a message asking for UTF-8.
11. Attach a large text file (over 32 MiB): the preview says it cannot be sent as it is.

**MCP**, with `python scripts/stub_mcp_server.py`

12. Open **Tools**, **Servers…**, **Add** `stub` at `http://127.0.0.1:8098/mcp`, **Save**, then
    **Load tools**: era 2026-07-28.
13. Run `echo` and insert the result. Run `slow` and watch the progress. Run `hang` and press
    **Stop**: the stub logs the disconnect at once, and the page says the connection was closed.
14. **Numbers through the form.** Choose `numbers`: the form is pre-filled with
    `9007199254740993` and `0.10000000000000000001`. Run it unchanged: the stub's log shows
    `{"id":9007199254740993,"ratio":0.10000000000000000001}` exactly, and the result and the
    insertion preview show both values exactly. Then type `9007199254740995` and
    `0.30000000000000000004`, run, and check the log and result again.
15. **Numbers through the editor.** Press **Edit as JSON**, change `id` to `9007199254740997` and
    `ratio` to `0.12345678901234567890123`, run, and check that the log and result show the same
    values exactly.
16. Restart the stub with `--era legacy`, press **Load tools** again (era 2025-11-25), and repeat
    `hang` and **Stop**: the stub logs a cancellation notice.
17. Restart the stub with `--token secret`: **Load tools** fails with a credentials error. Type
    `secret` in the server's token field in **Servers…**, save, and it works.

Optionally, try the web-retrieval-mcp example above.

## Limitations

- **Stop and the 8-request limit.** Stop closes the model connection only when more output
  arrives, which can take a long time before the first token or while a backend sends only
  keepalive traffic. At most 8 model requests can be in progress, stopped ones included. If all 8
  stay occupied, new requests are refused; restarting AgentWiz is the remedy.
- **Backend error details are not shown.** Failures after a request is sent appear as a fixed
  description with the error class, HTTP status and base URL, because backend error text can
  contain your API key in forms AgentWiz cannot reliably remove. MCP servers' error text is shown
  only when no token is held for that server, labelled "the server says:".
- **Accepted is not applied.** A task setting accepted by a backend may still be ignored by it.
  OpenRouter's provider restriction can refuse requests that would otherwise have succeeded with a
  setting quietly ignored. On LM Studio, seed, stop sequences and the presence and frequency
  penalties are never sent; top-k, min-p and repetition penalty need reasoning off.
- **Settings and keys.** Model settings are shared by all tabs of one AgentWiz process; only
  profiles, the saved task settings and the MCP server list survive a restart. Keys and typed MCP
  tokens are held in memory only and must be re-entered after a restart.
- **MCP.** Local servers over Streamable HTTP only; tools only (no resources, prompts, sampling,
  elicitation, roots, logging, tasks or subscriptions); no stdio servers; no servers that speak only
  the 2024-11-05 HTTP+SSE transport; no stream resumption; tool results are not checked against the
  tool's output schema. On a 2026-07-28 server, only tools that the server lists can be called. A
  tool argument that is required but may be `null` needs the JSON editor. At most 8 tool operations
  and 4 cancellation or session-ending tasks run at once; a server whose name never resolves holds
  its slot until the resolver gives up.
- **Unknown tool outcomes.** A lost connection, a server failure, a deadline or Stop after a call
  was sent leaves its outcome unknown; AgentWiz never resends it.
- **Attachments** are the four text formats, inserted whole; the token count is an estimate.
  Insertion records live in the page and are lost on reload.
- **Workspaces.** One AgentWiz process per workspace; restart to switch. The default folders are
  relative to where AgentWiz is launched. Shared, network and cloud-synced folders are unsupported
  and not detected.
- **Damaged or newer local files** make the workspace, its profiles or its task settings read-only
  until AgentWiz restarts. Damaged defaults disable import/export only, and a damaged MCP server list
  disables MCP; both are detected again as soon as they are repaired. Numbers with more precision
  than AgentWiz can keep exactly count as damage.
- **Profile files rewritten by 0.1.2** are read-only to AgentWiz 0.1.1.
- **Files.** Files AgentWiz rewrites become readable by their owner only (mode 0600). A crash during
  a write can leave a `.<file>.<random>.tmp` file beside an intact original; after a power cut, the
  most recent change may be lost.
- **Backends.** OpenAI-compatible servers (including llama-server) have no reasoning control from
  AgentWiz; their reasoning text is still displayed. On LM Studio's native route (reasoning "off"),
  truncation is modelSpell's estimate.
- **The page's JavaScript** is not covered by automated tests; the tour above is its check.
- **WSL2** is not checked by the automated tests.

## License

MIT. See `LICENSE`.
