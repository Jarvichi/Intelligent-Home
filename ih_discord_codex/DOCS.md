# Codex Discord module

The module provides a private coding conversation through the existing IH Discord transport. It does not hold a Discord token, Home Assistant API token or model API key. The module receives signed events over MQTT and can only reply through its configured channel grant.

## Access and privacy

Configure exactly one Discord channel key and one user ID in both the transport grant and this add-on. Give the transport bot read-history and post access only to the private channel. The add-on has no Home Assistant API, no host configuration mount, and no Brain or MCP connection. Its only mapped directory is its own private add-on configuration folder.

Discord prompt text and scratch-workspace context are sent to the Codex model service. Do not use this channel for house secrets or private household data. Codex command network access is disabled; its workspace is writable, escalated requests are denied, and files remain in the add-on's private configuration directory.

## Setup

The worker needs an existing Codex ChatGPT login in its private add-on configuration. It uses the official Codex SDK and does not use an API key.

1. Install the add-on with manual boot and shadow mode enabled.
2. Put a valid Codex ChatGPT login in the add-on configuration directory at `codex-home/auth.json`. The module sets this file to mode `0600` at startup. Never commit, post or log this file.
3. Set the signing key and MQTT credentials to the same module-specific values configured in the Discord transport. Set `channel_keys` to the single approved channel key and `allowed_user_ids` to the one approved user ID.
4. Verify the transport grant contains only that channel and the `reply` operation. Keep `live_modules` disabled while checking module health, `/status`, and the shadow run.
5. Enable the module in the transport's live-module list and set this add-on's `shadow` option to `false` only after confirming the observed result.

## Commands

- `/help` shows this channel's basic use.
- `/status` shows queue and delivery counts without message text or user IDs.
- `/new` starts a fresh Codex thread and abandons interrupted turns or ambiguous deliveries without replaying them.

Normal messages are processed one at a time. The queue is bounded. If a Codex turn or Discord send has an uncertain outcome, it is held for review and is never automatically replayed. `/new` is the explicit recovery action; check Discord manually for a possibly delivered reply before using it.

The HTTP `/healthz` and `/status` endpoints expose status metadata only. The service logs no prompts, replies, tokens, or signing keys.
