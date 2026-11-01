"""System tray: the panel keeps watching while it is not on screen.

pystray runs its own loop, so it gets a thread and talks back through signals.
When Pillow or pystray is missing the tray degrades to nothing and the window
title keeps the count - the design's rule is that a missing affordance is stated,
never faked.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Optional

from PySide6.QtCore import QObject, Signal


def available() -> bool:
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
        return True
    except Exception:
        return False


def _icon_image(count: int, colour: str):
    """A 64x64 dot with a badge count: no asset file, no theme dependency."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(20, 23, 28, 255))
    draw.ellipse([18, 14, 46, 42], fill=colour)
    if count > 0:
        text = str(min(count, 99))
        draw.text((30, 46), text, fill=(230, 230, 230, 255))
    return image


class Tray(QObject):
    """A tray icon whose menu drives the window."""

    show_requested = Signal()
    refresh_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self.reason = "" if available() else "pystray/Pillow not installed"

    def start(self) -> bool:
        if not available():
            return False
        if self._icon is not None:
            return True
        try:
            import pystray
        except Exception:
            return False
        menu = pystray.Menu(
            pystray.MenuItem("Show Ostlr", lambda: self.show_requested.emit(), default=True),
            pystray.MenuItem("Refresh", lambda: self.refresh_requested.emit()),
            pystray.MenuItem("Quit", lambda: self.quit_requested.emit()),
        )
        self._icon = pystray.Icon("ostlr", _icon_image(0, "#4f9cf0"), "Ostlr", menu)
        self._thread = threading.Thread(target=self._run, name="ostlr-tray", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception:
            self._icon = None

    def update(self, waiting: int, running: int) -> None:
        """Repaint the badge. Any colour failure leaves the previous icon."""
        if self._icon is None:
            return
        colour = "#e2c07c" if waiting else ("#7ec97e" if running else "#5a6068")
        try:
            self._icon.icon = _icon_image(waiting or running, colour)
            self._icon.title = "Ostlr - %d waiting, %d running" % (waiting, running)
        except Exception:
            pass

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
        self._icon = None
