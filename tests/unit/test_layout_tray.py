"""Layout persistence and tray: the parts that make the panel survive a restart.

The processes are never restored (a restored pane is started fresh in the same
directory with the same command); the *shape* is. And the tray is optional by
design: pystray and Pillow may be missing, and the panel says so instead of
showing a dead icon.
"""

import json
import os
import tempfile
import unittest

from ostlr.qt import layout, tray


class TestLayout(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "layout.json")
            tabs = [layout.tab_entry("shell", [{"argv": ["cmd.exe"], "cwd": "D:/ws"}])]
            self.assertTrue(layout.save(tabs, active=0, path=path))
            data = layout.load(path)
        self.assertEqual(1, len(data["tabs"]))
        self.assertEqual(["cmd.exe"], data["tabs"][0]["panes"][0]["argv"])
        self.assertEqual("D:/ws", data["tabs"][0]["panes"][0]["cwd"])

    def test_a_split_tab_keeps_its_orientation_and_sizes(self):
        entry = layout.tab_entry("two", [{"argv": ["a"], "cwd": ""}, {"argv": ["b"], "cwd": ""}],
                                 split="v", sizes=[300, 700])
        self.assertEqual("v", entry["split"])
        self.assertEqual([300, 700], entry["sizes"])
        self.assertEqual(2, len(entry["panes"]))

    def test_a_broken_file_is_an_empty_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "layout.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            data = layout.load(path)
        self.assertEqual([], data["tabs"])

    def test_a_missing_file_is_an_empty_layout(self):
        data = layout.load(os.path.join(tempfile.gettempdir(), "nope-does-not-exist.json"))
        self.assertEqual({"v": 1, "active": 0, "tabs": []}, data)

    def test_restore_is_capped(self):
        tabs = [layout.tab_entry("t%d" % i, [{"argv": ["cmd"], "cwd": ""}]) for i in range(9)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "layout.json")
            layout.save(tabs, path=path)
            data = layout.load(path)
        self.assertLessEqual(len(data["tabs"]), layout.MAX_RESTORE)


class TestTray(unittest.TestCase):
    def test_absence_is_reported_not_faked(self):
        icon = tray.Tray()
        if tray.available():
            self.assertEqual("", icon.reason)
        else:
            self.assertTrue(icon.reason)
            self.assertFalse(icon.start())

    def test_update_without_an_icon_is_a_no_op(self):
        icon = tray.Tray()
        icon.update(3, 4)   # must not raise when the tray never started

    def test_the_icon_image_can_be_drawn(self):
        if not tray.available():
            self.skipTest("Pillow/pystray not installed")
        image = tray._icon_image(7, "#e2c07c")
        self.assertEqual((64, 64), image.size)


if __name__ == "__main__":
    unittest.main()
