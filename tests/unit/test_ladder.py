"""T5 - the ladder: precedence, the never-idle property, and the ordering.

The property test is the important one. It walks the whole grid of
(state x age x alive) and asserts that `idle` appears ONLY when a fresh L2 file
literally says `idle`. That is the anchor test from the plan (section 1, rule 2)
expressed as an exhaustive check instead of a promise.
"""

import unittest

from ostlr import config as config_mod
from ostlr import ladder, schema


def l2rec(state="working", kind="", ts=1000, msg="", cwd="D:/ws/a"):
    return {"pid": 100, "ts_epoch": ts, "source": "test", "cwd": cwd,
            "state": state, "kind": kind, "msg": msg, "exit_code": None}


CFG = config_mod.Config()          # l2_stale_s = 90
NOW = 2000


class TestPrecedence(unittest.TestCase):
    def test_fresh_l2_wins_over_title(self):
        got = ladder.combine(l2rec(state="blocked", kind="permission", ts=NOW - 10),
                             "claude - thinking", True, NOW, CFG)
        self.assertEqual(got["state"], "blocked")
        self.assertEqual(got["source"], "l2")
        self.assertEqual(got["kind"], "permission")
        self.assertEqual(got["age_s"], 10)

    def test_stale_l2_falls_through_to_title(self):
        got = ladder.combine(l2rec(state="blocked", kind="permission", ts=NOW - 91),
                             "claude - thinking", True, NOW, CFG)
        self.assertEqual(got["state"], "working")
        self.assertEqual(got["source"], "l3")

    def test_stale_l2_without_title_is_unknown_with_reason(self):
        got = ladder.combine(l2rec(state="blocked", kind="permission", ts=NOW - 91),
                             None, True, NOW, CFG)
        self.assertEqual(got["state"], "unknown")
        self.assertEqual(got["source"], "none")
        self.assertEqual(got["reason"], ladder.REASON_STALE)
        self.assertEqual(got["age_s"], 91)

    def test_no_l2_no_title_is_unknown(self):
        got = ladder.combine(None, None, True, NOW, CFG)
        self.assertEqual(got["state"], "unknown")
        self.assertEqual(got["reason"], ladder.REASON_NO_DATA)

    def test_no_l2_with_unclassified_title_is_unknown(self):
        got = ladder.combine(None, "Windows PowerShell", True, NOW, CFG)
        self.assertEqual(got["state"], "unknown")
        self.assertEqual(got["reason"], ladder.REASON_NO_TITLE)

    def test_dead_process_is_done(self):
        got = ladder.combine(l2rec(state="working", ts=NOW - 5), None, False, NOW, CFG)
        self.assertEqual(got["state"], "done")
        self.assertEqual(got["reason"], ladder.REASON_DEAD)

    def test_l3_can_never_say_blocked_or_idle(self):
        for title in ("waiting for approval", "permission required", "idle",
                      "blocked", "Approve rm -rf build?"):
            got = ladder.combine(None, title, True, NOW, CFG)
            self.assertNotIn(got["state"], ("blocked", "idle"), title)


class TestStalenessBoundary(unittest.TestCase):
    def test_89s_is_fresh(self):
        got = ladder.combine(l2rec(state="blocked", kind="permission", ts=NOW - 89),
                             None, True, NOW, CFG)
        self.assertEqual(got["state"], "blocked")
        self.assertEqual(got["source"], "l2")

    def test_91s_is_stale(self):
        got = ladder.combine(l2rec(state="blocked", kind="permission", ts=NOW - 91),
                             None, True, NOW, CFG)
        self.assertEqual(got["state"], "unknown")
        self.assertEqual(got["source"], "none")

    def test_90s_is_still_fresh(self):
        got = ladder.combine(l2rec(state="working", ts=NOW - 90), None, True, NOW, CFG)
        self.assertEqual(got["state"], "working")

    def test_cfg_override_moves_the_boundary(self):
        cfg = config_mod.Config(l2_stale_s=30)
        got = ladder.combine(l2rec(state="working", ts=NOW - 45), None, True, NOW, cfg)
        self.assertEqual(got["state"], "unknown")


class TestNeverIdleProperty(unittest.TestCase):
    def test_idle_only_with_a_fresh_l2_file(self):
        states = ["idle", "working", "blocked", "done", "error"]
        ages = [0, 89, 91, 600]
        titles = [None, "claude - thinking", "projV - done", "error: boom",
                  "Windows PowerShell"]
        checked = 0
        for state in states:
            for age in ages:
                for alive in (True, False):
                    for title in titles:
                        kind = "permission" if state == "blocked" else ""
                        rec = l2rec(state=state, kind=kind, ts=NOW - age)
                        got = ladder.combine(rec, title, alive, NOW, CFG)
                        checked += 1
                        if got["state"] == "idle":
                            self.assertTrue(
                                alive and got["source"] == "l2" and age <= 90,
                                "idle without a fresh L2 file: %r" % (got,))
                            self.assertEqual(state, "idle")
        self.assertEqual(checked, 5 * 4 * 2 * 5)

    def test_no_l2_ever_yields_idle(self):
        for age in (0, 89, 91, 600):
            for title in (None, "thinking", "done", "error"):
                got = ladder.combine(None, title, True, NOW, CFG)
                self.assertNotEqual(got["state"], "idle")


class TestOrdering(unittest.TestCase):
    def test_rank_order(self):
        rows = [
            {"pid": 1, "state": "idle", "state_since": 10, "project": "a"},
            {"pid": 2, "state": "done", "state_since": 10, "project": "a"},
            {"pid": 3, "state": "unknown", "state_since": 10, "project": "a"},
            {"pid": 4, "state": "working", "state_since": 10, "project": "a"},
            {"pid": 5, "state": "error", "state_since": 10, "project": "a"},
            {"pid": 6, "state": "blocked", "state_since": 10, "project": "a"},
        ]
        ordered = ladder.order(rows)
        self.assertEqual([r["state"] for r in ordered],
                         ["blocked", "error", "working", "unknown", "done", "idle"])

    def test_oldest_state_since_first_inside_a_state(self):
        rows = [
            {"pid": 1, "state": "blocked", "state_since": 300, "project": "a"},
            {"pid": 2, "state": "blocked", "state_since": 100, "project": "b"},
            {"pid": 3, "state": "blocked", "state_since": 200, "project": "c"},
        ]
        ordered = ladder.order(rows)
        self.assertEqual([r["pid"] for r in ordered], [2, 3, 1])

    def test_project_then_pid_break_ties(self):
        rows = [
            {"pid": 9, "state": "working", "state_since": 100, "project": "b"},
            {"pid": 8, "state": "working", "state_since": 100, "project": "a"},
            {"pid": 7, "state": "working", "state_since": 100, "project": "a"},
        ]
        ordered = ladder.order(rows)
        self.assertEqual([r["pid"] for r in ordered], [7, 8, 9])

    def test_missing_state_since_sorts_first(self):
        rows = [
            {"pid": 1, "state": "working", "state_since": 100, "project": "a"},
            {"pid": 2, "state": "working", "project": "a"},
        ]
        ordered = ladder.order(rows)
        self.assertEqual([r["pid"] for r in ordered], [2, 1])

    def test_rank_of(self):
        self.assertEqual(ladder.rank_of("blocked"), 0)
        self.assertEqual(ladder.rank_of("idle"), 5)
        self.assertEqual(ladder.rank_of(None), schema.rank_for("unknown"))


class TestUnknownForDir(unittest.TestCase):
    def test_unreadable_dir_is_unknown_not_idle(self):
        got = ladder.unknown_for_dir("status_dir_unreadable(C:/x)")
        self.assertEqual(got["state"], "unknown")
        self.assertIn("status_dir_unreadable", got["reason"])


if __name__ == "__main__":
    unittest.main()
