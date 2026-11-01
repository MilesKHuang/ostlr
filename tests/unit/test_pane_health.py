"""What the pane says about workload, and what it is allowed to claim.

Three defects lived here, all of them "the panel states something it did not
measure":

  * CPU was a permanent 0% - `psutil.cpu_percent(interval=None)` needs the same
    Process object between two calls, and a fresh object every tick answers 0.0;
  * memory was a percentage of 64 GB, so a 200 MB session read "0%";
  * `End session` passed `started_at: 0`, which the identity guard refuses, so the
    pane stopped while the process kept running.

And one design rule, from the user: a state is a report, not a heartbeat. A live
pane's `blocked` must survive any amount of silence.
"""

import os
import unittest
from pathlib import Path

from ostlr.qt import procstat

QT = Path(__file__).resolve().parent.parent.parent / "ostlr" / "qt"


def read(name: str) -> str:
    return (QT / name).read_text(encoding="utf-8")


class TestCpuIsMeasured(unittest.TestCase):
    def test_one_core_for_one_second_on_four_cores_is_25_percent(self):
        self.assertAlmostEqual(25.0, procstat.percent_between(1.0, 2.0, 1.0, 4))

    def test_no_interval_is_unknown_not_zero(self):
        # The first sample of a pid has nothing to subtract from. Reporting 0.0
        # there is how "cpu is stuck at 0%" started.
        self.assertIsNone(procstat.percent_between(1.0, 2.0, 0.0, 4))

    def test_a_negative_delta_never_reports_negative(self):
        self.assertEqual(0.0, procstat.percent_between(2.0, 1.0, 1.0, 4))

    def test_it_is_capped_at_a_whole_machine(self):
        self.assertEqual(100.0, procstat.percent_between(0.0, 100.0, 1.0, 1))

    def test_a_broken_number_is_unknown_not_a_crash(self):
        self.assertIsNone(procstat.percent_between(None, 2.0, 1.0, 4))

    def test_the_creation_time_of_this_process_is_readable(self):
        # This is the identity a kill is verified against.
        self.assertGreater(procstat.start_epoch(os.getpid()), 0)

    def test_memory_is_shown_in_bytes_not_a_percentage(self):
        self.assertIn('human_mb(row.get("rss"))', read("rowdelegate.py"))


class TestKillIsWired(unittest.TestCase):
    def test_the_menu_reads_the_real_identity(self):
        source = read("app.py")
        self.assertIn("procstat.start_epoch(pid)", source)
        self.assertIn("identity_probe=procstat.start_epoch", source)

    def test_nothing_asks_for_a_kill_without_an_identity(self):
        self.assertNotIn('"started_at": 0', read("app.py"))

    def test_the_pane_stops_only_after_the_kill_was_accepted(self):
        # Stopping the pane first made a refused kill look successful: the row
        # vanished and the process kept running.
        source = read("app.py")
        kill_at = source.index("actions_mod.kill(")
        stop_at = source.index("pane.stop()", kill_at)
        self.assertLess(kill_at, stop_at)


class TestStateIsHeld(unittest.TestCase):
    def test_a_live_panes_report_is_never_aged_out(self):
        # `l2_stale_s` is for producers whose liveness we cannot see. A pane owns
        # its PTY, so the process being alive keeps its last word valid.
        self.assertNotIn("age > stale_after", read("app.py"))

    def test_silence_withdraws_only_the_panes_own_guess(self):
        source = read("terminal.py")
        self.assertIn('!= "ostlr-pane"', source)

    def test_an_l1_report_is_never_overwritten_by_the_pane(self):
        self.assertIn('== "l1"', read("terminal.py"))


if __name__ == "__main__":
    unittest.main()
