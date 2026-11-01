"""Frozen-build entry point (PyInstaller).

`py -3 -m ostlr` needs an interpreter; a one-file exe does not have one. This
module is the equivalent entry point and is what `tools/build_exe.py` freezes.
"""
import sys

from ostlr.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
