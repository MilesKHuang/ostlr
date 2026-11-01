"""Parity probe v2: measure a REAL Ostlr pane in PIXELS, not in the DOM.

Why pixels: the whole question is "does it look the same as the OS terminal", and
the DOM stops existing the moment a canvas/WebGL renderer draws the grid. One
metric has to work for every renderer, so it is measured on the screenshot.

Two numbers decide the frame:

  frame gaps   - at the left border's column, how many 2+ px runs of background
                 appear inside the frame's vertical span. A font-drawn border
                 leaves one gap per row boundary; a terminal-drawn border leaves
                 none.
  gauge fill   - inside a cell made of U+2588, what fraction of the cell box is
                 ink. A font block character is shorter than the row box; a
                 terminal-drawn block fills it.

    py -3 tools\\parity_probe.py                    # current build
    py -3 tools\\parity_probe.py --webgl            # with the WebGL addon injected
    py -3 tools\\parity_probe.py --png              # also save a screenshot to %TEMP%
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")

from PySide6.QtCore import QTimer                     # noqa: E402
from PySide6.QtGui import QImage                      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from ostlr import config as config_mod                 # noqa: E402
from ostlr.qt import terminal                          # noqa: E402

CHILD = os.path.join(HERE, "parity_child.py")
ADDON = os.path.join(ROOT, "ostlr", "qt", "vendor", "xterm-addon-webgl.js")

GEOM_JS = r"""
(function () {
  const cell = term._core._renderService.dimensions.css.cell;
  const holder = document.getElementById('terminal');
  const rect = holder.getBoundingClientRect();
  const screenEl = document.querySelector('.xterm-screen');
  const srect = screenEl ? screenEl.getBoundingClientRect() : rect;
  let renderer = 'unknown';
  try { renderer = term._core._renderService._renderer.constructor.name; } catch (e) {}
  return JSON.stringify({
    renderer: renderer,
    cell: [cell.width, cell.height],
    holder: [rect.left, rect.top, rect.width, rect.height],
    screen: [srect.left, srect.top, srect.width, srect.height],
    cols: term.cols, rows: term.rows,
    dpr: window.devicePixelRatio,
    fonts: document.fonts ? document.fonts.status : '?',
    family: term.options.fontFamily,
    canvases: document.querySelectorAll('#terminal canvas').length,
    domRows: document.querySelectorAll('.xterm-rows > div').length
  });
})()
"""

ADDON_JS = r"""
(function () {
  function attach() {
    try {
      const addon = new WebglAddon.WebglAddon();
      addon.onContextLoss(function () { window.__addonState = 'context lost'; });
      term.loadAddon(addon);
      window.__addon = addon;
      window.__addonState = 'attached';
    } catch (e) { window.__addonState = 'attach failed: ' + e; }
  }
  if (window.WebglAddon) { attach(); return window.__addonState; }
  const script = document.createElement('script');
  script.src = window.__addonPath;
  script.onload = attach;
  script.onerror = function () { window.__addonState = 'script failed to load'; };
  document.head.appendChild(script);
  return 'loading';
})()
"""


def bright(pixel: int) -> bool:
    r = pixel & 0xFF
    g = (pixel >> 8) & 0xFF
    b = (pixel >> 16) & 0xFF
    return (r + g + b) > 180


def analyze(img: QImage, geom: dict) -> dict:
    """Frame gaps on the left border, and how full a block cell is."""
    scale = img.width() / float(geom["holder"][2])
    cell_w = geom["cell"][0] * scale
    cell_h = geom["cell"][1] * scale
    origin = geom.get("screen") or geom["holder"]
    left = origin[0] * scale
    top = origin[1] * scale
    if img.format() != QImage.Format_RGB32:
        img = img.convertToFormat(QImage.Format_RGB32)
    stride = img.bytesPerLine()
    data = bytes(img.constBits())

    def px(x: int, y: int) -> int:
        offset = y * stride + x * 4
        return data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)

    # The left border lives in column 0: scan the middle of that cell.
    x = int(left + cell_w * 0.5)
    span_top = int(top + cell_h * 0.4)
    span_bottom = int(top + cell_h * 7.6)
    gaps = 0
    run = 0
    ink_rows = 0
    for y in range(span_top, span_bottom):
        if bright(px(x, y)):
            ink_rows += 1
            if run >= 2:
                gaps += 1
            run = 0
        else:
            run += 1
    if run >= 2:
        gaps += 1

    # The gauge row is the 6th line of the sample frame; its middle cell is
    # eight U+2588 characters.
    row_index = 7
    cell_x0 = left + (1 + 8 + 1) * cell_w
    filled = 0
    total = 0
    for cx in range(int(cell_x0 + cell_w * 0.5), int(cell_x0 + cell_w * 7.5), 3):
        for cy in range(int(top + cell_h * (row_index + 0.05)),
                        int(top + cell_h * (row_index + 0.95))):
            total += 1
            if bright(px(cx, cy)):
                filled += 1
    return {
        "frame_gaps": gaps,
        "border_ink_rows": ink_rows,
        "border_span_rows": span_bottom - span_top,
        "gauge_fill_pct": (100.0 * filled / total) if total else 0.0,
    }


def main() -> int:
    use_webgl = "--webgl" in sys.argv
    want_png = "--png" in sys.argv
    tag = "webgl" if use_webgl else "dom"
    png_path = os.path.join(tempfile.gettempdir(), "ostlr-parity-%s.png" % tag)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    cfg = config_mod.Config()
    pane = terminal.TerminalPane([sys.executable, CHILD], cwd=ROOT,
                                 title="parity", cfg=cfg)
    pane.resize(1000, 420)
    pane.move(-3000, -3000)
    pane.show()

    state = {"geom": None, "done": False, "addon": ""}

    def finish(status="ok"):
        if state["done"]:
            return
        state["done"] = True
        print("status:", status)
        app.quit()

    def after_geometry(payload):
        if not payload:
            finish("no geometry")
            return
        geom = json.loads(payload)
        state["geom"] = geom
        QTimer.singleShot(600, lambda: grab_and_report(geom))

    def grab_and_report(geom):
        img = pane.view.grab().toImage()
        metrics = analyze(img, geom)
        if want_png:
            try:
                pane.view.grab().save(png_path)
            except Exception as exc:
                print("png failed:", exc)
        title = ("WebGL addon (%s)" % state["addon"]) if use_webgl \
            else "current build (DOM renderer)"
        print("\n--- %s ---" % title)
        print("renderer=%s  canvases=%d  domRows=%d  fonts=%s"
              % (geom["renderer"], geom["canvases"], geom["domRows"], geom["fonts"]))
        print("cell=%.2f x %.2f CSS   grid %dx%d   dpr=%.2f"
              % (geom["cell"][0], geom["cell"][1], geom["cols"], geom["rows"],
                 geom["dpr"]))
        print("screenshot %dx%d px" % (img.width(), img.height()))
        print("pane.renderer reported to Python: %r" % (pane.renderer,))
        print("frame gaps along the left border : %d  (0 = continuous)"
              % metrics["frame_gaps"])
        print("border ink %d/%d rows" % (metrics["border_ink_rows"],
                                         metrics["border_span_rows"]))
        print("gauge cell fill                  : %.0f%%  (100%% = solid)"
              % metrics["gauge_fill_pct"])
        if want_png:
            print("png:", png_path)
        finish()

    def measure():
        pane.view.page().runJavaScript(GEOM_JS, after_geometry)

    def read_addon_state():
        pane.view.page().runJavaScript(
            "window.__addonState || 'not attached'",
            lambda value: (state.update({"addon": value or "?"}), measure()))

    def inject_addon():
        js = ("window.__addonPath = %s;\n%s"
              % (json.dumps("file:///" + ADDON.replace("\\", "/")), ADDON_JS))
        pane.view.page().runJavaScript(js, lambda _x: None)
        QTimer.singleShot(2000, read_addon_state)

    def on_started(_pid):
        if use_webgl:
            QTimer.singleShot(2200, inject_addon)
        else:
            QTimer.singleShot(2200, measure)

    pane.started.connect(on_started)
    QTimer.singleShot(12000, lambda: finish("timeout"))
    QTimer.singleShot(25000, app.quit)
    app.exec()
    return 0 if state["geom"] else 2


if __name__ == "__main__":
    sys.exit(main())
