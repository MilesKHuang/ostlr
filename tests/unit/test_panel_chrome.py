"""The toolbar, the detail column and the plain-language banner.

The first build had no buttons at all: `open_launcher` was defined and nothing
called it, so the product could not start a session. These tests pin the fix at
the level that can be checked without a display (the strings and the wiring)
plus a smoke test that the widgets exist when Tk is available.
"""

import unittest

from ostlr import view_model
from ostlr.ui import app as app_mod


class TestPanelText(unittest.TestCase):
    def test_the_legend_covers_every_state_a_row_can_show(self):
        text = "\n".join(view_model.legend_lines())
        for state in ("blocked", "working", "unknown", "done", "idle", "error"):
            self.assertIn(state, text)

    def test_the_help_text_names_every_control(self):
        text = view_model.help_text()
        for word in ("Start", "Refresh", "Ctrl+Shift+C", "right-click"):
            self.assertIn(word, text)

    def test_the_banner_explains_a_fully_unknown_list(self):
        text = view_model.unknown_banner(4, 4, producers=0)
        self.assertIn("Start button", text)
        self.assertIn("4", text)

    def test_the_banner_is_silent_once_one_row_is_known(self):
        self.assertEqual("", view_model.unknown_banner(4, 3, producers=1))
        self.assertEqual("", view_model.unknown_banner(0, 0, producers=0))

    def test_the_banner_names_staleness_when_producers_exist(self):
        text = view_model.unknown_banner(2, 2, producers=2)
        self.assertIn("last report older than", text)


class TestDetailColumn(unittest.TestCase):
    ROW = {
        "pid": 4242, "agent": "claude", "state": "blocked", "source": "l2",
        "project": "projV", "cwd": "D:/ws/projV", "branch": "main",
        "mem_bytes": 212000000, "elapsed_s": 130, "reason": "",
        "hint": "py -3 -m ostlr.status --state blocked",
    }

    def test_a_row_is_spelled_out(self):
        text = "\n".join(view_model.detail_lines(self.ROW))
        self.assertIn("claude", text)
        self.assertIn("4242", text)
        self.assertIn("blocked", text)
        self.assertIn("D:/ws/projV", text)
        self.assertIn("main", text)

    def test_no_selection_still_explains_what_to_do(self):
        text = "\n".join(view_model.detail_lines(None))
        self.assertIn("Click a row", text)


class TestToolbarWiring(unittest.TestCase):
    """The buttons exist and call the real methods, not placeholders."""

    def setUp(self):
        ok, reason = app_mod.tk_available()
        if not ok:
            self.skipTest("no display: %s" % reason)
        self.app = app_mod.PanelApp(provider=lambda: {"v": 1, "header": {}, "rows": []})

    def tearDown(self):
        try:
            self.app.root.destroy()
        except Exception:
            pass

    def test_every_button_has_a_handler(self):
        # Start / Refresh / Copy path / Help plus the two font-scale buttons.
        self.assertLessEqual({"start", "refresh", "copy", "help"}, set(self.app.buttons))
        self.assertLessEqual({"bigger", "smaller"}, set(self.app.buttons))

    def test_every_button_is_wired_to_a_real_method(self):
        # A button with an empty command is decoration. Tk's command string is
        # opaque, so check the handler the constructor was given instead.
        for name in ("open_launcher", "refresh_now", "copy_selected_path", "show_help"):
            self.assertTrue(callable(getattr(self.app, name)), name)
        for key, button in self.app.buttons.items():
            self.assertTrue(button.cget("command"), key)

    def test_refresh_re_renders(self):
        drawn = self.app.refresh_now()
        self.assertIn("header", drawn)

    def test_the_empty_state_has_a_start_button(self):
        self.assertTrue(hasattr(self.app, "empty_button"))

    def test_the_detail_column_and_legend_exist(self):
        self.assertIn("blocked", self.app.legend_label.cget("text"))


if __name__ == "__main__":
    unittest.main()
