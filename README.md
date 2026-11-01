# Ostlr

A local panel for the AI agent sessions running on this machine: who is running,
who is **waiting for you**, and one click to focus, kill, restart or start one -
in a real terminal, inside the panel.

Self-use tool. No cloud, no telemetry, no account.

## What it does

```
+--------------------------------------------------------------+
| + New session   Refresh   Focus all              [receipt]    |
+----------------+--------------------------------+------------+
| Sessions       | projv                     [x]  | Files      |
| * projV projV  | projV (TUI)                    | D:\ws\projV|
|   working      |   ...                          |  src/      |
|   cpu 3% mem 2%|                                |  docs/     |
+----------------+--------------------------------+------------+
```

- **A real terminal, and only that.** The list holds the panes this panel started:
  it owns their PTYs (ConPTY), so it can size them, read their bytes, answer
  protocol probes and type into them. A process it did not start is not listed -
  the panel does not claim to manage what it cannot see inside.
- **State you can check.** `working` while output flows; when the child goes quiet
  the pane **withdraws its claim** and the row falls back to `unknown`, because
  silence is not idleness - an agent waiting for an approval is silent too.
  `blocked` and `idle` come only from the program itself over OSC 7501 (proJV
  does this) or from a producer's status file. The program's words, never ours.
- **Tabs.** Each tab is one session; the tabs, their commands and their
  directories come back on the next start.
- **Discovery in the launcher.** The launcher scans PATH for agent names,
  remembers whatever you pick with *Browse...*, and states out loud which copy of
  an exe it will run and whether that copy has an API key. A new agent needs no
  config edit.
- **Looks like your terminal.** The pane uses the OS terminal's own font (read
  from Windows Terminal's settings) and lets the terminal draw its own frame, so a
  TUI hosted here looks like the same TUI outside -
  [`docs/parity.md`](docs/parity.md) records the measurements behind that.
- **Tray.** A tray badge counts what waits.

### What it does not do, on purpose

It never looks at a session it did not start. There is no process scan and no
window-title guessing, so an agent running in Windows Terminal or an editor is not
in this list - the panel cannot type into it, size it, or read its bytes, and a row
you cannot act on is a row that teaches you to stop believing the list. Add a
program by choosing its exe once (the launcher remembers it in `config.toml`); that
is the whole contract for what appears.

## Install

```
py -3 -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
```

The mirror matters: the default index here serves only pure-Python wheels, so
`PySide6` and `pywinpty` look unavailable when they are not. `python` on this
machine is the Microsoft Store stub - always use `py -3`.

## Run

```
py -3 -m ostlr                 # the panel
py -3 -m ostlr --selftest      # headless PASS/SKIP lines
py -3 -m ostlr run -- <cmd>    # the producer wrapper
py -3 -m ostlr.status --list   # what the programs on this machine report
```

## Build the exe

```
py -3 tools\build_exe.py               # one folder, ~640 MB, starts in ~1 s
py -3 tools\build_exe.py --onefile     # one file, unpacks Chromium per start
```

`dist\ostlr\ostlr.exe`. Console build on purpose: the same binary answers
`--once` and `--selftest`.

## How a session reports its state

| Level | Source | Status |
|---|---|---|
| **L1** | OSC 7501 over the PTY we own | **live**: the pane answers a probe and parses reports; proJV emits them (`L1: done: turn finished`) |
| **L2** | a JSON status file per pid | works, and the only source that can say `blocked` |
| **L3** | the window title | weak evidence of liveness only; never `blocked`, never `idle` |

A session started **in the panel** writes its own record (`source=ostlr-pane`),
so it shows a real state immediately and is marked `*` in the list. A session
started elsewhere shows `unknown` until something reports it - deliberate: "waiting
for you" rendered as "nothing happening" is the one error that ends trust.

### The status file

`%LOCALAPPDATA%\ostlr\status\<pid>.json`, written with a temp file plus
`os.replace`:

```json
{"v": 1, "ts": "2026-10-30T20:11:03Z", "source": "claude-code", "pid": 12345,
 "cwd": "D:/ws/sop-test", "state": "blocked", "kind": "permission",
 "msg": "Approve rm -rf build?", "exit_code": 0}
```

Write one by hand:

```
py -3 -m ostlr.status --state blocked --kind permission --pid <pid> --msg "Approve rm -rf build?"
```

For a session you cannot start in the panel, the wrapper writes the same record
around any command, so its row stops reading `unknown`:

```
py -3 -m ostlr run -- <cmd>
```

The copy-paste recipe for a third-party CLI is in
[`docs/producers/claude-hook.md`](docs/producers/claude-hook.md) - marked
**unverified on this machine**, because no third-party hook exists here to fire.

## The honest limit

`blocked` needs a producer. A session started **in a pane** now has one: the pane
owns the PTY, answers the OSC 7501 probe and parses what the CLI reports (proJV
does this today), so its row is a report rather than a guess. A stock `claude` or
`codex` writes no status file and emits no sequence, so its row reads `unknown`
until something reports - the panel states this on screen rather than showing a
green row that means nothing. `unknown` is never rendered as `idle`, and L3
(window titles) can never produce `blocked`.

Two rendering rules matter as much as the state ladder, because both were visible
before they were understood (`docs/parity.md`): the terminal draws its own frame
(so a TUI's borders are continuous and its gauges are solid), and the default font
is the OS terminal's own, read from its settings - hosting a TUI here must not
change how it looks.

## Docs

- [`docs/m1-notes.md`](docs/m1-notes.md) - acceptance, the defects that shipped
  green, deliberate deferrals, deviations.
- [`docs/producers/claude-hook.md`](docs/producers/claude-hook.md) - the L2 recipe.
- [`docs/sop/steve-ostlr.md`](docs/sop/steve-ostlr.md) - the agreed plan.
- [`AGENTS.md`](AGENTS.md) - measured environment facts and the rules.

## Test

```
py -3 -m unittest discover -s tests -v
```
