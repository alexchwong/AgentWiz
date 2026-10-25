# AgentWiz

A local browser UI for building and testing LLM steps, built on
[modelSpell](https://github.com/alexchwong/modelSpell).

You choose a model backend (LM Studio, OpenRouter, or any OpenAI-compatible server such as
llama-server), type an optional system prompt and an input, and watch the reasoning and the answer
stream back, followed by token usage, cost and a clear outcome.

What 0.1.0 does:

- **Each request stands alone.** There is no chat history; nothing is saved to disk.
- **Settings are shared by all tabs of one AgentWiz process.** If another tab changes them, a tab
  that is still showing the old settings is asked to check them before sending, so a request never
  goes to a backend the tab is not showing.
- **API keys stay in memory.** They are never shown again, never written to disk and are lost when
  AgentWiz stops.

## Install

Requires Python 3.10 or later. Install the release wheel by URL and checksum (the GitHub release
page shows the exact line):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install "AgentWiz @ https://github.com/alexchwong/AgentWiz/releases/download/v0.1.0/agentwiz-0.1.0-py3-none-any.whl#sha256=<sha256>"
```

This also installs modelSpell 0.1.0, pinned by URL and checksum.

## Launch

```bash
agentwiz                    # opens your browser at the printed address
agentwiz --no-browser       # just print the address
agentwiz --port 8765        # fixed port (default: any free port)
agentwiz --version          # AgentWiz and modelSpell versions
python -m agentwiz          # same as agentwiz
```

AgentWiz listens on 127.0.0.1 only and prints an address such as
`http://127.0.0.1:8765/?t=<token>`. The token changes every launch, and every request must carry it,
so other web pages in your browser cannot use AgentWiz. Anyone who can read the terminal output can
use it while it runs. Press Ctrl-C to stop.

## Settings

Open **Model settings** to choose:

| Setting | Notes |
| --- | --- |
| Model source | LM Studio, OpenRouter or OpenAI-compatible. |
| Base URL | Including `/v1`. Blank uses the default (`http://localhost:1234/v1` for LM Studio, `https://openrouter.ai/api/v1` for OpenRouter). Required for OpenAI-compatible. |
| API key | Write-only. Bound to the model source and base URL it was saved for, so it is never sent anywhere else. **Clear** removes it. For OpenRouter, `OPENROUTER_API_KEY` is used when no key is entered. Keys must be visible ASCII characters without spaces. |
| Model ID | Type it, or use **Load models**. |
| Provider | OpenRouter only. **Load providers** lists the model's providers; choosing one pins the request to it with no fallback. |
| Reasoning | Backend default, off, low, medium or high. Not available for OpenAI-compatible servers. |
| Max tokens | Blank uses the backend's default. |

The header shows the current settings and where the key comes from: `key: session` (entered here),
`key: env OPENROUTER_API_KEY`, or `key: none`.

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

| Configuration | Status |
| --- | --- |
| Linux, LM Studio on the same machine | documented, awaiting evidence |
| Linux, llama-server | documented, awaiting evidence |
| macOS, LM Studio on the same machine | documented, awaiting evidence |
| WSL2, LM Studio on Windows, mirrored networking | documented, awaiting evidence |
| WSL2, LM Studio on Windows, NAT with "Serve on Local Network" | documented, awaiting evidence |
| WSL2, LM Studio headless (`llmster`) inside WSL2 | documented, awaiting evidence |
| OpenRouter (any one platform) | documented, awaiting evidence |

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

## Limitations

- **Stop and the 8-request limit.** Stop closes the model connection only when more output
  arrives, which can take a long time before the first token or while a backend sends only
  keepalive traffic. At most 8 requests can be in progress, stopped ones included. If all 8 stay
  occupied, new requests are refused; restarting AgentWiz is the remedy.
- **Backend error details are not shown.** Failures after a request is sent appear as a fixed
  description with the error class, HTTP status and base URL, because backend error text can
  contain your API key in forms AgentWiz cannot reliably remove.
- **Settings and keys.** Settings are shared by all tabs of one AgentWiz process and lost on
  restart. Keys are held in memory only; for OpenRouter, `OPENROUTER_API_KEY` is used when no key
  is entered. Keys typed in the UI must be visible ASCII characters without spaces.
- **Backends.** OpenAI-compatible servers (including llama-server) have no reasoning control from
  AgentWiz; their reasoning text is still displayed. On LM Studio's native route (reasoning "off"),
  truncation is modelSpell's estimate.
- **Request size.** There is no request-size limit in this release.

## License

MIT. See `LICENSE`.
