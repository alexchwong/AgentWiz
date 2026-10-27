# NEWS
## Unreleased
- Local server startup no longer waits for reverse-DNS resolution, avoiding delays on macOS.

## 0.1.1
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
