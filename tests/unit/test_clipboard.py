"""T15 - copy path and the clipboard.

The clipboard is the one place where "it worked on my machine" is not enough:
another process can hold it, it can contain a foreign value, and a double copy
must not write twice. So every test here uses a writer double with a call
counter, and the real ctypes path is exercised only by `--selftest --clipboard`
(which is run separately and reported honestly).
"""

import os
import unittest

from ostlr import actions, view_model as vm


class CountingWriter:
    """A clipboard-writer double: the counted seam the DoD asks for."""

    def __init__(self, ok=True, reason="copied"):
        self.calls = []
        self.ok = ok
        self.reason = reason

    def write(self, text):
        self.calls.append(text)
        if not self.ok:
            return {"ok": False, "reason": self.reason}
        return {"ok": True, "reason": "copied", "text": text}

    def read(self):
        return {"ok": True, "reason": "copied",
                "text": self.calls[-1] if self.calls else ""}


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestReceipts(unittest.TestCase):
    def test_three_variants(self):
        same = vm.receipt("copied", "D:/ws/projV/main.cpp", "claude", "projV", True)
        cross = vm.receipt("copied", "D:/ws/projV/main.cpp", "codex", "sop-test", False)
        none = vm.receipt("copied", "D:/ws/projV/main.cpp", "", "", None)
        self.assertEqual(same, "copied D:/ws/projV/main.cpp -> claude / projV (same project)")
        self.assertEqual(cross, "copied D:/ws/projV/main.cpp -> codex / sop-test (cross project)")
        self.assertEqual(none, "copied D:/ws/projV/main.cpp -> ? / ? (no target)")

    def test_receipt_is_clipped_to_120_with_the_tail_kept(self):
        out = vm.receipt("copied", "D:/" + "x" * 400, "claude", "projV", True)
        self.assertLessEqual(len(out), 120)
        self.assertTrue(out.endswith("-> claude / projV (same project)"), out)


class TestCopyPath(unittest.TestCase):
    def test_same_project(self):
        writer = CountingWriter()
        result = actions.copy_path("D:/ws/projV/main.cpp",
                                   {"agent": "claude", "project": "projV"},
                                   "projV", clipboard=writer)
        self.assertTrue(result["ok"])
        self.assertEqual(result["receipt"],
                         "copied D:/ws/projV/main.cpp -> claude / projV (same project)")
        self.assertEqual(writer.calls, ["D:/ws/projV/main.cpp"])

    def test_cross_project(self):
        writer = CountingWriter()
        result = actions.copy_path("D:/ws/projV/main.cpp",
                                   {"agent": "codex", "project": "sop-test"},
                                   "projV", clipboard=writer)
        self.assertIn("(cross project)", result["receipt"])

    def test_no_target(self):
        writer = CountingWriter()
        result = actions.copy_path("D:/ws/projV/main.cpp", None, "projV",
                                   clipboard=writer)
        self.assertIn("(no target)", result["receipt"])
        self.assertEqual(writer.calls, ["D:/ws/projV/main.cpp"])

    def test_empty_path_is_a_value(self):
        result = actions.copy_path("", None, "", clipboard=CountingWriter())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_path")

    def test_writer_failure_is_a_value(self):
        writer = CountingWriter(ok=False, reason="clipboard_busy")
        result = actions.copy_path("D:/x", None, "", clipboard=writer)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "clipboard_busy")
        self.assertIn("receipt", result)

    def test_no_clipboard_available(self):
        result = actions.copy_path("D:/x", None, "", clipboard=None)
        # On a non-Windows host default_clipboard() is None; on Windows it is a
        # real object, so this only asserts the value shape.
        self.assertIn("ok", result)
        self.assertIn("reason", result)


class TestDedupe(unittest.TestCase):
    def test_two_copies_within_100ms_write_once(self):
        # The DoD check: 2 copies in < 100 ms -> exactly 1 clipboard write.
        writer = CountingWriter()
        clock = FakeClock()
        gate = actions.CopyGate(clock=clock)
        first = actions.copy_path("D:/ws/projV/main.cpp", None, "", clipboard=writer,
                                  gate=gate)
        clock.advance(0.05)
        second = actions.copy_path("D:/ws/projV/main.cpp", None, "", clipboard=writer,
                                   gate=gate)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(first["wrote"])
        self.assertFalse(second["wrote"])
        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(gate.writes, 1)

    def test_a_different_path_writes_again(self):
        writer = CountingWriter()
        clock = FakeClock()
        gate = actions.CopyGate(clock=clock)
        actions.copy_path("D:/a", None, "", clipboard=writer, gate=gate)
        clock.advance(0.01)
        actions.copy_path("D:/b", None, "", clipboard=writer, gate=gate)
        self.assertEqual(len(writer.calls), 2)

    def test_after_the_window_it_writes_again(self):
        writer = CountingWriter()
        clock = FakeClock()
        gate = actions.CopyGate(clock=clock)
        actions.copy_path("D:/a", None, "", clipboard=writer, gate=gate)
        clock.advance(0.2)
        actions.copy_path("D:/a", None, "", clipboard=writer, gate=gate)
        self.assertEqual(len(writer.calls), 2)

    def test_the_window_is_100ms(self):
        self.assertEqual(actions.CLIP_DEDUPE_S, 0.1)


class TestClipboardContract(unittest.TestCase):
    def test_bounded_retry_constants(self):
        # Plan 8.6: <= 10 x 20 ms, then a typed failure.
        self.assertLessEqual(actions.CLIP_RETRIES, 10)
        self.assertLessEqual(actions.CLIP_RETRY_S, 0.02)

    def test_no_tkinter_in_the_clipboard_path(self):
        source = (os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))) + os.sep + "ostlr" + os.sep + "actions.py")
        with open(source, "r", encoding="utf-8") as handle:
            text = handle.read()
        # The clipboard must not go through tkinter: the selftest runs with
        # tkinter blocked, so a tkinter clipboard would make it untestable.
        self.assertNotIn("import tkinter", text)
        self.assertNotIn("clipboard_clear", text)
        self.assertNotIn("clipboard_append", text)

    def test_win32_clipboard_is_the_only_implementation(self):
        self.assertTrue(hasattr(actions, "Win32Clipboard"))
        self.assertTrue(hasattr(actions, "default_clipboard"))


if __name__ == "__main__":
    unittest.main()
