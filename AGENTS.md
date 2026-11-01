# AGENTS.md

Ostlr is a local Windows panel for the AI agent sessions running on this
machine: it lists them, says who is waiting for a human, and lets you start,
watch, type into, focus, kill and restart them. Self-use tool; no cloud, no
telemetry. The design baseline is `docs/ostlr-implmentation.md`; the agreed
implementation plan is `docs/sop/steve-ostlr.md`.

## Environment (measured, not assumed)

| Fact | Value | Consequence |
|---|---|---|
| OS | Windows (64-bit), real desktop | Qt windows, ConPTY and a tray all work |
| Python | 3.13 via `py -3` - **`python` is the Store stub** | always `py -3` in scripts and docs |
| Default PyPI index | serves **pure-Python wheels only** (`psutil`, `PySide6`, `pywinpty` all "not found") | do not conclude a package is impossible from it - that mistake cost a whole design pivot |
| Mirror | `https://mirrors.aliyun.com/pypi/simple/` works and has the win_amd64 wheels; USTC works; Tsinghua times out here | install with `-i <mirror>`; `requirements.txt` documents it |
| WebView2 runtime | installed (156.x) | an alternative renderer path exists if Qt is ever dropped |
| Windows Terminal | `wt.exe` present | an external-window fallback is available |

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
py -3 -m ostlr --once --json      # one snapshot, headless
py -3 -m ostlr --selftest         # PASS/SKIP lines
py -3 -m unittest discover -s tests -v
py -3 tools\build_exe.py          # dist\ostlr\ostlr.exe (one folder)
```

- Every module has a unit test. Pure logic (sessions, ladder, store, view_model,
  discovery, layout) must be testable without a window.
- `--selftest` must run with Qt unavailable and still exit 0.
- The import test enforces "no UNDECLARED third-party imports": the allow-list is
  `schema.ALLOWED_THIRD_PARTY`. Adding a dependency means adding it there, to
  `requirements.txt`, and to the README.

## Do not

- Do not invent a state. No data means `unknown`, never `idle`.
- Do not fake an affordance. If the tray cannot be drawn or a PTY cannot be
  adopted, say so on screen.
- Do not adopt a PTY you do not own. An external session gets focus, a copy of
  its path, or a shell in its directory - never a pretend "attach".
- Do not kill a process tree without showing the pid count and the memory first.
- Do not leave orphan children on exit; panes stop their own PTY.
- Do not put a colour, a font size or a glyph outside `ostlr/qt/style.py` (roles)
  and `ostlr/qt/vendor` (the renderer).
- Do not hardcode agent paths in the launcher. Discovery is the contract; a
  remembered pick is the only stored location, and the user chose it.
- Do not commit `%APPDATA%\ostlr` contents, logs, `dist/` or `build_pyi/`.

## Style

- Python 3.13, type hints on public functions, `from __future__ import annotations`.
- One module = one responsibility; the view never parses process data.
- User-facing strings in English, in the user's words (no protocol names in the
  first screen).
- Commit messages: `area: what changed` plus one line of why.
