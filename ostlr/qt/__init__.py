"""Entry point for the Qt panel.

QtWebEngine needs its attributes set BEFORE the QApplication exists, so this
module owns the process bootstrap. `ostlr` (no arguments) lands here; `--once`
and `--selftest` never touch Qt.
"""

from __future__ import annotations

import os
import sys
from typing import Optional


def run_panel(cfg=None) -> int:
    from .. import __main__ as cli
    from .. import config as config_mod

    if cfg is None:
        cfg = config_mod.load()

    # Must happen before QApplication: without the shared GL context the web view
    # silently never loads, and the panel looks broken with no error anywhere.
    # Must happen before QApplication: without the shared GL context the web view
    # silently never loads.
    #
    # NO `--disable-gpu`: that flag was there to silence a warning and it cost the
    # real thing - Chromium then composites in software, and a 1500x1050 surface
    # with a 1.5 device pixel ratio paints far slower. Measured on this machine:
    # ~54 repaints/s with the GPU path, visibly worse without it.
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Ostlr")
    app.setOrganizationName("ostlr")

    from .app import MainWindow
    window = MainWindow(cfg, provider=lambda: cli.build_snapshot(cfg))
    window.show()
    return app.exec()


def run_headless_probe(cfg) -> int:
    """Open the window, refresh once, close. Used by the smoke test."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from .. import __main__ as cli
    from .app import MainWindow
    window = MainWindow(cfg, provider=lambda: cli.build_snapshot(cfg))
    window.show()
    QTimer.singleShot(2500, window.close)
    QTimer.singleShot(3500, app.quit)
    return app.exec()
