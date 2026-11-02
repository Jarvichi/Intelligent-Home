# Codex Discord module

The module provides a private coding conversation through the existing IH Discord transport. It does not hold a Discord token, Home Assistant API token or model API key. The module receives signed and encrypted private-message events over MQTT and can only reply through its configured channel grant.

## Access and privacy

The transport encrypts private inbound message metadata/content and the module encrypts reply text with AES-GCM before publishing MQTT messages. The per-module signing key is domain-separated through HKDF for encryption. The transport grant must set `encrypt_content: true`; the Codex worker rejects inbound messages without a valid encrypted envelope. MQTT topics, routing identifiers, and message size remain visible.

Configure exactly one Discord channel key and one user ID in both the transport grant and this add-on. Give the transport bot read-history and post access only to the private channel. The add-on's Supervisor API token is used only by its parent process behind a loopback-only MCP server. The Codex subprocess does not receive the token or a Home Assistant configuration mount. HA tools expose filtered entity search/state/history for ordinary device domains; private domains and unapproved attributes are excluded. No arbitrary REST route or service-call tool is exposed.

Discord prompt text, public IH source files opened by the worker, filtered HA tool results, and retrieved `share: true` Brain notes are sent to the Codex model service. The Brain endpoint excludes inbox, private or unmarked notes, attachments, and writes. Do not use this channel for secrets or notes marked `share: false`. Codex command network access is disabled; its workspace is writable, escalated requests are denied, and files remain in the add-on's private configuration directory.

The worker starts from a packaged copy of the public IH add-on repository and creates a local `codex/work` branch. It has no private host git history or remote. `/code status` and `/code diff` expose the branch state. `/code submit` is an owner-only deterministic command; it scans the changes and creates a new GitHub branch and review PR containing only changed files beneath `ih_*` add-on folders. It does not merge, release, or deploy. The PR does not include the user's prompt or HA state. The optional `github_repo_token` must be a fine-grained token restricted to the IH add-on source repository with Contents read/write and Pull requests read/write. The parent process uses the token for the fixed repository API; it is never passed to Codex.

By default `review_channel_key` is `claude_review`. The Discord transport must map that key to the Claude review channel and grant Codex `post`. Codex can then request Claude to take over an IH code task using a concise summary. A successful `/code submit` also queues the public PR URL. Handoffs exclude the original prompt, Home Assistant state, entity IDs and Brain content. Codex does not claim or subscribe to the review channel, so it cannot read Claude's replies. Uncertain deliveries are held for review and are not automatically posted again. Set the option to an empty string to disable handoffs.

For device actions, Codex can only propose `light` or `switch` `turn_on`, `turn_off`, or `toggle` for one entity. The backend records the exact target and adds a canonical five-minute, 16-character code to its reply. A separate `/approve CODE` message from the authorized owner invokes that one call. `/deny CODE` cancels it. Ambiguous API responses are recorded as uncertain and are never retried.

When the Brain access policy changes, the worker drops the saved thread pointer once and starts a new thread. This ensures a conversation created before the Brain tools were enabled does not retain stale instructions or tool context.

## Setup

The worker needs an existing Codex ChatGPT login in its private add-on configuration. It uses the official Codex SDK and does not use an API key.

1. Install the add-on with manual boot and shadow mode enabled.
2. Put a valid Codex ChatGPT login in the add-on configuration directory at `codex-home/auth.json`. The module sets this file to mode `0600` at startup. Never commit, post or log this file.
3. Set the signing key and MQTT credentials to the same module-specific values configured in the Discord transport. Set `channel_keys` to the single approved channel key and `allowed_user_ids` to the one approved user ID.
4. Verify the transport grant contains only that channel and the `reply` operation. Keep `live_modules` disabled while checking module health, `/status`, and the shadow run.
5. Enable the module in the transport's live-module list and set this add-on's `shadow` option to `false` only after confirming the observed result.

The add-on defaults to `review_channel_key: claude_review`. In the Discord transport, add an `extra_channels` mapping for key `claude_review` to the `claude` channel name (or its channel ID), and extend only the `ih_discord_codex` module grant to channels `codex` and `claude_review` with `reply` and `post` operations. The Codex worker subscribes only to `codex`; the review channel is outbound-only. A post grants the transport bot permission to send there, so verify that the bot can see the channel before enabling it.

## Commands

- `/help` shows this channel's basic use.
- `/status` shows queue and delivery counts without message text or user IDs.
- `/new` starts a fresh Codex thread and abandons interrupted turns or ambiguous deliveries without replaying them.
- `/approve CODE` performs the one pending light or switch action identified by the 16-character code.
- `/deny CODE` cancels the pending action.
- `/code status` and `/code diff` show edits on the local IH source branch; `/code submit` opens a review PR when the repository token is configured.

Normal messages are processed one at a time. The queue is bounded. If a Codex turn or Discord send has an uncertain outcome, it is held for review and is never automatically replayed. `/new` is the explicit recovery action; check Discord manually for a possibly delivered reply before using it.

The HTTP `/healthz` and `/status` endpoints expose status metadata only. The service logs no prompts, replies, tokens, or signing keys.
