"""The painted UI, checked as geometry: where an icon is, not what colour it is.

A delegate that paints is hard to assert on, so the arithmetic lives in small
pure functions (`height_for`, `action_at`, `gauge_columns`, `send_action_at`) and
this file tests those. One test per claim the user can feel:

    * a click lands on the icon it hit, and nowhere else;
    * a session with no GPU measurement gets a shorter row, not a 0% bar;
    * every named icon actually draws something.
"""

import unittest

Qt = None


def _app():
    """A QGuiApplication for the painting test; skip if there is no Qt."""
    global Qt
    if Qt is None:
        from PySide6.QtGui import QGuiApplication
        Qt = QGuiApplication.instance() or QGuiApplication([])
    return Qt


class TestRowHeight(unittest.TestCase):
    def test_a_measured_gpu_adds_a_line(self):
        from ostlr.qt import rowdelegate
        with_gpu = rowdelegate.height_for({"cpu": 5, "mem": 3, "gpu": 9})
        without = rowdelegate.height_for({"cpu": 5, "mem": 3})
        self.assertEqual(rowdelegate.GAUGE_H, with_gpu - without)

    def test_no_measurement_is_no_line_not_a_zero_bar(self):
        from ostlr.qt import rowdelegate
        labels = [line[0] for line in rowdelegate.gauge_lines({"cpu": 1, "mem": 2})]
        self.assertEqual(["CPU", "MEM"], labels)
        labels = [line[0] for line in rowdelegate.gauge_lines(
            {"cpu": 1, "mem": 2, "gpu": None})]
        self.assertEqual(["CPU", "MEM"], labels)

    def test_cpu_first_then_mem_then_gpu(self):
        from ostlr.qt import rowdelegate
        labels = [line[0] for line in rowdelegate.gauge_lines(
            {"cpu": 1, "mem": 2, "gpu": 3})]
        self.assertEqual(["CPU", "MEM", "GPU"], labels)


class TestRowActions(unittest.TestCase):
    def _rect(self):
        from PySide6.QtCore import QRectF
        return QRectF(0, 0, 420, 100)

    def test_the_two_slots_are_where_a_click_is_read(self):
        from ostlr.qt import rowdelegate
        rect = self._rect()
        slots = rowdelegate.action_rects(rect)
        self.assertEqual(2, len(slots))
        self.assertEqual("terminal", rowdelegate.action_at(rect, slots[0].center()))
        self.assertEqual("close", rowdelegate.action_at(rect, slots[1].center()))

    def test_a_click_on_the_text_is_not_an_action(self):
        from PySide6.QtCore import QPointF
        from ostlr.qt import rowdelegate
        rect = self._rect()
        self.assertIsNone(rowdelegate.action_at(rect, QPointF(40, 10)))

    def test_the_slots_stay_inside_the_row(self):
        from ostlr.qt import rowdelegate
        rect = self._rect()
        for slot in rowdelegate.action_rects(rect):
            self.assertGreaterEqual(slot.left(), rect.left())
            self.assertLessEqual(slot.right(), rect.right())


class TestTreeSendIcons(unittest.TestCase):
    def test_a_click_on_a_send_icon_names_its_prefix(self):
        from PySide6.QtCore import QRectF
        from ostlr.qt import icons, tree
        rect = QRectF(0, 0, 400, 22)
        slots = icons.right_aligned(rect, 2)
        self.assertEqual("path", tree.send_action_at(rect, slots[0].center()))
        self.assertEqual("at", tree.send_action_at(rect, slots[1].center()))
        self.assertIsNone(tree.send_action_at(rect, QRectF(0, 0, 400, 22).center()))


class TestIconsPaintSomething(unittest.TestCase):
    def test_every_named_icon_marks_its_rect(self):
        try:
            _app()
        except Exception as exc:                 # no Qt, no painting
            self.skipTest("Qt unavailable: %s" % exc)
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        from ostlr.qt import icons

        for name in icons.NAMES:
            with self.subTest(name=name):
                canvas = QImage(24, 24, QImage.Format_ARGB32)
                canvas.fill(0)
                painter = QPainter(canvas)
                try:
                    icons.paint(painter, name, QRectF(0, 0, 24, 24), "fg")
                finally:
                    painter.end()
                painted = sum(1 for y in range(24) for x in range(24)
                              if canvas.pixelColor(x, y).alpha() > 0)
                self.assertGreater(painted, 6, "%s drew nothing" % name)


if __name__ == "__main__":
    unittest.main()
