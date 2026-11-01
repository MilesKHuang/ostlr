"""Build the Ostlr exe.

    py -3 tools\\build_exe.py              # one folder (recommended, fast start)
    py -3 tools\\build_exe.py --onefile    # single file (slow start, big)

PySide6 + QtWebEngine decide the shape: a one-file build unpacks Chromium into
%TEMP% on every start, which costs seconds. One folder starts in about a second
and is what a tool you open all day should be.

The exe is built as a CONSOLE app on purpose: the same binary must answer
`ostlr --once --json` and `--selftest`. Double-clicking shows a console window
next to the panel; that is the price of one binary that can do both.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "ostlr_entry.py")
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build_pyi")


def main() -> int:
    onefile = "--onefile" in sys.argv
    if not os.path.exists(ENTRY):
        print("missing entry point: %s" % ENTRY, file=sys.stderr)
        return 2
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed: py -3 -m pip install -i <mirror> pyinstaller",
              file=sys.stderr)
        return 2
    for module in ("PySide6", "winpty"):
        try:
            __import__(module)
        except ImportError:
            print("%s is not installed; the panel cannot start without it" % module,
                  file=sys.stderr)
            return 2

    shutil.rmtree(WORK, ignore_errors=True)
    qt_dir = os.path.join(ROOT, "ostlr", "qt")

    argv = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile" if onefile else "--onedir",
        "--console",
        "--name", "ostlr",
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", WORK,
        # Keep the package layout: terminal.py resolves term.html relative to its
        # own __file__, and that path exists inside the bundle only when the
        # directory structure is preserved.
        # winpty ships DLLs AND two executables (OpenConsole.exe, winpty-agent.exe).
        # Missing either one makes the PTY fail with no message - the pane just
        # stays blank, which is the worst possible symptom to debug.
        "--collect-all", "winpty",
        # Keep the package layout: terminal.py resolves term.html relative to its
        # own __file__, and that path exists inside the bundle only when the
        # directory structure is preserved.
        "--add-data", os.path.join(qt_dir, "term.html") + os.pathsep + os.path.join("ostlr", "qt"),
        "--add-data", os.path.join(qt_dir, "vendor") + os.pathsep + os.path.join("ostlr", "qt", "vendor"),
        ENTRY,
    ]
    print(" ".join(argv))
    proc = subprocess.run(argv, cwd=ROOT)
    if proc.returncode != 0:
        return proc.returncode

    exe = (os.path.join(DIST, "ostlr.exe") if onefile
           else os.path.join(DIST, "ostlr", "ostlr.exe"))
    if not os.path.exists(exe):
        print("build finished but %s is missing" % exe, file=sys.stderr)
        return 1
    size = os.path.getsize(exe) / 1e6
    total = sum(os.path.getsize(os.path.join(base, name))
                for base, _dirs, files in os.walk(os.path.dirname(exe))
                for name in files) / 1e6
    print("built %s (exe %.1f MB, folder %.0f MB)" % (exe, size, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
