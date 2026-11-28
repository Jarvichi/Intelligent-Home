# IH UI package

The canonical, dependency-free UI tokens and native Web Component primitives for Intelligent Home add-ons. Pages load these local files from their own ingress origin; no CDN or third-party runtime is used.

Run `ih_ui/sync_ih_ui.sh` after changing canonical assets. Run it with `--check` to detect missing or stale copies in add-ons that contain `app/ui/`. Optional `patterns.css` is copied only to pages that have explicitly adopted `ih-patterns.css`; those pages must also serve and load it. Every IH add-on has an ingress page. Most use the standard status-console sidecar (`app/ui_server.py`); Cooking, Shopping, Orchestrator and Brain serve their richer page from the existing app server and expose the same assets through their Supervisor-only ingress handler.

Use `--shared-only` (also with `--check`) to sync tokens, components and adopted patterns without replacing status-page layouts or scripts that an add-on has customised.

New pages use `tokens.css` and `components.js`, relative asset/API URLs, `ih-` elements, and the requirements in the House Brain note `projects/intelligent-home/ih-ui-style-guide.md`.

The local [UI consistency plan](consistency.md) describes reusable Games patterns and their rollout across add-ons. The normative guide and durable design decisions live in the House Brain note `projects/intelligent-home/ih-ui-style-guide.md`.

## Top bar (`ih-nav`)

Every page puts `<ih-nav data-home-href="/app/32c8114c_ih_orchestrator" data-title="Name"></ih-nav>` as the first child of `<body>` (before `<ih-app>`) and loads `ih-ui.js`. It is sticky, with 44px targets. The hub omits `data-home-href`. Sub-screens call `IHNav.setTrail([{label: 'Parent', onClick: fn}], 'Title')` (empty list returns to the top-level look); `IHNav.goTop(href)` navigates the whole Home Assistant window (tested live: a plain link inside the ingress frame opens Home Assistant nested inside the frame, so links to other `/app/...` pages need `target="_top"`). Remaining adopters: Cooking, Brain, Shopping. Tests: `python3 -m unittest discover -s ih_ui/tests`.

## Look: shadcn/ui

Tokens, buttons, cards, badges, inputs, tabs, dialog, alert, table, switch, skeleton and toast follow the shadcn/ui neutral theme, rewritten as plain CSS and Web Components (no React, Tailwind or build step). Old `--ih-*` names are kept as aliases of the shadcn variables (`--background`, `--foreground`, `--card`, `--muted`, `--border`, `--input`, `--ring`, `--primary`, `--destructive`, `--radius`). Classes: `.btn` (+ `-secondary`, `-outline`, `-ghost`, `-destructive`, `-sm`), `.card` (+ `-header`, `-title`, `-description`, `-footer`), `.badge` (+ `-secondary`, `-outline`, `-destructive`), `.input`, `.label`, `.alert`, `.table`, `.switch`, `.skeleton`, `IHToast.show()`.

Licence: design tokens and component patterns adapted from shadcn/ui, Copyright (c) 2023 shadcn, MIT licence (https://github.com/shadcn-ui/ui). Fonts and icons stay local; no CDN.

## Shared interactions

`IHUI.menu(details, onClose)` gives a native details menu outside-click and Escape dismissal, restoring focus to its summary on Escape. It returns `close(focus)` and `destroy()`; call `destroy()` when removing the menu. Page code owns the content and can use `onClose` to close nested disclosures.

`IHUI.tabs(tablist, panels)` binds existing `role="tab"` buttons and their matching panels in the same order. It maintains `aria-selected`, roving keyboard focus and panel visibility, and supports Left, Right, Home and End. Give each tab `aria-controls` and each panel `aria-labelledby`. It returns `select(index, focus)`; keep the nodes in place when switching tabs so entered form values survive.
