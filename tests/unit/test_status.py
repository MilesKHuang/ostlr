"""T8 - the producer: atomic status writer and the `run` wrapper.

The DoD checks are here: 200 concurrent writes with 0 unparsable files and 0
`.tmp` left behind, a wrapper run of a zero-exit and a non-zero-exit child with
the resulting status JSON, a spawn failure reported as `error`, and a grep
proving the wrapper holds no pipe on the child's stdio.

Every test points OSTLR_STATUS_DIR at a temp directory, so the real user profile
is never touched.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ostlr import status as status_mod

HERE = Path(__file__).resolve()
REPO = HERE.parent.parent.parent
STATUS_PY = REPO / "ostlr" / "status.py"


class TempStatusDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ostlr-status-")
        self.dir = self._tmp.name
        self._old = os.environ.get("OSTLR_STATUS_DIR")
        os.environ["OSTLR_STATUS_DIR"] = self.dir

    def tearDown(self):
        if self._old is None:
            os.environ.pop("OSTLR_STATUS_DIR", None)
        else:
            os.environ["OSTLR_STATUS_DIR"] = self._old
        self._tmp.cleanup()

    def files(self):
        return sorted(p.name for p in Path(self.dir).iterdir())


class TestRecord(TempStatusDir):
    def test_record_shape(self):
        rec = status_mod.build_record(1234, "blocked", kind="permission",
                                      msg="Approve rm -rf build?",
                                      cwd="D:\\ws\\projV")
        self.assertEqual(list(rec.keys()),
                         ["v", "ts", "source", "pid", "cwd", "state", "kind",
                          "msg", "exit_code"])
        self.assertEqual(rec["v"], 1)
        self.assertEqual(rec["pid"], 1234)
        self.assertEqual(rec["state"], "blocked")
        self.assertEqual(rec["kind"], "permission")
        self.assertEqual(rec["cwd"], "D:/ws/projV")
        self.assertIsNone(rec["exit_code"])
        self.assertRegex(rec["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_invalid_state_rejected(self):
        with self.assertRaises(ValueError):
            status_mod.build_record(1, "zombie")

    def test_blocked_requires_kind(self):
        with self.assertRaises(ValueError):
            status_mod.build_record(1, "blocked")

    def test_invalid_kind_rejected(self):
        with self.assertRaises(ValueError):
            status_mod.build_record(1, "blocked", kind="whatever")

    def test_msg_is_single_line_and_clipped(self):
        rec = status_mod.build_record(1, "working", msg="line1\nline2\r\nline3")
        self.assertNotIn("\n", rec["msg"])
        self.assertNotIn("\r", rec["msg"])
        long = status_mod.build_record(1, "working", msg="x" * 500)
        self.assertLessEqual(len(long["msg"]), status_mod.CLIP_CHARS)

    def test_write_and_read_back(self):
        path = status_mod.write_status(4242, "working", msg="thinking",
                                       cwd="D:/ws/x")
        self.assertTrue(Path(path).exists())
        data = status_mod.read_status(4242)
        self.assertEqual(data["state"], "working")
        self.assertEqual(data["pid"], 4242)

    def test_clear(self):
        status_mod.write_status(4242, "working")
        self.assertTrue(status_mod.clear_status(4242))
        self.assertIsNone(status_mod.read_status(4242))
        # Clearing twice is not an error.
        self.assertTrue(status_mod.clear_status(4242))

    def test_no_tmp_left_behind(self):
        for i in range(5):
            status_mod.write_status(4242, "working", msg="tick %d" % i)
        self.assertEqual(self.files(), ["4242.json"])


class TestConcurrency(TempStatusDir):
    def test_200_concurrent_writes(self):
        """The DoD check: 200 threads, 0 unparsable files, 0 .tmp left."""
        errors = []
        count = 200

        def writer(i):
            try:
                status_mod.write_status(9000 + (i % 4), "working", msg="w%d" % i)
            except Exception as exc:  # pragma: no cover - the failure we test for
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        names = self.files()
        self.assertEqual([n for n in names if n.endswith(".tmp")], [])
        self.assertEqual(len(names), 4)
        for name in names:
            with open(Path(self.dir) / name, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["v"], 1)
            self.assertIn(data["state"], status_mod.STATES)


class TestNoPipes(unittest.TestCase):
    def test_no_stdio_pipe_in_the_wrapper(self):
        """The DoD check: the wrapper holds no pipe on the child's stdio."""
        text = STATUS_PY.read_text(encoding="utf-8")
        hits = re.findall(r"(?:stdout|stderr|stdin)\s*=\s*(?:subprocess\.)?PIPE", text)
        self.assertEqual(hits, [])

    def test_never_detached(self):
        """DETACHED_PROCESS would give a TUI no console at all."""
        # Only real code counts: a comment or docstring that names the flag is
        # documentation, not a use of it. Parse the module and look for the
        # constant being referenced as a value.
        import ast

        tree = ast.parse(STATUS_PY.read_text(encoding="utf-8"))
        used = [node for node in ast.walk(tree)
                if isinstance(node, ast.Name) and "DETACHED" in node.id]
        used += [node for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute) and "DETACHED" in node.attr]
        self.assertEqual(used, [])

    def test_creation_flags(self):
        flags = status_mod._creation_flags()
        if os.name == "nt":
            self.assertTrue(flags & status_mod.CREATE_NEW_CONSOLE)
            self.assertTrue(flags & status_mod.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertEqual(flags, 0)


class TestWrapper(TempStatusDir):
    def _run(self, argv):
        """Run the wrapper in-process and return (exit_code, status record)."""
        code = status_mod.run(argv, status_dir=self.dir, cwd=self.dir,
                              heartbeat_s=0.2)
        wrapper_pid = os.getpid()
        return code, status_mod.read_status(wrapper_pid, self.dir)

    def test_list_argv_runs_a_real_child(self):
        """The launcher's path: a LIST of argv elements must actually spawn.

        A single joined string ("exe /t 60 /nobreak") is looked up by
        CreateProcess as a file with that literal name -> WinError 2, which is
        the defect the live smoke found.
        """
        code, rec = self._run(["cmd", "/c", "exit", "0"])
        self.assertEqual(code, 0)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["state"], "done")
        self.assertEqual(rec["exit_code"], 0)

    def test_list_argv_nonzero_exit(self):
        code, rec = self._run(["cmd", "/c", "exit", "7"])
        self.assertEqual(code, 7)
        self.assertEqual(rec["state"], "done")
        self.assertEqual(rec["exit_code"], 7)

    def test_raw_string_argv_is_passed_as_a_string(self):
        """The restart path: a raw command line reaches Popen as a STRING."""
        seen = {}

        class _Child:
            pid = 1

            def wait(self):
                return 0

        real_popen = status_mod.subprocess.Popen

        def fake_popen(arg, **kwargs):
            seen["arg"] = arg
            seen["kwargs"] = kwargs
            return _Child()

        status_mod.subprocess.Popen = fake_popen
        try:
            code = status_mod.run('"C:\\Program Files\\a.exe" --flag',
                                  status_dir=self.dir, cwd=self.dir,
                                  heartbeat_s=0.2)
        finally:
            status_mod.subprocess.Popen = real_popen
        self.assertEqual(code, 0)
        self.assertIsInstance(seen["arg"], str)
        self.assertEqual(seen["arg"], '"C:\\Program Files\\a.exe" --flag')
        self.assertNotIn("shell", seen["kwargs"])

    def test_list_argv_reaches_popen_as_a_list(self):
        seen = {}

        class _Child:
            pid = 1

            def wait(self):
                return 0

        real_popen = status_mod.subprocess.Popen

        def fake_popen(arg, **kwargs):
            seen["arg"] = arg
            return _Child()

        status_mod.subprocess.Popen = fake_popen
        try:
            status_mod.run(["cmd", "/c", "exit", "0"], status_dir=self.dir,
                           cwd=self.dir, heartbeat_s=0.2)
        finally:
            status_mod.subprocess.Popen = real_popen
        self.assertIsInstance(seen["arg"], list)
        self.assertEqual(seen["arg"], ["cmd", "/c", "exit", "0"])

    def test_zero_exit_child(self):
        code, rec = self._run([sys.executable, "-c", "raise SystemExit(0)"])
        self.assertEqual(code, 0)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["state"], "done")
        self.assertEqual(rec["exit_code"], 0)

    def test_nonzero_exit_child(self):
        code, rec = self._run([sys.executable, "-c", "raise SystemExit(7)"])
        self.assertEqual(code, 7)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["state"], "done")
        self.assertEqual(rec["exit_code"], 7)

    def test_spawn_failure_is_error_with_first_line(self):
        code, rec = self._run([str(Path(self.dir) / "no-such-agent.exe")])
        self.assertEqual(code, 127)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["state"], "error")
        self.assertTrue(rec["msg"])
        self.assertNotIn("\n", rec["msg"])

    def test_no_command(self):
        self.assertEqual(status_mod.run([], status_dir=self.dir), 2)

    def test_heartbeat_rewrites_working(self):
        beat = status_mod.Heartbeat(os.getpid(), self.dir, self.dir, interval_s=0.05)
        beat.start()
        try:
            time.sleep(0.3)
        finally:
            beat.stop()
        self.assertGreaterEqual(beat.beats, 1)
        rec = status_mod.read_status(os.getpid(), self.dir)
        self.assertEqual(rec["state"], "working")

    def test_heartbeat_interval_is_under_stale(self):
        # l2_stale_s is 90 s; the heartbeat must be far below it.
        self.assertLess(status_mod.HEARTBEAT_S, 90)
        self.assertLessEqual(status_mod.HEARTBEAT_S, 30)


class TestCli(TempStatusDir):
    def _cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "ostlr.status", *args],
            cwd=str(REPO), capture_output=True, text=True,
            env=dict(os.environ, OSTLR_STATUS_DIR=self.dir),
        )

    def test_write_then_clear(self):
        result = self._cli("--state", "blocked", "--kind", "permission",
                           "--pid", "4242", "--msg", "t")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = status_mod.read_status(4242, self.dir)
        self.assertEqual(data["state"], "blocked")
        self.assertEqual(data["kind"], "permission")

        result = self._cli("--pid", "4242", "--clear")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(status_mod.read_status(4242, self.dir))

    def test_missing_state_is_usage_error(self):
        result = self._cli("--pid", "1")
        self.assertEqual(result.returncode, 2)

    def test_blocked_without_kind_is_usage_error(self):
        result = self._cli("--state", "blocked", "--pid", "1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("kind", result.stderr)

    def test_default_pid_is_this_process(self):
        result = self._cli("--state", "working", "--msg", "self")
        self.assertEqual(result.returncode, 0, result.stderr)
        names = self.files()
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith(".json"))


if __name__ == "__main__":
    unittest.main()
