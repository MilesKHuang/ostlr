"""T12/T13 - actions: launch and kill.

Every test uses a double: a fake taskkill runner, a fake Popen. No test opens a
window, kills a real process, or leaves a child running. The point of the doubles
is that the guard can be proven - `taskkill` is never called when the identity
does not match.

`focus` and `restart` used to be tested here. Both were removed with the code:
every row is a pane in this window (nothing to raise), and restart was never
wired to a control.
"""

import ast
import os
import unittest
from pathlib import Path

from ostlr import actions

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ACTIONS_PY = REPO / "ostlr" / "actions.py"


# --- doubles --------------------------------------------------------------

class FakeRunner:
    """A taskkill double that records every invocation."""

    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv):
        self.calls.append(list(argv))

        class _Result:
            pass

        result = _Result()
        result.returncode = self.returncode
        return result


class FakeChild:
    def __init__(self, pid=9999):
        self.pid = pid


class FakePopen:
    """A Popen double: records argv/cwd/flags, never spawns anything."""

    def __init__(self, pid=9999, raises=None):
        self.calls = []
        self.pid = pid
        self.raises = raises

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises:
            raise self.raises
        return FakeChild(self.pid)


def session(pid=100, started_at=1000, cwd="D:/ws/projV",
            cmdline="claude.exe --resume", mem=1024):
    return {
        "pid": pid,
        "started_at": started_at,
        "cwd": cwd,
        "cmdline": cmdline,
        "mem_bytes": mem,
        "agent": "claude",
        "project": "projV",
    }


# --- launch ---------------------------------------------------------------

class TestLaunch(unittest.TestCase):
    def test_launch_returns_the_child_pid(self):
        popen = FakePopen(pid=4321)
        result = actions.launch({"command": "claude"}, "D:/ws/projV", popen=popen)
        self.assertTrue(result["ok"])
        self.assertEqual(result["pid"], 4321)
        self.assertEqual(result["reason"], "launched")

    def test_launch_uses_a_new_console_and_no_pipes(self):
        popen = FakePopen()
        actions.launch({"command": "claude"}, "D:/ws/projV", popen=popen)
        argv, kwargs = popen.calls[0]
        self.assertEqual(argv[-3:], ["run", "--", "claude"])
        self.assertIn("creationflags", kwargs)
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"] & actions.CREATE_NEW_CONSOLE)
            self.assertTrue(kwargs["creationflags"] & actions.CREATE_NEW_PROCESS_GROUP)
        # No stdio redirection of any kind.
        for key in ("stdout", "stderr", "stdin"):
            self.assertNotIn(key, kwargs)

    def test_launch_refuses_unknown_cwd(self):
        result = actions.launch({"command": "claude"}, "?", popen=FakePopen())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "cwd_unknown")

    def test_launch_without_a_command(self):
        result = actions.launch({"command": ""}, "D:/ws", popen=FakePopen())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_command")

    def test_spawn_failure_is_a_value(self):
        popen = FakePopen(raises=OSError("no such file"))
        result = actions.launch({"command": "claude"}, "D:/ws", popen=popen)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "spawn_failed")
        self.assertIn("no such file", result["detail"])

    def test_creation_flags_never_detached(self):
        flags = actions.creation_flags()
        if os.name == "nt":
            self.assertTrue(flags & actions.CREATE_NEW_CONSOLE)
            self.assertFalse(flags & 0x00000008)  # DETACHED_PROCESS
        else:
            self.assertEqual(flags, 0)

    def test_no_pipe_and_no_detached_in_the_source(self):
        tree = ast.parse(ACTIONS_PY.read_text(encoding="utf-8"))
        names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
        attrs = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
        self.assertNotIn("DETACHED_PROCESS", names + attrs)
        self.assertNotIn("PIPE", names + attrs)


class TestStartFailed(unittest.TestCase):
    def test_error_record_becomes_an_error_verdict(self):
        verdict = actions.start_failed(
            session(), {"state": "error", "msg": "claude: [WinError 2] not found\nmore"})
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict["state"], "error")
        self.assertEqual(verdict["reason"], "claude: [WinError 2] not found")
        self.assertEqual(verdict["retain_s"], 30)

    def test_a_healthy_record_is_not_a_failure(self):
        self.assertIsNone(actions.start_failed(session(), {"state": "working"}))
        self.assertIsNone(actions.start_failed(session(), None))


# --- kill -----------------------------------------------------------------

class TestKill(unittest.TestCase):
    def test_missing_probe_fails_closed(self):
        """The verifier's exact sequence: no probe must mean NO kill.

        `kill({"pid": 15284, "started_at": 1})` used to return
        `kill requested (pending)` and really run taskkill on a pid whose real
        creation time was 0. A caller that forgets the probe must be safe.
        """
        runner = FakeRunner()
        result = actions.kill({"pid": 15284, "started_at": 1}, runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "identity_unverified")
        self.assertEqual(runner.calls, [])

    def test_identity_mismatch_never_calls_taskkill(self):
        # The DoD check: a recycled pid must not be killed.
        runner = FakeRunner()
        result = actions.kill(session(pid=100, started_at=1000),
                              runner=runner,
                              identity_probe=lambda pid: 2000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "identity_unverified")
        self.assertEqual(runner.calls, [])
        self.assertEqual(result["expected"], 1000)
        self.assertEqual(result["actual"], 2000)

    def test_zero_started_at_is_unverified(self):
        runner = FakeRunner()
        result = actions.kill(session(pid=100, started_at=0), runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "identity_unverified")
        self.assertEqual(runner.calls, [])

    def test_unparsable_identity_is_unverified(self):
        runner = FakeRunner()
        result = actions.kill(session(pid=100, started_at=1000), runner=runner,
                              identity_probe=lambda pid: 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "identity_unverified")
        self.assertEqual(runner.calls, [])

    def test_matching_identity_requests_the_kill(self):
        runner = FakeRunner()
        result = actions.kill(session(pid=100, started_at=1000), runner=runner,
                              identity_probe=lambda pid: 1000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "kill requested (pending)")
        self.assertEqual(runner.calls, [["taskkill", "/PID", "100", "/T", "/F"]])

    def test_confirmation_receives_the_facts(self):
        seen = {}

        def confirm(facts):
            seen.update(facts)
            return True

        runner = FakeRunner()
        actions.kill(session(pid=100, started_at=1000), confirm=confirm,
                     runner=runner, identity_probe=lambda pid: 1000,
                     tree_pids=[100, 101, 102], tree_mem=212000000)
        self.assertEqual(seen["pid_count"], 3)
        self.assertEqual(seen["mem_bytes"], 212000000)
        self.assertIn("3 processes", seen["line1"])
        self.assertIn("202 MB", seen["line2"])

    def test_cancelled_confirmation_runs_nothing(self):
        runner = FakeRunner()
        result = actions.kill(session(pid=100, started_at=1000),
                              confirm=lambda facts: False, runner=runner,
                              identity_probe=lambda pid: 1000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "cancelled")
        self.assertEqual(runner.calls, [])

    def test_a_broken_dialog_kills_nothing(self):
        def boom(facts):
            raise RuntimeError("dialog exploded")

        runner = FakeRunner()
        result = actions.kill(session(pid=100, started_at=1000), confirm=boom,
                              runner=runner, identity_probe=lambda pid: 1000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "confirm_failed")
        self.assertEqual(runner.calls, [])

    def test_runner_failure_is_a_value(self):
        def boom(argv):
            raise OSError("taskkill missing")

        result = actions.kill(session(pid=100, started_at=1000), runner=boom,
                              identity_probe=lambda pid: 1000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "kill_failed")

    def test_identity_probe_from_cim(self):
        probe = actions.identity_probe_from_cim([
            {"pid": 100, "started_at": 1000},
            {"pid": 200, "started_at": 2000},
        ])
        self.assertEqual(probe(100), 1000)
        self.assertEqual(probe(200), 2000)
        self.assertEqual(probe(300), 0)


class TestResolveKill(unittest.TestCase):
    def test_killed_when_the_row_is_gone(self):
        result = actions.resolve_kill(session(pid=100, started_at=1000), [])
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "killed")
        self.assertEqual(result["survivors"], [])

    def test_survivors_when_the_row_is_still_alive(self):
        rows = [{"pid": 100, "started_at": 1000, "alive": True}]
        result = actions.resolve_kill(session(pid=100, started_at=1000), rows,
                                      tree_pids=[100, 101])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "survivors")
        self.assertEqual(result["survivors"], [100, 101])

    def test_a_recycled_pid_is_not_our_row(self):
        rows = [{"pid": 100, "started_at": 2000, "alive": True}]
        result = actions.resolve_kill(session(pid=100, started_at=1000), rows)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "killed")


if __name__ == "__main__":
    unittest.main()
