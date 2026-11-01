"""The launcher's wiring, including the enum trap that crashed it.

`dialog.Accepted` is a class attribute in PySide6, not something an instance
answers to - the first click on "+ New session" raised AttributeError. These
tests drive `start_session` with a stand-in dialog so the contract is "it reads
`chosen`", not "it knows a Qt enum by name".
"""

import os
import unittest

from ostlr import config
from ostlr.qt import launcher as launcher_mod


def _qt():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class FakeDialog:
    """Only what `start_session` may rely on."""

    def __init__(self, chosen, directory="D:\\ws", location="panel"):
        self.chosen = chosen
        self.directory = directory
        self.location = location
        self.executed = 0

    def exec(self):
        self.executed += 1
        return 1


class StartSessionCase(unittest.TestCase):
    def setUp(self):
        try:
            _qt()
        except Exception as exc:
            self.skipTest("Qt unavailable: %s" % exc)
        from ostlr.qt.app import MainWindow
        self.cfg = config.Config()
        self.cfg.restore_layout = False
        self.window = MainWindow(self.cfg, provider=lambda: {"v": 1, "header": {}, "rows": []})
        self.window.tray.stop()
        self._real = launcher_mod.Launcher

    def tearDown(self):
        launcher_mod.Launcher = self._real
        try:
            for pane in list(self.window.panes):
                pane.stop()
            self.window.close()
        except Exception:
            pass

    def _stub(self, dialog):
        launcher_mod.Launcher = lambda *a, **k: dialog


class TestStartSession(StartSessionCase):
    def test_a_cancelled_dialog_starts_nothing(self):
        dialog = FakeDialog(chosen=None)
        self._stub(dialog)
        before = len(self.window.panes)
        self.window.start_session(r"D:\ws")
        self.assertEqual(before, len(self.window.panes))
        self.assertEqual(1, dialog.executed)

    def test_an_accepted_dialog_starts_the_session_in_that_directory(self):
        command = os.environ.get("COMSPEC", "cmd.exe")
        dialog = FakeDialog(chosen={"id": "cmd", "command": command, "args": []},
                            directory=r"D:\ws")
        self._stub(dialog)
        self.window.start_session(r"D:\ws")
        self.assertEqual(1, len(self.window.panes))
        self.assertEqual(os.path.normcase(r"D:\ws"),
                         os.path.normcase(self.window.panes[-1].cwd))

    def test_the_enum_is_never_touched_on_the_instance(self):
        # The exact defect: an instance attribute that does not exist.
        dialog = FakeDialog(chosen=None)
        self.assertFalse(hasattr(dialog, "Accepted"))
        self._stub(dialog)
        self.window.start_session(None)   # must not raise


class TestLauncherDialog(StartSessionCase):
    def test_it_opens_without_a_chosen_program(self):
        dialog = launcher_mod.Launcher(self.cfg, r"D:\ws")
        self.assertIsNone(dialog.chosen)
        self.assertEqual(r"D:\ws", dialog.directory)
        dialog.close()

    def test_entries_are_listed_and_unavailable_ones_are_marked(self):
        dialog = launcher_mod.Launcher(self.cfg, r"D:\ws")
        self.assertGreaterEqual(dialog.combo.count(), 1)
        labels = [dialog.combo.itemText(i) for i in range(dialog.combo.count())]
        self.assertTrue(any(label for label in labels))
        dialog.close()


if __name__ == "__main__":
    unittest.main()
