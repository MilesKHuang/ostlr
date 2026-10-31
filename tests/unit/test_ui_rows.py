"""T10 - row layout: elide, markers, columns, cap.

All pure, so this runs headless. The two checks the DoD names explicitly are
here: a 300-char command line elided with the leaf visible, and the same row
height with and without a marker.
"""

import unittest

from ostlr.ui import glyphs, rows, theme


def measure(text):
    """A deterministic stand-in for a font metric: 1 px per character."""
    return len(text)


def measure_wide(text):
    """A metric where every character is 8 px (a mono font at 9 pt)."""
    return len(text) * 8


class TestElide(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(rows.elide("abc", 100, measure), "abc")

    def test_empty(self):
        self.assertEqual(rows.elide("", 100, measure), "")

    def test_zero_budget(self):
        self.assertEqual(rows.elide("abcdef", 0, measure), rows.ELLIPSIS)
        self.assertEqual(rows.elide("abcdef", -5, measure), rows.ELLIPSIS)

    def test_head_kept(self):
        out = rows.elide("abcdefghij", 5, measure)
        self.assertTrue(out.endswith(rows.ELLIPSIS))
        self.assertTrue(out.startswith("abcd"))
        self.assertLessEqual(measure(out), 5)

    def test_tail_kept_from_left(self):
        out = rows.elide("abcdefghij", 5, measure, from_left=True)
        self.assertTrue(out.startswith(rows.ELLIPSIS))
        self.assertTrue(out.endswith("ghij"))
        self.assertLessEqual(measure(out), 5)

    def test_300_char_command_line_keeps_the_leaf(self):
        # The DoD check: a 300-char command line elided with the leaf visible.
        cmd = "C:\\Program Files\\Some Agent\\bin\\" + ("x" * 260) + "\\agent.exe"
        self.assertGreater(len(cmd), 300)
        out = rows.elide_path(cmd, 40, measure)
        self.assertLessEqual(measure(out), 40)
        self.assertTrue(out.endswith("agent.exe"), out)
        self.assertTrue(out.startswith(rows.ELLIPSIS), out)

    def test_300_char_command_line_wide_metric(self):
        cmd = "D:\\ws\\" + ("deep\\" * 60) + "main.py"
        self.assertGreater(len(cmd), 300)
        out = rows.elide_path(cmd, 320, measure_wide)
        self.assertLessEqual(measure_wide(out), 320)
        self.assertTrue(out.endswith("main.py"), out)

    def test_measure_that_reports_zero_terminates(self):
        # A pathological metric must not hang the binary search.
        out = rows.elide("abcdef", 10, lambda s: 0)
        self.assertEqual(out, "abcdef")

    def test_unicode_text(self):
        out = rows.elide("\u4e2d\u6587\u8def\u5f84\\leaf.txt", 8, measure)
        self.assertLessEqual(measure(out), 8)


class TestMarkers(unittest.TestCase):
    def test_no_markers(self):
        row = {"state": "working", "source": "l2", "started_at": 123}
        self.assertEqual(rows.markers_for(row), [])
        self.assertEqual(rows.marker_text(row), "")

    def test_title_marker(self):
        row = {"state": "working", "source": "l3", "started_at": 123}
        self.assertEqual(rows.markers_for(row), ["(title)"])

    def test_no_identity_marker(self):
        row = {"state": "unknown", "source": "l2", "started_at": 0}
        self.assertEqual(rows.markers_for(row), ["(no identity)"])

    def test_exit_marker(self):
        row = {"state": "done", "source": "l2", "started_at": 1, "exit_code": 3}
        self.assertEqual(rows.markers_for(row), ["exit=3"])

    def test_exit_zero_is_not_a_marker(self):
        row = {"state": "done", "source": "l2", "started_at": 1, "exit_code": 0}
        self.assertEqual(rows.markers_for(row), [])

    def test_all_three_in_order(self):
        row = {"state": "done", "source": "l3", "started_at": 0, "exit_code": 2}
        self.assertEqual(rows.markers_for(row),
                         ["(title)", "(no identity)", "exit=2"])


class TestRowHeight(unittest.TestCase):
    def test_height_identical_with_and_without_marker(self):
        # The DoD check: the same row height with and without a marker.
        plain = {"state": "working", "source": "l2", "started_at": 1,
                 "agent": "claude", "project": "projV", "elapsed_s": 10,
                 "mem_bytes": 1024, "branch": "main"}
        marked = dict(plain, source="l3", started_at=0, exit_code=1)
        a = rows.build_row(plain, mono_linespace=16)
        b = rows.build_row(marked, mono_linespace=16)
        self.assertEqual(a.height_px, b.height_px)
        self.assertEqual(a.height_px, 16 + 8)
        self.assertEqual(len(a.line1), len(b.line1))
        self.assertEqual(len(a.line2), len(b.line2))
        self.assertNotEqual(a.markers, b.markers)

    def test_height_scales_with_linespace(self):
        self.assertEqual(rows.row_height_px(20), 28)


class TestBuildRow(unittest.TestCase):
    def base(self, **kw):
        row = {"state": "working", "source": "l2", "started_at": 1,
               "agent": "claude", "project": "projV", "elapsed_s": 130,
               "mem_bytes": 212000000, "branch": "main"}
        row.update(kw)
        return row

    def test_two_lines(self):
        layout = rows.build_row(self.base())
        self.assertEqual(len(layout.line1), 3)   # glyph, agent, project
        self.assertEqual(len(layout.line2), 4)   # state, elapsed, mem, branch
        self.assertEqual(layout.line1[0].text, glyphs.STATE_MAP["working"].char)
        self.assertEqual(layout.line2[0].text, "working")
        self.assertEqual(layout.line2[1].text, "2m")
        self.assertEqual(layout.line2[2].text, "202 MB")
        self.assertEqual(layout.line2[3].text, "main")

    def test_done_with_exit_code_uses_danger(self):
        layout = rows.build_row(self.base(state="done", exit_code=1))
        self.assertEqual(layout.line2[0].role, "danger")
        self.assertEqual(layout.glyph_role, "danger")
        self.assertIn("exit=1", layout.markers)

    def test_done_with_zero_exit_uses_ok(self):
        layout = rows.build_row(self.base(state="done", exit_code=0))
        self.assertEqual(layout.line2[0].role, "ok")

    def test_inferred_row_is_dim_and_marked(self):
        layout = rows.build_row(self.base(state="working", source="l3"))
        self.assertEqual(layout.line2[0].role, "fg_dim")
        self.assertIn("(title)", layout.line2[0].text)
        self.assertIn("(title)", layout.markers)

    def test_starting_transient(self):
        layout = rows.build_row(self.base(state="unknown", transient="starting"))
        self.assertEqual(layout.line2[0].text, "starting...")
        self.assertEqual(layout.glyph, glyphs.TRANSIENT_MAP["starting"].char)

    def test_missing_fields_do_not_raise(self):
        layout = rows.build_row({})
        self.assertEqual(layout.line2[0].text, "unknown")
        self.assertEqual(layout.line1[1].text, "?")

    def test_unknown_state_uses_unknown_glyph(self):
        layout = rows.build_row(self.base(state="zombie"))
        self.assertEqual(layout.glyph, glyphs.STATE_MAP["unknown"].char)


class TestFormatters(unittest.TestCase):
    def test_mem(self):
        self.assertEqual(rows.format_mem(None), "\u2014")
        self.assertEqual(rows.format_mem(-1), "\u2014")
        self.assertEqual(rows.format_mem("nonsense"), "\u2014")
        self.assertEqual(rows.format_mem(512), "512 B")
        self.assertEqual(rows.format_mem(2048), "2 KB")
        self.assertEqual(rows.format_mem(212000000), "202 MB")
        self.assertEqual(rows.format_mem(2 * 1024 ** 3), "2.0 GB")

    def test_elapsed(self):
        self.assertEqual(rows.format_elapsed(None), "\u2014")
        self.assertEqual(rows.format_elapsed(42), "42s")
        self.assertEqual(rows.format_elapsed(130), "2m")
        self.assertEqual(rows.format_elapsed(7500), "2h05m")

    def test_waiting_unit_rule(self):
        # 0s-59s in seconds, >= 60s in minutes (plan 8.3).
        self.assertEqual(rows.format_waiting(0), "waiting 0s")
        self.assertEqual(rows.format_waiting(59), "waiting 59s")
        self.assertEqual(rows.format_waiting(60), "waiting 1m")
        self.assertEqual(rows.format_waiting(125), "waiting 2m")


class TestRenderLine(unittest.TestCase):
    def test_markers_survive_elision(self):
        row = {"state": "done", "source": "l3", "started_at": 0, "exit_code": 1,
               "agent": "a" * 200, "project": "p" * 200, "elapsed_s": 1,
               "mem_bytes": 1, "branch": "b" * 200}
        out = rows.render_row(row, 40, measure)
        joined = " ".join(out["line2"])
        self.assertIn("(title)", joined)
        self.assertIn("(no identity)", joined)
        self.assertIn("exit=1", joined)
        # the descriptive text yielded instead
        self.assertLessEqual(measure(out["line1"][2]), 40)

    def test_glyph_never_elided(self):
        row = {"state": "blocked", "source": "l2", "started_at": 1,
               "agent": "a" * 100, "project": "p" * 100}
        out = rows.render_row(row, 5, measure)
        self.assertEqual(out["line1"][0], glyphs.STATE_MAP["blocked"].char)

    def test_short_row_unchanged(self):
        row = {"state": "working", "source": "l2", "started_at": 1,
               "agent": "claude", "project": "projV", "elapsed_s": 5,
               "mem_bytes": 1024, "branch": "main"}
        out = rows.render_row(row, 500, measure)
        self.assertEqual(out["line1"], ["\u25d0", "claude", "projV"])
        self.assertEqual(out["markers"], [])


class TestColumns(unittest.TestCase):
    def test_wide_keeps_everything(self):
        self.assertEqual(rows.drop_columns(1440), [])
        self.assertEqual(rows.visible_columns(1440),
                         ["state", "elapsed", "mem", "branch"])

    def test_narrow_drops_optional_columns(self):
        self.assertEqual(rows.drop_columns(900), ["mem", "branch"])
        self.assertEqual(rows.visible_columns(900), ["state", "elapsed"])

    def test_breakpoint_is_exclusive(self):
        self.assertEqual(rows.drop_columns(1100), [])
        self.assertEqual(rows.drop_columns(1099), ["mem", "branch"])


class TestCap(unittest.TestCase):
    def test_under_cap(self):
        data = [{"i": i} for i in range(10)]
        rendered, truncated = rows.apply_cap(data, 60)
        self.assertEqual(len(rendered), 10)
        self.assertEqual(truncated, 0)
        self.assertEqual(rows.footer_text(len(rendered), truncated), "")

    def test_over_cap(self):
        data = [{"i": i} for i in range(75)]
        rendered, truncated = rows.apply_cap(data, 60)
        self.assertEqual(len(rendered), 60)
        self.assertEqual(truncated, 15)
        self.assertEqual(rows.footer_text(60, 15), "showing 60 of 75")

    def test_exactly_cap_has_no_footer(self):
        data = [{"i": i} for i in range(60)]
        rendered, truncated = rows.apply_cap(data, 60)
        self.assertEqual(rows.footer_text(len(rendered), truncated), "")

    def test_zero_cap_renders_everything(self):
        data = [{"i": i} for i in range(5)]
        rendered, truncated = rows.apply_cap(data, 0)
        self.assertEqual(len(rendered), 5)
        self.assertEqual(truncated, 0)


class TestThemeIntegration(unittest.TestCase):
    def test_every_row_role_is_a_theme_role(self):
        for state in ("blocked", "working", "idle", "done", "error", "unknown"):
            self.assertIn(theme.role_for_state(state), theme.ROLES)


if __name__ == "__main__":
    unittest.main()
