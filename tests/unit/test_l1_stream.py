"""L1 tests: OSC 7501 (Program Status Protocol, revision 0.3) as the terminal side.

The cases are the ones that break a naive implementation: a sequence split across
two reads, a base64 message with padding loss, an unknown state (dropped, never
guessed), and the probe that must be answered rather than treated as a state.

The spec's discard rules are the other half: a report that breaks a limit is
dropped **whole**. Clamping a percentage or repairing bad base64 would show the
user a state the program never meant.
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
        self.assertEqual("main", report["id"])
        self.assertEqual("Approve rm -rf build?", report["msg"])

    def test_a_probe_is_not_a_report(self):
        self.assertIsNone(l1.parse_body("?"))

    def test_an_unknown_state_is_dropped(self):
        self.assertIsNone(l1.parse_body("state=zombie:msg=%s" % b64("x")))

    def test_blocked_without_a_kind_is_valid(self):
        # `kind` is optional in the spec: a blocked report with no kind still means
        # "a human is needed", it just does not say what for.
        report = l1.parse_body("state=blocked")
        self.assertEqual("blocked", report["state"])
        self.assertEqual("", report["kind"])

    def test_kind_is_only_meaningful_for_blocked(self):
        self.assertIsNone(l1.parse_body("state=working:kind=permission"))
        self.assertIsNone(l1.parse_body("state=blocked:kind=nonsense"))

    def test_progress_outside_zero_to_a_hundred_discards_the_report(self):
        self.assertIsNone(l1.parse_body("state=working:progress=250"))
        self.assertIsNone(l1.parse_body("state=working:progress=-4"))
        self.assertEqual(40, l1.parse_body("state=working:progress=40")["progress"])

    def test_progress_belongs_to_working_and_blocked(self):
        self.assertIsNone(l1.parse_body("state=done:progress=10"))

    def test_padding_loss_is_tolerated(self):
        raw = b64("thinking")
        report = l1.parse_body("state=working:msg=%s" % raw.rstrip("="))
        self.assertEqual("thinking", report["msg"])

    def test_bad_base64_discards_the_report(self):
        # Repairing it would mean guessing what the program said.
        self.assertIsNone(l1.parse_body("state=working:msg=!!!not base64!!!"))

    def test_limits_from_the_spec(self):
        self.assertIsNone(l1.parse_body("state=idle:app=my app"))
        self.assertIsNone(l1.parse_body("state=idle:app=%s" % ("a" * 33)))
        self.assertIsNone(l1.parse_body("state=idle:id=%s" % ("a" * 33)))
        deep = "/".join(["s"] * 9)
        self.assertIsNone(l1.parse_body("state=idle:id=%s" % deep))
        self.assertIsNone(l1.parse_body("state=idle:msg=%s" % b64("x" * 3000)))
        self.assertIsNotNone(l1.parse_body("state=idle:id=a/b/c"))

    def test_control_characters_in_text_discard_the_report(self):
        self.assertIsNone(l1.parse_body("state=idle:msg=%s" % b64("first\nsecond")))

    def test_clear_uses_only_the_id_for_scope(self):
        # Extra fields do not discard the report: the reference `osc7501` library
        # sends `app` with a clear, and a dropped clear leaves a stale record.
        self.assertIsNotNone(l1.parse_body("state=clear"))
        self.assertEqual("sub", l1.parse_body("state=clear:id=sub")["id"])
        self.assertEqual("sub/1",
                         l1.parse_body("state=clear:app=pi:id=sub/1")["id"])
        self.assertEqual("", l1.parse_body("state=clear:app=pi")["id"])


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


class TestRecords(unittest.TestCase):
    def report(self, state, record_id="", kind="", msg="", progress=None):
        return {"state": state, "id": record_id, "app": "pi", "kind": kind,
                "title": "", "msg": msg, "progress": progress}

    def test_one_record_per_id(self):
        store = l1.Store()
        store.apply(self.report("working"))
        store.apply(self.report("idle", "sub/1"))
        self.assertEqual(2, store.count())

    def test_the_most_urgent_record_is_shown(self):
        store = l1.Store()
        store.apply(self.report("working"))
        store.apply(self.report("idle", "sub/1"))
        self.assertEqual("working", store.summary().state)
        store.apply(self.report("blocked", "sub/2", kind="question", msg="Which?"))
        self.assertEqual("blocked", store.summary().state)

    def test_a_report_replaces_its_record(self):
        store = l1.Store()
        store.apply(self.report("blocked", "", "permission", "Approve?"))
        store.apply(self.report("working"))
        self.assertEqual("working", store.summary().state)
        self.assertEqual("", store.summary().kind)   # omitted fields are gone

    def test_clear_removes_a_whole_subtree(self):
        store = l1.Store()
        for record_id in ("", "sub", "sub/1", "sub/1/deep", "other"):
            store.apply(self.report("idle", record_id))
        store.apply({"state": "clear", "id": "sub", "app": "", "kind": "",
                     "title": "", "msg": "", "progress": None})
        self.assertEqual(["", "other"], [r.id for r in store.records()])

    def test_clear_without_an_id_removes_everything(self):
        store = l1.Store()
        store.apply(self.report("working"))
        store.apply({"state": "clear", "id": "", "app": "", "kind": "",
                     "title": "", "msg": "", "progress": None})
        self.assertIsNone(store.summary())

    def test_the_cap_drops_the_least_urgent_first(self):
        store = l1.Store(max_records=8)
        store.apply(self.report("blocked", "keep", kind="permission"))
        for index in range(20):
            store.apply(self.report("idle", "idle-%d" % index))
        self.assertEqual(8, store.count())
        self.assertEqual("blocked", store.summary().state)

    def test_an_exit_drops_working_and_blocked_but_keeps_the_result(self):
        store = l1.Store()
        store.apply(self.report("working"))
        store.apply(self.report("done", "result"))
        store.drop_transient()
        self.assertEqual(["result"], [r.id for r in store.records()])


class TestSupport(unittest.TestCase):
    def test_l1_is_supported_now_that_we_own_a_pty(self):
        self.assertTrue(l1.supported())
        self.assertIn("pane", l1.probe()["reason"])

    def test_the_probe_constant_is_the_documented_form(self):
        self.assertEqual("\x1b]7501;?\x1b\\", l1.PROBE)

    def test_the_selftest_line_names_the_revision(self):
        self.assertIn("rev 0.3", l1.selftest_line())


if __name__ == "__main__":
    unittest.main()
