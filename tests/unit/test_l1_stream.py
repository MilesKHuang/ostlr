"""L1 tests: OSC 7501 (Program Status Protocol, revision 0.4) as the terminal side.

The cases are the ones that break a naive implementation: a sequence split across
two reads, a base64 message with padding loss, an unknown state (dropped, never
guessed), and the probe that must be answered rather than treated as a state.

The 0.4 half is about which failures are fatal. A limit, undecodable base64 or a
control character discards the report whole - clamping a percentage or repairing
bad base64 would show a state the program never meant. Everything the spec calls
"treated as absent" (an unknown `kind`, a `progress` outside 0-100, an `app`
outside the character set) leaves the rest of the report standing, because hiding
a `blocked` record over one bad field is the lie this panel exists to avoid.
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

    def test_an_unusable_kind_is_absent_not_fatal(self):
        # 0.4: outside blocked the key is ignored, and an unrecognized value is
        # treated as absent. Dropping the report would lose the state itself.
        report = l1.parse_body("state=working:kind=permission")
        self.assertEqual("working", report["state"])
        self.assertEqual("", report["kind"])
        report = l1.parse_body("state=blocked:kind=nonsense")
        self.assertEqual("blocked", report["state"])
        self.assertEqual("", report["kind"])

    def test_an_unusable_progress_is_absent_not_fatal(self):
        self.assertIsNone(l1.parse_body("state=working:progress=250")["progress"])
        self.assertIsNone(l1.parse_body("state=working:progress=-4")["progress"])
        self.assertIsNone(l1.parse_body("state=working:progress=half")["progress"])
        self.assertEqual(40, l1.parse_body("state=working:progress=40")["progress"])

    def test_progress_belongs_to_working_and_blocked(self):
        self.assertIsNone(l1.parse_body("state=done:progress=10")["progress"])
        self.assertEqual(10, l1.parse_body("state=blocked:progress=10")["progress"])

    def test_padding_loss_is_tolerated(self):
        raw = b64("thinking")
        report = l1.parse_body("state=working:msg=%s" % raw.rstrip("="))
        self.assertEqual("thinking", report["msg"])

    def test_bad_base64_discards_the_report(self):
        # Repairing it would mean guessing what the program said. Both shapes of
        # failure are the spec's: bytes that are not UTF-8 once decoded, and a
        # base64 alphabet string of a length that cannot be decoded at all.
        broken = base64.b64encode(b"\xff\xfe").decode("ascii")
        self.assertIsNone(l1.parse_body("state=working:msg=%s" % broken))
        self.assertIsNone(l1.parse_body("state=working:msg=a"))

    def test_a_malformed_pair_is_skipped_and_the_rest_survives(self):
        report = l1.parse_body("state=working:oops:Bad=1:msg=%s" % b64("reading"))
        self.assertEqual("working", report["state"])
        self.assertEqual("reading", report["msg"])
        # A byte outside the value set is skipped too: a space cannot travel.
        report = l1.parse_body("state=idle:app=my app")
        self.assertEqual("idle", report["state"])
        self.assertEqual("", report["app"])

    def test_a_repeated_key_keeps_the_last_value(self):
        report = l1.parse_body("state=idle:state=blocked:kind=auth")
        self.assertEqual("blocked", report["state"])
        self.assertEqual("auth", report["kind"])

    def test_limits_from_the_spec(self):
        self.assertIsNone(l1.parse_body("state=idle:app=%s" % ("a" * 33)))
        self.assertIsNone(l1.parse_body("state=idle:id=%s" % ("a" * 33)))
        deep = "/".join(["s"] * 9)
        self.assertIsNone(l1.parse_body("state=idle:id=%s" % deep))
        self.assertIsNotNone(l1.parse_body("state=idle:id=a/b/c"))
        # An over-long key breaks a limit, so the whole report goes.
        self.assertIsNone(l1.parse_body("state=idle:%s=1" % ("k" * 17)))
        self.assertIsNotNone(l1.parse_body("state=idle:%s=1" % ("k" * 16)))

    def test_text_limits_are_checked_encoded_then_decoded(self):
        self.assertIsNone(l1.parse_body("state=idle:msg=%s" % b64("x" * 3000)))
        self.assertIsNone(l1.parse_body("state=idle:title=%s" % b64("x" * 193)))
        self.assertIsNotNone(l1.parse_body("state=idle:title=%s" % b64("x" * 192)))
        # 1025 two-byte characters pass the encoded cap and break the decoded one.
        self.assertIsNone(l1.parse_body("state=idle:msg=%s" % b64("\u00e9" * 1025)))

    def test_control_characters_in_text_discard_the_report(self):
        self.assertIsNone(l1.parse_body("state=idle:msg=%s" % b64("first\nsecond")))
        self.assertIsNone(l1.parse_body("state=idle:msg=%s" % b64("a\tb")))
        self.assertIsNone(l1.parse_body("state=idle:msg=%s" % b64("a\x7fb")))
        # 0.4 names C1: U+009B is a single-byte CSI to a terminal.
        self.assertIsNone(l1.parse_body("state=idle:msg=%s" % b64("a\u009bb")))
        self.assertIsNotNone(l1.parse_body("state=idle:msg=%s" % b64("plain text")))

    def test_invisible_formatting_is_disarmed_outside_the_grid(self):
        # 0.4 asks for this: an RLO would reverse the rest of the row, and the row
        # is not the program's screen. The zero-width joiners go too.
        report = l1.parse_body("state=blocked:msg=%s"
                               % b64("Approve \u202erm -rf build?\u200b"))
        self.assertEqual("Approve rm -rf build?", report["msg"])
        self.assertEqual("", l1.disarm("\u2066\u2069\ufeff"))

    def test_a_limit_applies_to_clear_too(self):
        # A limit is a cap on the work one sequence may cause, not a rule about
        # what the sequence means, so `clear` is held to it as well.
        self.assertIsNone(l1.parse_body("state=clear:msg=%s" % b64("x" * 3000)))
        self.assertIsNone(l1.parse_body("state=clear:msg=%s" % b64("a\nb")))
        self.assertEqual("sub", l1.parse_body("state=clear:id=sub")["id"])

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
        result = self.stream.feed(seq("state=working:msg=%s" % b64("tool: read_file")))
        self.assertEqual(1, len(result.reports))
        self.assertEqual("working", result.reports[0]["state"])
        self.assertEqual("tool: read_file", result.reports[0]["msg"])
        self.assertFalse(result.probe)

    def test_a_sequence_split_across_two_reads(self):
        payload = seq("state=done:msg=%s" % b64("turn finished"))
        cut = len(payload) // 2
        first = self.stream.feed(payload[:cut])
        second = self.stream.feed(payload[cut:])
        self.assertEqual([], first.reports)
        self.assertEqual(1, len(second.reports))
        self.assertEqual("done", second.reports[0]["state"])

    def test_a_probe_is_reported_and_not_treated_as_state(self):
        result = self.stream.feed(seq("?"))
        self.assertEqual([], result.reports)
        self.assertTrue(result.probe)

    def test_two_reports_in_one_read(self):
        chunk = seq("state=working") + b"ordinary output" + seq("state=blocked:kind=question")
        result = self.stream.feed(chunk)
        self.assertEqual(["working", "blocked"], [r["state"] for r in result.reports])

    def test_text_without_sequences_does_not_grow_the_buffer(self):
        for _ in range(50):
            result = self.stream.feed(b"x" * 1000)
            self.assertEqual([], result.reports)
        self.assertLessEqual(len(self.stream._buffer), len(l1.OSC_START))

    def test_a_bel_terminated_sequence(self):
        result = self.stream.feed(b"\x1b]7501;state=idle\x07")
        self.assertEqual(1, len(result.reports))
        self.assertEqual("idle", result.reports[0]["state"])

    def test_an_oversized_sequence_is_dropped(self):
        result = self.stream.feed(seq("state=working:msg=" + "A" * (l1.MAX_SEQUENCE + 10)))
        self.assertEqual([], result.reports)

    def test_a_new_shell_prompt_is_reported(self):
        # OSC 133 A: the shell starts a prompt, so the command before it ended.
        result = self.stream.feed(b"\x1b]133;A\x07$ ")
        self.assertTrue(result.prompt)
        self.assertFalse(result.reset)
        # `B` is "command began": nothing to drop, and it must not be read as A.
        self.assertFalse(self.stream.feed(b"\x1b]133;B\x07").prompt)
        # Parameters after A are legal.
        self.assertTrue(self.stream.feed(b"\x1b]133;A;cl=m\x07").prompt)

    def test_a_full_reset_is_reported_and_a_soft_reset_is_not(self):
        self.assertTrue(self.stream.feed(b"\x1bc").reset)        # RIS
        self.assertFalse(self.stream.feed(b"\x1b[!p").reset)     # DECSTR

    def test_a_lifetime_marker_split_across_two_reads_still_counts(self):
        stream = l1.Stream()
        self.assertFalse(stream.feed(b"\x1b").reset)
        self.assertTrue(stream.feed(b"c").reset)

        stream = l1.Stream()
        self.assertFalse(stream.feed(b"\x1b]133;").prompt)
        self.assertTrue(stream.feed(b"A\x07").prompt)

    def test_an_osc_payload_is_not_searched_for_markers(self):
        # A title or a hyperlink holds arbitrary text, so `c` and `133;A` in one
        # are data: records the program never withdrew must survive.
        stream = l1.Stream()
        result = stream.feed(b"\x1b]0;c 133;A\x07")
        self.assertFalse(result.reset)
        self.assertFalse(result.prompt)
        self.assertFalse(stream.feed(b"\x1b]8;;https://x/?c\x07link\x1b]8;;\x07").reset)
        self.assertTrue(stream.feed(b"\x1bc").reset)

    def test_an_esc_inside_an_osc_aborts_it_like_a_terminal(self):
        # A real parser ends the string at ESC and reads what follows, so a bare
        # `ESC c` there is RIS. Pinned so nobody "fixes" it into a false negative.
        self.assertTrue(l1.Stream().feed(b"\x1b]0;title\x1bc").reset)


class TestRecords(unittest.TestCase):
    def report(self, state, record_id="", kind="", msg="", progress=None, app="pi"):
        return {"state": state, "id": record_id, "app": app, "kind": kind,
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

    def test_a_record_without_an_app_inherits_one(self):
        store = l1.Store()
        store.apply(self.report("working", "", app="deploy"))
        store.apply(self.report("working", "us-east", app=""))
        store.apply(self.report("working", "us-east/zone-a", app=""))
        child = [r for r in store.records() if r.id == "us-east"][0]
        grandchild = [r for r in store.records() if r.id == "us-east/zone-a"][0]
        self.assertEqual("deploy", store.app_for(child))
        # Nearest ancestor wins over the root.
        store.apply(self.report("working", "us-east", app="region"))
        self.assertEqual("region", store.app_for(grandchild))
        root = [r for r in store.records() if r.id == ""][0]
        self.assertEqual("deploy", store.app_for(root))

    def test_a_record_with_no_app_anywhere_has_none(self):
        store = l1.Store()
        store.apply(self.report("idle", "orphan", app=""))
        self.assertEqual("", store.app_for(store.summary()))

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

    def test_a_full_reset_removes_every_record(self):
        store = l1.Store()
        store.apply(self.report("done", "result"))
        store.reset()
        self.assertIsNone(store.summary())

    def test_the_cap_drops_the_least_recently_updated(self):
        # 0.4 names this rule: it is not "the least urgent", because otherwise a
        # program that floods ids could erase the record the user has to answer.
        store = l1.Store(max_records=8)
        for index in range(20):
            store.apply(self.report("idle", "idle-%d" % index))
        store.apply(self.report("blocked", "keep", kind="permission"))
        self.assertEqual(8, store.count())
        self.assertEqual("blocked", store.summary().state)
        self.assertIn("keep", [r.id for r in store.records()])
        self.assertNotIn("idle-0", [r.id for r in store.records()])

    def test_an_exit_drops_working_and_blocked_but_keeps_the_result(self):
        store = l1.Store()
        store.apply(self.report("working"))
        store.apply(self.report("done", "result"))
        store.drop_transient()
        self.assertEqual(["result"], [r.id for r in store.records()])

    def test_a_new_prompt_drops_working_but_keeps_done(self):
        store = l1.Store()
        store.apply(self.report("blocked", "ask", kind="permission"))
        store.apply(self.report("done", "result"))
        store.drop_transient()
        self.assertEqual(["result"], [r.id for r in store.records()])


class TestSupport(unittest.TestCase):
    def test_l1_is_supported_now_that_we_own_a_pty(self):
        self.assertTrue(l1.supported())
        self.assertIn("pane", l1.probe()["reason"])

    def test_the_bare_probe_is_still_a_legal_reply(self):
        # 0.3 knew only this form, and it stays valid in 0.4.
        self.assertEqual("\x1b]7501;?\x1b\\", l1.PROBE)

    def test_the_reply_lists_the_states_and_kinds_we_accept(self):
        self.assertEqual("\x1b]7501;?:states=idle,working,blocked,done,error"
                         ":kinds=permission,question,auth\x1b\\", l1.REPLY)
        self.assertNotIn("clear", l1.REPLY)   # not a state, an instruction

    def test_the_selftest_line_names_the_revision(self):
        self.assertIn("rev 0.4", l1.selftest_line())


if __name__ == "__main__":
    unittest.main()
