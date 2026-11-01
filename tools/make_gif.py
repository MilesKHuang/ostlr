"""Record the running panel as an animated GIF for the README.

Frames come from the same `PrintWindow` path as the still shot, so nothing here
depends on what is in front of the window. Pillow assembles them, because a GIF
with a palette is an order of magnitude smaller than N PNGs.

    py -3 tools\\make_gif.py docs\\ostlr.gif [--frames 8] [--interval 1.5]

The first frame is also what a reader sees if the animation does not play, so the
whole run should be plausible on its own.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

from PySide6.QtGui import QGuiApplication, QImage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import shot_window  # noqa: E402  - the window finder and the PrintWindow capture

user32 = ctypes.windll.user32
if hasattr(ctypes, "windll"):
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def pick_window(title: str = "Ostlr"):
    import ctypes.wintypes as wt

    candidates = []
    for hwnd, text, klass, pid in shot_window.windows():
        if title.lower() not in text.lower():
            continue
        if "Popup" in klass or "ToolTip" in klass or not klass.startswith("Qt"):
            continue
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area < 600 * 400:
            continue
        candidates.append((area, hwnd, text, klass))
    candidates.sort(reverse=True)
    return candidates[0] if candidates else None


def grab(hwnd: int):
    """One client-area frame as a QImage."""
    import ctypes.wintypes as wt

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    client = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    origin = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    image = shot_window.window_image(hwnd, width, height)
    if image is None:
        return None
    return image.copy(origin.x - rect.left, origin.y - rect.top,
                      client.right, client.bottom)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("docs", "ostlr.gif")
    frames_wanted = 8
    interval = 1.6
    for index, arg in enumerate(sys.argv):
        if arg == "--frames" and index + 1 < len(sys.argv):
            frames_wanted = max(2, int(sys.argv[index + 1]))
        if arg == "--interval" and index + 1 < len(sys.argv):
            interval = float(sys.argv[index + 1])

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    chosen = pick_window()
    if chosen is None:
        print("no Ostlr window found")
        return 2
    _area, hwnd, title, klass = chosen
    print("window: %r %s" % (title, klass))

    from PIL import Image

    frames = []
    target_width = 1000
    for index in range(frames_wanted):
        image = grab(hwnd)
        if image is None:
            print("frame %d: PrintWindow gave nothing" % index)
            continue
        if image.width() > target_width:
            from PySide6.QtCore import Qt
            image = image.scaledToWidth(target_width, Qt.SmoothTransformation)
        image = image.convertToFormat(QImage.Format_RGB32)
        raw = bytes(image.constBits())
        frame = Image.frombytes("RGBA", (image.width(), image.height()),
                                raw, "raw", "BGRA")
        frames.append(frame.convert("P", palette=Image.ADAPTIVE, colors=128))
        print("frame %d: %dx%d" % (index, image.width(), image.height()))
        time.sleep(interval)

    if len(frames) < 2:
        print("not enough frames")
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=int(interval * 1000), loop=0, optimize=True)
    print("wrote %s (%dx%d, %d frames, %.0f KB)"
          % (out, frames[0].width, frames[0].height, len(frames),
             os.path.getsize(out) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
