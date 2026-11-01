"""T10 - visual discipline: one file per kind of literal.

The plan (section 8.7) says `ui/theme.py` is the only file allowed to contain a
colour, `ui/type.py` the only one with a font tuple, `ui/glyphs.py` the only one
with a codepoint. This test is what makes that true instead of aspirational: it
greps the source and fails on a violation.

It also pins the two map invariants: STATE_MAP has exactly 6 entries,
TRANSIENT_MAP exactly 1, and they share no codepoint and no colour role.
"""

import os
import re
import unittest

from ostlr.ui import glyphs, rows, theme, type as type_mod

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "ostlr", "ui")

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
# A Tk colour name used as a value: fg="red", background='gray50', fill="white"
TK_COLOR_RE = re.compile(
    r"""(?:fg|bg|foreground|background|fill|activebackground|selectbackground)"""
    r"""\s*=\s*['"]([a-zA-Z][a-zA-Z0-9 ]*)['"]"""
)
# An inline font tuple: ("Segoe UI", 9) / ('Consolas', 10, 'bold')
FONT_TUPLE_RE = re.compile(
    r"""\(\s*['"][A-Za-z][A-Za-z0-9 ]*['"]\s*,\s*\d+\s*(?:,\s*['"][a-z]+['"])?\s*\)"""
)
# A raw glyph literal: the codepoint itself, or a \uXXXX escape.
RAW_GLYPH_RE = re.compile("[\u25cf\u25d0\u25cb\u2713\u2715\u25cc\u22ef]")
ESCAPED_GLYPH_RE = re.compile(r"\\u(?:25cf|25d0|25cb|2713|2715|25cc|22ef)", re.I)

# Tk colour names that are not hex - a representative set; the point is that a
# named colour outside theme.py is a violation, not that this list is complete.
TK_COLOR_NAMES = {
    "red", "green", "blue", "white", "black", "gray", "grey", "gray50",
    "yellow", "orange", "purple", "cyan", "magenta", "pink", "brown",
    "lightgray", "darkgray", "lightblue", "darkblue", "darkgreen",
}


def _ui_sources():
    out = {}
    for name in sorted(os.listdir(UI_DIR)):
        if name.endswith(".py"):
            path = os.path.join(UI_DIR, name)
            with open(path, "r", encoding="utf-8") as fh:
                out[name] = fh.read()
    return out

class TestNoStrayLiterals(unittest.TestCase):
    def setUp(self):
        self.sources = _ui_sources()
        self.assertIn("theme.py", self.sources)
        self.assertIn("type.py", self.sources)
        self.assertIn("glyphs.py", self.sources)

    def test_hex_only_in_theme(self):
        for name, text in self.sources.items():
            if name == "theme.py":
                continue
            with self.subTest(file=name):
                self.assertEqual(HEX_RE.findall(text), [],
                                 "%s contains a hex colour literal" % name)

    def test_tk_colour_names_only_in_theme(self):
        for name, text in self.sources.items():
            if name == "theme.py":
                continue
            hits = [m.group(1) for m in TK_COLOR_RE.finditer(text)
                    if m.group(1).strip().lower() in TK_COLOR_NAMES]
            with self.subTest(file=name):
                self.assertEqual(hits, [],
                                 "%s uses a Tk colour name" % name)

    def test_font_tuples_only_in_type(self):
        for name, text in self.sources.items():
            if name == "type.py":
                continue
            with self.subTest(file=name):
                self.assertEqual(FONT_TUPLE_RE.findall(text), [],
                                 "%s contains an inline font tuple" % name)

    def test_glyph_literals_only_in_glyphs(self):
        for name, text in self.sources.items():
            if name == "glyphs.py":
                continue
            with self.subTest(file=name):
                self.assertEqual(RAW_GLYPH_RE.findall(text), [],
                                 "%s contains a raw glyph" % name)
                self.assertEqual(ESCAPED_GLYPH_RE.findall(text), [],
                                 "%s contains a \\u escape for a glyph" % name)

    def test_theme_has_no_tk_colour_names(self):
        # theme.py may hold hex, but a named colour there would bypass the
        # palette just as badly.
        hits = [m.group(1) for m in TK_COLOR_RE.finditer(self.sources["theme.py"])
                if m.group(1).strip().lower() in TK_COLOR_NAMES]
        self.assertEqual(hits, [])


class TestMaps(unittest.TestCase):
    def test_state_map_has_six_entries(self):
        self.assertEqual(len(glyphs.STATE_MAP), 6)
        self.assertEqual(set(glyphs.STATE_MAP),
                         {"blocked", "working", "idle", "done", "error", "unknown"})

    def test_transient_map_has_one_entry(self):
        self.assertEqual(len(glyphs.TRANSIENT_MAP), 1)
        self.assertIn("starting", glyphs.TRANSIENT_MAP)

    def test_codepoints_disjoint(self):
        state_chars = {g.char for g in glyphs.STATE_MAP.values()}
        transient_chars = {g.char for g in glyphs.TRANSIENT_MAP.values()}
        self.assertEqual(state_chars & transient_chars, set())

    def test_colour_roles_disjoint(self):
        state_roles = {g.role for g in glyphs.STATE_MAP.values()}
        transient_roles = {g.role for g in glyphs.TRANSIENT_MAP.values()}
        self.assertEqual(state_roles & transient_roles, set())

    def test_every_role_exists_in_theme(self):
        for g in list(glyphs.STATE_MAP.values()) + list(glyphs.TRANSIENT_MAP.values()):
            self.assertIn(g.role, theme.ROLES)

    def test_expected_codepoints(self):
        self.assertEqual(glyphs.STATE_MAP["blocked"].char, "\u25cf")
        self.assertEqual(glyphs.STATE_MAP["working"].char, "\u25d0")
        self.assertEqual(glyphs.STATE_MAP["idle"].char, "\u25cb")
        self.assertEqual(glyphs.STATE_MAP["done"].char, "\u2713")
        self.assertEqual(glyphs.STATE_MAP["error"].char, "\u2715")
        self.assertEqual(glyphs.STATE_MAP["unknown"].char, "\u25cc")
        self.assertEqual(glyphs.TRANSIENT_MAP["starting"].char, "\u22ef")

    def test_ascii_fallback_covers_every_glyph(self):
        self.assertTrue(glyphs.fallback_covers_all())
        for g in list(glyphs.STATE_MAP.values()) + list(glyphs.TRANSIENT_MAP.values()):
            self.assertIn(g.char, glyphs.ASCII_FALLBACK)
            self.assertTrue(glyphs.ASCII_FALLBACK[g.char].isascii())
        self.assertEqual(len(glyphs.ASCII_FALLBACK), 7)

    def test_family_available(self):
        self.assertTrue(glyphs.family_available(["Segoe UI", "Segoe UI Symbol"]))
        self.assertFalse(glyphs.family_available(["Segoe UI"]))
        self.assertFalse(glyphs.family_available([]))
        self.assertFalse(glyphs.family_available(None))


class TestTypeScale(unittest.TestCase):
    def test_four_specs(self):
        self.assertEqual(len(type_mod.SPECS), 4)
        self.assertEqual(set(type_mod.SPECS), {"row", "meta", "title", "mono"})

    def test_sizes(self):
        self.assertEqual(type_mod.ROW[1], 10)
        self.assertEqual(type_mod.META[1], 9)
        self.assertEqual(type_mod.TITLE[1], 11)
        self.assertEqual(type_mod.TITLE[2], "bold")
        self.assertEqual(type_mod.MONO[0], "Consolas")
        self.assertEqual(type_mod.MONO[1], 9)

    def test_as_tk(self):
        # The family is braced: Tk reads an unbraced "Segoe UI 9 normal" as
        # family "Segoe", size "UI" and raises TclError at the first widget.
        self.assertEqual(type_mod.as_tk(type_mod.ROW), "{Segoe UI} 10 normal")


class TestThemeRoles(unittest.TestCase):
    def test_all_roles_present(self):
        for name in ("bg", "bg_row", "bg_selected", "fg", "fg_dim", "mono",
                     "accent", "ok", "warn", "danger"):
            self.assertIn(name, theme.ROLES)
            self.assertRegex(theme.ROLES[name], r"^#[0-9a-fA-F]{6}$")

    def test_done_with_nonzero_exit_is_danger(self):
        # The anchor test: a failure must never render in a success colour.
        self.assertEqual(theme.role_for_state("done", 1), "danger")
        self.assertEqual(theme.role_for_state("done", 137), "danger")
        self.assertEqual(theme.role_for_state("done", 0), "ok")
        self.assertEqual(theme.role_for_state("done", None), "ok")

    def test_unknown_state_falls_back_to_dim(self):
        self.assertEqual(theme.role_for_state("nonsense"), "fg_dim")
        self.assertEqual(theme.role_for_state(None), "fg_dim")

    def test_inferred_is_dim(self):
        self.assertEqual(theme.role_for_state("working", None, inferred=True), "fg_dim")
        self.assertEqual(theme.role_for_state("done", 0, inferred=True), "fg_dim")
        # an inferred failure still reads as a failure
        self.assertEqual(theme.role_for_state("error", None, inferred=True), "danger")

    def test_role_lookup_falls_back(self):
        self.assertEqual(theme.role("nope"), theme.ROLES["fg"])


class TestRowsReexport(unittest.TestCase):
    def test_rows_reexports_maps(self):
        self.assertIs(rows.STATE_MAP, glyphs.STATE_MAP)
        self.assertIs(rows.TRANSIENT_MAP, glyphs.TRANSIENT_MAP)


class TestLauncherDiscipline(unittest.TestCase):
    """T14's dialog is a view too: no colour, font or glyph literal in it."""

    def setUp(self):
        repo = os.path.dirname(os.path.dirname(HERE))
        path = os.path.join(repo, "ostlr", "launcher.py")
        with open(path, "r", encoding="utf-8") as handle:
            self.source = handle.read()

    def test_no_hex_colour(self):
        self.assertEqual(HEX_RE.findall(self.source), [])

    def test_no_tk_colour_name(self):
        hits = [m.group(1) for m in TK_COLOR_RE.finditer(self.source)
                if m.group(1).strip().lower() in TK_COLOR_NAMES]
        self.assertEqual(hits, [])

    def test_no_inline_font_tuple(self):
        self.assertEqual(FONT_TUPLE_RE.findall(self.source), [])

    def test_no_raw_glyph(self):
        self.assertEqual(RAW_GLYPH_RE.findall(self.source), [])
        self.assertEqual(ESCAPED_GLYPH_RE.findall(self.source), [])

    def test_colours_come_from_theme(self):
        # Every colour in the dialog is a theme role lookup.
        self.assertIn("theme.role(", self.source)


if __name__ == "__main__":
    unittest.main()
