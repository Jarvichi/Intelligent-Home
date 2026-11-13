import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import options_env as E


class OptionsEnv(unittest.TestCase):
    def test_defaults(self):
        o = {**E.DEFAULTS}
        self.assertEqual(E.models(o), ["moondream"])
        self.assertEqual(E.env_lines(o), ["export OLLAMA_KEEP_ALIVE=30s", "export OLLAMA_MAX_LOADED_MODELS=1", "export OLLAMA_NUM_PARALLEL=1"])

    def test_bad_model_names_are_dropped(self):
        o = {**E.DEFAULTS, "models": ["moondream", "x; rm -rf /", "$(id)", "nomic-embed-text", "qwen2.5:1.5b", ""]}
        self.assertEqual(E.models(o), ["moondream", "nomic-embed-text", "qwen2.5:1.5b"])

    def test_all_bad_falls_back(self):
        self.assertEqual(E.models({"models": ["a b", "`x`"]}), ["moondream"])

    def test_values_are_checked_not_passed_through(self):
        o = {**E.DEFAULTS, "keep_alive": "5m; echo hi", "max_loaded_models": 99, "num_parallel": "x"}
        lines = E.env_lines(o)
        self.assertEqual(lines[0], "export OLLAMA_KEEP_ALIVE=30s")
        self.assertEqual(lines[1], "export OLLAMA_MAX_LOADED_MODELS=3"); self.assertEqual(lines[2], "export OLLAMA_NUM_PARALLEL=1")
        self.assertEqual(E.env_lines({**E.DEFAULTS, "keep_alive": "5m"})[0], "export OLLAMA_KEEP_ALIVE=5m")

    def test_missing_file_gives_defaults(self):
        self.assertEqual(E.load("/nonexistent/options.json"), E.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
