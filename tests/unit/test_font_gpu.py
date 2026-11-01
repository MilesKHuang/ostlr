"""The font contract and the GPU counters, tested where they can be."""

import os
import unittest

from ostlr import config
from ostlr.qt import fonts, procstat


class TestFontContract(unittest.TestCase):
    def test_the_default_stack_is_the_os_terminal_stack(self):
        # The pane must look like the user's own terminal. Geometry is the
        # renderer's job now (docs/parity.md), so the font is a look, and the look
        # must not change because a TUI is hosted in Ostlr.
        family = fonts.family_for(config.Config())
        first = family.split(",")[0].strip().strip('"')
        configured = fonts.os_terminal_face()
        if configured:
            self.assertEqual(configured, first,
                             "Windows Terminal names %r but the stack starts with %r"
                             % (configured, first))
        else:
            self.assertIn(first, ("Cascadia Mono", "Consolas"), family)
        self.assertIn("Consolas", family)

    def test_a_cjk_face_is_still_named_explicitly(self):
        # Chinese must be drawn by the face Windows uses, not by whatever the
        # browser happens to pick.
        family = fonts.family_for(config.Config())
        self.assertIn(fonts._CJK_FACE, family)

    def test_no_font_file_ships_with_the_panel(self):
        # 583 files / 29.5 MB used to travel with the exe for a claim about
        # alignment that turned out to belong to the renderer.
        fonts_dir = os.path.join(os.path.dirname(os.path.abspath(fonts.__file__)), "fonts")
        self.assertFalse(os.path.exists(fonts_dir), fonts_dir)

    def test_a_configured_family_wins_over_everything(self):
        cfg = config.Config()
        cfg.font_family = "Consolas, Sarasa Mono SC"
        self.assertTrue(fonts.family_for(cfg).startswith("Consolas"))

    def test_an_unreadable_windows_terminal_setting_is_not_an_error(self):
        # No settings file must degrade to the built-in candidates, not raise.
        self.assertIsInstance(fonts.os_terminal_face(), str)

    def test_font_family_round_trips_through_config(self):
        cfg = config.from_mapping({"font_family": "MS Gothic, Consolas"})
        self.assertEqual("MS Gothic, Consolas", cfg.font_family)
        self.assertIn('font_family = "MS Gothic, Consolas"', config.dumps(cfg))


class TestGpuCounters(unittest.TestCase):
    def test_gpu_support_is_detected_not_assumed(self):
        available = procstat.gpu_available()
        if os.name != "nt":
            self.assertFalse(available)

    def test_the_pid_parser_handles_the_counter_instance_names(self):
        match = procstat._RE.search("pid_19708_luid_0x0_0x9BF6_phys_0_eng_0_engtype_3D")
        self.assertIsNotNone(match)
        self.assertEqual("19708", match.group(1))
        self.assertIsNone(procstat._RE.search("luid_0x0_0x9BF6"))

    def test_formatting_never_invents_a_zero(self):
        self.assertEqual("-", procstat.format_gpu(None))
        self.assertEqual("0%", procstat.format_gpu(0.0))
        self.assertEqual("37%", procstat.format_gpu(37.4))

    def test_a_missing_pid_stays_unknown(self):
        stats = procstat.tree_stats(0)
        self.assertIsNone(stats["cpu"])
        self.assertIsNone(stats["mem"])


if __name__ == "__main__":
    unittest.main()
