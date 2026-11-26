"""Every ih_* add-on must ship an ingress page (a headless add-on uses the status page from ih_ui/template).
ALLOW_NO_UI lists add-ons still waiting for theirs; it can only shrink: an entry that has gained a page fails the test.
Run: python3 -m unittest discover -s tests"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW_NO_UI = {
    'ih_room_insights', 'ih_parcel_check', 'ih_work_runner', 'ih_lifestyle_groceries',
    'ih_kokoro', 'ih_files',
    'ih_ollama',   # found by this test; also needs a status page (no login on Ollama, so ingress only)
}


def has_ingress(config_text):
    on = re.search(r'^ingress:\s*true\s*$', config_text, re.M)
    port = re.search(r'^ingress_port:\s*\d+\s*$', config_text, re.M)
    return bool(on and port)


def addon_configs(root=ROOT):
    """ih_* folders that have a config.yaml (ih_ui and ihlib are shared packages and have none)."""
    return {p.parent.name: p for p in sorted(root.glob('ih_*/config.yaml'))}


def missing_ui(root=ROOT, allow=ALLOW_NO_UI):
    cfgs = addon_configs(root)
    return sorted(n for n, p in cfgs.items() if not has_ingress(p.read_text()) and n not in allow)


class EveryAddonHasUi(unittest.TestCase):
    def test_all_addons_have_ingress_or_are_listed(self):
        self.assertEqual(missing_ui(), [], 'add-on(s) without an ingress page: add one (ih_ui/template) or, only if a page task is open, list it in ALLOW_NO_UI')

    def test_allow_list_only_shrinks(self):
        cfgs = addon_configs()
        stale = sorted(n for n in ALLOW_NO_UI if n not in cfgs or has_ingress(cfgs[n].read_text()))
        self.assertEqual(stale, [], 'these now have a page (or are gone): remove them from ALLOW_NO_UI')

    def test_shared_packages_are_skipped(self):
        self.assertNotIn('ih_ui', addon_configs())
        self.assertNotIn('ihlib', addon_configs())

    def test_guard_catches_a_dummy_addon(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dummy = Path(d) / 'ih_dummy'
            dummy.mkdir()
            (dummy / 'config.yaml').write_text('name: x\nslug: ih_dummy\nstartup: application\n')
            self.assertEqual(missing_ui(Path(d), allow=set()), ['ih_dummy'])
            self.assertEqual(missing_ui(Path(d), allow={'ih_dummy'}), [])
            (dummy / 'config.yaml').write_text('slug: ih_dummy\ningress: true\ningress_port: 8800\n')
            self.assertEqual(missing_ui(Path(d), allow=set()), [])


if __name__ == '__main__':
    unittest.main()
