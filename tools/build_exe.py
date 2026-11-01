"""Build the single-file Ostlr exe.

    py -3 tools\\build_exe.py

PyInstaller is used only for packaging; the program itself still imports
nothing outside the standard library, which is what keeps the exe small enough
to be practical and the whole thing runnable on a machine with no packages.

Output: dist\\ostlr.exe. It is built as a CONSOLE app on purpose: the same exe
must answer `ostlr.exe --once --json` and `--selftest`, and a windowed build
would swallow those. Double-clicking it shows a console window next to the
panel; that is the price of one binary that can do both.
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
    if not os.path.exists(ENTRY):
        print("missing entry point: %s" % ENTRY, file=sys.stderr)
        return 2
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed: py -3 -m pip install pyinstaller", file=sys.stderr)
        return 2

    for path in (WORK,):
        shutil.rmtree(path, ignore_errors=True)

    argv = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--console",
        "--name", "ostlr",
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", WORK,
        # tkinter needs its Tcl/Tk data; --collect-data keeps the theme files.
        "--collect-data", "tkinter",
        ENTRY,
    ]
    print(" ".join(argv))
    proc = subprocess.run(argv, cwd=ROOT)
    if proc.returncode != 0:
        return proc.returncode

    exe = os.path.join(DIST, "ostlr.exe" if os.name == "nt" else "ostlr")
    if not os.path.exists(exe):
        print("build finished but %s is missing" % exe, file=sys.stderr)
        return 1
    print("built %s (%.1f MB)" % (exe, os.path.getsize(exe) / 1e6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
