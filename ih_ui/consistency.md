# Intelligent Home UI consistency plan

Use the canonical tokens and components for every add-on. Keep page-specific content and layouts while sharing controls, spacing, typography and state language. Games established the optional `patterns.css` layout classes; Operations, Cooking, Brain, Jobs and Shopping are the next adopters.

## Shared patterns available now

| Pattern | Use |
| --- | --- |
| `.ih-page-heading` | Page title and separate service health indicator. |
| `.ih-card-grid`, `.ih-detail-stack` | Aligned cards and readable expanded sections. |
| `.ih-toolbar` | Visible labels, search, filters and sort controls with 44px targets. |
| `.ih-disclosure` | Native button with accessible name, `aria-expanded` and `aria-controls`. |
| `.ih-metadata`, `.ih-empty-inline` | Secondary facts and brief empty sections. |
| `.ih-freshness` | Text describing current, stale, unavailable or unknown tracker data; colour supplements the text. |

Keep service health separate from data freshness: a healthy service may hold old or unavailable provider data. Display the supplied update timestamp, and call data current only when the provider explicitly reports a successful freshness check. Label it “current at last check”; it is not a live guarantee. Do not invent an age threshold for providers that do not define one. Unknown freshness and unconfigured trackers need distinct text. Render untrusted values as text, never as HTML.

## Games behaviour

- Library search and platform filters combine; clear filters restores the list without changing the selected sort. Sort options are last played, title and playtime, with unavailable values last and title as the tie-breaker.
- Library preferences and expanded cards are remembered per player for the current page session. Reloading resets them. Switching players must not let a slower response replace the active view.
- Tracker details load on expansion. Library summary cards omit tracker names and provider-freshness sentences. Keep provider state and failures in expanded details, with only the small refresh time beside the details control. Short-lived cached details reduce repeat requests.
- Research rows show title, tracker/recommendation category, research question, status and next step. The queue does not establish that investigation has started: say that the item is listed for review and that investigation status is not reported. Retain a generic category for unrecognised requests.
- Loading, genuinely empty, no filter matches and unavailable data each have their own message. A failed read must not appear as an empty queue.

## Proposed rollout

1. Adopt page headings, labelled toolbars and disclosure controls as each add-on is updated. Serve the optional stylesheet locally and check it with the sync script.
2. Apply the same loading, empty, error and freshness language to richer pages. Each provider must supply its own freshness contract before the UI labels data current.
3. Review navigation, primary action placement, cards and metadata across add-ons at narrow widths and in both colour schemes. Retain domain-specific content while removing duplicate layout rules where shared patterns suffice.

Before rolling out a page, check keyboard navigation, visible focus, disclosure state, long content, 320px width, reduced motion, both colour schemes and failure states. Games and the 4 Nov 2026 Operations, Cooking, Brain, Jobs and Shopping work use these patterns; remaining add-on pages still need an inventory and rollout.

The Brain style guide is maintained in the House Brain note `projects/intelligent-home/ih-ui-style-guide.md`; its data-heavy screens section records the tracker freshness and Research queue rules derived from Games.


## Visual acceptance for every UI release

Games is the visual reference for the shared IH system. Native Home Assistant surfaces use the House theme and house components with the same hierarchy and control conventions. Review pages together as one product, including their interaction states.

- Use canonical tokens and existing controls for typography, spacing, surfaces, borders, focus and status. Make a repeated pattern once in the shared layer; compose domain-specific content around it.
- Keep forms readable with visible labels, short hints, grouped fields and one clear primary action. Use compact filters with a visible result count and a clear reset action.
- Check the actual rendered interface at phone and desktop widths, in light and dark themes. Check long labels, large lists, empty results, loading, errors, disabled controls and saved feedback.
- Check keyboard order, focus visibility and touch targets, plus search/filter/sort combinations and expanded details. Preserve every existing data field, action and access boundary.
- Review before/after screenshots for hierarchy, density, alignment and visual balance. A successful build or an overflow check alone does not establish visual quality.
- Record the tested surfaces, modes, dimensions and remaining gaps. Keep physical iOS Safari status separate from Chromium phone emulation.
- Publish, install and verify one issue before moving to the next. Do not retire a dashboard until the replacement preserves its behavior and access and has been reviewed live.
