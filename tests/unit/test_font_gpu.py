"""The font contract and the GPU counters, tested where they can be.

Two product requirements that are easy to regress silently:

  * a terminal font whose CJK advance is exactly 2x its ASCII advance - otherwise
    every Chinese character is drawn narrower than its two-cell box and adjacent
    borders look broken;
  * per-process GPU usage on Windows without any vendor tool.
"""

import os
import tempfile
import unittest

from ostlr import config
from ostlr.qt import fonts, procstat


class TestFontContract(unittest.TestCase):
    def test_the_default_stack_starts_with_an_exact_2to1_family(self):
        # Measured at 14 px: Consolas 7.70/14.00 = 1.819 (drifts), MS Gothic and
        # NSimSun 7.00/14.00 = 2.000 (exact). The first family must be one of the
        # exact ones, or the grid lies.
        family = fonts.family_for(config.Config())
        first = family.split(",")[0].strip().strip('"')
        self.assertIn(first, ("MS Gothic", "NSimSun", "SimSun", "OstlrMono"), family)

    def test_a_configured_family_wins(self):
        cfg = config.Config()
        cfg.font_family = "Sarasa Mono SC, Consolas"
        self.assertEqual("Sarasa Mono SC, Consolas", fonts.family_for(cfg))

    def test_a_bundled_font_is_prepended(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "SarasaMono.ttc"), "wb").close()
            self.assertTrue(fonts.bundled_font(tmp).endswith("SarasaMono.ttc"))
            family = fonts.family_for(config.Config(), tmp)
        self.assertTrue(family.startswith("OstlrMono"), family)

    def test_an_empty_directory_reports_no_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual("", fonts.bundled_font(tmp))

    def test_font_family_round_trips_through_config(self):
        cfg = config.from_mapping({"font_family": "MS Gothic, Consolas"})
        self.assertEqual("MS Gothic, Consolas", cfg.font_family)
        self.assertIn('font_family = "MS Gothic, Consolas"', config.dumps(cfg))


class TestGpuCounters(unittest.TestCase):
    def test_gpu_support_is_detected_not_assumed(self):
        # The point is that the answer is honest on any platform.
        available = procstat.gpu_available()
        if os.name != "nt":
            self.assertFalse(available)
        started = procstat.start_gpu_worker(interval_s=60.0)
        self.assertEqual(available, started)

    def test_the_pid_parser_handles_the_counter_instance_names(self):
        # Instances look like: pid_19708_luid_0x0_0x9BF6_phys_0_eng_0_engtype_3D
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
