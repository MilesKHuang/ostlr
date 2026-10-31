"""T2 - the CIM probe: shapes, CreationDate, timeout, last-good cache.

The load-bearing check is the LIVE one: every pid's `started_at` must be > 0.
If the live `CreationDate` shape ever disagrees with the parser, the identity
guard silently stops refusing a recycled pid, and the panel could kill an
unrelated process. A fixture written by the same person who wrote the parser
cannot catch that - only a real sample can.
"""

import time
import unittest
from pathlib import Path

from ostlr.probe import cim

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures"


def read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestCreationDate(unittest.TestCase):
    def test_powershell_ms_date(self):
        # The shape a live sample actually returns (json-unescaped).
        self.assertEqual(cim.parse_creation_date("/Date(1793415260890)/"), 1793415260)
        self.assertEqual(cim.parse_creation_date("\\/Date(1793415260890)\\/"), 1793415260)

    def test_cim_datetime_utc(self):
        # 2026-10-30T20:11:03Z
        expected = 1793391063
        self.assertEqual(cim.parse_creation_date("20261030201103.000000+000"), expected)
        self.assertEqual(cim.parse_creation_date("20261030201103.000000-000"), expected)

    def test_cim_datetime_with_offset(self):
        utc = cim.parse_creation_date("20261030201103.000000+000")
        plus120 = cim.parse_creation_date("20261030201103.000000+120")
        minus300 = cim.parse_creation_date("20261030201103.000000-300")
        self.assertEqual(plus120, utc - 120 * 60)
        self.assertEqual(minus300, utc + 300 * 60)

    def test_numeric_epoch(self):
        self.assertEqual(cim.parse_creation_date(1793415260890), 1793415260)
        self.assertEqual(cim.parse_creation_date(1793415260), 1793415260)

    def test_unparseable_is_zero_not_a_guess(self):
        for value in (None, "", "not a date", "2026-10-30T20:11:03Z", True, 0, -5,
                      "20261332201103.000000+000"):
            with self.subTest(value=value):
                self.assertEqual(cim.parse_creation_date(value), 0)


class TestParsePayload(unittest.TestCase):
    def test_array_shape(self):
        rows = cim.parse_payload(read_fixture("cim_array.json"))
        self.assertEqual([r["pid"] for r in rows], [100, 101, 102])
        self.assertEqual(rows[0]["ppid"], 4)
        self.assertEqual(rows[0]["started_at"], 1793415260)
        self.assertEqual(rows[0]["mem_bytes"], 1048576)
        self.assertEqual(rows[0]["cmdline"], "claude.exe --resume")
        self.assertEqual(rows[0]["exe"], "C:\\bin\\claude.exe")
        self.assertEqual(rows[0]["name"], "claude.exe")

    def test_single_object_shape(self):
        rows = cim.parse_payload(read_fixture("cim_single.json"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], 4242)
        self.assertEqual(rows[0]["started_at"], 1793415260)

    def test_cim_datetime_shape(self):
        rows = cim.parse_payload(read_fixture("cim_cimdatetime.json"))
        self.assertEqual([r["pid"] for r in rows], [200, 201, 202])
        for row in rows:
            self.assertGreater(row["started_at"], 0, row)

    def test_nulls_and_bad_rows_are_tolerated(self):
        rows = cim.parse_payload(read_fixture("cim_nulls.json"))
        pids = [r["pid"] for r in rows]
        # the row without a pid and the non-object entry are dropped
        self.assertNotIn(-1, pids)
        # 6 usable rows: the duplicate pid 302 is kept twice here on purpose -
        # de-duplication is procs.index_by_pid's job (first wins), not the
        # parser's, so the parser stays a faithful view of the payload.
        self.assertEqual(len(rows), 6, pids)
        # null CommandLine / ExecutablePath become empty strings, not None
        system = [r for r in rows if r["pid"] == 4][0]
        self.assertEqual(system["cmdline"], "")
        self.assertEqual(system["exe"], "")
        # an unparseable date is 0 (no identity), never a guess
        broken = [r for r in rows if r["pid"] == 300][0]
        self.assertEqual(broken["started_at"], 0)
        self.assertEqual(broken["mem_bytes"], 0)
        # numeric strings are coerced
        numeric = [r for r in rows if r["pid"] == 301][0]
        self.assertEqual(numeric["ppid"], 300)
        self.assertEqual(numeric["mem_bytes"], 4096)

    def test_garbage_raises(self):
        with self.assertRaises(cim.CimParseError):
            cim.parse_payload(read_fixture("cim_garbage.json"))
        with self.assertRaises(cim.CimParseError):
            cim.parse_payload("42")

    def test_empty_payload_is_zero_rows(self):
        self.assertEqual(cim.parse_payload(""), [])
        self.assertEqual(cim.parse_payload("   "), [])
        self.assertEqual(cim.parse_payload("null"), [])
        self.assertEqual(cim.parse_payload("[]"), [])


class TestProbeCache(unittest.TestCase):
    def test_good_sample_is_cached(self):
        payload = read_fixture("cim_array.json")
        probe = cim.CimProbe(runner=lambda cmd, timeout: payload, clock=lambda: 1000.0)
        first = probe.sample()
        self.assertTrue(first.ok)
        self.assertFalse(first.stale)
        self.assertEqual(first.count, 3)
        self.assertEqual(first.staleness_text(), "")
        cached = probe.last()
        self.assertEqual(cached.count, 3)

    def test_failure_keeps_last_good_and_reports_staleness(self):
        payload = read_fixture("cim_array.json")
        calls = {"n": 0}

        def runner(cmd, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                return payload
            raise cim.CimTimeout("timeout after 3.0s")

        probe = cim.CimProbe(runner=runner, clock=lambda: 1000.0)
        self.assertTrue(probe.sample().ok)
        second = probe.sample()
        self.assertFalse(second.ok)
        self.assertTrue(second.stale)
        self.assertEqual(second.count, 3)          # last good rows survive
        self.assertIn("stale, last good", second.staleness_text())

    def test_garbage_payload_is_a_failure_not_a_crash(self):
        probe = cim.CimProbe(runner=lambda cmd, timeout: "not json",
                             clock=lambda: 1000.0)
        sample = probe.sample()
        self.assertFalse(sample.ok)
        self.assertTrue(sample.stale)
        self.assertIn("not JSON", sample.error)

    def test_missing_powershell_is_a_failure(self):
        def runner(cmd, timeout):
            raise FileNotFoundError("powershell")

        sample = cim.CimProbe(runner=runner, clock=lambda: 1000.0).sample()
        self.assertFalse(sample.ok)
        self.assertIn("not found", sample.error)

    def test_forced_stall_returns_under_3_2s(self):
        # A sleepy fake command: the probe must give up at its timeout, not
        # wait for the child. This is the R1 mitigation, measured.
        sleepy = ["py", "-3", "-c", "import time; time.sleep(30)"]
        probe = cim.CimProbe(command=sleepy, timeout_ms=3000)
        started = time.monotonic()
        sample = probe.sample()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.2, "probe took %.2fs" % elapsed)
        self.assertFalse(sample.ok)
        self.assertTrue(sample.stale)
        self.assertIn("stale,", sample.staleness_text())
        print("\n[stall] returned in %.2fs, text=%r" % (elapsed, sample.staleness_text()))

    def test_one_probe_at_a_time(self):
        # A second sample() while one is in flight must not start a second
        # process; it returns the cache marked stale.
        import threading

        gate = threading.Event()
        started = threading.Event()

        def runner(cmd, timeout):
            started.set()
            gate.wait(5)
            return read_fixture("cim_array.json")

        probe = cim.CimProbe(runner=runner, clock=lambda: 1000.0)
        result = {}

        def first():
            result["first"] = probe.sample()

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(started.wait(5))
        second = probe.sample()          # while the first is in flight
        gate.set()
        thread.join(5)
        self.assertTrue(result["first"].ok)
        self.assertTrue(second.stale)
        self.assertEqual(second.count, 0)  # nothing cached yet


class TestLive(unittest.TestCase):
    def test_live_sample_has_identity_for_every_row(self):
        sample = cim.probe_once(timeout_ms=10000)
        if not sample.ok:
            self.skipTest("cim probe unavailable: %s" % sample.error)
        self.assertGreater(sample.count, 0)
        missing = [r["pid"] for r in sample.rows if r["started_at"] <= 0]
        self.assertEqual(missing, [], "rows without identity: %s" % missing[:10])
        print("\n[live] %d pids, all started_at > 0 (e.g. pid %d -> %d)"
              % (sample.count, sample.rows[0]["pid"], sample.rows[0]["started_at"]))

    def test_live_check_line(self):
        line = cim.live_check(timeout_ms=10000)
        self.assertTrue(line.startswith("PASS cim rows:") or line.startswith("SKIP"),
                        line)
        print("\n[live] " + line)


if __name__ == "__main__":
    unittest.main()
