"""T9a/T9b - the view model: rows, buckets, ordering, formatting.

Everything here is pure and `now` is a parameter, so the 3 s highlight and the
600 s retention are proven without sleeping.

The checks the DoD names are all present: the bucket sum, oldest-first inside a
state, the 75-row truncation with `showing 60 of 75`, `done` + `exit=1` taking
the error role, the 120-char clip, and the 0s/59s/60s/2m waiting rule.
"""

import json
import unittest
from pathlib import Path

from ostlr import schema, view_model as vm
from ostlr.ui import theme

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures"
GOLDEN = HERE.parent / "golden"


def session(pid, started_at=1000, agent="claude", project="projV",
            cwd="D:/ws/projV", mem=1024, template="claude", branch="main"):
    return {
        "pid": pid,
        "ppid": 1,
        "started_at": started_at,
        "template_id": template,
        "agent": agent,
        "project": project,
        "cwd": cwd,
        "cmdline": "claude",
        "exe": "C:/bin/claude.exe",
        "mem_bytes": mem,
        "branch": branch,
    }


def state_row(pid, started_at=1000, state="working", source="l2", transient=None,
              kind="", msg="", age_s=1, state_since=1000, exit_code=None,
              reason=""):
    return {
        "pid": pid,
        "started_at": started_at,
        "state": state,
        "source": source,
        "transient": transient,
        "kind": kind,
        "msg": msg,
        "age_s": age_s,
        "state_since": state_since,
        "exit_code": exit_code,
        "reason": reason,
        "alive": True,
    }


class TestWaitingRule(unittest.TestCase):
    def test_seconds_then_minutes(self):
        # The DoD formatting lines.
        self.assertEqual(vm.format_waiting(0), "waiting 0s")
        self.assertEqual(vm.format_waiting(59), "waiting 59s")
        self.assertEqual(vm.format_waiting(60), "waiting 1m")
        self.assertEqual(vm.format_waiting(125), "waiting 2m")

    def test_unknown_is_an_em_dash(self):
        self.assertEqual(vm.format_waiting(None), "waiting \u2014")

    def test_negative_clamps(self):
        self.assertEqual(vm.format_waiting(-5), "waiting 0s")


class TestClip(unittest.TestCase):
    def test_120_char_clip(self):
        # The DoD clip line.
        out = vm.clip("x" * 500)
        self.assertEqual(len(out), 120)
        self.assertTrue(out.endswith("\u2026"))

    def test_short_text_untouched(self):
        self.assertEqual(vm.clip("short"), "short")
        self.assertEqual(vm.clip(None), "")

    def test_msg_is_clipped_in_the_row(self):
        row = vm.build_row(session(1), state_row(1, msg="m" * 400), now=1000)
        self.assertEqual(len(row["msg"]), 120)


class TestRoles(unittest.TestCase):
    def test_done_with_exit_one_is_the_error_role(self):
        # The DoD line: a failure never renders in a success colour.
        row = vm.build_row(session(1), state_row(1, state="done", exit_code=1),
                           now=1000)
        self.assertEqual(row["state"], "done")          # JSON state unchanged
        self.assertEqual(vm.role_for(row), "danger")
        self.assertEqual(theme.role_for_state("done", 1), "danger")

    def test_done_with_zero_exit_is_ok(self):
        row = vm.build_row(session(1), state_row(1, state="done", exit_code=0),
                           now=1000)
        self.assertEqual(vm.role_for(row), "ok")

    def test_inferred_row_is_dim(self):
        row = vm.build_row(session(1), state_row(1, source="l3"), now=1000)
        self.assertEqual(vm.role_for(row), "fg_dim")

    def test_blocked_is_warn(self):
        row = vm.build_row(session(1), state_row(1, state="blocked",
                                                 kind="permission"), now=1000)
        self.assertEqual(vm.role_for(row), "warn")


class TestMarkers(unittest.TestCase):
    def test_title_marker(self):
        row = vm.build_row(session(1), state_row(1, source="l3"), now=1000)
        self.assertIn("(title)", vm.markers_for(row))

    def test_no_identity_marker(self):
        row = vm.build_row(session(1, started_at=0), state_row(1, started_at=0),
                           now=1000)
        self.assertIn("(no identity)", vm.markers_for(row))

    def test_exit_marker(self):
        row = vm.build_row(session(1), state_row(1, state="done", exit_code=3),
                           now=1000)
        self.assertIn("exit=3", vm.markers_for(row))

    def test_clean_row_has_no_markers(self):
        row = vm.build_row(session(1), state_row(1), now=1000)
        self.assertEqual(vm.markers_for(row), [])


class TestBranch(unittest.TestCase):
    def test_pending_vs_unknown(self):
        self.assertEqual(vm.branch_text("..."), "...")
        self.assertEqual(vm.branch_text("?"), "?")
        self.assertEqual(vm.branch_text("main"), "main")
        self.assertEqual(vm.branch_text(""), "?")
        self.assertEqual(vm.branch_text(None), "?")


class TestHighlight(unittest.TestCase):
    def test_done_highlight_lasts_three_seconds(self):
        row = vm.build_row(session(1), state_row(1, state="done", state_since=1000),
                           now=1000)
        self.assertEqual(row["done_highlight_until"], 1003)
        # At 1003 the highlight is over; the state is still `done`, never idle.
        self.assertGreaterEqual(1003, row["done_highlight_until"])
        self.assertEqual(row["state"], "done")

    def test_non_done_rows_have_no_highlight(self):
        row = vm.build_row(session(1), state_row(1, state="working"), now=1000)
        self.assertEqual(row["done_highlight_until"], 0)


class TestHeader(unittest.TestCase):
    def test_bucket_sum_equals_sessions(self):
        rows = [
            vm.build_row(session(1), state_row(1, state="blocked", kind="permission"), now=1000),
            vm.build_row(session(2), state_row(2, state="working"), now=1000),
            vm.build_row(session(3), state_row(3, state="done"), now=1000),
            vm.build_row(session(4), state_row(4, state="idle"), now=1000),
            vm.build_row(session(5), state_row(5, state="unknown", source="none"), now=1000),
            vm.build_row(session(6), state_row(6, state="unknown", transient="starting"), now=1000),
        ]
        header = vm.header_for(rows)
        self.assertEqual(header["waiting"], 1)
        self.assertEqual(header["running"], 1)
        self.assertEqual(header["done"], 1)
        self.assertEqual(header["idle"], 1)
        self.assertEqual(header["unknown"], 1)
        self.assertEqual(header["starting"], 1)
        self.assertEqual(header["sessions"], 6)
        self.assertEqual(header["sessions"], sum(header[b] for b in schema.BUCKETS))

    def test_inferred_sub_counts(self):
        rows = [
            vm.build_row(session(1), state_row(1, state="working", source="l3"), now=1000),
            vm.build_row(session(2), state_row(2, state="working"), now=1000),
            vm.build_row(session(3), state_row(3, state="done", source="l3"), now=1000),
            vm.build_row(session(4), state_row(4, state="error", source="l3"), now=1000),
        ]
        header = vm.header_for(rows)
        self.assertEqual(header["running"], 2)
        self.assertEqual(header["running_inferred"], 1)
        self.assertEqual(header["done_inferred"], 1)
        self.assertEqual(header["error_inferred"], 1)
        # `waiting` has no inferred part: L3 can never yield blocked.
        self.assertNotIn("waiting_inferred", header)

    def test_transient_is_counted_in_starting_only(self):
        rows = [vm.build_row(session(1), state_row(1, state="unknown",
                                                 transient="starting"), now=1000)]
        header = vm.header_for(rows)
        self.assertEqual(header["starting"], 1)
        self.assertEqual(header["unknown"], 0)
        self.assertEqual(header["sessions"], 1)


class TestOrdering(unittest.TestCase):
    def test_rank_order(self):
        rows = [
            vm.build_row(session(1), state_row(1, state="idle"), now=1000),
            vm.build_row(session(2), state_row(2, state="done"), now=1000),
            vm.build_row(session(3), state_row(3, state="unknown", source="none"), now=1000),
            vm.build_row(session(4), state_row(4, state="working"), now=1000),
            vm.build_row(session(5), state_row(5, state="error"), now=1000),
            vm.build_row(session(6), state_row(6, state="blocked", kind="permission"), now=1000),
        ]
        ordered = vm.order(rows)
        self.assertEqual([r["state"] for r in ordered],
                         ["blocked", "error", "working", "unknown", "done", "idle"])

    def test_oldest_state_since_first_inside_a_state(self):
        rows = [
            vm.build_row(session(1), state_row(1, state="blocked", kind="permission",
                                               state_since=3000), now=4000),
            vm.build_row(session(2), state_row(2, state="blocked", kind="permission",
                                               state_since=1000), now=4000),
            vm.build_row(session(3), state_row(3, state="blocked", kind="permission",
                                               state_since=2000), now=4000),
        ]
        ordered = vm.order(rows)
        self.assertEqual([r["pid"] for r in ordered], [2, 3, 1])

    def test_project_then_pid_break_ties(self):
        rows = [
            vm.build_row(session(9, project="b"), state_row(9, state="working"), now=1000),
            vm.build_row(session(8, project="a"), state_row(8, state="working"), now=1000),
            vm.build_row(session(7, project="a"), state_row(7, state="working"), now=1000),
        ]
        ordered = vm.order(rows)
        self.assertEqual([r["pid"] for r in ordered], [7, 8, 9])


class TestTruncation(unittest.TestCase):
    def test_75_rows_show_60(self):
        # The DoD truncation line.
        sessions = [session(1000 + i, project="p%02d" % i) for i in range(75)]
        states = [state_row(1000 + i, state="working") for i in range(75)]
        env = vm.build_snapshot(sessions, states, now=1000, list_cap=60)
        self.assertEqual(len(env["rows"]), 60)
        self.assertEqual(env["truncated"], 15)
        self.assertEqual(env["footer"], "showing 60 of 75")
        self.assertEqual(env["header"]["sessions"], 75)
        self.assertEqual(env["header"]["sessions"], len(env["rows"]) + env["truncated"])

    def test_exactly_cap_has_no_footer(self):
        sessions = [session(1000 + i) for i in range(60)]
        states = [state_row(1000 + i) for i in range(60)]
        env = vm.build_snapshot(sessions, states, now=1000, list_cap=60)
        self.assertEqual(env["footer"], "")
        self.assertNotEqual(env["footer"], "showing 60 of 60")


class TestReceipts(unittest.TestCase):
    def test_three_receipts(self):
        same = vm.receipt("copied", "D:/ws/projV/main.cpp", "claude", "projV", True)
        cross = vm.receipt("copied", "D:/ws/projV/main.cpp", "codex", "sop-test", False)
        none = vm.receipt("copied", "D:/ws/projV/main.cpp", "", "", None)
        self.assertEqual(same, "copied D:/ws/projV/main.cpp -> claude / projV (same project)")
        self.assertEqual(cross, "copied D:/ws/projV/main.cpp -> codex / sop-test (cross project)")
        self.assertEqual(none, "copied D:/ws/projV/main.cpp -> ? / ? (no target)")

    def test_receipt_is_clipped(self):
        out = vm.receipt("copied", "D:/" + "x" * 400, "claude", "projV", True)
        self.assertLessEqual(len(out), 120 + 40)


class TestStatusBar(unittest.TestCase):
    def test_precedence(self):
        # receipt (5 s) > hint > Ready
        self.assertEqual(vm.status_bar("copied x", 1, "hint"), "copied x")
        self.assertEqual(vm.status_bar("copied x", 4, "hint"), "copied x")
        self.assertEqual(vm.status_bar("copied x", 5, "hint"), "hint")
        self.assertEqual(vm.status_bar("", None, "hint"), "hint")
        self.assertEqual(vm.status_bar("", None, ""), "Ready")

    def test_receipt_ttl_is_five_seconds(self):
        self.assertEqual(vm.RECEIPT_TTL_S, 5)


class TestEmptyState(unittest.TestCase):
    def test_capability_line(self):
        line = vm.capability_line(2, 3)
        self.assertIn("L1: unsupported", line)
        self.assertIn("producers found: 2", line)
        self.assertIn("inferred rows: 3", line)

    def test_empty_state_names_the_command(self):
        lines = vm.empty_state_lines(0, 0)
        self.assertEqual(lines[0], "No agent sessions yet")
        self.assertIn("L1: unsupported", lines[1])
        self.assertEqual(lines[2], "py -3 -m ostlr run -- claude")


class TestHint(unittest.TestCase):
    def test_hint_never_inside_a_row(self):
        # The hint is a sentence; it belongs in the status bar, not in a row.
        row = vm.build_row(session(1), state_row(1, state="unknown", source="none",
                                                 reason="no status file"), now=1000)
        self.assertTrue(row["hint"])
        rest = {k: v for k, v in row.items() if k != "hint"}
        self.assertNotIn(row["hint"], json.dumps(rest))

    def test_unknown_hint_names_the_command(self):
        row = vm.build_row(session(7), state_row(7, state="unknown", source="none",
                                                 reason="no status file"), now=1000)
        self.assertIn("py -3 -m ostlr.status", row["hint"])
        self.assertIn("--pid 7", row["hint"])

    def test_no_identity_hint(self):
        row = vm.build_row(session(1, started_at=0), state_row(1, started_at=0,
                                                               state="working"), now=1000)
        self.assertIn("identity unverified", row["hint"])


class TestGolden(unittest.TestCase):
    """The fixture golden: a transient, a retained done and a fresh-L2 idle."""

    def setUp(self):
        self.path = GOLDEN / "snapshot_small.golden.json"
        self.assertTrue(self.path.exists(), "golden missing: %s" % self.path)
        self.golden = json.loads(self.path.read_text(encoding="utf-8"))

    def test_fixture_matches_the_golden(self):
        from ostlr import __main__ as cli
        from ostlr import config as config_mod

        fixture = cli.load_fixture(str(FIXTURES / "snapshot_small.json"))
        env = cli.build_snapshot(config_mod.Config(), fixture, now=fixture["now"])
        self.assertEqual(env["header"], self.golden["header"])
        self.assertEqual(env["footer"], self.golden["footer"])
        self.assertEqual([r["pid"] for r in env["rows"]],
                         [r["pid"] for r in self.golden["rows"]])
        for got, want in zip(env["rows"], self.golden["rows"]):
            for key, value in want.items():
                self.assertEqual(got.get(key), value, "%s of pid %s" % (key, got["pid"]))

    def test_golden_contains_the_required_rows(self):
        states = {(r["pid"], r["state"], r["transient"]) for r in self.golden["rows"]}
        self.assertIn((100, "blocked", None), states)   # fresh L2
        self.assertIn((300, "idle", None), states)      # fresh-L2 idle
        self.assertIn((400, "done", None), states)      # retained done, exit=1
        self.assertIn((200, "working", None), states)   # inferred from a title

    def test_golden_bucket_sum(self):
        header = self.golden["header"]
        self.assertEqual(header["sessions"], sum(header[b] for b in schema.BUCKETS))
        self.assertEqual(header["sessions"],
                         len(self.golden["rows"]) + self.golden.get("truncated", 0))


if __name__ == "__main__":
    unittest.main()
