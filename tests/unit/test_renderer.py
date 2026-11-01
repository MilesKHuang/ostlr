"""The renderer contract: geometry comes from the terminal, never from the font.

Measured on a real pane (a ConPTY child drawing an FTXUI-style table):

    DOM renderer    frame gaps 8, gauge block fill 84 %
    WebGL renderer  frame gaps 0-1, gauge block fill 100 %

So the page must load the WebGL addon, ask for xterm-drawn glyphs, and report
which renderer it got instead of assuming. These tests pin the three files that
make that true, and they cannot be satisfied by a mock: term.html is read as
text, the addon is checked as a shipped file.
"""

import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QT = os.path.join(HERE, "ostlr", "qt")
VENDOR = os.path.join(QT, "vendor")


def read(name: str) -> str:
    with open(os.path.join(QT, name), "r", encoding="utf-8") as handle:
        return handle.read()


class TestRendererContract(unittest.TestCase):
    def test_the_webgl_addon_ships_with_the_panel(self):
        # No CDN: a packaged pane has no network and a missing addon would be a
        # silent downgrade to the DOM renderer.
        path = os.path.join(VENDOR, "xterm-addon-webgl.js")
        self.assertTrue(os.path.exists(path), "the addon is not vendored")
        self.assertGreater(os.path.getsize(path), 20000)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(400)
        self.assertIn("WebglAddon", head)

    def test_every_vendored_file_is_covered_by_a_licence(self):
        licence = os.path.join(VENDOR, "LICENSE-xterm.txt")
        self.assertTrue(os.path.exists(licence))
        with open(licence, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("MIT License", text)
        self.assertIn("xterm-addon-webgl.js", text)

    def test_the_page_loads_the_addon_before_it_attaches(self):
        html = read("term.html")
        script = html.index("vendor/xterm-addon-webgl.js")
        attach = html.index("attachWebgl()")
        self.assertLess(script, attach, "the addon must be loaded before it is used")

    def test_the_page_reports_the_renderer_it_got(self):
        html = read("term.html")
        self.assertIn("send({ renderer:", html)
        self.assertIn("onContextLoss", html)

    def test_the_frame_is_asked_for_from_xterm(self):
        # customGlyphs is what draws the frame and the block characters per cell.
        # On the DOM renderer it is inert, which is exactly why the renderer is
        # reported rather than assumed.
        html = read("term.html")
        self.assertIn("customGlyphs: true", html)

    def test_an_option_this_core_does_not_implement_is_not_left_lying_around(self):
        # rescaleOverlappingGlyphs arrived in xterm 5.5; the vendored core is 5.3,
        # which ignores unknown options silently. Leaving it looked like a fix.
        html = read("term.html")
        self.assertNotIn("rescaleOverlappingGlyphs", html)


class TestPaneReportsFallback(unittest.TestCase):
    def test_a_fallback_is_a_receipt_not_a_silence(self):
        source = read("terminal.py")
        self.assertIn("renderer_changed", source)
        # The value must travel to the window, not stop at the pane.
        app = read("app.py")
        self.assertIn("renderer_changed.connect", app)
        self.assertIn("the frame is drawn by", app)


class TestSilenceIsNotIdle(unittest.TestCase):
    """The pane owns a PTY, but ownership is not knowledge.

    The old heartbeat called 2 s of silence `idle`. An agent waiting for an
    approval is silent; an agent thinking is silent. So silence withdraws the
    claim and the row falls back to `unknown`.
    """

    def setUp(self):
        try:
            from ostlr.qt import terminal
        except Exception as exc:                      # pragma: no cover
            self.skipTest("Qt not available: %s" % exc)
        self.terminal = terminal

    def test_output_flowing_is_working(self):
        self.assertEqual("working", self.terminal.pane_claim(0.0))
        self.assertEqual("working", self.terminal.pane_claim(1.0))

    def test_silence_withdraws_instead_of_inventing_idle(self):
        self.assertEqual("", self.terminal.pane_claim(self.terminal.QUIET_AFTER_S))
        self.assertEqual("", self.terminal.pane_claim(600.0))

    def test_the_pane_never_writes_idle(self):
        self.assertNotIn('_report("idle"', read("terminal.py"))

    def test_a_quiet_pane_says_why_its_row_is_unknown(self):
        app = read("app.py")
        self.assertIn("nothing reported", app)


if __name__ == "__main__":
    unittest.main()
