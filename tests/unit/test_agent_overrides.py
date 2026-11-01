"""`[[agents]]` overrides - the launcher's own fix text promises this path.

The panel greys a row and says "install it or set an absolute path in
config.toml", so that promise has to be true: baseline 4.5 defines the table
shape, and these tests pin parsing, merging and the PATH pre-check that follows.
"""

import os
import tempfile
import unittest

from ostlr import agents as agents_mod
from ostlr import config as config_mod
from ostlr import launcher


class OverrideTestCase(unittest.TestCase):
    def setUp(self):
        # Every test starts from the built-in registry.
        agents_mod.apply_overrides([])

    def tearDown(self):
        agents_mod.apply_overrides([])


class TestParsing(OverrideTestCase):
    def test_a_full_entry_survives(self):
        cfg = config_mod.from_mapping({
            "agents": [{"id": "projv", "label": "projV",
                        "command": "D:/ws/proJV/build/Release/proJv_tui.exe",
                        "args": ["--flag"]}],
        })
        self.assertEqual(1, len(cfg.agents))
        entry = cfg.agents[0]
        self.assertEqual("projv", entry["id"])
        self.assertEqual("projV", entry["label"])
        self.assertEqual(["--flag"], entry["args"])

    def test_a_half_written_entry_is_dropped(self):
        cfg = config_mod.from_mapping({
            "agents": [{"id": "projv"}, {"command": "x"}, "not a table", {"id": "", "command": "y"}],
        })
        self.assertEqual([], cfg.agents)

    def test_label_defaults_to_the_id(self):
        cfg = config_mod.from_mapping({"agents": [{"id": "x", "command": "x.exe"}]})
        self.assertEqual("x", cfg.agents[0]["label"])

    def test_round_trip_through_toml(self):
        cfg = config_mod.from_mapping({
            "agents": [{"id": "projv", "label": "projV",
                        "command": "D:/ws/proJV/build/Release/proJv_tui.exe"}],
        })
        text = config_mod.dumps(cfg)
        self.assertIn("[[agents]]", text)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            again = config_mod.load(path)
        self.assertEqual(1, len(again.agents))
        self.assertEqual("D:/ws/proJV/build/Release/proJv_tui.exe", again.agents[0]["command"])


class TestMerge(OverrideTestCase):
    def test_an_override_replaces_the_command_of_a_default(self):
        merged = agents_mod.apply_overrides([
            {"id": "projv", "label": "projV", "command": "D:/ws/x/proJv_tui.exe"}])
        projv = [t for t in merged if t.id == "projv"][0]
        self.assertEqual("D:/ws/x/proJv_tui.exe", projv.command)
        # The label and the matcher tokens are kept, so existing rows stay named.
        self.assertEqual("projV", projv.label)
        self.assertIn("projv_tui", projv.tokens)

    def test_a_new_id_appends_a_template(self):
        before = len(agents_mod.TEMPLATES)
        merged = agents_mod.apply_overrides([{"id": "aider", "command": "C:/bin/aider.exe"}])
        self.assertEqual(before + 1, len(merged))
        self.assertEqual("aider", merged[-1].id)
        self.assertIn("aider", merged[-1].tokens)

    def test_reapplying_does_not_stack(self):
        agents_mod.apply_overrides([{"id": "aider", "command": "x.exe"}])
        merged = agents_mod.apply_overrides([{"id": "aider", "command": "y.exe"}])
        self.assertEqual(1, len([t for t in merged if t.id == "aider"]))
        self.assertEqual("y.exe", [t for t in merged if t.id == "aider"][0].command)

    def test_the_matcher_follows_the_override(self):
        agents_mod.apply_overrides([{"id": "projv", "command": "D:/ws/proJV/proJv_tui.exe"}])
        template = [t for t in agents_mod.TEMPLATES if t.id == "projv"][0]
        self.assertTrue(template.matches("proJv_tui.exe", ""))


class TestPrecheck(OverrideTestCase):
    def test_an_absolute_path_turns_the_row_available(self):
        # Before: nothing on PATH, so the row is greyed with the fix text.
        before = launcher.precheck(which=lambda name: None)
        projv = [row for row in before if row["id"] == "projv"][0]
        self.assertFalse(projv["available"])
        self.assertIn("config.toml", projv["fix_text"])

        # After the override, the same lookup resolves the absolute path.
        agents_mod.apply_overrides([
            {"id": "projv", "command": "D:/ws/proJV/build/Release/proJv_tui.exe"}])
        after = launcher.precheck(which=lambda name: name if name.endswith(".exe") else None)
        projv = [row for row in after if row["id"] == "projv"][0]
        self.assertTrue(projv["available"])
        self.assertEqual("", projv["fix_text"])
        self.assertTrue(projv["resolved_path"].endswith("proJv_tui.exe"))

    def test_the_launch_argv_carries_the_override_and_its_args(self):
        template = agents_mod.apply_overrides([
            {"id": "projv", "command": "D:/ws/x/proJv_tui.exe", "args": ["--fast"]}])
        projv = [t for t in template if t.id == "projv"][0]
        argv = launcher.command_for(projv, python="py.exe")
        self.assertEqual(["py.exe", "-m", "ostlr", "run", "--",
                          "D:/ws/x/proJv_tui.exe", "--fast"], argv)


if __name__ == "__main__":
    unittest.main()
