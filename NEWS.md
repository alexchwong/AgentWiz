# NEWS
## 0.1.0
- First release: local browser UI to send one prompt to LM Studio, OpenRouter or OpenAI-compatible servers.
- Reasoning and answer stream live; each request stands alone and nothing is saved to disk.
- Every result shows an outcome: complete, empty, reasoning only, truncated, error or stopped.
- Settings are shared by all tabs; a tab showing outdated settings must reload before sending.
- At most 8 requests run at once, stopped ones included; further requests are refused until one ends.
- API keys stay in memory, are never shown again, and error messages never include them.
- `agentwiz --version` reports AgentWiz and modelSpell versions; `--port` and `--no-browser` control launch.
- Released as a wheel on GitHub releases; pin it by URL and sha256.
