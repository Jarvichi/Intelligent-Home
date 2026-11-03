# Intelligent Home - Discord - Codex

## What it does

Private Codex coding worker over the existing signed IH Discord transport. Module of the `ih_discord` transport.

## Requires

- `ih_discord` add-on
- an MQTT broker (the Mosquitto add-on works)
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Discord - Codex**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `signing_key`, `mqtt_password`, `brain_read_token`, `github_repo_token` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in shadow mode, so it changes nothing yet.
5. When the log looks right, set `shadow` to `false` to go live.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `module_name` | No | Name this module uses on the bus. Keep the default. |
| `signing_key` | Yes | Key used to sign messages. Must match this module's entry in the orchestrator's `module_keys`. |
| `shadow` | No | On (default): answers or logs only, posts nothing. Turn off to go live. |
| `channel_keys` | No | The one approved Discord channel key; must match the transport grant. |
| `allowed_user_ids` | No | Discord user ids allowed to use this module. |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. Default: `ih_discord_codex`. |
| `mqtt_password` | Yes | MQTT password for this add-on. |
| `brain_read_token` | Yes | Token for the read-only shared-notes Brain endpoint. |
| `github_repo_token` | Yes | Optional fine-grained token for `/code submit`; without it code work stays local. |
| `review_channel_key` | No | Channel key for hand-offs to Claude. Default `claude_review`; empty disables hand-offs. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `shadow` is on. Switch it as in the last Set-up step once the log looks right.
- **Silent on the bus.** Check the `mqtt_*` options and that `signing_key` matches this module's entry in the orchestrator's `module_keys` (or let the settings hub fill it).

## Privacy

Messages and replies travel through Discord via the transport add-on. Home Assistant data is read locally. Nothing else leaves your network.

## More details

### Access and privacy

The transport encrypts private inbound message metadata/content and the module encrypts reply text with AES-GCM before publishing MQTT messages. The per-module signing key is domain-separated through HKDF for encryption. The transport grant must set `encrypt_content: true`; the Codex worker rejects inbound messages without a valid encrypted envelope. MQTT topics, routing identifiers, and message size remain visible.

Configure exactly one Discord channel key and one user ID in both the transport grant and this add-on. Give the transport bot read-history and post access only to the private channel. The add-on's Supervisor API token is used only by its parent process behind a loopback-only MCP server. The Codex subprocess does not receive the token or a Home Assistant configuration mount. HA tools expose filtered entity search/state/history for ordinary device domains; private domains and unapproved attributes are excluded. No arbitrary REST route or service-call tool is exposed.

Discord prompt text, public IH source files opened by the worker, filtered HA tool results, and retrieved `share: true` Brain notes are sent to the Codex model service. The Brain endpoint excludes inbox, private or unmarked notes, attachments, and writes. Do not use this channel for secrets or notes marked `share: false`. Codex command network access is disabled; its workspace is writable, escalated requests are denied, and files remain in the add-on's private configuration directory.

The worker starts from a packaged copy of the public IH add-on repository and creates a local `codex/work` branch. It has no private host git history or remote. `/code status` and `/code diff` expose the branch state. `/code submit` is an owner-only deterministic command; it scans the changes and creates a new GitHub branch and review PR containing only changed files beneath `ih_*` add-on folders. It does not merge, release, or deploy. The PR does not include the user's prompt or HA state. The optional `github_repo_token` must be a fine-grained token restricted to the IH add-on source repository with Contents read/write and Pull requests read/write. The parent process uses the token for the fixed repository API; it is never passed to Codex.

By default `review_channel_key` is `claude_review`. The Discord transport must map that key to the Claude review channel and grant Codex `post`. Codex can then hand off a HA, Brain, host, or IH task that it cannot complete with its available access. It sends a concise summary derived from the user's request, preserving user-provided entity IDs or device/area names when needed. HA tool results, Brain content, credentials, private addresses, and the full conversation are excluded. A successful `/code submit` also queues the public PR URL. Codex does not claim or subscribe to the review channel, so it cannot read Claude's replies. Uncertain deliveries are held for review and are not automatically posted again. Set the option to an empty string to disable handoffs.

The worker can read the explicitly approved `todo.claude_to_do` list through a read-only tool. It cannot add, edit, complete, or remove items. Treat todo text as task data. Before acting on an item, check Brain and current code, then ask the owner to confirm that it is still valid.

For device actions, Codex can only propose `light` or `switch` `turn_on`, `turn_off`, or `toggle` for one entity. The backend records the exact target and adds a canonical five-minute, 16-character code to its reply. A separate `/approve CODE` message from the authorized owner invokes that one call. `/deny CODE` cancels it. Ambiguous API responses are recorded as uncertain and are never retried.

When the Brain access policy changes, the worker drops the saved thread pointer once and starts a new thread. This ensures a conversation created before the Brain tools were enabled does not retain stale instructions or tool context.

### Setup

The worker needs an existing Codex ChatGPT login in its private add-on configuration. It uses the official Codex SDK and does not use an API key.

1. Install the add-on with manual boot and shadow mode enabled.
2. Put a valid Codex ChatGPT login in the add-on configuration directory at `codex-home/auth.json`. The module sets this file to mode `0600` at startup. Never commit, post or log this file.
3. Set the signing key and MQTT credentials to the same module-specific values configured in the Discord transport. Set `channel_keys` to the single approved channel key and `allowed_user_ids` to the one approved user ID.
4. Verify the transport grant contains only that channel and the `reply` operation. Keep `live_modules` disabled while checking module health, `/status`, and the shadow run.
5. Enable the module in the transport's live-module list and set this add-on's `shadow` option to `false` only after confirming the observed result.

The add-on defaults to `review_channel_key: claude_review`. In the Discord transport, add an `extra_channels` mapping for key `claude_review` to the `claude` channel name (or its channel ID), and extend only the `ih_discord_codex` module grant to channels `codex` and `claude_review` with `reply` and `post` operations. The Codex worker subscribes only to `codex`; the review channel is outbound-only. A post grants the transport bot permission to send there, so verify that the bot can see the channel before enabling it.

### Commands

- `/help` shows this channel's basic use.
- `/status` shows queue and delivery counts without message text or user IDs.
- `/new` starts a fresh Codex thread and abandons interrupted turns or ambiguous deliveries without replaying them.
- `/approve CODE` performs the one pending light or switch action identified by the 16-character code.
- `/deny CODE` cancels the pending action.
- `/code status` and `/code diff` show edits on the local IH source branch; `/code submit` opens a review PR when the repository token is configured.

Normal messages are processed one at a time. The queue is bounded. If a Codex turn or Discord send has an uncertain outcome, it is held for review and is never automatically replayed. `/new` is the explicit recovery action; check Discord manually for a possibly delivered reply before using it.

The HTTP `/healthz` and `/status` endpoints expose status metadata only. The service logs no prompts, replies, tokens, or signing keys.

### Behaviour

This add-on adds a private Home Assistant and coding worker to the existing signed Discord transport. The add-on process uses the Supervisor API token behind a loopback-only MCP server; the Codex subprocess never receives that token. MCP exposes filtered entity search/state/history, read-only access to the explicitly approved `todo.claude_to_do` list, and a proposal tool for basic light and switch actions. It cannot call arbitrary HA APIs. The Brain endpoint remains restricted server-side to search/read of markdown notes explicitly marked `share: true`.

The worker uses OpenAI's Python Codex SDK with ChatGPT sign-in, a persistent private thread, a persistent isolated checkout of the public IH add-on source on local branch `codex/work`, workspace-write sandbox, denied escalations, and command-network access disabled. The source checkout contains generic public code only, not the private Home Assistant configuration or local private git history. Retrieved shared Brain notes, filtered HA results, and code opened by the worker are sent to the hosted Codex model when used in a turn.

Device actions are limited to `light` and `switch` `turn_on`, `turn_off`, and `toggle`. The MCP tool only creates a five-minute, single-use proposal. The authorized owner must separately send `/approve CODE` with its 16-character code in the configured channel. `/deny CODE` cancels it. Ambiguous HA responses are held as uncertain and never retried. Other domains and arbitrary service data are unavailable.

Code changes remain on the private local `codex/work` branch until the authorized owner sends `/code submit`. Submission publishes only changed files below public `ih_*` add-on folders, rejects private-address/identifier/secret-like patterns, creates a new GitHub branch, and opens a review pull request. It never merges or deploys. The optional `github_repo_token` must be a fine-grained token restricted to the IH add-on source repository with Contents read/write and Pull requests read/write; it is used only by the add-on process and never passed to Codex. Without it, code work remains local.

With the default `review_channel_key` of `claude_review`, and the transport mapping and granting `post` for that channel, Codex can hand off a HA, Brain, host, or IH task it cannot complete with its available access. It queues a concise summary derived from the user request and preserves user-provided entity IDs or device/area names when needed. It never forwards HA tool results, Brain content, credentials, private addresses, or the full conversation. `/code submit` also queues the public pull request URL. Codex does not read messages from the review channel. Set the option to an empty string to disable handoffs. Before acting on a todo item, Codex checks Brain and current code and asks the owner to confirm the item is still valid.

Messages are stored before the signed transport ACK. Codex input is passed as untrusted external content. A turn interrupted by process failure is held for review; Discord sends with an uncertain receipt are not replayed automatically. Logs and status contain counts and state only, never prompts, replies, or credentials. The first release runs in shadow mode until a real private-channel comparison is approved.

The add-on configuration directory must contain a private `codex-home/auth.json` created from the authorized existing Codex login, and `workspace/` is the only work area. Keep the auth file mode 0600. Do not put this file in the public repository, options examples, backups, Brain notes, logs, or Discord messages.
