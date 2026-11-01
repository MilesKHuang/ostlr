"""The wrapper must be importable from ANY project directory.

`launch()` starts `python -m ostlr run -- <agent>` with the *chosen project
directory* as cwd. Without the package root on PYTHONPATH that import fails
instantly, the new console closes, and the user sees "clicked launch, nothing
happened" - the one failure the plan forbids. These tests pin the fix.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from ostlr import actions
from ostlr import schema


class TestWrapperEnv(unittest.TestCase):
    def test_the_package_root_goes_first_on_pythonpath(self):
        env = actions.wrapper_env({"PYTHONPATH": r"C:\elsewhere"})
        parts = env["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(actions.package_root(), parts[0])
        self.assertIn(r"C:\elsewhere", parts)

    def test_the_marker_names_the_injected_root(self):
        env = actions.wrapper_env({})
        self.assertEqual(actions.package_root(), env[schema.WRAPPER_ROOT_ENV])

    def test_an_existing_root_is_not_duplicated(self):
        root = actions.package_root()
        env = actions.wrapper_env({"PYTHONPATH": root})
        self.assertEqual(1, env["PYTHONPATH"].split(os.pathsep).count(root))

    def test_the_base_environment_is_not_mutated(self):
        base = {"PYTHONPATH": r"C:\elsewhere"}
        actions.wrapper_env(base)
        self.assertEqual(r"C:\elsewhere", base["PYTHONPATH"])


class TestImportableFromAnyCwd(unittest.TestCase):
    """The real check: run the wrapper's import from a foreign directory."""

    def test_module_runs_from_a_temp_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = actions.wrapper_env(os.environ)
            proc = subprocess.run(
                [sys.executable, "-m", "ostlr", "--help"],
                cwd=tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=60,
            )
        self.assertEqual(0, proc.returncode, proc.stderr.decode("utf-8", "replace"))
        self.assertIn(b"--selftest", proc.stdout)

    def test_without_the_env_the_import_fails(self):
        # This is the defect the fix removes; keeping it as a test stops someone
        # from "simplifying" the env away later.
        clean = {k: v for k, v in os.environ.items()
                 if k not in (schema.WRAPPER_ROOT_ENV, "PYTHONPATH")}
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-m", "ostlr", "--help"],
                cwd=tmp, env=clean, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=60,
            )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn(b"No module named", proc.stderr)


class TestLaunchHandsTheEnvOver(unittest.TestCase):
    class _FakePopen:
        def __init__(self):
            self.kwargs = None
            self.pid = 4242

        def __call__(self, argv, **kwargs):
            self.argv = argv
            self.kwargs = kwargs
            return self

    def test_launch_passes_the_wrapper_env_and_the_creation_flags(self):
        fake = self._FakePopen()
        result = actions.launch({"command": "claude"}, r"C:\tmp\elsewhere", popen=fake)
        self.assertTrue(result["ok"])
        self.assertEqual(4242, result["pid"])
        self.assertEqual(actions.package_root(),
                         fake.kwargs["env"][schema.WRAPPER_ROOT_ENV])
        self.assertEqual(actions.creation_flags(), fake.kwargs["creationflags"])
        self.assertEqual(r"C:\tmp\elsewhere", fake.kwargs["cwd"])


class TestFrozenBuild(unittest.TestCase):
    """A PyInstaller build has no interpreter, so the wrapper is the exe itself."""

    def setUp(self):
        self._saved = getattr(sys, "frozen", None)
        sys.frozen = True

    def tearDown(self):
        if self._saved is None:
            del sys.frozen
        else:
            sys.frozen = self._saved

    def test_no_pythonpath_is_injected(self):
        env = actions.wrapper_env({"PYTHONPATH": r"C:\only"})
        self.assertEqual(r"C:\only", env["PYTHONPATH"])
        self.assertNotIn(schema.WRAPPER_ROOT_ENV, env)


class TestChildEnvironment(unittest.TestCase):
    def test_the_injected_root_is_removed_for_the_child(self):
        from ostlr import status

        root = actions.package_root()
        saved = dict(os.environ)
        try:
            os.environ["PYTHONPATH"] = os.pathsep.join([root, r"C:\keep"])
            os.environ[schema.WRAPPER_ROOT_ENV] = root
            env = status._child_env()
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertNotIn(schema.WRAPPER_ROOT_ENV, env)
        self.assertEqual([r"C:\keep"], env.get("PYTHONPATH", "").split(os.pathsep))

    def test_without_the_marker_the_environment_is_untouched(self):
        from ostlr import status

        saved = dict(os.environ)
        try:
            os.environ.pop(schema.WRAPPER_ROOT_ENV, None)
            os.environ["PYTHONPATH"] = r"C:\keep"
            env = status._child_env()
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertEqual(r"C:\keep", env["PYTHONPATH"])


if __name__ == "__main__":
    unittest.main()
