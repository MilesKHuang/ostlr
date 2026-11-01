"""T14 - the launcher: pre-check, memory, wrapper argv, hint, start.

Everything here is headless. The dialog is a thin Tk shell over these pure
functions, so the decisions (is the agent installed, what is the fix text, what
argv runs, which hint names the recipe) are proven without a window, and the
launch itself is proven with a double that records the cwd and the argv.
"""

import os
import tempfile
import unittest
from pathlib import Path

from ostlr import agents as agents_mod
from ostlr import config as config_mod
from ostlr import launcher


def fake_which(present):
    """A `which` double: only the names in `present` resolve."""
    def _which(command):
        if command in present:
            return "C:/bin/%s.exe" % command
        return None
    return _which


class TestPrecheck(unittest.TestCase):
    def test_miss_is_greyed_with_the_literal_fix(self):
        # The DoD check: a `which` miss greys the row and prints the fix.
        rows = launcher.precheck(which=fake_which({"claude"}))
        by_id = {r["id"]: r for r in rows}
        self.assertTrue(by_id["claude"]["available"])
        self.assertEqual(by_id["claude"]["resolved_path"], "C:/bin/claude.exe")
        self.assertEqual(by_id["claude"]["fix_text"], "")

        self.assertFalse(by_id["codex"]["available"])
        self.assertEqual(by_id["codex"]["resolved_path"], "")
        self.assertEqual(
            by_id["codex"]["fix_text"],
            "not found: codex - install it or set an absolute path in config.toml")

    def test_hit_reports_the_resolved_path(self):
        rows = launcher.precheck(which=fake_which({"pwsh"}))
        by_id = {r["id"]: r for r in rows}
        self.assertTrue(by_id["pwsh"]["available"])
        self.assertEqual(by_id["pwsh"]["resolved_path"], "C:/bin/pwsh.exe")

    def test_every_template_is_checked(self):
        rows = launcher.precheck(which=fake_which(set()))
        self.assertEqual(len(rows), len(agents_mod.TEMPLATES))
        for row in rows:
            self.assertFalse(row["available"])
            self.assertTrue(row["fix_text"].startswith("not found: "))

    def test_a_broken_which_does_not_raise(self):
        def boom(command):
            raise OSError("which exploded")

        rows = launcher.precheck(which=boom)
        self.assertTrue(all(not r["available"] for r in rows))


class TestCommandFor(unittest.TestCase):
    def test_wrapper_is_the_default_path(self):
        # The DoD check: the launcher runs our wrapper, so the row gets a
        # producer instead of sitting at `unknown`.
        argv = launcher.command_for({"command": "claude"}, python="PY")
        self.assertEqual(argv, ["PY", "-m", "ostlr", "run", "--", "claude"])

    def test_extra_args_are_appended(self):
        argv = launcher.command_for({"command": "claude"}, ["--resume"], python="PY")
        self.assertEqual(argv, ["PY", "-m", "ostlr", "run", "--", "claude", "--resume"])

    def test_template_object_works(self):
        template = agents_mod.TEMPLATES[0]
        argv = launcher.command_for(template, python="PY")
        self.assertEqual(argv[-1], template.command)


class TestHint(unittest.TestCase):
    def test_recipe_is_named_for_claude(self):
        # The DoD check: the hint for a template with a recipe names it.
        hint = launcher.hint_for({"id": "claude", "command": "claude"})
        self.assertIn("py -3 -m ostlr run -- claude", hint)
        self.assertIn("docs/producers/claude-hook.md", hint)

    def test_no_recipe_means_no_recipe_text(self):
        hint = launcher.hint_for({"id": "pwsh", "command": "pwsh"})
        self.assertEqual(hint, "py -3 -m ostlr run -- pwsh")
        self.assertNotIn("hook", hint)

    def test_recipe_file_exists(self):
        repo = Path(__file__).resolve().parent.parent.parent
        for template_id, recipe in launcher.RECIPES.items():
            self.assertTrue((repo / recipe).exists(), recipe)


class TestMemory(unittest.TestCase):
    def test_last_three_most_recent_first(self):
        cfg = config_mod.Config()
        for directory in ("D:/a", "D:/b", "D:/c", "D:/d"):
            launcher.remember(cfg, directory=directory)
        self.assertEqual(cfg.last_dirs, ["D:/d", "D:/c", "D:/b"])

    def test_dedupe_moves_to_the_front(self):
        cfg = config_mod.Config()
        launcher.remember(cfg, directory="D:/a")
        launcher.remember(cfg, directory="D:/b")
        launcher.remember(cfg, directory="D:/a")
        self.assertEqual(cfg.last_dirs, ["D:/a", "D:/b"])

    def test_agent_memory(self):
        cfg = config_mod.Config()
        launcher.remember(cfg, agent_id="claude")
        launcher.remember(cfg, agent_id="pwsh")
        self.assertEqual(cfg.last_agents, ["pwsh", "claude"])

    def test_survives_a_save_and_reload(self):
        # The DoD check: last-3 memory persists in config.toml.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.toml")
            cfg = config_mod.Config()
            launcher.remember(cfg, directory="D:/ws/projV", agent_id="claude")
            launcher.remember(cfg, directory="D:/ws/sop", agent_id="codex")
            self.assertTrue(launcher.remember_and_save(cfg, path=path))

            reloaded = config_mod.load(path)
            self.assertEqual(reloaded.last_dirs, ["D:/ws/sop", "D:/ws/projV"])
            self.assertEqual(reloaded.last_agents, ["codex", "claude"])

    def test_defaults_prefill(self):
        cfg = config_mod.Config()
        launcher.remember(cfg, directory="D:/ws/projV", agent_id="claude")
        self.assertEqual(launcher.default_directory(cfg), "D:/ws/projV")
        self.assertEqual(launcher.default_agent(cfg), "claude")

    def test_default_agent_falls_back_to_the_first_available(self):
        cfg = config_mod.Config()
        checked = launcher.precheck(which=fake_which({"pwsh"}))
        self.assertEqual(launcher.default_agent(cfg, checked), "pwsh")


class TestStart(unittest.TestCase):
    def test_launch_double_receives_the_chosen_directory(self):
        # The DoD check: start-in-chosen-directory, asserted on the double.
        seen = {}

        def double(template, cwd, argv=None):
            seen["template"] = template
            seen["cwd"] = cwd
            seen["argv"] = argv
            return {"ok": True, "reason": "launched", "pid": 4242}

        result = launcher.start({"id": "claude", "command": "claude"},
                                "D:/ws/projV", launch_fn=double)
        self.assertTrue(result["ok"])
        self.assertEqual(seen["cwd"], "D:/ws/projV")
        self.assertEqual(seen["argv"][-1], "claude")
        self.assertEqual(seen["argv"][3], "run")

    def test_unknown_cwd_is_refused(self):
        result = launcher.start({"id": "claude", "command": "claude"}, "?",
                                launch_fn=lambda *a, **k: {"ok": True})
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "cwd_unknown")

    def test_a_failing_launch_is_a_value(self):
        def double(template, cwd, argv=None):
            return {"ok": False, "reason": "spawn_failed", "detail": "gone"}

        result = launcher.start({"id": "claude", "command": "claude"}, "D:/ws",
                                launch_fn=double)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "spawn_failed")

    def test_a_raising_launch_is_a_value(self):
        def double(template, cwd, argv=None):
            raise OSError("no console")

        result = launcher.start({"id": "claude", "command": "claude"}, "D:/ws",
                                launch_fn=double)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "spawn_failed")

    def test_a_successful_start_is_remembered(self):
        cfg = config_mod.Config()
        launcher.start({"id": "claude", "command": "claude"}, "D:/ws/projV",
                       cfg=cfg, launch_fn=lambda *a, **k: {"ok": True, "pid": 1})
        self.assertEqual(cfg.last_dirs, ["D:/ws/projV"])
        self.assertEqual(cfg.last_agents, ["claude"])

class TestLatency(unittest.TestCase):
    def test_bound_is_tick_plus_one(self):
        self.assertEqual(launcher.latency_bound_s(5), 6)
        self.assertEqual(launcher.latency_bound_s(1), 2)
        self.assertEqual(launcher.latency_bound_s("x"), config_mod.DEFAULT_TICK_S + 1)

    def test_launched_row_appears_on_the_next_snapshot(self):
        # The DoD check, as a value: the launched pid is in the published
        # snapshot, so the row appears within `tick_s + 1 s` by construction.
        snapshot = {"rows": [{"pid": 4242, "state": "unknown",
                              "transient": "starting"}]}
        self.assertTrue(launcher.row_appears_within(snapshot, 4242, 5))
        self.assertFalse(launcher.row_appears_within(snapshot, 9999, 5))


if __name__ == "__main__":
    unittest.main()
