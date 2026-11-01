"""T11/T16 - the panel with a real window, pumped but never blocked.

Every test here creates a Tk window, calls `update()` a bounded number of times
and destroys it in `finally`. `mainloop()` is never called: a test that blocks
in the event loop is a hung suite, not a failing test.

If `Tk()` cannot start (no display, session 0, CI), each test SKIPs with the
reason instead of failing - a windowless machine must not read as a broken
build.
"""

import unittest

from ostlr import config as config_mod
from ostlr.ui import app as app_mod


def _tk_ok():
    return app_mod.tk_available()


class TkTestCase(unittest.TestCase):
    """A base class that skips cleanly when there is no display."""

    def setUp(self):
        ok, reason = _tk_ok()
        if not ok:
            self.skipTest("no display: %s" % reason)
        self.app = None

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.close()
            except Exception:
                pass
            self.app = None

    def make_app(self, snapshot=None, **kwargs):
        snap = snapshot or {"v": 1, "header": {}, "rows": [], "footer": ""}
        self.app = app_mod.PanelApp(config_mod.Config(),
                                    provider=lambda: snap, **kwargs)
        return self.app

    def pump(self, times=3):
        for _ in range(times):
            self.app.root.update()


class TestWindow(TkTestCase):
    def test_window_opens_and_renders_the_header(self):
        app = self.make_app({
            "v": 1,
            "header": {"waiting": 1, "running": 2, "unknown": 0, "sessions": 3},
            "rows": [],
            "footer": "",
        })
        app.render()
        self.pump()
        self.assertIn("waiting 1", app.header_var.get())
        self.assertIn("sessions 3", app.header_var.get())

    def test_rows_are_drawn(self):
        rows = [
            {"pid": 100, "started_at": 1, "agent": "claude", "project": "projV",
             "cwd": "D:/ws/projV", "branch": "main", "state": "blocked",
             "source": "l2", "kind": "permission", "msg": "Approve?",
             "age_s": 5, "mem_bytes": 1024, "elapsed_s": 40, "rank": 0,
             "hint": "", "exit_code": None},
            {"pid": 200, "started_at": 1, "agent": "codex", "project": "sop",
             "cwd": "D:/ws/sop", "branch": "main", "state": "working",
             "source": "l3", "kind": "", "msg": "", "age_s": 0,
             "mem_bytes": 2048, "elapsed_s": 10, "rank": 2, "hint": "",
             "exit_code": None},
        ]
        app = self.make_app({"v": 1, "header": {"sessions": 2}, "rows": rows,
                             "footer": ""})
        app.render()
        self.pump()
        self.assertEqual(len(app._row_widgets), 2)

    def test_empty_state_is_shown(self):
        app = self.make_app({"v": 1, "header": {}, "rows": [], "footer": ""})
        app.render()
        self.pump()
        texts = [label.cget("text") for label in app.empty_labels]
        self.assertIn("No agent sessions yet", texts)
        self.assertTrue(any("L1: unsupported" in t for t in texts))

    def test_destroy_in_teardown_leaves_nothing_open(self):
        app = self.make_app()
        app.render()
        self.pump()
        app.close()
        self.app = None  # already closed; tearDown must not double-close


class TestGestures(TkTestCase):
    def _snapshot(self):
        return {
            "v": 1,
            "header": {"sessions": 2},
            "rows": [
                {"pid": 100, "started_at": 1, "agent": "claude", "project": "p",
                 "cwd": "c", "branch": "main", "state": "working", "source": "l2",
                 "kind": "", "msg": "", "age_s": 0, "mem_bytes": 1,
                 "elapsed_s": 1, "rank": 2, "hint": "hint for 100",
                 "exit_code": None},
                {"pid": 200, "started_at": 1, "agent": "codex", "project": "q",
                 "cwd": "c", "branch": "main", "state": "idle", "source": "l2",
                 "kind": "", "msg": "", "age_s": 0, "mem_bytes": 1,
                 "elapsed_s": 1, "rank": 5, "hint": "", "exit_code": None},
            ],
            "footer": "",
        }

    def test_select_never_focuses(self):
        # The DoD check: a click selects and calls the focus backend 0 times.
        calls = []
        app = self.make_app(self._snapshot(),
                            focus_fn=lambda pid: calls.append(pid) or {"ok": True})
        app.render()
        app.select(100)
        self.pump()
        self.assertEqual(calls, [])
        self.assertEqual(app.selected_pid, 100)

    def test_enter_focuses_once(self):
        calls = []
        app = self.make_app(self._snapshot(),
                            focus_fn=lambda pid: calls.append(pid) or {"ok": True})
        app.render()
        app.select(100)
        app.focus_selected()
        self.pump()
        self.assertEqual(calls, [100])

    def test_arrow_keys_move_the_selection(self):
        app = self.make_app(self._snapshot())
        app.render()
        app.select_relative(1)
        self.assertEqual(app.selected_pid, 100)
        app.select_relative(1)
        self.assertEqual(app.selected_pid, 200)
        app.select_relative(1)  # clamped at the end
        self.assertEqual(app.selected_pid, 200)

    def test_selected_hint_reaches_the_status_bar(self):
        app = self.make_app(self._snapshot())
        app.render()
        app.select(100)
        self.assertEqual(app.status_var.get(), "hint for 100")


class TestReminderLive(TkTestCase):
    def test_title_prefix_appears_and_clears(self):
        app = self.make_app({"v": 1, "header": {"waiting": 0}, "rows": [],
                             "footer": ""})
        app.render()
        self.pump()
        self.assertEqual(app.root.title(), "Ostlr")

        app.render({"v": 1, "header": {"waiting": 1}, "rows": [], "footer": ""})
        self.pump()
        self.assertEqual(app.root.title(), "! 1 waiting - Ostlr")

        app.render({"v": 1, "header": {"waiting": 0}, "rows": [], "footer": ""})
        self.pump()
        self.assertEqual(app.root.title(), "Ostlr")

    def test_one_bell_per_streak(self):
        bells = []
        app = self.make_app({"v": 1, "header": {"waiting": 0}, "rows": [],
                             "footer": ""}, bell_fn=lambda: bells.append(1))
        for waiting in (0, 1, 1, 1, 0, 1):
            app.render({"v": 1, "header": {"waiting": waiting}, "rows": [],
                        "footer": ""})
            self.pump(1)
        self.assertEqual(len(bells), 2)

    def test_get_window_text_sees_the_prefix(self):
        """T16's live check: read the title back through GetWindowText."""
        app = self.make_app({"v": 1, "header": {"waiting": 2}, "rows": [],
                             "footer": ""})
        app.render()
        self.pump()
        app.root.deiconify()
        app.root.lift()
        self.pump()
        title = app_mod.window_title()
        if not title:
            self.skipTest("GetWindowText returned nothing (no foreground window)")
        # The foreground window may be another process; only assert when we can
        # see our own title, otherwise the check is meaningless.
        if "Ostlr" not in title:
            self.skipTest("our window is not the foreground window: %r" % title)
        self.assertIn("! 2 waiting", title)


if __name__ == "__main__":
    unittest.main()
