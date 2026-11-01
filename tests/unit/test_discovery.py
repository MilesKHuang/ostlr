"""Discovery: the launcher must find what is installed, not what we listed.

The first build demanded `config.toml` edits to launch a new agent. These tests
pin the replacement: whole-name matching (so "pi" is not ping), remembered picks
winning over PATH finds, and the "use the copy that already has my key" hint.
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


class TestNameMatching(unittest.TestCase):
    def test_agents_match_by_whole_name(self):
        for stem in ("claude", "codex", "projv_tui", "hermes", "claude-code",
                     "opencode", "aider"):
            self.assertTrue(discovery._looks_like_agent(stem), stem)

    def test_system_utilities_do_not(self):
        # The defect: a substring hint "pi" matched all of these.
        for stem in ("ping", "pip", "pip3", "pathping", "dpiscaling", "fixmapi",
                     "pickerhost", "appidtel", "printfilterpipelinesvc"):
            self.assertFalse(discovery._looks_like_agent(stem), stem)


class TestEntries(unittest.TestCase):
    def test_remembered_wins_over_a_path_find(self):
        cfg = config.Config()
        cfg.agents = [{"id": "projv", "label": "projV",
                       "command": r"D:\ws\proJV\build\Release\proJV_tui.exe", "args": []}]
        entries = discovery.launcher_entries(cfg)
        ids = [str(e["id"]) for e in entries]
        self.assertEqual(1, ids.count("projv"))
        projv = [e for e in entries if e["id"] == "projv"][0]
        self.assertEqual("remembered", projv["source"])

    def test_a_path_candidate_appears_without_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "hermes.exe")
            write(exe)
            old = os.environ.get("PATH", "")
            try:
                os.environ["PATH"] = tmp + os.pathsep + old
                ids = [str(e["id"]) for e in discovery.launcher_entries(config.Config())]
            finally:
                os.environ["PATH"] = old
        self.assertIn("hermes", ids)


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


class TestLiveCopy(unittest.TestCase):
    ROWS = [
        {"pid": 6340, "exe": r"D:\ws\proJV_tui.exe"},
        {"pid": 9999, "exe": r"C:\Windows\System32\ping.exe"},
    ]

    def test_a_running_copy_with_a_key_is_offered(self):
        # The machine's answer to "I already use this one, why is yours keyless".
        live = discovery.live_copies(self.ROWS)
        self.assertEqual(1, len(live))          # ping.exe is not an agent
        self.assertEqual(6340, live[0]["pid"])

    def test_a_non_agent_is_never_offered(self):
        stems = [os.path.basename(str(item["exe"])).lower() for item in
                 discovery.live_copies(self.ROWS)]
        self.assertNotIn("ping.exe", stems)


class TestRemember(unittest.TestCase):
    def test_browsing_an_exe_adds_one_entry(self):
        cfg = config.Config()
        discovery.remember(cfg, r"D:\tools\hermes.exe")
        discovery.remember(cfg, r"D:\tools\hermes.exe")
        self.assertEqual(1, len(cfg.agents))
        self.assertEqual(r"D:\tools\hermes.exe", cfg.agents[0]["command"])

    def test_remembering_twice_updates_in_place(self):
        cfg = config.Config()
        discovery.remember(cfg, r"D:\a\projv_tui.exe")
        discovery.remember(cfg, r"D:\b\projv_tui.exe")
        self.assertEqual(r"D:\b\projv_tui.exe", cfg.agents[0]["command"])


if __name__ == "__main__":
    unittest.main()
