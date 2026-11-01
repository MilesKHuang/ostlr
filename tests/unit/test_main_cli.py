"""T1 - the CLI skeleton: envelope, fixture loader, config, paths.

The load-bearing checks here are the ones a verifier runs:

  * `--once --json` prints exactly one JSON object and nothing else on stdout,
  * `--fixture` actually reads the recorded snapshot (the loader saw its rows
    and its `now`), rather than silently returning an empty envelope,
  * a broken fixture is a usage error with a reason, not a passing empty run,
  * the config and path overrides keep a test out of the real user profile.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from ostlr import __main__ as cli
from ostlr import config as config_mod
from ostlr import paths, schema

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures"
SMALL = FIXTURES / "snapshot_small.json"


def run_cli(argv):
    """Run main() and capture (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestEnvelope(unittest.TestCase):
    def test_empty_envelope_shape(self):
        env = schema.empty_envelope()
        self.assertEqual(env["v"], 1)
        self.assertEqual(env["rows"], [])
        self.assertEqual(set(env["header"]), set(schema.HEADER_KEYS))
        self.assertEqual(env["header"]["sessions"], 0)

    def test_header_keys_exact(self):
        self.assertEqual(
            schema.HEADER_KEYS,
            ("waiting", "running", "running_inferred", "error", "error_inferred",
             "done", "idle", "unknown", "starting", "sessions"),
        )

    def test_sessions_is_sum_of_buckets(self):
        header = schema.empty_header()
        header.update({"waiting": 1, "running": 2, "unknown": 3})
        env = schema.envelope(header, [])
        self.assertEqual(env["header"]["sessions"], 6)

    def test_bucket_for(self):
        self.assertEqual(schema.bucket_for("blocked"), "waiting")
        self.assertEqual(schema.bucket_for("working"), "running")
        self.assertEqual(schema.bucket_for("done"), "done")
        self.assertEqual(schema.bucket_for("idle"), "idle")
        self.assertEqual(schema.bucket_for("error"), "error")
        self.assertEqual(schema.bucket_for(None), "unknown")
        self.assertEqual(schema.bucket_for("nonsense"), "unknown")
        # a transient is counted in `starting` and in no state bucket
        self.assertEqual(schema.bucket_for("working", "starting"), "starting")

    def test_rank_order(self):
        self.assertEqual(schema.rank_for("blocked"), 0)
        self.assertEqual(schema.rank_for("error"), 1)
        self.assertEqual(schema.rank_for("working"), 2)
        self.assertEqual(schema.rank_for("unknown"), 3)
        self.assertEqual(schema.rank_for("done"), 4)
        self.assertEqual(schema.rank_for("idle"), 5)
        self.assertEqual(schema.rank_for("nonsense"), 3)

    def test_clip(self):
        self.assertEqual(schema.clip("short"), "short")
        self.assertEqual(schema.clip(None), "")
        clipped = schema.clip("x" * 500, 120)
        self.assertEqual(len(clipped), 120)
        self.assertTrue(clipped.endswith("\u2026"))


class TestOnceJson(unittest.TestCase):
    def test_once_json_is_one_object(self):
        code, out, err = run_cli(["--once", "--json"])
        self.assertEqual(code, 0, err)
        # Exactly one JSON object, nothing else on stdout.
        self.assertEqual(out.count("\n"), 1, out)
        env = json.loads(out)
        self.assertEqual(env["v"], 1)
        # The live pipeline now produces real rows; the shape is what matters
        # here (the deterministic row check is the fixture golden).
        self.assertIsInstance(env["rows"], list)
        self.assertEqual(set(schema.HEADER_KEYS) - set(env["header"]), set())
        self.assertEqual(env["header"]["sessions"],
                         sum(env["header"][b] for b in schema.BUCKETS))

    def test_once_human_output(self):
        code, out, err = run_cli(["--once"])
        self.assertEqual(code, 0, err)
        self.assertIn("sessions:", out)
        self.assertIn("rows:", out)

    def test_json_without_once_is_a_usage_error(self):
        code, out, err = run_cli(["--json"])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("require --once", err)


class TestFixtureLoader(unittest.TestCase):
    def test_loader_sees_the_fixture(self):
        fixture = cli.load_fixture(str(SMALL))
        self.assertEqual(fixture["now"], 1793415300)
        self.assertEqual(len(fixture["cim"]), 6)
        self.assertEqual(fixture["cim"][0]["ProcessId"], 100)
        self.assertEqual(len(fixture["l2"]), 3)
        self.assertEqual(fixture["l2"][0]["state"], "blocked")
        self.assertEqual(fixture["titles"], {"200": "codex | running tests"})
        self.assertIsNone(fixture["status_dir_error"])

    def test_once_json_with_fixture(self):
        # T1's DoD: `--fixture` prints exactly that fixture's rows. The full
        # field-by-field comparison lives in the golden (test_view_model.py);
        # this is the CLI-level proof that the input is no longer thrown away.
        code, out, err = run_cli(["--once", "--json", "--fixture", str(SMALL)])
        self.assertEqual(code, 0, err)
        env = json.loads(out)
        self.assertEqual(env["v"], 1)
        self.assertEqual([r["pid"] for r in env["rows"]], [100, 200, 400, 300])
        self.assertEqual(env["header"]["sessions"], 4)
        self.assertEqual(env["header"]["waiting"], 1)
        self.assertEqual(env["header"]["running_inferred"], 1)
        self.assertEqual(env["header"]["done"], 1)
        self.assertEqual(env["header"]["idle"], 1)

    def test_missing_fixture_is_a_usage_error(self):
        code, out, err = run_cli(["--once", "--json", "--fixture", "nope.json"])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("fixture not found", err)

    def test_garbage_fixture_is_a_usage_error(self):
        bad = FIXTURES / "cim_garbage.json"
        code, out, err = run_cli(["--once", "--json", "--fixture", str(bad)])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("not JSON", err)

    def test_fixture_must_be_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(cli.FixtureError):
                cli.load_fixture(str(path))

    def test_fixture_bad_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_now.json"
            path.write_text('{"now": "yesterday"}', encoding="utf-8")
            with self.assertRaises(cli.FixtureError):
                cli.load_fixture(str(path))

    def test_fixture_defaults_are_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimal.json"
            path.write_text('{"now": 5}', encoding="utf-8")
            fixture = cli.load_fixture(str(path))
            self.assertEqual(fixture["now"], 5)
            self.assertEqual(fixture["cim"], [])
            self.assertEqual(fixture["l2"], [])
            self.assertEqual(fixture["titles"], {})
            self.assertIsNone(fixture["status_dir_error"])


class TestSelftestAndStubs(unittest.TestCase):
    def test_selftest_exits_zero(self):
        code, out, err = run_cli(["--selftest"])
        self.assertEqual(code, 0, err)
        self.assertIn("PASS paths config:", out)
        self.assertIn("PASS config loaded from", out)

    def test_panel_wiring_without_a_window(self):
        """The panel path is entered, but no window is opened.

        `main([])` used to reach `run_panel` and block in the Tk mainloop, which
        hung the whole suite. The runner is injectable now, so the wiring is
        asserted here and the window is never created.
        """
        seen = {}

        def fake_runner(cfg):
            seen["cfg"] = cfg
            return 42

        code = cli.main([], panel_runner=fake_runner)
        self.assertEqual(code, 42)
        self.assertIsNotNone(seen.get("cfg"))

    def test_no_panel_env_refuses_to_open_a_window(self):
        old = os.environ.get(cli.ENV_NO_PANEL)
        os.environ[cli.ENV_NO_PANEL] = "1"
        try:
            code, out, err = run_cli([])
        finally:
            if old is None:
                os.environ.pop(cli.ENV_NO_PANEL, None)
            else:
                os.environ[cli.ENV_NO_PANEL] = old
        self.assertEqual(code, cli.EXIT_NOT_BUILT)
        self.assertIn(cli.ENV_NO_PANEL, err)

    def test_run_delegates_to_the_wrapper(self):
        # T8 landed: `run -- <cmd>` is the producer wrapper now, so a command
        # that cannot be spawned must report a spawn failure (127) and name the
        # command, not claim the feature is missing.
        code, out, err = run_cli(["run", "--", "no-such-agent-xyz"])
        self.assertEqual(code, 127)
        self.assertIn("no-such-agent-xyz", err)


class TestPaths(unittest.TestCase):
    def test_env_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = {k: os.environ.get(k) for k in
                   (paths.ENV_CONFIG, paths.ENV_STATUS_DIR, paths.ENV_LOG_DIR)}
            try:
                os.environ[paths.ENV_CONFIG] = str(Path(tmp) / "cfg" / "config.toml")
                os.environ[paths.ENV_STATUS_DIR] = str(Path(tmp) / "status")
                os.environ[paths.ENV_LOG_DIR] = str(Path(tmp) / "logs")
                self.assertEqual(paths.config_path(), Path(tmp) / "cfg" / "config.toml")
                self.assertEqual(paths.config_dir(), Path(tmp) / "cfg")
                self.assertEqual(paths.status_dir(), Path(tmp) / "status")
                self.assertEqual(paths.log_dir(), Path(tmp) / "logs")
                self.assertEqual(paths.status_file(1234), Path(tmp) / "status" / "1234.json")
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_defaults_are_under_appdata(self):
        old = {k: os.environ.pop(k, None) for k in
               (paths.ENV_CONFIG, paths.ENV_STATUS_DIR, paths.ENV_LOG_DIR)}
        try:
            self.assertIn("ostlr", str(paths.config_path()))
            self.assertIn("ostlr", str(paths.status_dir()))
            self.assertIn("ostlr", str(paths.log_dir()))
        finally:
            for key, value in old.items():
                if value is not None:
                    os.environ[key] = value


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = config_mod.Config()
        self.assertEqual(cfg.tick_s, 5)
        self.assertEqual(cfg.l2_stale_s, 90)
        self.assertEqual(cfg.done_retain_s, 600)
        self.assertEqual(cfg.spawn_error_retain_s, 30)
        self.assertEqual(cfg.cim_timeout_ms, 3000)
        self.assertEqual(cfg.list_cap, 60)
        self.assertEqual(cfg.clip_chars, 120)
        self.assertEqual(cfg.last_dirs, [])
        self.assertEqual(cfg.last_agents, [])

    def test_missing_file_keeps_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_mod.load(str(Path(tmp) / "absent.toml"))
            self.assertEqual(cfg.tick_s, 5)
            self.assertEqual(cfg.load_error, "")

    def test_bad_toml_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.toml"
            path.write_text("tick_s = = 5\n", encoding="utf-8")
            cfg = config_mod.load(str(path))
            self.assertEqual(cfg.tick_s, 5)
            self.assertIn("invalid TOML", cfg.load_error)

    def test_wrong_types_fall_back_per_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.toml"
            path.write_text(
                'tick_s = "soon"\nl2_stale_s = 45\nlist_cap = -3\n'
                'last_dirs = ["D:/a", "D:/a", "", 7, "D:/b", "D:/c", "D:/d"]\n',
                encoding="utf-8",
            )
            cfg = config_mod.load(str(path))
            self.assertEqual(cfg.tick_s, 5)          # bad type -> default
            self.assertEqual(cfg.l2_stale_s, 45)     # good value kept
            self.assertEqual(cfg.list_cap, 60)       # negative -> default
            self.assertEqual(cfg.last_dirs, ["D:/a", "D:/b", "D:/c"])  # dedup, cap 3

    def test_save_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            cfg = config_mod.Config(tick_s=7, last_dirs=["D:/ws"], last_agents=["claude"])
            self.assertTrue(config_mod.save(cfg, str(path)))
            self.assertTrue(path.exists())
            self.assertFalse((Path(tmp) / "config.toml.tmp").exists())
            again = config_mod.load(str(path))
            self.assertEqual(again.tick_s, 7)
            self.assertEqual(again.last_dirs, ["D:/ws"])
            self.assertEqual(again.last_agents, ["claude"])

    def test_remember_is_most_recent_first(self):
        cfg = config_mod.Config()
        config_mod.remember(cfg, directory="D:/a", agent="claude")
        config_mod.remember(cfg, directory="D:/b", agent="codex")
        config_mod.remember(cfg, directory="D:/a", agent="claude")
        self.assertEqual(cfg.last_dirs, ["D:/a", "D:/b"])
        self.assertEqual(cfg.last_agents, ["claude", "codex"])


if __name__ == "__main__":
    unittest.main()
