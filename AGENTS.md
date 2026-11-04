# AGENTS.md

Ostlr is a local Windows panel for the AI agent sessions running on this
machine: it lists them, says who is waiting for a human, and lets you start,
watch, type into, focus, kill and restart them. Self-use tool; no cloud, no
telemetry. The design baseline is `docs/ostlr-implmentation.md`; the agreed
implementation plan is `docs/sop/steve-ostlr.md`.

## Requirements (any machine)

| Requirement | Why |
|---|---|
| Windows 10 or 11, 64-bit, with a real desktop session | Qt windows, ConPTY and the tray need a desktop; a remote session or a CI runner has none |
| Python 3.10 or newer, launched as `py -3` | the `py` launcher ships with python.org installs; scripts and docs use that one form |
| `pip install -r requirements.txt` succeeds | PySide6, pywinpty, psutil; pystray and pillow are optional and their absence is stated on screen |
| WebView2 runtime | QtWebEngine hosts xterm.js; it ships with Windows 11 and is a download on Windows 10 |

What was measured on the author's machine - a thin PyPI index, mirror timings, a
WebView2 version, the build size - is in [`docs/dev-machine.md`](docs/dev-machine.md).
It does not constrain your machine, and it is not a support matrix.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Shell | PySide6 (Qt 6.12) | native window, DPI aware, no browser chrome |
| Terminal | ConPTY via `pywinpty` + `xterm.js` in `QWebEngineView` | we own the PTY (that is what L1 needs) and xterm.js does VT + IME; we write no terminal ourselves |
| Process facts | `psutil` + `Get-CimInstance Win32_Process` | tree memory, command lines, cwd where permitted |
| Tray | `pystray` + `pillow` | optional; absence is stated, never faked |

## Build, run, test

```
py -3 -m ostlr                    # the panel
py -3 -m ostlr --selftest         # PASS/SKIP lines
py -3 -m ostlr run -- <cmd>       # the producer wrapper
py -3 -m ostlr.status --list      # what the programs report
py -3 -m unittest discover -s tests -v
.venv\Scripts\python tools\build_exe.py   # dist\ostlr\ostlr.exe (one folder; build from the clean venv, see docs/dev-machine.md)
```

- Every module has a unit test. Pure logic (sessions, ladder, store, view_model,
  discovery, layout) must be testable without a window.
- `--selftest` must run with Qt unavailable and still exit 0.
- The import test enforces "no UNDECLARED third-party imports": the allow-list is
  `schema.ALLOWED_THIRD_PARTY`. Adding a dependency means adding it there, to
  `requirements.txt`, and to the README.

## Do not

- Do not list a process this panel did not start. The list is the panes we own;
  discovery belongs in the launcher. A row you cannot type into is a lie about
  what the panel can do.
- Do not let the font decide geometry. A cell's position belongs to the terminal:
  keep `customGlyphs` and a renderer that implements it (`docs/parity.md` has the
  measured numbers). A fallback to the DOM renderer is allowed only when the pane
  says so in the receipt.
- Do not invent a state. No data means `unknown`, never `idle`.
- Do not fake an affordance. If the tray cannot be drawn or a PTY cannot be
  adopted, say so on screen.
- Do not adopt a PTY you do not own. An external session gets focus, a copy of
  its path, or a shell in its directory - never a pretend "attach".
- Do not offer a second destination for a session. A TUI starts as a pane of
  this window or not at all: an external console is a row you cannot type into,
  and `start_from()` takes no `location`.
- Do not kill a process tree without showing the pid count and the memory first.
- Do not leave orphan children on exit; panes stop their own PTY.
- Do not put a colour, a font size or a glyph outside `ostlr/qt/style.py` (roles)
  and `ostlr/qt/vendor` (the renderer).
- Do not scan the machine for sessions. The list is the panes we own; a program
  joins it only by being named once (`Browse...` writes `[[agents]]` into
  config.toml). No process table, no window titles, no "probably an agent".
- Do not hardcode agent paths either: the user's own entry in `config.toml` is the
  only stored location, and the user chose it.
- Do not commit `%APPDATA%\ostlr` contents, logs, `dist/` or `build_pyi/`.

## Style

- Python 3.13, type hints on public functions, `from __future__ import annotations`.
- One module = one responsibility; the view never parses process data.
- User-facing strings in English, in the user's words (no protocol names in the
  first screen).
- Commit messages: `area: what changed` plus one line of why.
