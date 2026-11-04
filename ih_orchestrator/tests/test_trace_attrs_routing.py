"""Routing attrs are allowlisted; model ids with / are fine; text-like values are dropped."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import trace_store


class RoutingAttrs(unittest.TestCase):
    def test_new_keys_accepted(self):
        a = trace_store.clean_attrs({"capability": "general_qa", "classified": 1, "agent": "groq", "model": "openai/gpt-oss-120b",
                                     "score": 741, "candidates": 3, "tries": 1, "chars": 20})
        self.assertEqual(len(a), 8)
        self.assertEqual(a["model"], "openai/gpt-oss-120b")

    def test_results_attr(self):
        self.assertEqual(trace_store.clean_attrs({"results": 3}), {"results": 3})
        self.assertEqual(trace_store.clean_attrs({"results": -1}), {})

    def test_model_variants(self):
        for m in ("gemini-2.5-flash", "meta-llama/llama-3.3-70b:free", "mistral_small.v1"):
            self.assertEqual(trace_store.clean_attrs({"model": m}), {"model": m})

    def test_text_like_values_rejected(self):
        for k, v in (("model", "what is the weather today"), ("model", "x" * 65), ("model", "a b"), ("agent", "has space"), ("agent", "a/b"),
                     ("capability", "Tell me everything!"), ("tries", -1), ("tries", True)):
            self.assertEqual(trace_store.clean_attrs({k: v}), {}, (k, v))

    def test_slash_only_allowed_for_model(self):
        self.assertEqual(trace_store.clean_attrs({"sender": "a/b"}), {})


if __name__ == "__main__":
    unittest.main()
