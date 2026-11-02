# IH Discord Codex module

This add-on adds a private coding worker to the existing signed Discord transport. It has no Discord token, Home Assistant API, or host configuration mount. Its only MCP server is the dedicated read-only Brain endpoint, which enforces search/read access to markdown notes explicitly marked `share: true`. Inbox notes, private or unmarked notes, attachments, Brain writes, and other MCP servers are unavailable. The transport and this add-on must each allow only the approved channel key and one explicitly permitted user ID.

The worker uses OpenAI's Python Codex SDK with ChatGPT sign-in, a persistent private thread, a persistent empty scratch workspace, workspace-write sandbox, denied escalations, and command-network access disabled. It cannot see house configuration because the add-on container mounts only its own add-on configuration directory. Retrieved `share: true` Brain note contents are sent to the hosted Codex model when used in a turn.

Messages are stored before the signed transport ACK. Codex input is passed as untrusted external content. A turn interrupted by process failure is held for review; Discord sends with an uncertain receipt are not replayed automatically. Logs and status contain counts and state only, never prompts, replies, or credentials. The first release runs in shadow mode until a real private-channel comparison is approved.

The add-on configuration directory must contain a private `codex-home/auth.json` created from the authorized existing Codex login, and `workspace/` is the only work area. Keep the auth file mode 0600. Do not put this file in the public repository, options examples, backups, Brain notes, logs, or Discord messages.
