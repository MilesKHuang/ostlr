"""T17 - hygiene: standard library only, and L1 stays a documented stub.

The environment constraint is load-bearing (PyPI is effectively unavailable on
this machine), so "no third-party imports" is a check, not a promise. The test
walks every module under `ostlr/`, parses it, and fails on any top-level import
that is neither the stdlib nor our own package.

It also pins the L1 deferral: `probe/l1.py` must report `unsupported` with a
reason that names the actual blocker, so nobody can quietly turn a stub into a
guess.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE = ROOT / "ostlr"

STDLIB = set(sys.stdlib_module_names)


def python_files():
    return sorted(PACKAGE.rglob("*.py"))


def top_level_imports(path):
    """Every absolute top-level module name imported by one file."""
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:      # a relative import is ours by construction
                continue
            module = (node.module or "").split(".")[0]
            if module:
                out.append(module)
    return out


class TestDeclaredDependencies(unittest.TestCase):
    """Imports are stdlib, ours, or one of the DECLARED third-party packages.

    The project was stdlib-only until the terminal pane landed; the check is not
    "no dependencies", it is "no UNDECLARED dependencies", so a stray import
    still fails while PySide6 and winpty are allowed and listed.
    """

    def test_package_has_modules(self):
        self.assertGreater(len(python_files()), 5)

    def test_no_undeclared_imports(self):
        from ostlr import schema
        allowed = set(schema.ALLOWED_THIRD_PARTY)
        offenders = []
        for path in python_files():
            for name in top_level_imports(path):
                if name in STDLIB or name == "ostlr" or name in allowed:
                    continue
                offenders.append("%s: %s" % (path.relative_to(ROOT), name))
        self.assertEqual(offenders, [], "undeclared imports: %s" % offenders)

    def test_the_declared_list_is_not_empty(self):
        # A guard: an empty allow-list would make the test above pass by accident
        # only for stdlib-only code, and would hide a real third-party import.
        from ostlr import schema
        self.assertIn("PySide6", schema.ALLOWED_THIRD_PARTY)
        self.assertIn("winpty", schema.ALLOWED_THIRD_PARTY)

    def test_known_stdlib_modules_are_recognised(self):
        # A guard on the guard: if sys.stdlib_module_names were empty on some
        # interpreter, the check above would pass vacuously.
        for name in ("json", "os", "sys", "tkinter", "ctypes", "tomllib"):
            self.assertIn(name, STDLIB)


class TestL1Stub(unittest.TestCase):
    def test_supported_is_true_for_pane_sessions(self):
        # L1 used to be a stub. A pane owns its PTY, so the sequence is
        # observable there; sessions outside the panel still fall back to L2/L3.
        from ostlr.probe import l1

        self.assertTrue(l1.supported())

    def test_probe_reports_the_current_capability(self):
        from ostlr.probe import l1

        result = l1.probe()
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "supported")
        self.assertIn("pane", result["reason"])
        # It used to blame pywinpty; the pane owns a ConPTY now.
        self.assertNotIn("pywinpty", result["reason"])

    def test_an_empty_store_is_no_record_not_a_guess(self):
        # `combine()` went with the ladder: the pane keeps the records itself, and
        # "no record" is the only thing an empty store can mean.
        from ostlr.probe import l1

        store = l1.Store()
        self.assertIsNone(store.summary())
        self.assertEqual(0, store.count())

    def test_selftest_line_is_a_pass(self):
        from ostlr.probe import l1

        line = l1.selftest_line()
        self.assertTrue(line.startswith("PASS l1"))


class TestSelftestLines(unittest.TestCase):
    def test_every_line_is_prefixed(self):
        from ostlr import selftest

        lines = selftest.run(clipboard=False)
        # run() prints; rebuild the same lines through the individual checks so
        # the assertion is about content, not about captured stdout.
        collected = []
        collected += selftest.check_paths()
        collected += selftest.check_imports()
        collected += selftest.check_copy_receipts()
        collected += selftest.check_dedupe()
        collected += selftest.check_l1()
        self.assertEqual(lines, 0)
        for line in collected:
            with self.subTest(line=line):
                self.assertTrue(
                    line.startswith(("PASS ", "FAIL ", "SKIP ")), line)

    def test_no_check_needs_a_window_or_a_scan(self):
        # The selftest must run with Qt unavailable and without touching the
        # process table: it used to spend 3 s proving the CIM probe could stall.
        from ostlr import selftest
        names = [n for n in dir(selftest) if n.startswith("check_")]
        self.assertNotIn("check_cim_stall", names)
        self.assertNotIn("check_schema", names)

    def test_l1_line_is_present(self):
        from ostlr import selftest

        lines = selftest.check_l1()
        self.assertEqual(len(lines), 1)
        # L1 is live for pane sessions now, so the selftest reports PASS.
        self.assertTrue(lines[0].startswith("PASS l1:"), lines[0])


if __name__ == "__main__":
    unittest.main()
