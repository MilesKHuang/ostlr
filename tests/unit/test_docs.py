"""T17 - the docs are part of the contract, so they are tested.

The README and the producer recipe carry the two things a user copies by hand:
the exact JSON status record and the exact command. A typo there is a broken
product, not a typo, so both are asserted as literal strings.

The acceptance table in `docs/m1-notes.md` is asserted too: the plan says the
notes carry it verbatim, and "verbatim" is checkable.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"
NOTES = ROOT / "docs" / "m1-notes.md"
RECIPE = ROOT / "docs" / "producers" / "claude-hook.md"

# The exact command a user pastes. Kept as one string so the test and the docs
# cannot drift apart silently.
STATUS_COMMAND = ('py -3 -m ostlr.status --state blocked --kind permission '
                  '--pid <pid> --msg "Approve rm -rf build?"')
RUN_COMMAND = "py -3 -m ostlr run -- <cmd>"

# The JSON record from plan section 8.2, as it appears in the docs (the docs
# wrap it over two lines, so the test checks the fields, not the line breaks).
JSON_FIELDS = ('"v": 1', '"ts": "2026-10-30T20:11:03Z"', '"source": "claude-code"',
               '"pid": 12345', '"cwd": "D:/ws/sop-test"', '"state": "blocked"',
               '"kind": "permission"', '"msg": "Approve rm -rf build?"',
               '"exit_code": 0')


def read(path):
    return path.read_text(encoding="utf-8")


class TestReadme(unittest.TestCase):
    def setUp(self):
        self.text = read(README)

    def test_status_command_is_literal(self):
        self.assertIn(STATUS_COMMAND, self.text)

    def test_run_command_is_literal(self):
        self.assertIn(RUN_COMMAND, self.text)

    def test_json_record_fields(self):
        for field in JSON_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, self.text)

    def test_declared_dependencies_and_py3(self):
        # The project is no longer stdlib-only: it hosts a terminal. What must
        # still be true is that `py -3` is the documented interpreter, the
        # dependencies are declared, and the mirror (the thing that makes them
        # installable here) is named.
        self.assertIn("py -3", self.text)
        self.assertIn("Microsoft Store stub", self.text)
        self.assertIn("requirements.txt", self.text)
        self.assertIn("mirrors.aliyun.com", self.text)

    def test_honest_limit_is_stated(self):
        # The one thing a user must not have to discover by experiment.
        self.assertIn("`blocked` needs a producer", self.text)
        self.assertIn("never rendered as `idle`", self.text)

    def test_l1_state_is_stated_truthfully(self):
        # The README used to say "the sequence is not parsed yet" and blamed
        # pywinpty. The pane owns a ConPTY and parses the sequence now, so the
        # honest statement is that L1 is live and what it can and cannot see.
        self.assertIn("OSC 7501", self.text)
        self.assertIn("**live**", self.text)
        self.assertNotIn("not parsed yet", self.text)

    def test_the_renderer_rules_are_stated(self):
        # Two defects shipped because nothing said the rule out loud: the frame
        # belongs to the terminal, the font is the OS terminal's.
        self.assertIn("docs/parity.md", self.text)
        self.assertIn("draws its own frame", self.text)
        self.assertIn("OS terminal's own", self.text)


class TestRecipe(unittest.TestCase):
    def setUp(self):
        self.text = read(RECIPE)

    def test_marked_unverified(self):
        self.assertIn("unverified on this machine", self.text)

    def test_exact_json(self):
        for field in JSON_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, self.text)

    def test_exact_command(self):
        self.assertIn(STATUS_COMMAND, self.text)

    def test_pid_is_concrete_not_a_placeholder(self):
        # A recipe that tells the user to paste `<pid>` is a dead end.
        self.assertIn("os.getpid()", self.text)
        self.assertIn("Do not paste a literal", self.text)

    def test_status_path(self):
        self.assertIn("%LOCALAPPDATA%\\ostlr\\status\\<pid>.json", self.text)


class TestNotes(unittest.TestCase):
    def setUp(self):
        self.text = read(NOTES)

    def test_acceptance_table_is_verbatim(self):
        # The three milestone rows, as the plan writes them.
        self.assertIn("MS1 - a runnable panel", self.text)
        self.assertIn("MS2 - you can act on it", self.text)
        self.assertIn("MS3 - it looks after you", self.text)
        self.assertIn("`py -3 -m unittest discover -s tests` exits 0", self.text)
        self.assertIn("`--selftest --clipboard` equals the copied path", self.text)
        self.assertIn("the README producer command pasted into a fresh `cmd`", self.text)

    def test_manual_only_checks_listed(self):
        for item in ("three seconds", "1440p", "context-menu", "selection border"):
            with self.subTest(item=item):
                self.assertIn(item, self.text)

    def test_deferrals_listed(self):
        for item in ("OSC 7501", "tray icon", "file tree", "PEB", "SQLite",
                     "5000-row"):
            with self.subTest(item=item):
                self.assertIn(item, self.text)

    def test_deviations_listed(self):
        self.assertIn("Deviations from the plan", self.text)
        self.assertIn("expected integer but got", self.text)


if __name__ == "__main__":
    unittest.main()
