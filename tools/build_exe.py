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

# Packages that prove the interpreter is a shared, polluted environment. When
# any of them is importable, PyInstaller runs its hook and the exe grows by
# tens of MB of code the panel never imports - numpy shipped in a 621 MB build
# this way. The allow-list in ostlr/schema.py forbids importing them, but the
# bundler does not read that list; the build must run in a clean venv.
DIRTY_PACKAGES = ("numpy", "pandas", "matplotlib", "torch", "scipy", "pytest")


def _in_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _dirty_packages() -> list[str]:
    import importlib.metadata as metadata
    found = []
    for name in DIRTY_PACKAGES:
        try:
            metadata.distribution(name)
            found.append(name)
        except metadata.PackageNotFoundError:
            pass
    return found


def _check_clean_environment() -> int:
    """Refuse to bundle from a shared interpreter, unless told otherwise.

    Returns 0 to continue, 2 to stop. A one-flag escape hatch exists for the
    person who knows their global interpreter really is dedicated to this
    project, but the default is to stop and say how to build cleanly.
    """
    if "--allow-dirty" in sys.argv:
        return 0
    if _in_venv():
        return 0
    dirty = _dirty_packages()
    if not dirty:
        return 0
    print(
        "Refusing to build: the global interpreter also carries %s." % ", ".join(dirty),
        file=sys.stderr,
    )
    print(
        "PyInstaller collects their hooks, so the exe balloons with code the panel "
        "never imports. Build in a clean venv instead:",
        file=sys.stderr,
    )
    print("  py -3 -m venv .venv", file=sys.stderr)
    print("  .venv\\Scripts\\python -m pip install -r requirements.txt pyinstaller", file=sys.stderr)
    print("  .venv\\Scripts\\python tools\\build_exe.py", file=sys.stderr)
    print("  (or pass --allow-dirty if you are sure this interpreter is dedicated)", file=sys.stderr)
    return 2


def main() -> int:
    onefile = "--onefile" in sys.argv
    clean = _check_clean_environment()
    if clean != 0:
        return clean
    # A running panel holds DLLs open inside its own folder, so the build that
    # produced it cannot be overwritten while it runs. `--dist <dir>` builds beside
    # it; copy the folder over the old one when the panel is closed.
    dist_dir = DIST
    if "--dist" in sys.argv:
        index = sys.argv.index("--dist")
        if index + 1 < len(sys.argv):
            dist_dir = os.path.abspath(sys.argv[index + 1])
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
        "--distpath", dist_dir,
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

    exe = (os.path.join(dist_dir, "ostlr.exe") if onefile
           else os.path.join(dist_dir, "ostlr", "ostlr.exe"))
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
