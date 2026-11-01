"""Ostlr entry point.

    py -3 -m ostlr                      # the panel
    py -3 -m ostlr --once [--json]      # one snapshot to stdout, then exit
    py -3 -m ostlr --once --json --fixture <path>
    py -3 -m ostlr --selftest [--clipboard]
    py -3 -m ostlr run -- <cmd>         # the producer wrapper

Three rules this file exists to keep:

  * `--once --json` prints exactly ONE JSON object on stdout and nothing else.
    Diagnostics go to stderr, so a verifier can pipe stdout straight into a JSON
    parser. That is the whole reason the flag exists.
  * A fixture is an *input*, not an answer key: `--fixture` loads the recorded
    snapshot and runs the same pipeline on it, so the deterministic check and
    the live check exercise one code path.
  * `main()` never enters a mainloop unless it was asked to open the panel. A
    test that calls `main([])` must be able to assert the wiring without a
    window, so the panel runner is injectable and `OSTLR_NO_PANEL=1` refuses to
    open one at all.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import config as config_mod
from . import agents as agents_mod
from . import ladder, paths, schema, sessions as sessions_mod, store as store_mod
from . import view_model
from .probe import cim, l2, l3

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_BUILT = 3

# Set this to 1 to make `main([])` refuse to open a window. A test uses it so a
# stray `main([])` can never block the suite in a Tk mainloop.
ENV_NO_PANEL = "OSTLR_NO_PANEL"


# --- fixture --------------------------------------------------------------

class FixtureError(ValueError):
    """The fixture file is missing or not the shape we expect."""


def load_fixture(path: str) -> Dict[str, Any]:
    """Load a recorded snapshot: {now, cim, l2, titles, status_dir_error}.

    Every key is optional and gets a sane empty value, because a fixture that
    only wants to exercise the CIM rows should not have to spell out the rest.
    A malformed file raises `FixtureError` with the reason - a silent empty
    snapshot would make a broken fixture look like a passing test.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise FixtureError("fixture not found: %s" % path) from exc
    except OSError as exc:
        raise FixtureError("cannot read fixture %s: %s" % (path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise FixtureError("fixture %s is not JSON: %s" % (path, exc)) from exc

    if not isinstance(data, dict):
        raise FixtureError("fixture %s must be a JSON object" % path)

    now = data.get("now")
    try:
        now_int = int(now) if now is not None else 0
    except (TypeError, ValueError):
        raise FixtureError("fixture %s: 'now' must be an integer epoch" % path)

    raw_cim = data.get("cim") or []
    if not isinstance(raw_cim, list):
        raise FixtureError("fixture %s: 'cim' must be a list" % path)
    cim_rows: List[Dict[str, Any]] = []
    for item in raw_cim:
        if not isinstance(item, dict):
            raise FixtureError("fixture %s: every 'cim' entry must be an object" % path)
        cim_rows.append(dict(item))

    raw_l2 = data.get("l2") or []
    if not isinstance(raw_l2, list):
        raise FixtureError("fixture %s: 'l2' must be a list" % path)

    titles = data.get("titles") or {}
    if not isinstance(titles, dict):
        raise FixtureError("fixture %s: 'titles' must be an object" % path)

    status_dir_error = data.get("status_dir_error")
    if status_dir_error is not None and not isinstance(status_dir_error, str):
        raise FixtureError("fixture %s: 'status_dir_error' must be null or a string" % path)

    return {
        "now": now_int,
        "cim": cim_rows,
        "l2": [dict(x) for x in raw_l2 if isinstance(x, dict)],
        "titles": {str(k): str(v) for k, v in titles.items()},
        "status_dir_error": status_dir_error,
        "path": path,
    }


# --- snapshot -------------------------------------------------------------

def _fixture_l2(fixture: Dict[str, Any]) -> Tuple[Dict[int, Dict[str, Any]], str]:
    """Validate the fixture's L2 records exactly as the live reader would.

    A fixture record goes through `l2.validate`, so a malformed one is absent
    here for the same reason it would be absent live - the fixture cannot
    smuggle in a state the real pipeline would reject.
    """
    out: Dict[int, Dict[str, Any]] = {}
    for raw in fixture.get("l2") or []:
        record, _reason = l2.validate(raw)
        if record is not None:
            out[record["pid"]] = record
    return out, fixture.get("status_dir_error") or ""


def _fixture_titles(fixture: Dict[str, Any]) -> Dict[int, str]:
    """The fixture's `{pid: title}` map, with string keys coerced to int."""
    out: Dict[int, str] = {}
    for key, value in (fixture.get("titles") or {}).items():
        try:
            out[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return out


def build_snapshot(cfg: config_mod.Config,
                   fixture: Optional[Dict[str, Any]] = None,
                   now: Optional[int] = None) -> Dict[str, Any]:
    """Produce the v1 envelope: CIM -> sessions -> ladder -> store -> view model.

    One pipeline for both paths. A fixture supplies the CIM rows, the L2 records
    and the window titles; the live path probes the machine and reads the real
    status directory. `now` is a parameter (the fixture carries its own), so the
    3 s highlight and the 600 s retention are testable without sleeping.
    """
    if now is None:
        now = int(time.time())
    now = int(now)

    if fixture is not None:
        rows = [cim.normalise_row(r) for r in fixture["cim"]]
        cim_rows = [r for r in rows if r is not None]
        l2_records, status_dir_error = _fixture_l2(fixture)
        titles = _fixture_titles(fixture)
    else:
        sample = cim.CimProbe(timeout_ms=cfg.cim_timeout_ms).sample()
        cim_rows = sample.rows
        if not sample.ok:
            print("[ostlr] cim probe: %s" % (sample.error or "failed"), file=sys.stderr)
        l2_records, status_dir_error = l2.read_status_dir()
        titles = l3.enumerate_titles()

    sessions = sessions_mod.build_sessions(cim_rows, l2_records, {}, now)

    # The ladder decides each session's state; the store remembers it across
    # ticks (retention, already_exited, slots) and publishes the rows.
    verdicts: Dict[Any, Dict[str, Any]] = {}
    for session in sessions:
        pid = int(session.get("pid") or 0)
        if pid <= 0:
            continue
        if status_dir_error:
            verdict = ladder.unknown_for_dir(status_dir_error)
        else:
            verdict = ladder.combine(
                l2_records.get(pid),
                titles.get(pid),
                True,
                now,
                cfg,
            )
        # The exit code is a fact about the process, not a state: the ladder
        # decides `done`, the producer's record says whether it was a failure.
        # Without this the view cannot tell a clean exit from a crash, and a
        # crashed agent would render in the success colour.
        record = l2_records.get(pid) or {}
        if record.get("exit_code") is not None:
            verdict["exit_code"] = record.get("exit_code")
        verdicts[(pid, int(session.get("started_at") or 0))] = verdict

    store = store_mod.Store(cfg)
    store.apply_intents()
    state_rows = store.observe(sessions, verdicts, now)

    return view_model.build_snapshot(sessions, state_rows, {}, now, cfg.list_cap)


# --- commands -------------------------------------------------------------

def cmd_once(cfg: config_mod.Config, as_json: bool,
             fixture_path: Optional[str]) -> int:
    fixture = None
    if fixture_path:
        try:
            fixture = load_fixture(fixture_path)
        except FixtureError as exc:
            print("[ostlr] %s" % exc, file=sys.stderr)
            return EXIT_USAGE

    # A fixture carries its own `now`, so the deterministic check does not drift
    # with the wall clock (a 5 s old L2 record must stay 5 s old forever).
    snapshot = build_snapshot(cfg, fixture,
                              now=fixture["now"] if fixture is not None else None)

    if as_json:
        # Exactly one object, nothing else on stdout.
        sys.stdout.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return EXIT_OK

    header = snapshot["header"]
    print("sessions: %d" % header["sessions"])
    for key in schema.HEADER_KEYS:
        if key == "sessions":
            continue
        print("  %-16s %d" % (key, header[key]))
    print("rows: %d" % len(snapshot["rows"]))
    return EXIT_OK


def cmd_selftest(cfg: config_mod.Config, clipboard: bool = False) -> int:
    """Headless checks. One line each; SKIP is not a failure.

    The checks themselves live in `ostlr.selftest` so the panel, the CLI and a
    test all run the same code. This function only adds the config lines, which
    need the loaded config.
    """
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


def cmd_panel(cfg: config_mod.Config,
              panel_runner: Optional[Callable[[config_mod.Config], int]] = None) -> int:
    """Open the Tk panel and block on its mainloop.

    `panel_runner` is the injection point a test uses: it receives the config and
    returns an exit code, so the wiring can be asserted without a window. The
    environment variable `OSTLR_NO_PANEL=1` refuses to open one at all, which is
    the belt to that brace - a stray `main([])` in a test can never hang.
    """
    if panel_runner is not None:
        return panel_runner(cfg)

    if os.environ.get(ENV_NO_PANEL) == "1":
        print("[ostlr] %s=1: not opening the panel" % ENV_NO_PANEL, file=sys.stderr)
        return EXIT_NOT_BUILT

    from .ui import app as app_mod

    ok, reason = app_mod.tk_available()
    if not ok:
        print("[ostlr] no display: %s" % reason, file=sys.stderr)
        return EXIT_NOT_BUILT
    return app_mod.run_panel(cfg)


# --- argparse -------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ostlr",
        description="List the AI agent sessions running on this machine.",
    )
    parser.add_argument("--once", action="store_true",
                        help="print one snapshot and exit")
    parser.add_argument("--json", action="store_true",
                        help="with --once: print the v1 envelope as JSON")
    parser.add_argument("--fixture", metavar="PATH", default=None,
                        help="with --once: read a recorded snapshot instead of probing")
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
    # `[[agents]]` overrides (baseline 4.5) must land before anything reads the
    # registry: the launcher's PATH pre-check and the session matcher both read
    # agents.TEMPLATES at call time.
    agents_mod.apply_overrides(cfg.agents)

    if args.selftest:
        return cmd_selftest(cfg, clipboard=args.clipboard)

    if args.command == "run":
        rest = list(args.rest)
        if rest and rest[0] == "--":
            rest = rest[1:]
        return cmd_run(rest)

    if args.once:
        return cmd_once(cfg, args.json, args.fixture)

    if args.json or args.fixture:
        # These only mean something with --once; say so instead of ignoring them.
        print("[ostlr] --json and --fixture require --once", file=sys.stderr)
        return EXIT_USAGE

    return cmd_panel(cfg, panel_runner=panel_runner)


if __name__ == "__main__":
    raise SystemExit(main())
