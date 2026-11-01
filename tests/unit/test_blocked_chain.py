"""The `blocked` chain: a CLI says "a human is needed" and the whole panel reacts.

Baseline 3.5 wants four things at once: the row on top, the gauge/count showing
it, the tray badge, and no modal. This drives a snapshot with one blocked row
through the window and checks the visible consequences.
"""

import unittest


def _qt():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


BLOCKED = {
    "pid": 4242, "started_at": 1793415260, "state_since": 1793415300,
    "agent": "projV", "project": "projV", "cwd": "D:/ws/projV", "branch": "main",
    "state": "blocked", "source": "l1", "transient": None, "kind": "permission",
    "msg": "Approve rm -rf build?", "age_s": 4, "mem_bytes": 212000000,
    "elapsed_s": 130, "rank": 0, "hint": "", "exit_code": None, "waiting_s": 40,
    "done_highlight_until": 0, "reason": "", "template_id": "projv",
}
IDLE = dict(BLOCKED, pid=4243, state="idle", source="l2", kind="", msg="",
            rank=5, mem_bytes=1024, elapsed_s=10, waiting_s=0)


def envelope(rows):
    return {"v": 1, "header": {"waiting": 1, "running": 0, "running_inferred": 0,
                               "error": 0, "error_inferred": 0, "done": 0,
                               "idle": 2, "unknown": 0, "starting": 0,
                               "sessions": len(rows)},
            "rows": rows, "footer": "", "stale": ""}


class BlockedCase(unittest.TestCase):
    def setUp(self):
        try:
            _qt()
        except Exception as exc:
            self.skipTest("Qt unavailable: %s" % exc)
        from ostlr import config
        from ostlr.qt.app import MainWindow
        self.cfg = config.Config()
        self.cfg.restore_layout = False
        self.window = MainWindow(self.cfg, provider=lambda: envelope([BLOCKED, IDLE]))
        self.window.tray.stop()

    def tearDown(self):
        try:
            for pane in list(self.window.panes):
                pane.bridge.stop()
            self.window.close()
        except Exception:
            pass


class TestBlockedChain(BlockedCase):
    def test_the_waiting_gauge_counts_it(self):
        self.window.refresh_now()
        self.assertIn("waiting 1", self.window.gauges.waiting.caption)

    def test_the_row_shows_the_blocked_glyph_and_the_state_word(self):
        self.window.refresh_now()
        text = self.window.list.item(0).text()
        self.assertIn("\u25cf", text)          # the 3.2 glyph for blocked
        self.assertIn("blocked", text)
        self.assertIn("projV", text)

    def test_the_statelist_puts_it_first(self):
        self.window.refresh_now()
        self.assertIn("4242", str(self.window.list.item(0).data(0x0100)))

    def test_no_modal_appears(self):
        # Baseline 3.5: a blocked row must never steal focus with a dialog.
        self.window.refresh_now()
        from PySide6.QtWidgets import QApplication
        visible = [w for w in QApplication.topLevelWidgets() if w.isVisible()
                   and w is not self.window]
        self.assertEqual([], [w.windowTitle() for w in visible if w.windowTitle()])

    def test_the_detail_panel_shows_the_reason(self):
        self.window.refresh_now()
        self.window.list.setCurrentRow(0)
        self.assertIn("blocked", self.window.detail.text())
        self.assertIn("D:/ws/projV", self.window.detail.text())

    def test_the_tray_call_does_not_crash_without_an_icon(self):
        self.window.refresh_now()   # -> tray.update(1, 0) with no icon running
        self.assertTrue(True)

    def test_the_banner_stops_claiming_nothing_is_reported(self):
        self.window.refresh_now()
        self.assertEqual("", self.window.banner_label.text())


if __name__ == "__main__":
    unittest.main()
