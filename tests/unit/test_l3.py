"""T7 - L3 window titles: the classifier and the enumeration.

The classifier is the part that matters: it may return only working / done /
error, so a title can never invent `blocked` (a human is needed) or `idle`
(nothing is happening). Those two are the expensive mistakes.
"""

import unittest

from ostlr.probe import l3


# 8 title fixtures: (title, expected). The expected value is the whole point -
# a title that says nothing must classify to None, not to a guess.
FIXTURES = [
    ("claude - thinking... (esc to interrupt)", "working"),
    ("codex | running tests", "working"),
    ("projV - done", "done"),
    ("build complete", "done"),
    ("error: cannot resolve host", "error"),
    ("claude - failed to start", "error"),
    ("panic: runtime error", "error"),
    ("Windows PowerShell", None),
]


class TestClassify(unittest.TestCase):
    def test_eight_fixtures(self):
        self.assertEqual(len(FIXTURES), 8)
        for title, expected in FIXTURES:
            with self.subTest(title=title):
                self.assertEqual(l3.classify(title), expected)

    def test_never_blocked_never_idle(self):
        # The reason L3 exists: it cannot claim a human is needed, and it
        # cannot claim nothing is happening.
        probes = [
            "waiting for approval",
            "permission required",
            "idle",
            "ready and idle",
            "blocked",
            "Approve rm -rf build?",
            "",
            None,
            "   ",
            "x" * 5000,
        ]
        for title in probes:
            with self.subTest(title=title):
                self.assertNotIn(l3.classify(title), ("blocked", "idle"))

    def test_only_three_states_possible(self):
        for title, _ in FIXTURES:
            state = l3.classify(title)
            if state is not None:
                self.assertIn(state, l3.L3_STATES)

    def test_error_wins_over_done(self):
        # A title that mentions both must read as a failure, never as success.
        self.assertEqual(l3.classify("done, but error in step 3"), "error")

    def test_case_insensitive(self):
        self.assertEqual(l3.classify("THINKING"), "working")
        self.assertEqual(l3.classify("Done"), "done")

    def test_empty_and_none(self):
        self.assertIsNone(l3.classify(""))
        self.assertIsNone(l3.classify(None))


class TestHelpers(unittest.TestCase):
    def test_titles_for(self):
        titles = {100: "claude - thinking", 200: "notepad"}
        self.assertEqual(l3.titles_for(100, titles), ["claude - thinking"])
        self.assertEqual(l3.titles_for(999, titles), [])
        self.assertEqual(l3.titles_for(0, titles), [])

    def test_has_window(self):
        titles = {100: "claude - thinking"}
        self.assertTrue(l3.has_window(100, titles))
        self.assertFalse(l3.has_window(101, titles))

    def test_classify_pid(self):
        titles = {100: "claude - thinking", 200: "notepad"}
        self.assertEqual(l3.classify_pid(100, titles), "working")
        self.assertIsNone(l3.classify_pid(200, titles))
        self.assertIsNone(l3.classify_pid(300, titles))

    def test_iter_classified(self):
        got = list(l3.iter_classified([(1, "thinking"), (2, "notepad")]))
        self.assertEqual(got, [(1, "thinking", "working")])


class TestLive(unittest.TestCase):
    def test_enumerate_returns_dict(self):
        titles = l3.enumerate_titles()
        self.assertIsInstance(titles, dict)
        for pid, title in titles.items():
            self.assertIsInstance(pid, int)
            self.assertIsInstance(title, str)
            self.assertTrue(title.strip())

    def test_live_check_line(self):
        line = l3.live_check()
        # A windowless session must SKIP, never fail.
        self.assertTrue(
            line.startswith("PASS titled windows:") or
            line == "SKIP (no titled window in this session)",
            line,
        )
        print("\n[live] " + line)


if __name__ == "__main__":
    unittest.main()
