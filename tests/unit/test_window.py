"""The window's wiring, tested without a visible screen.

Three claims the user can feel immediately, so they get tests rather than a
promise:

  * a session started from a tree directory runs with THAT directory;
  * an OSC 7501 report from a pane becomes a status record with source=l1;
  * the welcome tab exists only while there is no pane.
"""

import os
import tempfile
import unittest

Qt = None


def _qt():
    global Qt
    if Qt is None:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt as _Qt
        app = QApplication.instance() or QApplication([])
        Qt = (_Qt, app)
    return Qt


class WindowCase(unittest.TestCase):
    def setUp(self):
        try:
            self.qt, self.app = _qt()
        except Exception as exc:      # no Qt at all: the rest of the suite still runs
            self.skipTest("Qt unavailable: %s" % exc)
        from ostlr import config
        from ostlr.qt.app import MainWindow
        self.cfg = config.Config()
        self.cfg.restore_layout = False
        self.window = MainWindow(self.cfg, provider=lambda: {"v": 1, "header": {}, "rows": []})

    def tearDown(self):
        try:
            self.window.close()
        except Exception:
            pass


class TestStartFrom(WindowCase):
    def test_a_session_started_from_a_tree_directory_gets_that_directory(self):
        # A real directory but not TemporaryDirectory: the child process holds it
        # as its cwd, and Windows refuses to delete a directory in use.
        import shutil
        tmp = os.path.join(tempfile.gettempdir(), "ostlr-window-test-cwd")
        os.makedirs(tmp, exist_ok=True)
        try:
            entry = {"id": "cmd", "args": [],
                     "command": os.environ.get("COMSPEC", "cmd.exe")}
            pane = self.window.start_from(entry, tmp, "panel")
            self.assertIsNotNone(pane)
            self.assertEqual(os.path.normcase(tmp), os.path.normcase(pane.cwd))
            pane.bridge.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_missing_program_is_a_receipt_not_a_crash(self):
        result = self.window.start_from({"id": "nope", "command": "definitely-not-here.exe"},
                                        os.getcwd(), "panel")
        self.assertIsNone(result)
        self.assertIn("not found", self.window.receipt)


class TestL1Wiring(WindowCase):
    def test_an_l1_report_becomes_a_status_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OSTLR_STATUS_DIR"] = tmp
            try:
                pid = 424242
                self.window._on_l1(pid, {"state": "blocked", "kind": "permission",
                                         "msg": "Approve rm -rf build?"})
                from ostlr import status
                record = status.read_status(pid, tmp)
            finally:
                os.environ.pop("OSTLR_STATUS_DIR", None)
        self.assertIsNotNone(record)
        self.assertEqual("blocked", record["state"])
        self.assertEqual("permission", record["kind"])
        self.assertEqual("l1", record["source"])
        self.assertEqual("Approve rm -rf build?", record["msg"])

    def test_a_clear_report_does_not_erase_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OSTLR_STATUS_DIR"] = tmp
            try:
                self.window._on_l1(7, {"state": "clear"})
                from ostlr import status
                self.assertIsNone(status.read_status(7, tmp))
            finally:
                os.environ.pop("OSTLR_STATUS_DIR", None)


class TestWelcome(WindowCase):
    def test_the_welcome_tab_disappears_when_a_pane_opens(self):
        before = self.window.tabs.count()
        self.assertGreaterEqual(before, 1)
        self.window.new_terminal(cwd=os.getcwd(), title="t")
        titles = [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())]
        self.assertNotIn("welcome", titles)
        for pane in list(self.window.panes):
            pane.bridge.stop()

    def test_the_welcome_text_says_where_a_session_comes_from(self):
        if self.window.placeholder is None:
            self.skipTest("a pane was already open")
        text = self.window.placeholder.text()
        self.assertIn("Right-click a folder", text)
        self.assertIn("Start a TUI here", text)


class TestReceipts(WindowCase):
    def test_copy_from_the_tree_names_the_target_pane(self):
        # Not TemporaryDirectory: a pane's child holds its cwd open, and Windows
        # then refuses the cleanup. A stable temp dir deleted with ignore_errors.
        import shutil
        tmp = os.path.join(tempfile.gettempdir(), "ostlr-copy-target")
        os.makedirs(tmp, exist_ok=True)
        target = os.path.join(tmp, "main.cpp")
        open(target, "w").close()
        try:
            pane = self.window.new_terminal(cwd=tmp, title="t")
            self.assertIsNotNone(pane)
            self.window._receipt_copy(target, from_tree=True)
            self.assertIn("copied", self.window.receipt)
            self.assertIn("Pane", self.window.receipt)
            self.assertIn("same project", self.window.receipt)
            pane.bridge.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_copy_without_a_pane_still_copies_and_says_so(self):
        self.window._receipt_copy(r"D:\ws\x\y.txt")
        self.assertIn("clipboard only", self.window.receipt)

    def test_inject_without_a_pane_falls_back_to_the_clipboard(self):
        self.window.inject_path_into_pane(r"D:\x\y.txt", at_prefix=True)
        self.assertIn("clipboard", self.window.receipt)


if __name__ == "__main__":
    unittest.main()
