"""L1 tests: the sequence a CLI actually sends, parsed out of the byte stream.

The cases are the ones that break a naive implementation: a sequence split across
two reads, a base64 message with padding loss, an unknown state (dropped, never
guessed), a blocked report without a kind, and the probe that must be answered
rather than treated as a state.
"""

import base64
import unittest

from ostlr.probe import l1


def seq(body: str) -> bytes:
    return (b"\x1b]7501;" + body.encode("utf-8") + b"\x1b\\")


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class TestParseBody(unittest.TestCase):
    def test_a_full_report(self):
        report = l1.parse_body("state=blocked:kind=permission:app=projv:id=main:msg=%s"
                               % b64("Approve rm -rf build?"))
        self.assertEqual("blocked", report["state"])
        self.assertEqual("permission", report["kind"])
        self.assertEqual("projv", report["app"])
        self.assertEqual("Approve rm -rf build?", report["msg"])

    def test_a_probe_is_not_a_report(self):
        self.assertIsNone(l1.parse_body("?"))

    def test_an_unknown_state_is_dropped(self):
        # Never guess: a state we do not understand would render as a lie.
        self.assertIsNone(l1.parse_body("state=zombie:msg=%s" % b64("x")))

    def test_blocked_without_a_kind_still_needs_a_human(self):
        report = l1.parse_body("state=blocked")
        self.assertEqual("blocked", report["state"])
        self.assertEqual("permission", report["kind"])

    def test_progress_is_clamped(self):
        self.assertEqual(100, l1.parse_body("state=working:progress=250")["progress"])
        self.assertEqual(0, l1.parse_body("state=working:progress=-4")["progress"])
        self.assertNotIn("progress", l1.parse_body("state=working"))

    def test_padding_loss_is_tolerated(self):
        raw = b64("thinking")
        report = l1.parse_body("state=working:msg=%s" % raw.rstrip("="))
        self.assertEqual("thinking", report["msg"])

    def test_a_broken_base64_message_is_empty_not_fatal(self):
        report = l1.parse_body("state=working:msg=!!!not base64!!!")
        self.assertEqual("working", report["state"])
        self.assertEqual("", report["msg"])


class TestStream(unittest.TestCase):
    def setUp(self):
        self.stream = l1.Stream()

    def test_a_report_in_one_chunk(self):
        reports, probe = self.stream.feed(seq("state=working:msg=%s" % b64("tool: read_file")))
        self.assertEqual(1, len(reports))
        self.assertEqual("working", reports[0]["state"])
        self.assertEqual("tool: read_file", reports[0]["msg"])
        self.assertFalse(probe)

    def test_a_sequence_split_across_two_reads(self):
        payload = seq("state=done:msg=%s" % b64("turn finished"))
        cut = len(payload) // 2
        first, _ = self.stream.feed(payload[:cut])
        second, _ = self.stream.feed(payload[cut:])
        self.assertEqual([], first)
        self.assertEqual(1, len(second))
        self.assertEqual("done", second[0]["state"])

    def test_a_probe_is_reported_and_not_treated_as_state(self):
        reports, probe = self.stream.feed(seq("?"))
        self.assertEqual([], reports)
        self.assertTrue(probe)

    def test_two_reports_in_one_read(self):
        chunk = seq("state=working") + b"ordinary output" + seq("state=blocked:kind=question")
        reports, _ = self.stream.feed(chunk)
        self.assertEqual(["working", "blocked"], [r["state"] for r in reports])

    def test_text_without_sequences_does_not_grow_the_buffer(self):
        for _ in range(50):
            reports, probe = self.stream.feed(b"x" * 1000)
            self.assertEqual([], reports)
        self.assertLessEqual(len(self.stream._buffer), len(l1.OSC_START))

    def test_a_bel_terminated_sequence(self):
        chunk = b"\x1b]7501;state=idle\x07"
        reports, _ = self.stream.feed(chunk)
        self.assertEqual(1, len(reports))
        self.assertEqual("idle", reports[0]["state"])

    def test_an_oversized_sequence_is_dropped(self):
        chunk = seq("state=working:msg=" + "A" * (l1.MAX_SEQUENCE + 10))
        reports, _ = self.stream.feed(chunk)
        self.assertEqual([], reports)


class TestSupport(unittest.TestCase):
    def test_l1_is_supported_now_that_we_own_a_pty(self):
        # It used to return False and blame pywinpty. The pane owns a ConPTY, so
        # the honest answer is "supported for pane sessions".
        self.assertTrue(l1.supported())
        self.assertIn("pane", l1.probe()["reason"])

    def test_the_probe_constant_is_the_documented_form(self):
        self.assertEqual("\x1b]7501;?\x1b\\", l1.PROBE)

    def test_selftest_does_not_claim_unsupported(self):
        self.assertNotIn(l1.UNSUPPORTED, l1.selftest_line())


if __name__ == "__main__":
    unittest.main()
