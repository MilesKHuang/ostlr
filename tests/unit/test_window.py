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
            pane = self.window.start_from(entry, tmp)
            self.assertIsNotNone(pane)
            self.assertEqual(os.path.normcase(tmp), os.path.normcase(pane.cwd))
            pane.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_missing_program_is_a_receipt_not_a_crash(self):
        result = self.window.start_from({"id": "nope", "command": "definitely-not-here.exe"},
                                        os.getcwd())
        self.assertIsNone(result)
        self.assertIn("not found", self.window.receipt)


class TestL1Wiring(WindowCase):
    """The pane is the terminal side of OSC 7501, so the pane writes the record.

    The window used to write it from `_on_l1`, which meant two writers for one
    file; now the pane folds the report into its per-id records and publishes the
    most urgent one, and the window only shows what arrived.
    """

    def test_an_l1_report_becomes_a_status_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OSTLR_STATUS_DIR"] = tmp
            try:
                pane = self.window.new_terminal(cwd=os.getcwd(), title="l1")
                self.assertIsNotNone(pane)
                pane.session_pid = 424242
                pane._apply_l1({"state": "blocked", "id": "", "app": "projv",
                                "kind": "permission", "title": "",
                                "msg": "Approve rm -rf build?", "progress": None})
                from ostlr import status
                record = status.read_status(424242, tmp)
            finally:
                os.environ.pop("OSTLR_STATUS_DIR", None)
                for open_pane in list(self.window.panes):
                    open_pane.stop()
        self.assertIsNotNone(record)
        self.assertEqual("blocked", record["state"])
        self.assertEqual("permission", record["kind"])
        self.assertEqual("l1", record["source"])
        self.assertEqual("Approve rm -rf build?", record["msg"])

    def test_a_clear_report_withdraws_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OSTLR_STATUS_DIR"] = tmp
            try:
                pane = self.window.new_terminal(cwd=os.getcwd(), title="l1")
                self.assertIsNotNone(pane)
                pane.session_pid = 7
                pane._apply_l1({"state": "working", "id": "", "app": "projv",
                                "kind": "", "title": "", "msg": "",
                                "progress": None})
                pane._apply_l1({"state": "clear", "id": "", "app": "", "kind": "",
                                "title": "", "msg": "", "progress": None})
                from ostlr import status
                self.assertIsNone(status.read_status(7, tmp))
            finally:
                os.environ.pop("OSTLR_STATUS_DIR", None)
                for open_pane in list(self.window.panes):
                    open_pane.stop()


class TestWelcome(WindowCase):
    def test_the_welcome_page_disappears_when_a_pane_opens(self):
        # No tab bar any more: the welcome text is a page in the stack, and the
        # first session replaces it.
        self.assertIsNotNone(self.window.placeholder)
        self.window.new_terminal(cwd=os.getcwd(), title="t")
        self.assertIsNone(self.window.placeholder)
        self.assertGreaterEqual(self.window.stack.count(), 1)
        for pane in list(self.window.panes):
            pane.stop()

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
            pane.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_copy_without_a_pane_still_copies_and_says_so(self):
        self.window._receipt_copy(r"D:\ws\x\y.txt")
        self.assertIn("clipboard only", self.window.receipt)

    def test_inject_without_a_pane_falls_back_to_the_clipboard(self):
        self.window.inject_path_into_pane(r"D:\x\y.txt", at_prefix=True)
        self.assertIn("clipboard", self.window.receipt)

    def test_the_sent_text_is_the_quoted_one(self):
        # The receipt shows what the PTY got: a path with a space arrives quoted,
        # so "D:\my project" is one argument and not two.
        class _Pane:
            title = "t"
            cwd = r"D:\ws"
            written = []

            def write(self, text):
                self.written.append(text)

        pane = _Pane()
        self.window.panes.append(pane)
        try:
            self.window.inject_path_into_pane(r"D:\my project\a.py")
            self.assertEqual(['"D:\\my project\\a.py"'], pane.written)
            self.assertIn(r'"D:\my project\a.py"', self.window.receipt)
        finally:
            self.window.panes.remove(pane)


class TestRowIcons(WindowCase):
    """The row's two icons, clicked the way a user clicks them."""

    def _start(self):
        entry = {"id": "cmd", "args": [],
                 "command": os.environ.get("COMSPEC", "cmd.exe")}
        pane = self.window.start_from(entry, os.getcwd())
        self.assertIsNotNone(pane)
        # The PTY is born on the page's first grid report, which needs a web view
        # that is not on screen here. The row only needs an identity, and a fake
        # one keeps the test from killing anything real.
        pane.session_pid = 43210
        self.window.refresh_now()
        return pane

    def _click(self, which: int):
        from PySide6.QtCore import QRectF
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtTest import QTest
        from ostlr.qt import rowdelegate
        item = self.window.list.item(0)
        self.assertIsNotNone(item, "no row to click")
        rect = QRectF(self.window.list.visualItemRect(item))
        slot = rowdelegate.action_rects(rect)[which]
        QTest.mouseClick(self.window.list.viewport(), _Qt.LeftButton,
                         pos=slot.center().toPoint())

    def test_a_session_started_here_is_always_a_pane(self):
        pane = self._start()
        self.assertIn(pane, self.window.panes)
        pane.stop()

    def test_the_close_icon_closes_that_session(self):
        self._start()
        self.window._confirm_close = lambda pane, facts: True
        self._click(1)
        self.assertEqual([], self.window.panes)
        self.assertIn("closed", self.window.receipt)

    def test_a_cancelled_confirmation_keeps_the_session(self):
        self._start()
        self.window._confirm_close = lambda pane, facts: False
        self._click(1)
        self.assertEqual(1, len(self.window.panes))
        self.assertIn("cancelled", self.window.receipt)
        for pane in list(self.window.panes):
            pane.stop()

    def test_the_terminal_icon_opens_one_more_pane(self):
        self._start()
        before = len(self.window.panes)
        self._click(0)
        self.assertEqual(before + 1, len(self.window.panes))
        for pane in list(self.window.panes):
            pane.stop()


if __name__ == "__main__":
    unittest.main()
