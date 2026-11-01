"""The font contract and the GPU counters, tested where they can be."""

import os
import tempfile
import unittest

from ostlr import config
from ostlr.qt import fonts, procstat

EMPTY_DIR = os.path.join(tempfile.gettempdir(), "ostlr-no-such-font-dir")


class TestFontContract(unittest.TestCase):
    def test_the_bundled_webfont_is_the_first_family(self):
        # Both halves are bundled web fonts (OFL): JetBrains Mono for ASCII and the
        # box characters, WenKai for Chinese. No file is loaded from the system
        # first, and neither may be a Microsoft face.
        family = fonts.family_for(config.Config(), EMPTY_DIR)
        self.assertIn("OstlrMono Latin", family.split(",")[0], family)
        self.assertIn("LXGW WenKai Mono", family)

    def test_nothing_microsoft_is_required(self):
        family = fonts.family_for(config.Config(), EMPTY_DIR)
        for banned in ("YaHei", "MS Gothic", "SimSun"):
            self.assertNotIn(banned, family.split("monospace")[0], family)

    def test_a_cjk_face_is_in_the_stack(self):
        family = fonts.family_for(config.Config(), EMPTY_DIR)
        self.assertTrue(any(name in family for name in
                            ("SimHei", "NSimSun", "SimSun", "Microsoft YaHei")), family)

    def test_a_configured_family_wins_over_the_default(self):
        cfg = config.Config()
        cfg.font_family = "Consolas, Sarasa Mono SC"
        self.assertTrue(fonts.family_for(cfg, EMPTY_DIR).startswith("Consolas"))

    def test_an_empty_directory_reports_no_bundle(self):
        self.assertEqual("", fonts.bundled_font(EMPTY_DIR))

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
