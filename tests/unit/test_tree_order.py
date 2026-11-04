"""The tree's order, and the promise that an index still names the right file.

A `QSortFilterProxyModel` between the model and the view is the one place where
an index can be read as if it came from the other model - and the symptom would
be a send that hands the pane the path of the row above. So the order is tested
here, and so is every row's two icons, row by row.
"""

import os
import shutil
import tempfile
import time
import unittest

Qt = None


def _qt():
    global Qt
    if Qt is None:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QApplication
        Qt = (_Qt, QApplication.instance() or QApplication([]))
    return Qt


class TreeCase(unittest.TestCase):
    def setUp(self):
        try:
            self.qt, self.app = _qt()
        except Exception as exc:                 # no Qt at all
            self.skipTest("Qt unavailable: %s" % exc)
        self.tmp = tempfile.mkdtemp(prefix="ostlr-tree-order-")
        for name in ("b10.txt", "b2.txt", "A.txt"):
            open(os.path.join(self.tmp, name), "w").close()
        os.makedirs(os.path.join(self.tmp, "zz"))
        from ostlr.qt import tree as tree_mod
        self.tree = tree_mod.FileTree(self.tmp)
        self.tree.resize(520, 400)
        self.sent = []
        self.tree.inject_path.connect(lambda path, at: self.sent.append((path, at)))
        self._wait_rows(4)

    def tearDown(self):
        try:
            self.tree.deleteLater()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_rows(self, wanted: int) -> None:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            self.app.processEvents()
            if self.tree.proxy.rowCount(self.tree.view.rootIndex()) >= wanted:
                return
            time.sleep(0.02)
        self.skipTest("QFileSystemModel did not populate in time")

    def _root(self):
        return self.tree.view.rootIndex()

    def _names(self):
        root = self._root()
        names = []
        for row in range(self.tree.proxy.rowCount(root)):
            index = self.tree.proxy.index(row, 0, root)
            names.append(self.tree.model.fileName(self.tree.proxy.mapToSource(index)))
        return names


class TestOrder(TreeCase):
    def test_folders_first_then_natural_names(self):
        self.assertEqual(["zz", "A.txt", "b2.txt", "b10.txt"], self._names())

    def test_the_order_does_not_depend_on_the_directory_listing_order(self):
        # Two FileTrees over the same directory must agree: the rule, not the
        # filesystem's own enumeration order, decides.
        from ostlr.qt import tree as tree_mod
        second = tree_mod.FileTree(self.tmp)
        second.resize(520, 400)
        try:
            deadline = time.time() + 8.0
            while time.time() < deadline:
                self.app.processEvents()
                if second.proxy.rowCount(second.view.rootIndex()) >= 4:
                    break
                time.sleep(0.02)
            names = []
            for row in range(second.proxy.rowCount(second.view.rootIndex())):
                index = second.proxy.index(row, 0, second.view.rootIndex())
                names.append(second.model.fileName(second.proxy.mapToSource(index)))
            self.assertEqual(self._names(), names)
        finally:
            second.deleteLater()


class TestThePathThatLeaves(TreeCase):
    def _click(self, row: int, slot: int):
        from PySide6.QtCore import QRectF
        from PySide6.QtTest import QTest
        from ostlr.qt import icons
        index = self.tree.proxy.index(row, 0, self._root())
        rect = QRectF(self.tree.view.visualRect(index))
        self.assertTrue(rect.height() > 0, "the row is not laid out")
        boxes = icons.right_aligned(rect, 2)
        self.sent.clear()
        QTest.mouseClick(self.tree.view.viewport(), self.qt.LeftButton,
                         pos=boxes[slot].center().toPoint())

    def test_every_row_sends_its_own_path_and_the_right_prefix(self):
        names = self._names()
        for row, name in enumerate(names):
            for slot, expect_at in ((0, False), (1, True)):
                with self.subTest(row=name, at=expect_at):
                    self._click(row, slot)
                    self.assertEqual(1, len(self.sent),
                                     "%s: the click did not reach an icon" % name)
                    path, at = self.sent[0]
                    self.assertEqual(name, os.path.basename(path))
                    self.assertEqual(expect_at, at)

    def test_the_selected_path_is_the_row_that_was_selected(self):
        target = self.tree.proxy.index(2, 0, self._root())
        self.tree.view.setCurrentIndex(target)
        self.assertEqual("b2.txt", os.path.basename(self.tree.selected_path()))

    def test_a_double_clicked_directory_re_roots_the_tree(self):
        directory = os.path.join(self.tmp, "zz")
        row = self._names().index("zz")
        self.tree._on_double_click(self.tree.proxy.index(row, 0, self._root()))
        # `samefile`, not string equality: the model answers with the long path
        # while tempfile handed us the 8.3 short one (ADMINI~1).
        self.assertTrue(os.path.samefile(directory, self.tree.root))
        self.assertTrue(self.tree.view.rootIndex().isValid())


if __name__ == "__main__":
    unittest.main()
