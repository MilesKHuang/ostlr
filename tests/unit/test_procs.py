"""T3 - process tree and per-tree memory: pure, cycle-safe, exact sums.

Every fixture here is a hand-built CIM row list with an exact expected answer,
because the two failures that matter are silent: a cycle that recurses forever
and a recycled pid that shows up as two rows.
"""

import unittest

from ostlr.probe import procs


def row(pid, ppid, mem=0, name="x.exe"):
    return {"pid": pid, "ppid": ppid, "started_at": 1, "mem_bytes": mem,
            "cmdline": name, "exe": "", "name": name}


# --- fixtures -------------------------------------------------------------

# Three trees under one absent parent (4 = System, not in the sample).
THREE_TREES = [
    row(100, 4, 1000), row(101, 100, 200), row(102, 100, 300),
    row(200, 4, 5000), row(201, 200, 500),
    row(300, 4, 70),
]

# A cycle: 400 -> 401 -> 402 -> 400. A naive DFS never returns.
CYCLE = [
    row(400, 402, 10), row(401, 400, 20), row(402, 401, 30),
]

# An orphan: its parent (999) is not in the sample, so it is a root.
ORPHAN = [
    row(500, 999, 111), row(501, 500, 222),
]

# A recycled pid: 600 appears twice with different memory. First wins.
RECYCLED = [
    row(600, 4, 1000), row(600, 4, 9999), row(601, 600, 5),
]

# A deep chain: 700 -> 701 -> ... -> 740 (41 processes, deeper than the cap).
DEEP = [row(700 + i, 700 + i - 1 if i else 4, 1) for i in range(41)]


class TestRoots(unittest.TestCase):
    def test_three_trees(self):
        self.assertEqual(procs.roots(THREE_TREES), [100, 200, 300])

    def test_orphan_is_a_root(self):
        self.assertEqual(procs.roots(ORPHAN), [500])

    def test_cycle_has_no_absent_parent(self):
        # Every pid in the cycle has a parent that is present, so none of them
        # is a root by the "parent absent" rule - and the walk must still
        # terminate when asked about any of them.
        self.assertEqual(procs.roots(CYCLE), [])

    def test_self_parent_is_a_root(self):
        self.assertEqual(procs.roots([row(800, 800, 1)]), [800])

    def test_recycled_pid_yields_one_root(self):
        self.assertEqual(procs.roots(RECYCLED), [600])


class TestTreePids(unittest.TestCase):
    def test_three_trees_exact(self):
        self.assertEqual(procs.tree_pids(100, THREE_TREES), [100, 101, 102])
        self.assertEqual(procs.tree_pids(200, THREE_TREES), [200, 201])
        self.assertEqual(procs.tree_pids(300, THREE_TREES), [300])

    def test_cycle_terminates(self):
        got = procs.tree_pids(400, CYCLE)
        self.assertEqual(sorted(got), [400, 401, 402])
        self.assertEqual(len(got), len(set(got)))

    def test_orphan_tree(self):
        self.assertEqual(procs.tree_pids(500, ORPHAN), [500, 501])

    def test_recycled_pid_is_one_row(self):
        self.assertEqual(procs.tree_pids(600, RECYCLED), [600, 601])

    def test_depth_cap(self):
        # 41 processes in a chain; the cap is 32, so the walk stops early.
        got = procs.tree_pids(700, DEEP)
        self.assertEqual(len(got), procs.MAX_DEPTH + 1)
        self.assertEqual(got[0], 700)

    def test_unknown_root(self):
        self.assertEqual(procs.tree_pids(12345, THREE_TREES), [])


class TestTreeMem(unittest.TestCase):
    def test_exact_sums(self):
        self.assertEqual(procs.tree_mem(100, THREE_TREES), 1500)
        self.assertEqual(procs.tree_mem(200, THREE_TREES), 5500)
        self.assertEqual(procs.tree_mem(300, THREE_TREES), 70)

    def test_cycle_sum(self):
        self.assertEqual(procs.tree_mem(400, CYCLE), 60)

    def test_orphan_sum(self):
        self.assertEqual(procs.tree_mem(500, ORPHAN), 333)

    def test_recycled_uses_first_row(self):
        # 1000 (first) + 5, not 9999 + 5.
        self.assertEqual(procs.tree_mem(600, RECYCLED), 1005)

    def test_negative_and_missing_memory_is_zero(self):
        rows = [row(900, 4, -5), {"pid": 901, "ppid": 900}]
        self.assertEqual(procs.tree_mem(900, rows), 0)

    def test_tree_size(self):
        self.assertEqual(procs.tree_size(100, THREE_TREES), 3)
        self.assertEqual(procs.tree_size(300, THREE_TREES), 1)


class TestBuildTrees(unittest.TestCase):
    def test_one_entry_per_root(self):
        trees = procs.build_trees(THREE_TREES)
        self.assertEqual([t["pid"] for t in trees], [100, 200, 300])
        self.assertEqual(trees[0]["mem_bytes"], 1500)
        self.assertEqual(trees[0]["pids"], [100, 101, 102])
        self.assertFalse(trees[0]["depth_capped"])

    def test_deep_tree_is_marked_capped(self):
        trees = procs.build_trees(DEEP)
        self.assertEqual(len(trees), 1)
        self.assertTrue(trees[0]["depth_capped"])
        self.assertEqual(len(trees[0]["pids"]), procs.MAX_DEPTH + 1)

    def test_cycle_does_not_hang(self):
        trees = procs.build_trees(CYCLE)
        self.assertEqual(trees, [])   # no root by the absent-parent rule

    def test_empty_input(self):
        self.assertEqual(procs.build_trees([]), [])
        self.assertEqual(procs.roots([]), [])
        self.assertEqual(procs.children_map([]), {})


class TestSummarise(unittest.TestCase):
    def test_counts(self):
        summary = procs.summarise(THREE_TREES)
        self.assertEqual(summary["rows"], 6)
        self.assertEqual(summary["roots"], 3)
        self.assertEqual(summary["largest_tree"], 3)

    def test_empty(self):
        self.assertEqual(procs.summarise([]),
                         {"rows": 0, "roots": 0, "largest_tree": 0})


class TestChildrenMap(unittest.TestCase):
    def test_children(self):
        kids = procs.children_map(THREE_TREES)
        self.assertEqual(kids[100], [101, 102])
        self.assertEqual(kids[200], [201])
        self.assertNotIn(300, kids)

    def test_absent_parent_is_not_a_child(self):
        kids = procs.children_map(ORPHAN)
        self.assertNotIn(999, kids)
        self.assertEqual(kids[500], [501])


if __name__ == "__main__":
    unittest.main()
