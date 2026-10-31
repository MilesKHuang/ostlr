# AGENTS.md

Ostlr is a local Windows panel that lists the AI agent sessions running on this
machine (Claude Code / Codex / proJV / any shell), shows which one is waiting for
a human, and lets you focus / kill / restart / start one. Self-use tool, no cloud,
no telemetry. The design baseline is `docs/ostlr-implmentation.md` - read it
before changing behaviour; it carries the pain-point table, the rejected stacks
and the hard constraints.

## Environment (measured, not assumed)

| Fact | Value | Consequence |
|---|---|---|
| OS | Windows (64-bit), real desktop, 2560x1440 | GUI windows can be created and focused |
| Python | 3.13 via `py -3` - **`python` is the Store stub and does not work** | always use `py -3` in scripts and docs |
| PyPI | effectively unavailable: `psutil`, `PySide6`, `pywinpty`, `pystray`, `pillow`, `pywin32` all fail to install (only a few pure-python wheels resolve, downloads reset) | **standard library only** |
| GUI | `tkinter` (Tk 8.6) works | the panel is tkinter, not PySide6 |
| Win32 API | reachable through `ctypes` | window enumeration / focus / process walk without pywin32 |
| Process data | `powershell -NoProfile -Command "Get-CimInstance Win32_Process"` returns CommandLine, ParentProcessId, WorkingSetSize as JSON | parse it, do not shell out per row |

Deviations from `docs/ostlr-implmentation.md` §4.1 that follow from the table
above are **decided, not open**: tkinter replaces PySide6, ctypes replaces
pywin32, CIM replaces psutil, and L1 (OSC 7501 over our own ConPTY, needs
pywinpty) is **deferred with a documented stub**. L2 status files and L3 window
titles are the M1 state sources; the session list must show `unknown` rather than
guess (design §3.2, U5).

## Build, run, test

```
py -3 -m ostlr                 # run the panel
py -3 -m ostlr --selftest      # headless self-check, prints PASS/FAIL lines, exit 1 on failure
py -3 -m unittest discover -s tests -v
```

- No build step, no requirements.txt with third-party pins, no virtualenv.
- Every new module gets a unit test under `tests/`; pure logic (parsing CIM rows,
  state precedence, path handling, receipt text) must be testable without a GUI.
- `--selftest` must run on a machine with no agent sessions and still exit 0.

## Do not

- Do not add a dependency outside the Python standard library. If a feature
  truly needs one, write it down as blocked instead of adding it.
- Do not invent a state: no data means `unknown`, never `idle` (U5).
- Do not simulate keystrokes into another application's window. External
  sessions get focus + clipboard + receipt only (design §3.4, §4.8 rule 5).
- Do not write to other tools' config files or session databases. Read-only.
- Do not kill a process tree without showing what will die first.
- Do not commit `%APPDATA%/ostlr` contents, logs, or anything machine-specific.
- Do not leave orphan child processes on exit.

## Style

- Python 3.13, type hints on public functions, `from __future__ import annotations`.
- One module = one responsibility; the tkinter view never parses process data.
- User-facing strings in English; comments explain *why*, not *what*.
- Commit messages: `area: what changed` and one line of why.
