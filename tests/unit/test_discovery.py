"""Discovery: the user's own list, and nothing else.

The first build demanded `config.toml` edits to launch a new agent; the second
walked PATH and the process table to guess. Both are gone. What is pinned here is
the contract the user asked for: a program is offered because it was named once,
and naming it writes `[[agents]]` into config.toml.

    py -3 -m unittest tests.unit.test_discovery
"""

import os
import tempfile
import unittest

from ostlr import config
from ostlr.qt import discovery


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class TestEntries(unittest.TestCase):
    def test_only_the_users_choices_are_listed(self):
        cfg = config.Config()
        cfg.agents = [{"id": "projv", "label": "projV",
                       "command": r"D:\ws\proJV\build\Release\proJV_tui.exe", "args": []}]
        entries = discovery.launcher_entries(cfg)
        self.assertEqual(1, len(entries))
        self.assertEqual("projv", entries[0]["id"])
        self.assertEqual("remembered", entries[0]["source"])

    def test_nothing_on_path_is_offered_by_itself(self):
        # The old build walked PATH and offered every exe whose name looked like an
        # agent. A machine with ten CLIs installed is not a list the user asked for.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "hermes.exe"))
            old = os.environ.get("PATH", "")
            try:
                os.environ["PATH"] = tmp + os.pathsep + old
                ids = [str(e["id"]) for e in discovery.launcher_entries(config.Config())]
            finally:
                os.environ["PATH"] = old
        self.assertEqual([], ids)

    def test_resolve_only_answers_for_something_that_exists(self):
        self.assertIsNone(discovery.resolve(r"D:\no\such\agent.exe"))
        self.assertIsNone(discovery.resolve(""))


class TestHome(unittest.TestCase):
    def test_home_reports_the_key_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "proJV_tui.exe")
            write(exe)
            write(os.path.join(tmp, "projv_files", "config.toml"), 'api_key = "sk-test"\n')
            info = discovery.home_of(exe)
        self.assertTrue(info["files_dir"].endswith("projv_files"))
        self.assertTrue(info["has_key"])

    def test_home_without_projv_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "proJV_tui.exe")
            write(exe)
            info = discovery.home_of(exe)
        self.assertEqual("", info["files_dir"])
        self.assertFalse(info["has_key"])


class TestRemember(unittest.TestCase):
    def test_browsing_an_exe_adds_one_entry(self):
        cfg = config.Config()
        cfg.agents = []
        discovery.remember(cfg, r"D:\tools\hermes.exe")
        self.assertEqual(1, len(cfg.agents))
        self.assertEqual(r"D:\tools\hermes.exe", cfg.agents[0]["command"])
        self.assertEqual("hermes", cfg.agents[0]["id"])

    def test_remembering_twice_updates_in_place(self):
        cfg = config.Config()
        cfg.agents = []
        discovery.remember(cfg, r"D:\tools\hermes.exe")
        discovery.remember(cfg, r"D:\tools\hermes.exe", label="Hermes")
        self.assertEqual(1, len(cfg.agents))
        self.assertEqual("Hermes", cfg.agents[0]["label"])


if __name__ == "__main__":
    unittest.main()
