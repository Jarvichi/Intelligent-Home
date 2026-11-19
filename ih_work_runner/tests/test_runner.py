"""Runner tests with a mocked worker and no claude binary (nothing is claimed, nothing is run)."""
import json, os, sys, tempfile, unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import runner as R


def opts(**k):
    o = {"mode": "dry", "stop": False, "work_token": "t" * 10, "claude_oauth_token": "", "mcp_config_file": "", "work_url": "", "idle_max_s": 1, "idle_check_s": 1}
    o.update(k); return o


class Runner(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def touch(self, name): open(os.path.join(self.d, name), "w").close()

    def test_read_options_defaults_to_dry(self):
        f = os.path.join(self.d, "o.json"); json.dump({"mode": "bogus"}, open(f, "w"))
        self.assertEqual(R.read_options(f)["mode"], "dry")
        self.assertEqual(R.read_options(os.path.join(self.d, "none"))["mode"], "dry")
        json.dump({"mode": "live", "stop": True}, open(f, "w")); o = R.read_options(f)
        self.assertEqual((o["mode"], o["stop"]), ("live", True))

    def test_live_needs_option_and_file_and_no_stop(self):
        self.assertFalse(R.is_live(opts(mode="live"), self.d))   # no LIVE file
        self.touch("LIVE")
        self.assertFalse(R.is_live(opts(mode="dry"), self.d))
        self.assertTrue(R.is_live(opts(mode="live"), self.d))
        self.assertFalse(R.is_live(opts(mode="live", stop=True), self.d))
        self.touch("STOP")
        self.assertFalse(R.is_live(opts(mode="live"), self.d))
        self.assertIn("STOP", R.pause_reason(opts(), self.d))

    def test_pass_runs_worker_dry_or_live(self):
        calls = []
        run = lambda args, **kw: (calls.append((args, kw["cwd"])), SimpleNamespace(returncode=0))[1]
        self.assertIn("dry", R.run_pass(opts(), self.d, run)); self.assertEqual(calls[-1][0][-1], "--once")
        self.touch("LIVE")
        self.assertIn("live", R.run_pass(opts(mode="live"), self.d, run)); self.assertEqual(calls[-1][0][-1], "--live")
        self.assertEqual(calls[-1][1], self.d)

    def test_env_carries_tokens_only_in_memory(self):
        e = R.build_env(opts(claude_oauth_token="c" * 30, work_url="http://x"), base={"PATH": "/bin"})
        self.assertEqual((e["WORK_TOKEN"], e["CLAUDE_CODE_OAUTH_TOKEN"], e["WORK_URL"]), ("t" * 10, "c" * 30, "http://x"))
        self.assertEqual(e["HOME"], R.HOME)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", R.build_env(opts(), base={}))

    def test_preflight_lists_problems(self):
        p = R.preflight(opts(work_token=""), self.d, which=lambda b: None)
        self.assertTrue(any("queue_worker.py" in x for x in p)); self.assertTrue(any("claude" in x for x in p)); self.assertTrue(any("work_token" in x for x in p))
        for f in ("queue_worker.py", "task_flow.py", "tasks_api.py", "work_queue.py", "wait_for_work.py"): self.touch(f)
        p = R.preflight(opts(), self.d, which=lambda b: "/bin/" + b)
        self.assertFalse(any("missing" in x or "not found" in x or "work_token" in x for x in p))

    def test_mcp_config_copied_with_private_mode(self):
        src = os.path.join(self.d, "mcp.json"); json.dump({"mcpServers": {}}, open(src, "w")); home = os.path.join(self.d, "home")
        self.assertEqual(R.install_mcp_config(opts(mcp_config_file=src), home), "")
        self.assertEqual(oct(os.stat(os.path.join(home, ".claude.json")).st_mode & 0o777), "0o600")
        self.assertIn("no mcp_config_file", R.install_mcp_config(opts(), home))
        self.assertIn("not found", R.install_mcp_config(opts(mcp_config_file=src + "x"), home))

    def test_wait_returns_early_when_job_waits(self):
        n = []
        self.assertTrue(R.wait(opts(idle_max_s=60), self.d, sleep=lambda s: n.append(s), waiting=lambda o, w: len(n) >= 2))
        self.assertEqual(len(n), 2)
        self.assertFalse(R.wait(opts(idle_max_s=0), self.d, sleep=lambda s: None, waiting=lambda o, w: True))


if __name__ == "__main__":
    unittest.main()
