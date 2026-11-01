"""Photograph the running Ostlr window for the README.

Finds the window by title, raises it, grabs the screen and crops to the window's
own frame. Qt does the pixels (`QScreen.grabWindow`), ctypes only finds and raises
the window, so nothing here needs PIL.

    py -3 _verify\\shot_window.py docs\\ostlr.png [--title Ostlr]

Refuses to write a picture that is one flat colour: a wrong crop is worse than no
picture, and this runs unattended.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

user32 = ctypes.windll.user32
try:
    user32.SetProcessDPIAware()
except Exception:
    pass


def windows():
    """Every visible top-level window as (hwnd, title, class name)."""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        klass = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, klass, 256)
        found.append((hwnd, title.value, klass.value))
        return True

    user32.EnumWindows(callback, 0)
    return found


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("docs", "ostlr.png")
    wanted = "Ostlr"
    for index, arg in enumerate(sys.argv):
        if arg == "--title" and index + 1 < len(sys.argv):
            wanted = sys.argv[index + 1]

    candidates = []
    for hwnd, title, klass in windows():
        if wanted.lower() not in title.lower():
            continue
        # Qt tooltips, popups and drop-shadow helpers carry the window title too;
        # photographing one of those is how this produced a 297x194 picture.
        if "Popup" in klass or "ToolTip" in klass:
            continue
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area < 600 * 400:
            continue
        candidates.append((area, hwnd, title, klass))
    candidates.sort(reverse=True)
    if not candidates:
        print("no window whose title contains %r. visible windows:" % wanted)
        for _hwnd, title, klass in windows():
            print("  %-38s %s" % (klass, title[:64]))
        return 2
    _area, hwnd, title, klass = candidates[0]
    print("window: %r class=%s hwnd=%s" % (title, klass, hwnd))

    user32.ShowWindow(hwnd, 9)              # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)                         # let the compositor settle

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    x, y = rect.left, rect.top
    width, height = rect.right - rect.left, rect.bottom - rect.top

    # The client area is the window without Windows' own title bar: a README
    # picture of the product, not of the desktop's chrome.
    try:
        client = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(client))
        origin = wt.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(origin))
        if client.right > 200 and client.bottom > 150:
            x, y = origin.x, origin.y
            width, height = client.right - client.left, client.bottom - client.top
    except Exception as exc:
        print("client rect unavailable:", exc)
    print("frame: %dx%d at %d,%d" % (width, height, x, y))

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    screen = QGuiApplication.primaryScreen()
    shot = screen.grabWindow(0)
    print("screen grab: %dx%d" % (shot.width(), shot.height()))
    # A maximized window reports a frame one border wider than the screen; without
    # clamping, the crop silently shifts everything by that offset.
    x = max(0, x)
    y = max(0, y)
    width = min(width, shot.width() - x)
    height = min(height, shot.height() - y)
    crop = shot.copy(x, y, width, height)
    if crop.width() < 200 or crop.height() < 150:
        print("crop is too small: %dx%d" % (crop.width(), crop.height()))
        return 1

    # README size: a 4K window scaled to 1600 px reads the same and is 10x smaller.
    target = 1600
    if crop.width() > target:
        crop = crop.scaledToWidth(target, Qt.SmoothTransformation)

    # A flat crop means the window was not where Win32 said it was.
    image = crop.toImage()
    colours = set()
    for gx in range(0, image.width(), 24):
        for gy in range(0, image.height(), 24):
            colours.add(image.pixel(gx, gy))
            if len(colours) > 24:
                break
    print("distinct colours sampled: %d" % len(colours))
    if len(colours) < 4:
        print("refusing to write a flat picture")
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if not crop.save(out, "PNG"):
        print("could not write %s" % out)
        return 1
    print("wrote %s (%dx%d, %.0f KB)"
          % (out, crop.width(), crop.height(), os.path.getsize(out) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
