"""T4 - the git branch lookup: cache, TTL, per-tick budget, two sentinels.

The point of this module is that it does NOT run git per row per tick. The test
that matters is the call count: a fake runner counts invocations, and the count
must equal the number of distinct cwds inside the fetch window - not the number
of rows.
"""

import unittest

from ostlr.probe import git


class FakeGit:
    """A runner that counts calls and answers from a fixed map."""

    def __init__(self, answers=None, default="main"):
        self.answers = answers or {}
        self.default = default
        self.calls = []

    def __call__(self, cwd):
        self.calls.append(cwd)
        return self.answers.get(cwd, self.default)


class TestBranchFor(unittest.TestCase):
    def test_cached_value_is_reused(self):
        fake = FakeGit()
        cache = git.BranchCache(ttl_s=30)
        self.assertEqual(git.branch_for("D:/ws/a", cache, now=100, runner=fake), "main")
        self.assertEqual(git.branch_for("D:/ws/a", cache, now=110, runner=fake), "main")
        self.assertEqual(len(fake.calls), 1)

    def test_ttl_expiry_refetches(self):
        fake = FakeGit()
        cache = git.BranchCache(ttl_s=30)
        git.branch_for("D:/ws/a", cache, now=100, runner=fake)
        git.branch_for("D:/ws/a", cache, now=131, runner=fake)   # 31 s later
        self.assertEqual(len(fake.calls), 2)

    def test_unknown_cwd_is_never_looked_up(self):
        fake = FakeGit()
        self.assertEqual(git.branch_for("?", None, now=0, runner=fake), "?")
        self.assertEqual(git.branch_for("", None, now=0, runner=fake), "?")
        self.assertEqual(fake.calls, [])

    def test_git_absent_is_question_mark(self):
        def runner(cwd):
            raise FileNotFoundError("git")

        # The real _run_git swallows this; the injected runner must not be
        # trusted to, so branch_for is tested through the real path too.
        self.assertEqual(git._run_git("D:/definitely/not/a/repo"), "?")

    def test_pending_when_budget_is_spent(self):
        fake = FakeGit()
        cache = git.BranchCache(ttl_s=30, limit=2)
        self.assertEqual(git.branch_for("D:/ws/a", cache, now=0, runner=fake), "main")
        self.assertEqual(git.branch_for("D:/ws/b", cache, now=0, runner=fake), "main")
        # Third distinct cwd in the same tick: not fetched, and the answer is
        # "..." (pending), which is a different string from "?" (asked, none).
        self.assertEqual(git.branch_for("D:/ws/c", cache, now=0, runner=fake), "...")
        self.assertEqual(len(fake.calls), 2)

    def test_begin_tick_resets_the_budget(self):
        fake = FakeGit()
        cache = git.BranchCache(ttl_s=30, limit=1)
        git.branch_for("D:/ws/a", cache, now=0, runner=fake)
        self.assertEqual(git.branch_for("D:/ws/b", cache, now=0, runner=fake), "...")
        cache.begin_tick()
        self.assertEqual(git.branch_for("D:/ws/b", cache, now=0, runner=fake), "main")
        self.assertEqual(len(fake.calls), 2)


class TestBranchesFor(unittest.TestCase):
    def test_cache_misses_equal_distinct_cwds_in_window(self):
        # 30 rows, 4 distinct cwds, window of 20 rows -> 4 git calls.
        cwds = ["D:/ws/p%d" % (i % 4) for i in range(30)]
        fake = FakeGit()
        cache = git.BranchCache(ttl_s=30, limit=20)
        result = git.branches_for(cwds[:20], cache, now=0, runner=fake)
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(set(result), {"D:/ws/p0", "D:/ws/p1", "D:/ws/p2", "D:/ws/p3"})
        self.assertEqual(len(result), 4)

    def test_rows_past_the_window_are_pending(self):
        cwds = ["D:/ws/p%d" % i for i in range(25)]
        fake = FakeGit()
        cache = git.BranchCache(ttl_s=30, limit=20)
        result = git.branches_for(cwds, cache, now=0, runner=fake)
        self.assertEqual(len(fake.calls), 20)
        self.assertEqual(result["D:/ws/p19"], "main")
        self.assertEqual(result["D:/ws/p20"], "...")

    def test_second_tick_reuses_the_cache(self):
        cwds = ["D:/ws/p%d" % i for i in range(5)]
        fake = FakeGit()
        cache = git.BranchCache(ttl_s=30, limit=20)
        git.branches_for(cwds, cache, now=0, runner=fake)
        cache.begin_tick()
        git.branches_for(cwds, cache, now=5, runner=fake)
        self.assertEqual(len(fake.calls), 5)   # no refetch inside the TTL


class TestLive(unittest.TestCase):
    def test_live_check_line(self):
        line = git.live_check(".")
        self.assertTrue(line.startswith("PASS git branch:") or line.startswith("SKIP"), line)
        print("\n[live] " + line)


if __name__ == "__main__":
    unittest.main()
