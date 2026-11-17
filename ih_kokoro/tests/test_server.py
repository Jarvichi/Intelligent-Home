import importlib.util, json, os, sys, tempfile, unittest
spec = importlib.util.spec_from_file_location("server", os.path.join(os.path.dirname(__file__), "..", "app", "server.py"))
server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)


class Options(unittest.TestCase):
    def load(self, obj):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(obj, f)
        try:
            return server.load_options(f.name)
        finally:
            os.unlink(f.name)

    def test_defaults_when_missing(self):
        self.assertEqual(server.load_options("/nonexistent"), server.DEFAULTS)

    def test_bad_values_fall_back_and_speed_is_clamped(self):
        o = self.load({"default_voice": "../etc/passwd", "speed": 9})
        self.assertEqual(o["default_voice"], "bm_george")
        self.assertEqual(o["speed"], 1.5)

    def test_good_values_kept(self):
        o = self.load({"default_voice": "af_heart", "speed": 0.9})
        self.assertEqual((o["default_voice"], o["speed"]), ("af_heart", 0.9))


class Voices(unittest.TestCase):
    avail = {"bm_george", "af_heart"}

    def test_pick(self):
        self.assertEqual(server.pick_voice("af_heart", self.avail, "bm_george"), "af_heart")
        self.assertEqual(server.pick_voice("nope", self.avail, "bm_george"), "bm_george")
        self.assertEqual(server.pick_voice(None, self.avail, "zz_missing"), "af_heart")

    def test_lang(self):
        self.assertEqual(server.lang_of("bm_george"), "en-gb")
        self.assertEqual(server.lang_of("af_heart"), "en-us")


if __name__ == "__main__":
    unittest.main()
