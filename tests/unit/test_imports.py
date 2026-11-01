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


class TestStdlibOnly(unittest.TestCase):
    def test_package_has_modules(self):
        self.assertGreater(len(python_files()), 5)

    def test_no_third_party_imports(self):
        offenders = []
        for path in python_files():
            for name in top_level_imports(path):
                if name in STDLIB or name == "ostlr":
                    continue
                offenders.append("%s: %s" % (path.relative_to(ROOT), name))
        self.assertEqual(offenders, [], "non-stdlib imports: %s" % offenders)

    def test_known_stdlib_modules_are_recognised(self):
        # A guard on the guard: if sys.stdlib_module_names were empty on some
        # interpreter, the check above would pass vacuously.
        for name in ("json", "os", "sys", "tkinter", "ctypes", "tomllib"):
            self.assertIn(name, STDLIB)


class TestL1Stub(unittest.TestCase):
    def test_supported_is_false(self):
        from ostlr.probe import l1

        self.assertFalse(l1.supported())

    def test_probe_reports_unsupported_with_a_reason(self):
        from ostlr.probe import l1

        result = l1.probe()
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unsupported")
        self.assertIn("pywinpty", result["reason"])

    def test_combine_is_unsupported(self):
        from ostlr.probe import l1

        result = l1.combine(None, "claude - thinking", True, 0, None)
        self.assertEqual(result["state"], "unsupported")

    def test_selftest_line_is_a_skip(self):
        from ostlr.probe import l1

        line = l1.selftest_line()
        self.assertTrue(line.startswith("SKIP l1:"), line)
        self.assertIn("unsupported", line)


class TestSelftestLines(unittest.TestCase):
    def test_every_line_is_prefixed(self):
        from ostlr import selftest

        lines = selftest.run(clipboard=False)
        # run() prints; rebuild the same lines through the individual checks so
        # the assertion is about content, not about captured stdout.
        collected = []
        collected += selftest.check_paths()
        collected += selftest.check_schema()
        collected += selftest.check_imports()
        collected += selftest.check_copy_receipts()
        collected += selftest.check_dedupe()
        collected += selftest.check_cim_stall()
        collected += selftest.check_l1()
        self.assertEqual(lines, 0)
        for line in collected:
            with self.subTest(line=line):
                self.assertTrue(
                    line.startswith(("PASS ", "FAIL ", "SKIP ")), line)

    def test_stall_line_reports_a_real_measurement(self):
        from ostlr import selftest

        lines = selftest.check_cim_stall()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("PASS cim stall:"), lines[0])
        self.assertIn("< 3.2s", lines[0])

    def test_l1_line_is_present(self):
        from ostlr import selftest

        lines = selftest.check_l1()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("SKIP l1:"), lines[0])


if __name__ == "__main__":
    unittest.main()
