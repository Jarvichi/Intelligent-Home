# Changelog

## [0.1.33] - 2026-11-05

### Changed
- Ingress status page now uses the shared Games-style layout (page heading with service health, status and capabilities cards, collapsible About).

## [0.1.32] - 2026-11-04
- Send a visible acknowledgement before starting normal prompts or `/code` work, and a delayed progress note if a turn lasts over a minute.
- Report safe failure categories and clear `/status` guidance when a turn or reply needs review.
- Keep model execution isolated from the bus and status service; preserve uncertain-turn handling.

## [0.1.31] - 2026-11-04
- Make `/code rebase` fetch current public IH main instead of relying on the source snapshot packaged when the add-on was built.
- Explain that submit conflicts are file-specific and direct the owner to rebase before retrying.

## [0.1.30] - 2026-11-04
- Allow `/code rebase --continue` to verify Git's recorded original branch while a conflicted rebase has detached `HEAD`.

## [0.1.29] - 2026-11-04
- Add `/code rebase` to checkpoint local edits, preserve a complete pre-rebase backup, and rebase `codex/work` onto the latest bundled public IH source before submission.
- Add `/code rebase --continue` for resolving conflicts and continuing the rebase; document the workflow in `/help`.

## [0.1.28] - 2026-11-04
- Allow only the fixed Home Assistant Supervisor network (`172.30.x.x`) in code submission scans, while retaining checks for other private addresses and secret-like values.
- Let `/code refresh` archive the existing `codex/work` checkout, including local edits or commits, before reseeding; clarify that `/code submit` leaves the local checkout intact.

## [0.1.27] - 2026-11-03
- Derive the bus heartbeat, MCP client, and health status version from `app/manifest.json` so runtime reporting stays aligned with the installed add-on version.

## [0.1.26] - 2026-11-03
- Add `/code refresh` for clean, unchanged workspaces whose source snapshot is stale. It refuses dirty workspaces and branches with commits, reseeds from the bundled IH source, and preserves the prior workspace as a local backup.

## [0.1.25] - 2026-11-03
- Use the Codex bundled Bubblewrap with the corrected AppArmor transition. System Bubblewrap 0.13.1 uses an error format that prevents Codex 0.163.0 from recognizing restricted-container proc mount failures and invoking its recovery path.

## [0.1.24] - 2026-11-03
- Correct Bubblewrap AppArmor transitions to the fully qualified loaded child profile; nested relative transitions failed with profile transition not found.

## [0.1.23] - 2026-11-03
- Put the verified system Bubblewrap in Codex’s executable search directory and log a secret-free launcher probe for path, execute permission, and required options.

## [0.1.22] - 2026-11-03
- Set an explicit trusted executable PATH for Codex and its filesystem sandbox helper so it can discover the direct-path system Bubblewrap launcher.

## [0.1.21] - 2026-11-03
- Build verified upstream Bubblewrap 0.13.1 as the preferred system launcher so Codex can execute it by pathname under the dedicated child AppArmor profile, avoiding bundled `/proc/self/fd` execution. Keep workspace-write scope, command networking disabled, and denied escalations.

## [0.1.20] - 2026-11-03
- Transition Codex's bundled Bubblewrap executable into the dedicated Bubblewrap AppArmor profile so its loopback setup receives the approved namespace capabilities.

## [0.1.19] - 2026-11-03
- Allow the Codex sandbox helper's namespace capability checks while keeping its mount operations denied; grant `SYS_PTRACE` and `SETPCAP` only inside the Bubblewrap child AppArmor profile.

## [0.1.18] - 2026-11-03
- Allow Bubblewrap's isolated network namespace to configure loopback by granting `NET_ADMIN` only in the Bubblewrap child AppArmor profile; command networking remains disabled.

## [0.1.17] - 2026-11-03
- Reissue the scoped Bubblewrap AppArmor and capability configuration with a fresh add-on version so Home Assistant can detect the release.


## [0.1.16] - 2026-11-03
- Run filesystem-restricted Codex commands with Bubblewrap under a dedicated AppArmor child profile that grants only the sandbox launcher mount and namespace permissions.
- Keep Codex workspace-write scope, disabled command networking, and denied escalations.


## [0.1.15] - 2026-11-03
- Use Codex's Landlock sandbox backend because Home Assistant's container blocks Bubblewrap mount propagation; keep workspace-write scope and sandbox network disabled.


## [0.1.14] - 2026-11-03
- Install the distribution `bubblewrap` package used by Codex workspace-write sandboxing on Linux, instead of relying on the bundled user-namespace helper.

## [0.1.13] - 2026-11-03
- Accept up to three bounded JPEG, PNG, or WebP attachments over the encrypted Discord bus and pass them to Codex as tool-level image input.
- Validate Discord CDN URLs, response MIME and file signatures; cap each image at 4 MiB and total data at 8 MiB. Store bytes only in the private local queue until the turn ends.

## [0.1.12] - 2026-11-03
- Move the ingress UI to port 8812 so it cannot race the loopback Home Assistant MCP server on port 8811.
- Run the health UI and worker as supervised child processes so a worker startup failure stops the add-on instead of leaving a misleading live UI process.
- Report the deployed add-on version in the signed bus heartbeat and status.

## [0.1.11] - 2026-11-03

### Changed
- Health and status port moved from 8804 to 8815 (8804 belongs to the Gemini AI agent).

### Fixed
- Make `/status` tolerate older or stub status data without `claude_handoffs`; the command reports “no Claude handoffs” instead of raising `KeyError`.

## [0.1.9] - 2026-11-02
- Add a read-only tool for the explicitly approved live `todo.claude_to_do` list.
- Allow access-limited HA, Brain, host, and IH task requests to be handed off to Claude with a concise user-request summary.
- Keep HA tool results and Brain content out of Claude handoffs.

## [0.1.8] - 2026-11-02
- Default the code-only Claude handoff to the fixed `claude_review` channel key so enabling it does not depend on adding an option to an already-installed add-on schema.

## [0.1.7] - 2026-11-02
- Add a durable, one-way Claude handoff for code tasks that need host context or a final pass; send a concise code-only summary and optional public PR URL.
- Keep uncertain handoff sends from replaying automatically and expose handoff state in status.

## [0.1.6] - 2026-11-02
- Re-trigger image publishing after the first 0.1.5 workflow skipped its build matrix because the version bump was in an earlier commit in the same push.
- Keep repository ownership derived from the add-on image configuration so generic public source passes the house privacy scan.

## [0.1.5] - 2026-11-02
- Add filtered Home Assistant entity search/state/history tools through a loopback MCP server; only the add-on process handles the Supervisor token.
- Add one-step light/switch action proposals that execute only after the authorized owner confirms with `/approve CODE`; uncertain requests are never retried automatically.
- Describe the worker's coding workspace and limited HA capabilities accurately.

## [0.1.4] - 2026-11-02
- Auto-approve only the explicitly enabled read-only Brain search and read MCP tools, so the non-interactive worker can use them while all other approvals remain denied.

## [0.1.3] - 2026-11-02
- Start a fresh Codex thread once when the Brain tool-access policy changes, so older thread context cannot retain the previous no-Brain instructions.
- Mark the shareable Brain MCP server explicitly enabled in Codex configuration.

## [0.1.2] - 2026-11-02
- Add an optional authenticated Brain MCP connection, restricted server-side to search/read of explicitly share:true notes. No Brain writes, inbox, private notes, attachments, or general Home Assistant access.

## [0.1.1] - 2026-11-02
- Encrypt and authenticate private inbound events and outbound reply text on the MQTT bus; reject unauthenticated ciphertext.

## [0.1.0] - 2026-11-02
- Add private Codex worker using the existing signed IH Discord transport.
- Persist incoming messages before transport acknowledgement and hold uncertain turn or delivery outcomes for manual recovery.
- Restrict the module to one configured channel and user, a private scratch workspace, denied escalations, and disabled tool network access.

## 0.1.10
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
