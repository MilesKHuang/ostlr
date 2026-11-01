"""Photograph the running Ostlr window for the README.

Two ways to get the pixels, in order:

  1. `PrintWindow(hwnd, ..., PW_RENDERFULLCONTENT)` - the window renders itself
     into a bitmap, so the picture does not depend on what is in front. A screen
     grab made a half-browser, half-panel picture the moment the user browsed the
     web while it ran.
  2. a screen grab of the window's rectangle, with the window raised first, if
     PrintWindow returns nothing usable.

Only the client area is kept (no Windows title bar) and the result is scaled to
1600 px, because a 4K window in a README reads the same at a tenth of the size.

    py -3 tools\\shot_window.py docs\\ostlr.png [--title Ostlr | --pid 1234]
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
from PySide6.QtGui import QGuiApplication, QImage

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
try:
    user32.SetProcessDPIAware()
except Exception:
    pass

PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def windows():
    """Every visible top-level window as (hwnd, title, class name, pid)."""
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
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, title.value, klass.value, int(pid.value)))
        return True

    user32.EnumWindows(callback, 0)
    return found


def window_image(hwnd: int, width: int, height: int) -> QImage | None:
    """The window's own pixels, whether or not it is in front."""
    hdc = user32.GetWindowDC(hwnd)
    if not hdc:
        return None
    mem = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(mem, bitmap)
    try:
        if not user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
            return None
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height        # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        stride = width * 4
        buffer = ctypes.create_string_buffer(stride * height)
        copied = gdi32.GetDIBits(mem, bitmap, 0, height, buffer, ctypes.byref(info),
                                 DIB_RGB_COLORS)
        if copied != height:
            return None
        image = QImage(bytes(buffer), width, height, stride,
                       QImage.Format_RGB32)
        return image.copy()          # detach from the buffer we are about to free
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def distinct(image: QImage, step: int = 24) -> int:
    colours = set()
    for gx in range(0, image.width(), step):
        for gy in range(0, image.height(), step):
            colours.add(image.pixel(gx, gy))
            if len(colours) > 24:
                return len(colours)
    return len(colours)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("docs", "ostlr.png")
    wanted = "Ostlr"
    wanted_pid = 0
    for index, arg in enumerate(sys.argv):
        if arg == "--title" and index + 1 < len(sys.argv):
            wanted = sys.argv[index + 1]
        if arg == "--pid" and index + 1 < len(sys.argv):
            wanted_pid = int(sys.argv[index + 1])

    candidates = []
    for hwnd, title, klass, pid in windows():
        if wanted_pid and pid != wanted_pid:
            continue
        if not wanted_pid and wanted.lower() not in title.lower():
            continue
        # Qt tooltips, popups and drop-shadow helpers carry the window title too;
        # photographing one of those is how this produced a 297x194 picture. The
        # panel is a Qt window, so a console window beside it is not a candidate.
        if "Popup" in klass or "ToolTip" in klass or not klass.startswith("Qt"):
            continue
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area < 600 * 400:
            continue
        candidates.append((area, hwnd, title, klass))
    candidates.sort(reverse=True)
    if not candidates:
        print("no window for title=%r pid=%s. visible windows:" % (wanted, wanted_pid))
        for _hwnd, title, klass, pid in windows():
            print("  %-38s pid=%-7s %s" % (klass, pid, title[:56]))
        return 2
    _area, hwnd, title, klass = candidates[0]
    print("window: %r class=%s hwnd=%s" % (title, klass, hwnd))

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top

    # The client area is the window without Windows' own title bar: a README
    # picture of the product, not of the desktop's chrome.
    client = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    origin = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    offset_x = origin.x - rect.left
    offset_y = origin.y - rect.top
    print("window %dx%d, client %dx%d at offset %d,%d"
          % (width, height, client.right, client.bottom, offset_x, offset_y))

    image = window_image(hwnd, width, height)
    if image is not None:
        image = image.copy(offset_x, offset_y, client.right, client.bottom)
    else:
        print("PrintWindow gave nothing; falling back to a screen grab")
        user32.ShowWindow(hwnd, 9)              # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(1.0)
        app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
        screen = QGuiApplication.primaryScreen()
        shot = screen.grabWindow(0)
        x = max(0, origin.x)
        y = max(0, origin.y)
        grab_w = min(client.right, shot.width() - x)
        grab_h = min(client.bottom, shot.height() - y)
        image = shot.copy(x, y, grab_w, grab_h).toImage()

    if image.width() < 200 or image.height() < 150:
        print("crop is too small: %dx%d" % (image.width(), image.height()))
        return 1
    colours = distinct(image)
    print("distinct colours sampled: %d" % colours)
    if colours < 4:
        print("refusing to write a flat picture")
        return 1

    target = 1600
    if image.width() > target:
        image = image.scaledToWidth(target, Qt.SmoothTransformation)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if not image.save(out, "PNG"):
        print("could not write %s" % out)
        return 1
    print("wrote %s (%dx%d, %.0f KB)"
          % (out, image.width(), image.height(), os.path.getsize(out) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
