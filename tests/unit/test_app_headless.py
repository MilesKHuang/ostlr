"""T11 - the panel's headless half: header, rows, empty state, banner.

Nothing here creates a window. The panel's rendering decisions are pure
functions of a snapshot, so they are proven without Tk: the header's fixed
slots, the golden fixture render, the empty-state capability line, the
degraded banner, and the rule that the view owns no user-facing literal.
"""

import json
import os
import re
import unittest
from pathlib import Path

from ostlr import config as config_mod
from ostlr import view_model
from ostlr.ui import app as app_mod
from ostlr.ui import rows as ui_rows

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures"
GOLDEN = HERE.parent / "golden"
APP_PY = Path(app_mod.__file__)


def header(**kw):
    base = {"waiting": 0, "running": 0, "running_inferred": 0, "error": 0,
            "error_inferred": 0, "done": 0, "idle": 0, "unknown": 0,
            "starting": 0, "sessions": 0}
    base.update(kw)
    return base


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def configure(self, **kw):
        self.text = kw.get("text", self.text)


class TestHeaderSlots(unittest.TestCase):
    """The header must not reflow: the three that matter keep a fixed slot."""

    def setUp(self):
        # header_text is a pure method; it needs no window.
        self.app = app_mod.PanelApp.__new__(app_mod.PanelApp)

    def test_fixed_slots_render_at_zero(self):
        text = self.app.header_text(header())
        self.assertIn("waiting 0", text)
        self.assertIn("running 0", text)
        self.assertIn("unknown 0", text)

    def test_zero_buckets_are_hidden(self):
        text = self.app.header_text(header())
        for key in ("error", "done", "idle", "starting"):
            self.assertNotIn(key, text)

    def test_non_zero_buckets_appear_in_rank_order(self):
        text = self.app.header_text(header(waiting=1, error=2, done=3, idle=4,
                                           starting=5, sessions=15))
        order = [text.index(k) for k in ("waiting", "error", "done", "idle", "starting")]
        self.assertEqual(order, sorted(order))

    def test_total_is_last(self):
        text = self.app.header_text(header(waiting=1, sessions=1))
        self.assertTrue(text.endswith("sessions 1"), text)

    def test_one_line_never_wrapped(self):
        text = self.app.header_text(header(waiting=1, running=2, error=3, done=4,
                                           idle=5, starting=6, unknown=7, sessions=28))
        self.assertNotIn("\n", text)


class TestGoldenRender(unittest.TestCase):
    """The fixture golden, rendered the way the panel renders it."""

    def setUp(self):
        from ostlr import __main__ as cli

        fixture = cli.load_fixture(str(FIXTURES / "snapshot_small.json"))
        self.snapshot = cli.build_snapshot(config_mod.Config(), fixture,
                                           now=fixture["now"])
        self.app = app_mod.PanelApp.__new__(app_mod.PanelApp)

    def test_header_line(self):
        text = self.app.header_text(self.snapshot["header"])
        self.assertEqual(
            text,
            "waiting 1  running 1  unknown 0  done 1  idle 1  sessions 4")

    def test_two_row_lines(self):
        rows = self.snapshot["rows"]
        self.assertGreaterEqual(len(rows), 2)
        rendered = [ui_rows.render_row(r, ui_rows.LIST_COLUMN_PX, len,
                                       mono_linespace=16) for r in rows[:2]]
        first = " ".join(rendered[0]["line1"])
        second = " ".join(rendered[1]["line1"])
        self.assertIn("claude", first)
        self.assertIn("projV", first)
        self.assertIn("codex", second)
        # The state word and the markers are on line 2.
        self.assertIn("blocked", " ".join(rendered[0]["line2"]))
        self.assertIn("(title)", " ".join(rendered[1]["line2"]))

    def test_footer_is_empty_under_the_cap(self):
        self.assertEqual(self.snapshot["footer"], "")

    def test_footer_states_the_truncation(self):
        sessions = [{"pid": 1000 + i, "started_at": 1, "agent": "a",
                     "project": "p%02d" % i, "cwd": "c", "mem_bytes": 1}
                    for i in range(75)]
        states = [{"pid": 1000 + i, "started_at": 1, "state": "working",
                   "source": "l2", "state_since": 1} for i in range(75)]
        env = view_model.build_snapshot(sessions, states, now=1000, list_cap=60)
        self.assertEqual(env["footer"], "showing 60 of 75")


class TestEmptyState(unittest.TestCase):
    def test_capability_line_is_in_the_users_words(self):
        lines = view_model.empty_state_lines(2, 3)
        self.assertEqual(lines[0], "No agent sessions yet")
        self.assertIn("L1: unsupported", lines[1])
        self.assertIn("producers found: 2", lines[1])
        self.assertIn("inferred rows: 3", lines[1])
        self.assertEqual(lines[2], "py -3 -m ostlr run -- claude")


class TestBanner(unittest.TestCase):
    def test_stale_snapshot_shows_the_banner(self):
        app = app_mod.PanelApp.__new__(app_mod.PanelApp)
        app.snapshot = {"stale": "stale, last good 12:00:01"}
        app.banner_label = _FakeLabel()
        app._render_banner()
        self.assertEqual(app.banner_label.text, "stale, last good 12:00:01")

    def test_absent_stale_key_is_an_empty_banner(self):
        app = app_mod.PanelApp.__new__(app_mod.PanelApp)
        app.snapshot = {}
        app.banner_label = _FakeLabel()
        app._render_banner()
        self.assertEqual(app.banner_label.text, "")


class TestNoLiteralsInTheView(unittest.TestCase):
    """Every displayed string comes from view_model, not from the view."""

    def setUp(self):
        self.source = APP_PY.read_text(encoding="utf-8")

    def test_no_ready_literal(self):
        # `Ready` is view_model.READY; a literal here would let the panel and
        # --once --json disagree about the same field.
        self.assertNotIn('"Ready"', self.source)
        self.assertNotIn("'Ready'", self.source)

    def test_no_receipt_or_marker_literals(self):
        for literal in ("(title)", "(no identity)", "same project",
                        "cross project", "no target", "showing "):
            self.assertNotIn('"%s"' % literal, self.source, literal)

    def test_no_state_word_literals(self):
        # The header's bucket NAMES are schema keys, not state words: they are
        # the same strings `--once --json` prints, so they are allowed. What is
        # not allowed is a state WORD chosen by the view (the row's word comes
        # from view_model.state_word).
        self.assertNotIn("state_word(", self.source.replace(
            "view_model.state_word(", ""))
        for literal in ('"blocked"', '"working"'):
            self.assertNotIn(literal, self.source, literal)

    def test_no_hex_colour_in_the_view(self):
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{6}\b", self.source), [])


class TestRobustness(unittest.TestCase):
    def test_absent_snapshot_does_not_crash(self):
        app = app_mod.PanelApp.__new__(app_mod.PanelApp)
        app.snapshot = {}
        app.banner_label = _FakeLabel()
        app._render_banner()
        self.assertEqual(app.banner_label.text, "")

    def test_header_with_missing_keys(self):
        app = app_mod.PanelApp.__new__(app_mod.PanelApp)
        text = app.header_text({})
        self.assertIn("waiting 0", text)
        self.assertIn("sessions 0", text)

    def test_header_with_garbage_values(self):
        app = app_mod.PanelApp.__new__(app_mod.PanelApp)
        text = app.header_text({"waiting": "x", "running": None, "sessions": "?"})
        self.assertIn("waiting 0", text)
        self.assertIn("sessions 0", text)


class TestReminder(unittest.TestCase):
    """T16's edge detector, proven without a window."""

    def test_one_bell_per_blocked_streak(self):
        reminder = app_mod.BlockedReminder()
        sequence = [0, 1, 1, 1, 0, 1]
        rang = [reminder.update(header(waiting=n))[1] for n in sequence]
        self.assertEqual(rang, [False, True, False, False, False, True])
        self.assertEqual(reminder.bells, 2)

    def test_prefix_appears_and_disappears(self):
        reminder = app_mod.BlockedReminder()
        self.assertEqual(reminder.update(header(waiting=2))[0], "! 2 waiting")
        self.assertEqual(reminder.update(header(waiting=0))[0], "")

    def test_title_prefix(self):
        self.assertEqual(app_mod.title_prefix(0), "")
        self.assertEqual(app_mod.title_prefix(1), "! 1 waiting")
        self.assertEqual(app_mod.title_prefix(7), "! 7 waiting")

    def test_compose_title(self):
        self.assertEqual(app_mod.compose_title("", "Ostlr"), "Ostlr")
        self.assertEqual(app_mod.compose_title("! 1 waiting", "Ostlr"),
                         "! 1 waiting - Ostlr")


if __name__ == "__main__":
    unittest.main()


class TestChords(unittest.TestCase):
    """T14's two chords, with a counted clipboard double."""

    def _app(self, clipboard):
        app = app_mod.PanelApp.__new__(app_mod.PanelApp)
        app.snapshot = {
            "v": 1,
            "header": {"sessions": 2},
            "rows": [
                {"pid": 100, "started_at": 1, "agent": "claude", "project": "projV",
                 "cwd": "D:/ws/projV/main.cpp", "branch": "main", "state": "unknown",
                 "source": "none", "kind": "", "msg": "", "age_s": 0,
                 "mem_bytes": 1, "elapsed_s": 1, "rank": 3,
                 "hint": "no status file - run: py -3 -m ostlr.status --pid 100",
                 "exit_code": None},
                {"pid": 200, "started_at": 1, "agent": "codex", "project": "sop",
                 "cwd": "D:/ws/sop/x.py", "branch": "main", "state": "working",
                 "source": "l2", "kind": "", "msg": "", "age_s": 0,
                 "mem_bytes": 1, "elapsed_s": 1, "rank": 2, "hint": "",
                 "exit_code": None},
            ],
            "footer": "",
        }
        app.selected_pid = None
        app.receipt_text = ""
        app.receipt_at = 0
        app._clipboard = clipboard
        app._copy_gate = None
        app.status_var = _FakeVar()
        return app

    def test_ctrl_shift_c_copies_the_path_with_the_exact_receipt(self):
        writer = _CountingWriter()
        app = self._app(writer)
        app.selected_pid = 100
        result = app.copy_selected_path()
        self.assertTrue(result["ok"])
        self.assertEqual(writer.calls, ["D:/ws/projV/main.cpp"])
        self.assertEqual(result["receipt"],
                         "copied D:/ws/projV/main.cpp -> claude / projV (same project)")
        self.assertEqual(app.status_var.value, result["receipt"])

    def test_ctrl_shift_c_cross_project(self):
        # A cross-project receipt is produced when the target pane belongs to a
        # different project than the copied path - the launcher's case.
        from ostlr import actions

        writer = _CountingWriter()
        result = actions.copy_path("D:/ws/projV/main.cpp",
                                   {"agent": "codex", "project": "sop"},
                                   "projV", clipboard=writer)
        self.assertIn("(cross project)", result["receipt"])

    def test_ctrl_shift_r_copies_the_hint(self):
        writer = _CountingWriter()
        app = self._app(writer)
        app.selected_pid = 100
        result = app.copy_selected_hint()
        self.assertTrue(result["ok"])
        self.assertEqual(writer.calls,
                         ["no status file - run: py -3 -m ostlr.status --pid 100"])
        self.assertEqual(app.status_var.value, result["receipt"])

    def test_ctrl_shift_r_uses_the_same_gate_and_receipt(self):
        """The verifier's finding: the hint copy must not bypass the gate.

        Two Ctrl+Shift+R inside 100 ms write ONCE, and the receipt names what
        was copied and to whom - the same shape as Ctrl+Shift+C.
        """
        from ostlr import actions

        writer = _CountingWriter()
        app = self._app(writer)
        clock = [0.0]
        app._copy_gate = actions.CopyGate(clock=lambda: clock[0])
        app.selected_pid = 100

        first = app.copy_selected_hint()
        clock[0] = 0.05
        second = app.copy_selected_hint()

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(first["wrote"])
        self.assertFalse(second["wrote"])
        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(
            first["receipt"],
            "copied hint no status file - run: py -3 -m ostlr.status --pid 100"
            " -> claude / projV (same project)")
        self.assertEqual(app.status_var.value, first["receipt"])

    def test_no_selection_is_a_value(self):
        app = self._app(_CountingWriter())
        self.assertFalse(app.copy_selected_path()["ok"])
        self.assertFalse(app.copy_selected_hint()["ok"])

    def test_a_row_without_a_hint_says_so(self):
        app = self._app(_CountingWriter())
        app.selected_pid = 200
        result = app.copy_selected_hint()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no hint")


class _CountingWriter:
    def __init__(self):
        self.calls = []

    def write(self, text):
        self.calls.append(text)
        return {"ok": True, "reason": "copied", "text": text}


class _FakeVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class TestLaunchWiring(unittest.TestCase):
    """A launch publishes a `starting` row through the injected double."""

    def _app(self, launch_fn):
        app = app_mod.PanelApp.__new__(app_mod.PanelApp)
        app.cfg = config_mod.Config()
        app.snapshot = {"v": 1, "header": {}, "rows": [], "footer": ""}
        app.selected_pid = None
        app.receipt_text = ""
        app.receipt_at = 0
        app.starting_pids = []
        app.last_launch_error = ""
        app._launch_fn = launch_fn
        app._clipboard = None
        app._copy_gate = None
        app.status_var = _FakeVar()
        return app

    def test_launch_records_the_starting_pid(self):
        seen = {}

        def double(template, cwd, argv=None):
            seen["cwd"] = cwd
            seen["argv"] = argv
            return {"ok": True, "reason": "launched", "pid": 4242}

        app = self._app(double)
        result = app.launch_agent("claude", "D:/ws/projV")
        self.assertTrue(result["ok"])
        self.assertEqual(seen["cwd"], "D:/ws/projV")
        self.assertEqual(seen["argv"][3], "run")
        self.assertEqual(app.starting_pids, [4242])
        # The row appears on the next published snapshot (the store publishes
        # `starting` for that pid), so the bound is `tick_s + 1 s` by value.
        from ostlr import launcher as launcher_mod
        snapshot = {"rows": [{"pid": 4242, "transient": "starting"}]}
        self.assertTrue(launcher_mod.row_appears_within(snapshot, 4242,
                                                        app.cfg.tick_s))

    def test_a_failed_launch_is_inline_not_a_modal(self):
        def double(template, cwd, argv=None):
            return {"ok": False, "reason": "spawn_failed", "detail": "gone"}

        app = self._app(double)
        result = app.launch_agent("claude", "D:/ws")
        self.assertFalse(result["ok"])
        self.assertEqual(app.last_launch_error, "spawn_failed")
        self.assertEqual(app.status_var.value, "spawn_failed")

    def test_unknown_agent_is_a_value(self):
        app = self._app(lambda *a, **k: {"ok": True})
        result = app.launch_agent("nope", "D:/ws")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unknown_agent")
