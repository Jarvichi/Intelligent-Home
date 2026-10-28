# Intelligent Home - Orchestration - Inbox

Orchestration inbox: durable queue of messages on chosen Discord channel keys (bus module of ih_discord), eye-emoji acknowledgement, token-protected read API. Never answers in the channel.

- **Family:** Discord. Module of the `ih_discord` transport.
- **Needs:** `ih_discord`, mqtt
- **Starts safe:** shadow mode, answers and posts nothing until you switch it on
- Set-up, options and troubleshooting: [DOCS.md](DOCS.md)
- Version history: [CHANGELOG.md](CHANGELOG.md)
