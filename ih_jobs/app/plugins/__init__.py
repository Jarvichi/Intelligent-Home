"""Plugins: one file per plugin. Each exposes NAME, SCHEDULE ({"kind":"daily","at":"HH:MM"} or {"kind":"every","minutes":N}), DEFAULTS (dict), run(ctx).
House values (ids, entities, times, texts) are NEVER in code: they come from the plugin's `settings` JSON in the add-on options."""
