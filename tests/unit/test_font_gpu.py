"""The font contract and the GPU counters, tested where they can be."""

import os
import tempfile
import unittest

from ostlr import config
from ostlr.qt import fonts, procstat

EMPTY_DIR = os.path.join(tempfile.gettempdir(), "ostlr-no-such-font-dir")


class TestFontContract(unittest.TestCase):
    def test_the_bundled_font_comes_first_when_there_is_one(self):
        # A shipped font wins over every system font: that is what shipping one is
        # for. The exact-2:1 families stay behind it as fallbacks.
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "terminal.ttc"), "wb").close()
            family = fonts.family_for(config.Config(), tmp)
        self.assertEqual("OstlrMono", family.split(",")[0].strip(), family)

    def test_without_a_bundled_font_the_first_family_is_exact_two_to_one(self):
        # Measured at 14 px: SimHei / NSimSun / SimSun give ASCII 7.00, CJK 14.00.
        family = fonts.family_for(config.Config(), EMPTY_DIR)
        first = family.split(",")[0].strip().strip('"')
        self.assertIn(first, ("SimHei", "NSimSun", "SimSun"), family)

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
