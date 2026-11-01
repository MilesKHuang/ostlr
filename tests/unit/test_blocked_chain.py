"""The `blocked` chain, driven through the real path: record -> row -> gauge.

Baseline 3.5 wants four things at once: the row on top, the count/gauge, the tray
badge, and no modal. The list is now built from the panes the panel owns
(correction 1), so the test injects a pane stub plus a real status record and
checks what the user would see.
"""

import os
import tempfile
import unittest

PID = 424242


class StubPane:
    """The contract the window relies on: title, cwd, session_pid."""

    def __init__(self, pid=PID, title="projV @ projV", cwd=r"D:\ws\projV"):
        self.session_pid = pid
        self.title = title
        self.cwd = cwd
        self.argv = ["projV_tui.exe"]
        self.exited = None
        self.l1_report = None
        self.bridge = type("B", (), {"stop": lambda self=None: None,
                                     "started": None})()
        self.view = None


def _qt():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class BlockedCase(unittest.TestCase):
    def setUp(self):
        try:
            _qt()
        except Exception as exc:
            self.skipTest("Qt unavailable: %s" % exc)
        from ostlr import config
        from ostlr.qt.app import MainWindow
        self.tmp = tempfile.mkdtemp(prefix="ostlr-blocked-")
        os.environ["OSTLR_STATUS_DIR"] = self.tmp
        self.cfg = config.Config()
        self.cfg.restore_layout = False
        self.window = MainWindow(self.cfg)
        self.window.tray.stop()
        self.window.panes.append(StubPane())
        from ostlr import status
        status.write_status(PID, "blocked", kind="permission",
                            msg="Approve rm -rf build?", cwd=r"D:\ws\projV",
                            source="l1", status_dir=self.tmp)
        self.window.refresh_now()

    def tearDown(self):
        os.environ.pop("OSTLR_STATUS_DIR", None)
        try:
            self.window.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBlockedChain(BlockedCase):
    def test_the_waiting_gauge_counts_it(self):
        self.assertIn("waiting 1", self.window.gauges.waiting.caption)

    def test_the_row_carries_the_state_for_the_delegate(self):
        # The glyph is painted by RowDelegate from the row dict; the item text
        # stays readable for tests and tooltips.
        from ostlr.qt import rowdelegate
        self.assertEqual("\u25cf", rowdelegate.GLYPH["blocked"])
        text = self.window.list.item(0).text()
        self.assertIn("blocked", text)
        self.assertIn("projV", text)

    def test_the_list_holds_only_owned_panes(self):
        self.assertEqual(1, self.window.list.count())

    def test_the_detail_panel_shows_the_state_and_the_directory(self):
        self.window.list.setCurrentRow(0)
        text = self.window.detail.text()
        self.assertIn("blocked", text)
        self.assertIn(r"D:\ws\projV", text)
        self.assertIn("l1", text)

    def test_the_source_of_the_state_is_attributed(self):
        row = self.window.list.item(0).data(0x0100)
        self.assertEqual("l1", row["source"])

    def test_no_modal_appears(self):
        from PySide6.QtWidgets import QApplication
        visible = [w for w in QApplication.topLevelWidgets()
                   if w.isVisible() and w is not self.window and w.windowTitle()]
        self.assertEqual([], [w.windowTitle() for w in visible])

    def test_the_tray_call_is_safe_without_an_icon(self):
        self.window.refresh_now()   # -> tray.update(1, 0) with no icon running
        self.assertTrue(True)


class TestWorkload(BlockedCase):
    def test_every_row_carries_cpu_and_memory_percentages(self):
        row = self.window.list.item(0).data(0x0100)
        self.assertIn("cpu", row)
        self.assertIn("mem", row)
        # A pid that does not exist yields None, not a fabricated 0.
        self.assertIsNone(row["cpu"])

    def test_the_formatters_never_invent_a_number(self):
        from ostlr.qt import procstat
        self.assertEqual("-", procstat.format_cpu(None))
        self.assertEqual("-", procstat.format_gpu(None))
        self.assertEqual("12%", procstat.format_cpu(12.4))


if __name__ == "__main__":
    unittest.main()
