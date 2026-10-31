"""T5 - the L2 reader: strict about content, quiet about noise.

Three malformed cases are the whole point, and each must produce exactly ONE
log line per bad file per read:

    half-written JSON      -> bad_json
    state=zombie           -> bad_state
    blocked without kind   -> blocked_without_kind

All three count as ABSENT, so the ladder answers `unknown` - never a guess.
"""

import json
import logging
import tempfile
import unittest
from pathlib import Path

from ostlr.probe import l2


def record(pid=100, state="working", kind="", ts=1000, cwd="D:/ws/a",
           msg="", source="test", exit_code=None):
    return {"v": 1, "ts": ts, "source": source, "pid": pid, "cwd": cwd,
            "state": state, "kind": kind, "msg": msg, "exit_code": exit_code}


class CountingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def read_with_log(directory):
    """Read a directory and return (records, reason, log_lines)."""
    logger = logging.getLogger("ostlr.l2.test")
    logger.handlers = []
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = CountingHandler()
    logger.addHandler(handler)
    records, reason = l2.read_status_dir(Path(directory), log=logger)
    return records, reason, [r.getMessage() for r in handler.records]


class TestParseTs(unittest.TestCase):
    def test_iso_z(self):
        self.assertEqual(l2.parse_ts("2026-10-30T20:11:03Z"), 1793391063)

    def test_iso_offset(self):
        self.assertEqual(l2.parse_ts("2026-10-30T20:11:03+00:00"), 1793391063)

    def test_epoch_number(self):
        self.assertEqual(l2.parse_ts(1793391063), 1793391063)
        self.assertEqual(l2.parse_ts(1793391063000), 1793391063)

    def test_bad_values_are_zero(self):
        for value in (None, "", "yesterday", True, 0, -1):
            with self.subTest(value=value):
                self.assertEqual(l2.parse_ts(value), 0)


class TestValidate(unittest.TestCase):
    def test_good_record(self):
        rec, reason = l2.validate(record(state="blocked", kind="permission"))
        self.assertEqual(reason, "")
        self.assertEqual(rec["state"], "blocked")
        self.assertEqual(rec["kind"], "permission")
        self.assertEqual(rec["pid"], 100)

    def test_all_five_states_accepted(self):
        for state in ("idle", "working", "blocked", "done", "error"):
            kind = "permission" if state == "blocked" else ""
            rec, reason = l2.validate(record(state=state, kind=kind))
            self.assertEqual(reason, "", state)
            self.assertEqual(rec["state"], state)

    def test_zombie_state_rejected(self):
        rec, reason = l2.validate(record(state="zombie"))
        self.assertIsNone(rec)
        self.assertEqual(reason, "bad_state")

    def test_blocked_without_kind_rejected(self):
        rec, reason = l2.validate(record(state="blocked", kind=""))
        self.assertIsNone(rec)
        self.assertEqual(reason, "blocked_without_kind")

    def test_blocked_with_bad_kind_rejected(self):
        rec, reason = l2.validate(record(state="blocked", kind="whatever"))
        self.assertIsNone(rec)
        self.assertEqual(reason, "blocked_without_kind")

    def test_missing_pid_rejected(self):
        raw = record()
        del raw["pid"]
        rec, reason = l2.validate(raw)
        self.assertIsNone(rec)
        self.assertEqual(reason, "bad_pid")

    def test_bad_ts_rejected(self):
        rec, reason = l2.validate(record(ts="not a time"))
        self.assertIsNone(rec)
        self.assertEqual(reason, "bad_ts")

    def test_not_an_object(self):
        rec, reason = l2.validate([1, 2, 3])
        self.assertIsNone(rec)
        self.assertEqual(reason, "not_an_object")


class TestReadStatusDir(unittest.TestCase):
    def test_good_files_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            l2.write_record(record(pid=100, state="working"), Path(tmp))
            l2.write_record(record(pid=200, state="blocked", kind="auth"), Path(tmp))
            records, reason, lines = read_with_log(tmp)
            self.assertEqual(reason, "")
            self.assertEqual(sorted(records), [100, 200])
            self.assertEqual(records[200]["kind"], "auth")
            self.assertEqual(lines, [])

    def test_missing_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            records, reason, lines = read_with_log(Path(tmp) / "nope")
            self.assertEqual(records, {})
            self.assertEqual(reason, "")
            self.assertEqual(lines, [])

    def test_half_written_json_is_absent_with_one_log_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "100.json").write_text('{"pid": 100, "state": "wor',
                                                encoding="utf-8")
            records, reason, lines = read_with_log(tmp)
            self.assertEqual(records, {})
            self.assertEqual(reason, "")
            self.assertEqual(len(lines), 1, lines)
            self.assertIn("bad_json", lines[0])

    def test_zombie_state_is_absent_with_one_log_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "100.json").write_text(json.dumps(record(state="zombie")),
                                                encoding="utf-8")
            records, reason, lines = read_with_log(tmp)
            self.assertEqual(records, {})
            self.assertEqual(len(lines), 1, lines)
            self.assertIn("bad_state", lines[0])

    def test_blocked_without_kind_is_absent_with_one_log_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "100.json").write_text(
                json.dumps(record(state="blocked", kind="")), encoding="utf-8")
            records, reason, lines = read_with_log(tmp)
            self.assertEqual(records, {})
            self.assertEqual(len(lines), 1, lines)
            self.assertIn("blocked_without_kind", lines[0])

    def test_three_bad_files_give_three_lines_not_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "1.json").write_text("{", encoding="utf-8")
            (Path(tmp) / "2.json").write_text(json.dumps(record(pid=2, state="zombie")),
                                              encoding="utf-8")
            (Path(tmp) / "3.json").write_text(
                json.dumps(record(pid=3, state="blocked", kind="")), encoding="utf-8")
            records, reason, lines = read_with_log(tmp)
            self.assertEqual(records, {})
            self.assertEqual(len(lines), 3, lines)

    def test_pid_mismatch_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "100.json").write_text(json.dumps(record(pid=999)),
                                                encoding="utf-8")
            records, reason, lines = read_with_log(tmp)
            self.assertEqual(records, {})
            self.assertEqual(len(lines), 1)
            self.assertIn("pid_mismatch", lines[0])

    def test_non_json_files_are_skipped_silently(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_text("hello", encoding="utf-8")
            (Path(tmp) / "100.json.tmp").write_text("{", encoding="utf-8")
            records, reason, lines = read_with_log(tmp)
            self.assertEqual(records, {})
            self.assertEqual(lines, [])

    def test_write_record_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(l2.write_record(record(pid=7), Path(tmp)))
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["7.json"])


class TestAgeAndFreshness(unittest.TestCase):
    def test_age(self):
        rec = {"ts_epoch": 1000}
        self.assertEqual(l2.age_s(rec, 1089), 89)
        self.assertEqual(l2.age_s(rec, 1091), 91)
        self.assertEqual(l2.age_s(rec, 900), 0)      # future ts clamps to 0
        self.assertEqual(l2.age_s({}, 1000), 0)

    def test_freshness_boundary(self):
        rec = {"ts_epoch": 1000}
        self.assertTrue(l2.is_fresh(rec, 1089, 90))
        self.assertTrue(l2.is_fresh(rec, 1090, 90))
        self.assertFalse(l2.is_fresh(rec, 1091, 90))
        self.assertFalse(l2.is_fresh(None, 1000, 90))


if __name__ == "__main__":
    unittest.main()
