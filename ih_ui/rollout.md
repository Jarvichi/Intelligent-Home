# UI rollout work plan

Implement in order, using existing ingress boundaries and generic source data:

1. Operations: real connection feedback, searchable add-ons, next steps for stopped or misconfigured services, accessible navigation and mobile status text.
2. Cooking: labelled recipe search, keyboard accessible recipe cards, distinct loading/empty/error states, readable timeline and cooking feedback, reliable editor state.
3. Brain: result counts and area filtering of search, keyboard tabs, restore list focus and position, protect against out-of-order results.
4. Jobs: ingress-only summary of built-in and plugin runs, mode, outcomes and next run where supplied; retain unavailable states and timestamps.
5. Shopping: ingress-only read-only list browsing, search/counts and read status alongside existing Shortcut setup; no new write controls.

For every changed add-on, bump config and manifest versions, runtime version constants where present, and changelog. Check source syntax, asset copies, privacy boundaries and meaningful backend behaviour with synthetic fixtures. Browser and installed-house validation require a separate environment. No deployment or publication is part of this work.

## Recovery status (4 Nov 2026)

- Games: implemented in the 0.6.14 draft; includes library controls, session-scoped per-player preferences, tracker freshness and structured Research rows.
- Operations: implemented in the Orchestrator page; includes add-on search, keyboard navigation, actionable stopped/missing-settings hints and stale-health messaging.
- Cooking: implemented; includes labelled recipe controls, keyboard access, session feedback and preserved recipe identity after edits.
- Brain: implemented; includes result count and area filter, keyboard tab state, race-safe loading, and restoration to the selected row after reading.
- Jobs: implementation is present; includes built-in/plugin run summaries, modes, outcomes, timestamps and explicit unavailable states.
- Shopping: implementation is present; exposes a read-only, ingress-gated list endpoint plus local search/count and unavailable state; existing Shortcut controls remain.

The edits are private and unsubmitted. Verify version metadata and synchronize the optional pattern CSS before handoff. Browser/Safari visual review and live-house validation remain outstanding.
