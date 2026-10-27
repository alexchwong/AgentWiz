# AgentWiz

A local browser UI for building and testing LLM steps, built on
[modelSpell](https://github.com/alexchwong/modelSpell).

You choose a model backend (LM Studio, OpenRouter, or any OpenAI-compatible server such as
llama-server), type an optional system prompt and an input, and watch the reasoning and the answer
stream back, followed by token usage, cost and a clear outcome.

What 0.1.1 does:

- **Each request stands alone.** There is no chat history; prompts, inputs and outputs are not saved.
- **Settings can be saved as named profiles** in a workspace folder, and the active profile is
  restored when AgentWiz starts. Nothing else is saved.
- **Settings are shared by all tabs of one AgentWiz process.** If another tab changes them, a tab
  that is still showing the old settings is asked to check them before sending, so a request never
  goes to a backend the tab is not showing.
- **API keys stay in memory.** They are never shown again, never written to disk (profiles included)
  and are lost when AgentWiz stops.

## Install

Requires Python 3.10 or later.

```bash
curl -fsSL https://github.com/alexchwong/AgentWiz/releases/latest/download/install.sh | bash
```

The script creates `.venv` in the current directory, checks the newest release wheel against the
sha256 GitHub publishes, installs it, and prints how to launch. Pin a release with
`… | bash -s -- --version 0.1.1`; download the script first if you want to read it before running it.

Working from a clone? See DEVEL.md for the developer setup.

Prefer to do it by hand? Download the wheel from the release page (the wheel, not the source zip),
then run:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./agentwiz-0.1.1-py3-none-any.whl
```

For checksum verification, use `python -m pip install` with the wheel URL and `#sha256=…` fragment
from the release notes instead of the local filename. Both methods also install modelSpell 0.1.0,
pinned by URL and checksum.

## Launch

```bash
agentwiz                            # opens your browser at the printed address
agentwiz --no-browser               # just print the address
agentwiz --port 8765                # fixed port (default: any free port)
agentwiz --workspace work           # use the workspace "work" (default: default)
agentwiz --workspaces-dir ~/aw      # folder holding workspaces (default: ./workspaces)
agentwiz --profiles-dir ~/models    # shared default profiles (default: ./model_profiles)
agentwiz --version                  # AgentWiz and modelSpell versions
python -m agentwiz                  # same as agentwiz
```

AgentWiz listens on 127.0.0.1 only and prints an address such as
`http://127.0.0.1:8765/?t=<token>`. The token changes every launch, and every request must carry it,
so other web pages in your browser cannot use AgentWiz. Anyone who can read the terminal output can
use it while it runs. Press Ctrl-C to stop.

### Workspaces

Profiles are saved in a **workspace**: the folder `<workspaces-dir>/<name>/`, by default
`./workspaces/default`. The default is relative to the directory you launch AgentWiz from, so
launching from a different directory uses a different workspace. A workspace that does not exist is
created with copies of the default profiles, and its terminal line ends in `(created)`; if you did not
expect that, check the name you typed. Names are 1 to 64 letters, digits, `.`, `_` or `-`, starting
with a letter or digit.

- **One AgentWiz process per workspace.** A second AgentWiz on the same workspace prints which
  process holds it and exits with status 3. To switch workspace, stop AgentWiz and start it again
  with another `--workspace`.
- The folder holds `workspace.json`, `model-profiles/profiles.json` and `.agentwiz.lock`. If you keep workspaces
  inside a git repository, add `.agentwiz.lock` to that repository's `.gitignore`.
- **Supported locations:** local disks on Linux and macOS, and WSL2's own filesystem. Shared,
  network and cloud-synced folders are not supported and not detected: the workspace lock may not
  be visible from another machine. Under WSL2, workspaces on `/mnt/c` (the Windows drive) have not
  been tested; prefer a folder in WSL2's own filesystem, such as `~/agentwiz`.

## Settings

Open **Model settings** to choose:

| Setting | Notes |
| --- | --- |
| Model source | LM Studio, OpenRouter or OpenAI-compatible. |
| Base URL | Including `/v1`. Blank uses the default (`http://localhost:1234/v1` for LM Studio, `https://openrouter.ai/api/v1` for OpenRouter). Required for OpenAI-compatible. Must not contain a user name, password, query or fragment (`@`, `?`, `#`): put credentials in the API key. |
| API key | Write-only. Bound to the model source and base URL it was saved for, so it is never sent anywhere else. **Clear** removes it. For OpenRouter, `OPENROUTER_API_KEY` is used when no key is entered. Keys must be visible ASCII characters without spaces. |
| Model ID | Type it, or use **Load models**. |
| Provider | OpenRouter only. **Load providers** lists the model's providers; choosing one pins the request to it with no fallback. |
| Reasoning | Backend default, off, low, medium or high. Not available for OpenAI-compatible servers. |
| Max tokens | Blank uses the backend's default. |

**Apply** uses the dialog's settings for this AgentWiz process (it was called Save in 0.1.0).

The header shows the workspace (hover for its folder), the active profile, the current settings and
where the key comes from: `key: session` (entered here), `key: env OPENROUTER_API_KEY`, or
`key: none`.

### Profiles

A profile stores the settings above, except the API key, under a name. It never stores a key.

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
the profiles are. "Damaged" includes invalid JSON, a missing or wrongly typed field, a repeated key,
a number AgentWiz could not write back exactly, or a base URL breaking the rule above. The file is
left untouched, a warning names the file and the problem (in the terminal and in a banner on the
page), and the profile controls are disabled. Apply and Send keep working.

After repairing or restoring the file, **restart AgentWiz**: the workspace stays read-only for the
rest of the process even once the file is fixed. If you edit `profiles.json` by hand while AgentWiz
is running, AgentWiz picks it up at the next profile change, and fields it manages may be
overwritten; fields it does not know are kept.

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
value is shown as `?`, never as zero. **Result event** shows the final event exactly as received.

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

A quick tour of what 0.1.1 does, through the page. Use any backend you have. With no model running,
a clone of this repository has a scripted stand-in: run `python scripts/stub_backend.py`, then in
**Model settings** choose OpenAI-compatible, base URL `http://127.0.0.1:8099/v1`, model `complete`.

1. **Workspace.** In an empty folder run `agentwiz`. The terminal's third line names the workspace
   and ends in `(created)`; the page header shows `workspace: default` (hover for its folder).
   Stop with Ctrl-C and start again: no `(created)` this time.
2. **Send.** In **Model settings**, set the backend and model and press **Apply**. Type an input
   and press **Send**: the answer streams in and the status line shows the outcome.
3. **Profiles.** In **Model settings**, press **Save as profile…** and name it `a`. The header shows
   `profile: a`. Change the model and press **Apply**: the header adds `(modified)`. Press **Save
   profile**: `(modified)` goes. Save a second profile `b` with different settings, then switch
   between `a` and `b` with the **Profile** list; the dialog and header follow. Choose
   **(no profile)**: the settings stay, the header shows `no profile`. Select `b` and press
   **Delete**: it asks first, then `b` is gone.
4. **Restart.** Select `a`, stop AgentWiz and start it again. `a` is active with its settings, and
   the header shows `key: none` (keys are never saved); enter the key again if the backend needs one.
5. **Two tabs.** Open the address in a second tab. Change the model there and **Apply**. Back in the
   first tab, press **Send**: it is refused with a message to check the settings, and the header
   updates. The same happens for a profile action taken in a tab showing outdated settings.
6. **One process per workspace.** While AgentWiz runs, start a second `agentwiz` in the same folder
   in another terminal: it says the workspace is in use (with the first one's process ID) and exits.
7. **Damaged file.** Stop AgentWiz. In `workspaces/default/model-profiles/profiles.json`, change a `max_tokens`
   value to `"64"` (with the quotes), and start again. The terminal and a banner on the page name
   the file and the problem; the profile controls are disabled, but **Apply** and **Send** still
   work, and the file is left as it is. Undo the edit and restart: everything works again.
8. **Base URL rule.** In **Model settings**, enter a base URL ending in `?x=1` and press **Apply**:
   it is refused, and nothing changes.

## Limitations

- **Stop and the 8-request limit.** Stop closes the model connection only when more output
  arrives, which can take a long time before the first token or while a backend sends only
  keepalive traffic. At most 8 requests can be in progress, stopped ones included. If all 8 stay
  occupied, new requests are refused; restarting AgentWiz is the remedy.
- **Backend error details are not shown.** Failures after a request is sent appear as a fixed
  description with the error class, HTTP status and base URL, because backend error text can
  contain your API key in forms AgentWiz cannot reliably remove.
- **Settings and keys.** Settings are shared by all tabs of one AgentWiz process; only profiles
  survive a restart. Keys are held in memory only and must be re-entered after a restart; for
  OpenRouter, `OPENROUTER_API_KEY` is used when no key is entered (LM Studio's token has no
  environment fallback). Keys typed in the UI must be visible ASCII characters without spaces.
- **Workspaces.** One AgentWiz process per workspace; restart to switch. The default workspace
  folder is relative to where AgentWiz is launched. Shared, network and cloud-synced folders are
  unsupported and not detected.
- **Damaged or newer local files** make the workspace or its profiles read-only until AgentWiz restarts.
  Damaged defaults disable import/export only; repairing defaults is detected without restarting.
  Numbers with more precision than AgentWiz can keep exactly count as damage.
- **Workspace files.** Files AgentWiz rewrites become readable by their owner only (mode 0600). A
  crash during a write can leave a `.<file>.<random>.tmp` file beside an intact original; after a
  power cut, the most recent profile change may be lost.
- **Backends.** OpenAI-compatible servers (including llama-server) have no reasoning control from
  AgentWiz; their reasoning text is still displayed. On LM Studio's native route (reasoning "off"),
  truncation is modelSpell's estimate.
- **Request size.** There is no request-size limit in this release.

## License

MIT. See `LICENSE`.
