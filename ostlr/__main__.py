"""Ostlr entry point.

    py -3 -m ostlr                 # the panel
    py -3 -m ostlr --selftest      # headless PASS/SKIP lines
    py -3 -m ostlr run -- <cmd>    # the producer wrapper
    py -3 -m ostlr.status --list   # what the programs on this machine report

There is deliberately no `--once` and no machine scan. The panel lists the panes
it owns, so a snapshot taken without the window would describe a different world -
and it did: two definitions of "a session" that could never agree. What a program
says about itself is in the status directory, and `ostlr.status --list` prints
exactly that, without guessing anything.

Two rules this file keeps:

  * `main()` never enters a mainloop unless it was asked to open the panel, so a
    test can assert the wiring without a window (`OSTLR_NO_PANEL=1` refuses to
    open one at all).
  * Everything that can run without Qt does: `--selftest` and `run` are usable
    over SSH, in CI, or from a broken environment.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, Optional, Sequence

from . import config as config_mod

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_BUILT = 3

# Set this to 1 to make `main([])` refuse to open a window. A test uses it so a
# stray `main([])` can never block the suite in a Qt mainloop.
ENV_NO_PANEL = "OSTLR_NO_PANEL"


# --- commands -------------------------------------------------------------

def cmd_selftest(cfg: config_mod.Config, clipboard: bool = False) -> int:
    """Headless checks. One line each; SKIP is not a failure."""
    from . import selftest as selftest_mod

    if cfg.load_error:
        print("SKIP config: %s" % cfg.load_error)
    else:
        print("PASS config loaded from %s" % (cfg.path or "(defaults)"))
    print("PASS config tick_s=%d cim_timeout_ms=%d"
          % (cfg.tick_s, cfg.cim_timeout_ms))
    return selftest_mod.run(clipboard=clipboard)


def cmd_run(argv: Sequence[str]) -> int:
    """The producer wrapper (T8): delegate to ostlr.status.run."""
    from . import status as status_mod

    return status_mod.run(argv)


def _qt_available() -> bool:
    """Is the Qt stack importable? Package lookup only, no window is opened."""
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        return True
    except Exception:
        return False


def cmd_panel(cfg: config_mod.Config,
              panel_runner: Optional[Callable[[config_mod.Config], int]] = None) -> int:
    """Open the Qt panel and block on its event loop.

    `panel_runner` is the injection point a test uses: it receives the config and
    returns an exit code, so the wiring can be asserted without a window.
    """
    if panel_runner is not None:
        return panel_runner(cfg)

    if os.environ.get(ENV_NO_PANEL) == "1":
        print("[ostlr] %s=1: not opening the panel" % ENV_NO_PANEL, file=sys.stderr)
        return EXIT_NOT_BUILT

    from .qt import run_panel as qt_run_panel

    if not _qt_available():
        print("[ostlr] Qt (PySide6) is not available; install it from the mirror",
              file=sys.stderr)
        return EXIT_NOT_BUILT
    return qt_run_panel(cfg)


# --- argparse -------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ostlr",
        description="Run AI agent sessions in panes this panel owns, and read "
                    "what they report.",
    )
    parser.add_argument("--selftest", action="store_true",
                        help="run headless checks and print PASS/FAIL/SKIP lines")
    parser.add_argument("--clipboard", action="store_true",
                        help="with --selftest: also check the clipboard round trip")
    parser.add_argument("--config", metavar="PATH", default=None,
                        help="config file to use instead of the default")
    parser.add_argument("command", nargs="?", default="panel",
                        help="panel (default) or run")
    parser.add_argument("rest", nargs=argparse.REMAINDER,
                        help="arguments for the subcommand")
    return parser


def main(argv: Optional[Sequence[str]] = None,
         panel_runner: Optional[Callable[[config_mod.Config], int]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)

    if args.selftest:
        return cmd_selftest(cfg, clipboard=args.clipboard)

    if args.command == "run":
        rest = list(args.rest)
        if rest and rest[0] == "--":
            rest = rest[1:]
        return cmd_run(rest)

    return cmd_panel(cfg, panel_runner=panel_runner)


if __name__ == "__main__":
    raise SystemExit(main())
