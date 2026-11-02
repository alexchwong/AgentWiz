# NEWS
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
