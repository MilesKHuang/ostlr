"""T4 - agent templates, session correlation, cwd from three sources.

The two failures that matter here are silent: a wrong template match puts a
`claude-helper.exe` in the list as a claude session, and a wrong cwd starts an
agent in the wrong project. Both are tested as negatives.
"""

import unittest

from ostlr import agents, sessions


def row(pid, ppid, exe="", cmdline="", mem=0, started=1):
    return {"pid": pid, "ppid": ppid, "started_at": started, "mem_bytes": mem,
            "cmdline": cmdline, "exe": exe, "name": exe.rsplit("\\", 1)[-1]}


class TestTemplates(unittest.TestCase):
    def test_four_templates(self):
        ids = [t.id for t in agents.all_templates()]
        self.assertEqual(ids, ["claude", "codex", "projv", "pwsh"])

    def test_match_by_exe(self):
        self.assertEqual(agents.template_id_for("C:\\bin\\claude.exe", ""), "claude")
        self.assertEqual(agents.template_id_for("C:\\bin\\codex.exe", ""), "codex")
        self.assertEqual(agents.template_id_for("D:\\ws\\projv_tui.exe", ""), "projv")
        self.assertEqual(agents.template_id_for("C:\\bin\\pwsh.exe", ""), "pwsh")

    def test_match_by_cmdline(self):
        # A known interpreter running a script that names an agent.
        self.assertEqual(
            agents.template_id_for("", "node C:\\npm\\claude-code\\cli.js --resume"),
            "claude")
        # No executable path at all: the first token is the executable.
        self.assertEqual(agents.template_id_for("", "codex --model gpt"), "codex")

    def test_a_path_in_the_arguments_does_not_name_the_agent(self):
        """The Code.exe defect: a folder path must not make a process an agent.

        VS Code opened on D:\\ws\\projV had "projv" in its command line and was
        listed as a projV session. Only the executable (or an interpreter's
        script) may name the agent.
        """
        self.assertEqual(
            agents.template_id_for(r"C:\...\Code.exe", r"Code.exe D:\ws\projV"),
            agents.UNKNOWN_ID)
        self.assertEqual(
            agents.template_id_for(r"C:\WINDOWS\Explorer.EXE", r"Explorer.EXE D:\ws\ostlr"),
            agents.UNKNOWN_ID)
        # A bare module invocation names nothing either; the wrapper's L2 record
        # is what makes it a session.
        self.assertEqual(
            agents.template_id_for("", "py -3 -m ostlr run -- projv_tui"),
            agents.UNKNOWN_ID)

    def test_whole_word_only(self):
        # A substring match would make these claude sessions. They are not.
        self.assertEqual(agents.template_id_for("C:\\bin\\claude-helper.exe", ""),
                         agents.UNKNOWN_ID)
        self.assertEqual(agents.template_id_for("", "notclaude --x"), agents.UNKNOWN_ID)
        self.assertEqual(agents.template_id_for("", "claudette.exe"), agents.UNKNOWN_ID)

    def test_unknown_when_nothing_matches(self):
        self.assertEqual(agents.template_id_for("C:\\bin\\notepad.exe", "notepad a.txt"),
                         agents.UNKNOWN_ID)
        self.assertEqual(agents.template_id_for("", ""), agents.UNKNOWN_ID)

    def test_labels_and_commands(self):
        self.assertEqual(agents.label_for("projv"), "projV")
        self.assertEqual(agents.label_for("nope"), "nope")
        self.assertEqual(agents.command_for("claude"), "claude")
        self.assertEqual(agents.command_for("nope"), "")
        self.assertEqual(agents.inject_for("claude"), "at")
        self.assertEqual(agents.inject_for("projv"), "path")


class TestCwd(unittest.TestCase):
    def test_l2_wins(self):
        record = {"cwd": "D:/ws/projV"}
        self.assertEqual(sessions.resolve_cwd(record, "claude --cwd D:\\other"), "D:/ws/projV")

    def test_cmdline_path_when_no_l2(self):
        self.assertEqual(
            sessions.resolve_cwd(None, "claude --cwd D:\\ws\\projV --resume"),
            "D:\\ws\\projV")

    def test_quoted_path_with_a_space(self):
        # The case a naive split breaks: "C:\Program Files\..." must survive.
        self.assertEqual(
            sessions.resolve_cwd(None, 'claude "C:\\Program Files\\agent\\x" --go'),
            "C:\\Program Files\\agent\\x")

    def test_posix_path(self):
        self.assertEqual(sessions.resolve_cwd(None, "codex /home/me/proj"), "/home/me/proj")

    def test_unknown_is_a_question_mark(self):
        self.assertEqual(sessions.resolve_cwd(None, "claude --resume"), "?")
        self.assertEqual(sessions.resolve_cwd(None, ""), "?")
        self.assertEqual(sessions.resolve_cwd({"cwd": ""}, ""), "?")

    def test_project_name(self):
        self.assertEqual(sessions.project_name("D:/ws/projV"), "projV")
        self.assertEqual(sessions.project_name("D:\\ws\\projV\\"), "projV")
        self.assertEqual(sessions.project_name("?"), "?")
        self.assertEqual(sessions.project_name(""), "?")


class TestBuildSessions(unittest.TestCase):
    def test_one_session_per_agent_root(self):
        rows = [
            row(10, 4, "C:\\bin\\pwsh.exe", "pwsh"),          # shell
            row(11, 10, "C:\\bin\\claude.exe", "claude"),     # claude under the shell
            row(12, 11, "C:\\bin\\node.exe", "node worker"),  # claude's child
            row(13, 4, "C:\\bin\\codex.exe", "codex"),        # separate agent
        ]
        built = sessions.build_sessions(rows)
        pids = [s["pid"] for s in built]
        # The shell is its own session; claude is a session (its parent is a
        # different template); node is part of claude's tree, not a session.
        self.assertEqual(pids, [10, 11, 13])
        claude = [s for s in built if s["pid"] == 11][0]
        self.assertEqual(claude["template_id"], "claude")
        self.assertEqual(claude["agent"], "claude")

    def test_mem_is_the_whole_tree(self):
        rows = [
            row(20, 4, "C:\\bin\\claude.exe", "claude", mem=1000),
            row(21, 20, "C:\\bin\\node.exe", "node", mem=200),
            row(22, 21, "C:\\bin\\git.exe", "git", mem=30),
        ]
        built = sessions.build_sessions(rows)
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0]["mem_bytes"], 1230)

    def test_unknown_template_is_not_a_session(self):
        """An unnamed process is not listed. The panel is a list of agents.

        The first build kept parentless unknowns "so they are visible", and the
        window filled with msedge / Explorer / dllhost rows reading `unknown`.
        """
        rows = [row(30, 4, "C:\\bin\\mystery.exe", "mystery --serve")]
        built = sessions.build_sessions(rows)
        self.assertEqual([], built)

    def test_reported_unknown_child_is_a_row(self):
        """The verifier's defect: a producer's record beats our exe-name guess.

        An unknown-template process whose parent IS in the sample used to be
        dropped as a helper, so a wrapper script or a node launcher could never
        appear - and `ostlr.status --pid <pid>` was unreachable for exactly the
        agents M1 exists to observe.
        """
        rows = [
            row(50, 4, "C:\\bin\\pwsh.exe", "pwsh"),            # the parent
            row(51, 50, "C:\\bin\\mystery.exe", "mystery --serve"),  # the child
        ]
        records = {51: {"pid": 51, "state": "blocked", "kind": "permission",
                        "cwd": "D:/ws/x", "ts_epoch": 1000, "msg": "t",
                        "source": "ostlr-run", "exit_code": None}}
        built = sessions.build_sessions(rows, records, now=1005)
        pids = [s["pid"] for s in built]
        self.assertIn(51, pids)
        child = [s for s in built if s["pid"] == 51][0]
        self.assertEqual(child["template_id"], agents.UNKNOWN_ID)
        self.assertEqual(child["cwd"], "D:/ws/x")

    def test_unreported_unknown_child_is_still_suppressed(self):
        """The helper rule survives: no record, live parent -> not a row."""
        rows = [
            row(60, 4, "C:\\bin\\pwsh.exe", "pwsh"),
            row(61, 60, "C:\\bin\\mystery.exe", "mystery --serve"),
        ]
        built = sessions.build_sessions(rows, {})
        pids = [s["pid"] for s in built]
        self.assertNotIn(61, pids)
        self.assertIn(60, pids)

    def test_template_matched_child_is_still_a_row(self):
        """No regression: a named agent under a shell stays its own session."""
        rows = [
            row(70, 4, "C:\\bin\\pwsh.exe", "pwsh"),
            row(71, 70, "C:\\bin\\claude.exe", "claude"),
        ]
        built = sessions.build_sessions(rows, {})
        pids = [s["pid"] for s in built]
        self.assertIn(71, pids)
        self.assertEqual([s for s in built if s["pid"] == 71][0]["template_id"],
                         "claude")

    def test_reported_child_of_a_root_is_a_row(self):
        """Step 3's helper rule also yields to a producer's record."""
        rows = [
            row(80, 4, "C:\\bin\\claude.exe", "claude"),
            row(81, 80, "C:\\bin\\mystery.exe", "mystery"),
        ]
        records = {81: {"pid": 81, "state": "working", "kind": "", "cwd": "",
                        "ts_epoch": 1000, "msg": "", "source": "ostlr-run",
                        "exit_code": None}}
        built = sessions.build_sessions(rows, records, now=1005)
        self.assertIn(81, [s["pid"] for s in built])

    def test_cwd_from_l2_then_cmdline_then_question(self):
        rows = [
            row(40, 4, "C:\\bin\\claude.exe", "claude"),
            row(41, 4, "C:\\bin\\codex.exe", "codex --cwd D:\\ws\\sop-test"),
            row(42, 4, "C:\\bin\\pwsh.exe", "pwsh"),
        ]
        records = {40: {"pid": 40, "cwd": "D:/ws/projV", "state": "working",
                        "ts_epoch": 100, "kind": "", "msg": "", "source": "x",
                        "exit_code": None}}
        built = {s["pid"]: s for s in sessions.build_sessions(rows, records)}
        self.assertEqual(built[40]["cwd"], "D:/ws/projV")
        self.assertEqual(built[40]["project"], "projV")
        self.assertEqual(built[41]["cwd"], "D:\\ws\\sop-test")
        self.assertEqual(built[41]["project"], "sop-test")
        self.assertEqual(built[42]["cwd"], "?")
        self.assertEqual(built[42]["project"], "?")

    def test_empty_input(self):
        self.assertEqual(sessions.build_sessions([]), [])

    def test_cycle_does_not_hang(self):
        # A recycled pid can make two same-template processes each other's
        # parent. Neither is a root by the parent walk, so the cycle must be
        # represented by exactly one row - not zero (a vanished session) and
        # not two (a duplicated one).
        rows = [row(50, 51, "C:\\bin\\claude.exe", "claude"),
                row(51, 50, "C:\\bin\\claude.exe", "claude")]
        built = sessions.build_sessions(rows)
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0]["pid"], 50)

    def test_summarise(self):
        rows = [row(60, 4, "C:\\bin\\claude.exe", "claude --cwd D:\\ws\\a"),
                row(61, 4, "C:\\bin\\mystery.exe", "mystery")]
        summary = sessions.summarise(sessions.build_sessions(rows))
        self.assertEqual(summary["sessions"], 1)
        self.assertEqual(summary["named"], 1)
        self.assertEqual(summary["cwd_known"], 1)


class TestCwdsToFetch(unittest.TestCase):
    def test_distinct_and_capped(self):
        built = [{"pid": i, "cwd": "D:/ws/p%d" % (i % 3)} for i in range(30)]
        cwds = sessions.cwds_to_fetch(built, limit=20)
        # 20 sessions, 3 distinct cwds -> 3 lookups, not 20.
        self.assertEqual(cwds, ["D:/ws/p0", "D:/ws/p1", "D:/ws/p2"])

    def test_unknown_cwd_is_skipped(self):
        built = [{"pid": 1, "cwd": "?"}, {"pid": 2, "cwd": "D:/ws/a"}]
        self.assertEqual(sessions.cwds_to_fetch(built), ["D:/ws/a"])

    def test_rows_past_the_window_are_not_fetched(self):
        built = [{"pid": i, "cwd": "D:/ws/p%d" % i} for i in range(25)]
        cwds = sessions.cwds_to_fetch(built, limit=20)
        self.assertEqual(len(cwds), 20)
        self.assertNotIn("D:/ws/p20", cwds)

    def test_apply_branches_marks_pending_vs_unknown(self):
        built = [{"pid": 1, "cwd": "D:/ws/a"}, {"pid": 2, "cwd": "D:/ws/b"},
                 {"pid": 3, "cwd": "?"}]
        applied = sessions.apply_branches(built, {"D:/ws/a": "main"})
        self.assertEqual(applied[0]["branch"], "main")
        self.assertEqual(applied[1]["branch"], "...")   # not fetched yet
        self.assertEqual(applied[2]["branch"], "?")     # nothing to ask


if __name__ == "__main__":
    unittest.main()
