"""Protect Orchestrator's foreground/background roles across the selectable palettes."""
import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "app" / "ui"


def rgb(value):
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


def contrast(a, b):
    def luminance(c):
        return sum(weight * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
                   for weight, v in zip((.2126, .7152, .0722), c))
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)


class ThemeContrast(unittest.TestCase):
    def test_shared_token_names_are_not_redefined(self):
        page = (UI / "index.html").read_text()
        style = page.split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertNotRegex(style, r"--(?:muted|accent)\s*:")
        self.assertIn("--orch-muted:color-mix(in srgb,var(--muted-foreground) 85%,var(--foreground))", style)
        self.assertIn("--orch-primary:var(--primary)", style)
        self.assertIn(".btn:hover:not(:disabled){background:var(--accent);color:var(--accent-foreground)}", style)
        self.assertIn(".btn.primary:hover:not(:disabled){background:var(--orch-primary);color:var(--ink)}", style)

    def test_palette_text_is_readable_on_surfaces(self):
        js = (UI / "ih-ui.js").read_text()
        palettes = re.findall(r"(?:light|dark): \[([^]]+)\]", js)
        self.assertEqual(len(palettes), 8)
        for palette in palettes:
            colours = [rgb(c) for c in re.findall(r"#[0-9a-f]{6}", palette)]
            background, foreground, card, raised, muted, _, primary, primary_text = colours
            secondary = tuple(.85 * m + .15 * f for m, f in zip(muted, foreground))
            for surface in (background, card, raised):
                self.assertGreaterEqual(contrast(secondary, surface), 4.5, palette)
                for status in ("#15803d", "#a16207", "#dc2626"):
                    ink = tuple(.7 * f + .3 * c for f, c in zip(foreground, rgb(status)))
                    self.assertGreaterEqual(contrast(ink, surface), 4.5, palette)
            self.assertGreaterEqual(contrast(primary, primary_text), 4.5, palette)
