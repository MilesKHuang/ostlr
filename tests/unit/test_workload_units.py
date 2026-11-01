"""GPU and memory numbers: summed over the tree, and never invented.

Two defects lived here:

  * GPU was looked up on the root pid only. The process holding an engine
    instance is usually a grandchild (shell -> interpreter -> agent), so a pane
    that was visibly using the GPU printed "-" for ever.
  * the row drew its bar at a fixed 52 px offset, which is narrower than
    "mem 313 MB" - the bar painted over the number.
"""

import unittest
from unittest import mock

from ostlr.qt import procstat


class TestGpuIsSummedOverTheTree(unittest.TestCase):
    def _sample(self, percents, memory, tree):
        with mock.patch.object(procstat, "gpu_by_pid", return_value=percents), \
                mock.patch.object(procstat, "gpu_mib_by_pid", return_value=memory), \
                mock.patch.object(procstat, "tree_pids", return_value=tree), \
                mock.patch.object(procstat, "tree_stats",
                                  side_effect=lambda pid: {"cpu": 1.0, "mem": 1.0,
                                                           "rss": 1_000_000}):
            return procstat.sample([tree[0]])[tree[0]]

    def test_a_grandchild_using_the_gpu_counts_for_the_session(self):
        # The measured case: proJV(24584) -> cmd(3672) -> python(25320), and the
        # GPU belonged to 25320.
        stats = self._sample({25320: 105.0}, {}, [24584, 3672, 25320])
        self.assertEqual(100.0, stats["gpu"])

    def test_several_instances_in_one_tree_add_up(self):
        stats = self._sample({10: 30.0, 11: 25.0}, {}, [10, 11])
        self.assertEqual(55.0, stats["gpu"])

    def test_no_measurement_is_none_not_zero(self):
        stats = self._sample({99: 40.0}, {}, [1, 2])
        self.assertIsNone(stats["gpu"])

    def test_a_junk_dedicated_memory_reading_is_dropped(self):
        # 219 GB was measured from a counter that had nothing to say.
        stats = self._sample({}, {1: 219421.0, 2: 512.0}, [1, 2])
        self.assertEqual(512.0, stats["gpu_mib"])
        junk = self._sample({}, {1: 219421.0}, [1])
        self.assertIsNone(junk["gpu_mib"])

    def test_the_tree_walk_survives_a_dead_pid(self):
        self.assertEqual([], procstat.tree_pids(0))


class TestTheRowLeavesRoomForItsNumber(unittest.TestCase):
    def test_the_bar_is_not_drawn_at_a_fixed_offset(self):
        # "mem 313 MB" is wider than 52 px; the bar started there and covered it.
        from pathlib import Path
        source = (Path(procstat.__file__).parent / "rowdelegate.py").read_text(
            encoding="utf-8")
        self.assertNotIn("bx + 52", source)
        self.assertIn("horizontalAdvance(text)", source)


if __name__ == "__main__":
    unittest.main()
