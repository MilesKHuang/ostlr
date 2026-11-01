# Ostlr

A local panel for the AI agent sessions running on this machine: who is running,
who is **waiting for you**, and one click to focus, kill, restart or start one -
in a real terminal, inside the panel.

Self-use tool. No cloud, no telemetry, no account.

## What it does

```
+-------------------------------------------------------------+
| + New terminal  Start a session  Open in panel  Refresh ...  |
| waiting 1   running 1   unknown 4   sessions 6               |
+---------------------------+---------------------------------+
| Sessions                  | shell                     [x]   |
| * working  projV  projV   |  Windows PowerShell 5.1          |
| ! blocked  claude projV   |  PS D:\ws\projV> _              |
|   unknown  codex  sop     |                                 |
+---------------------------+---------------------------------+
```

- **A real terminal.** The panel owns the PTY (ConPTY), xterm.js renders it, so
  a session you start here can be typed into, split and kept. This is what makes
  the state trustworthy: we read the same bytes the terminal shows.
- **Session list.** Project, branch, agent, state, time in state, process-tree
  memory. `blocked` sorts first, then `error`, `working`, `unknown`, `done`,
  `idle`.
- **Discovery, not configuration.** The launcher shows what is installed (a PATH
  scan for agent names), remembers whatever you pick with *Browse...*, and states
  out loud which copy of an exe it will run and whether that copy has an API key.
  A new agent needs no config edit.
- **Tray + layout.** A tray badge counts what waits; the layout (tabs, splits,
  commands, directories) comes back on the next start.

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
py -3 -m ostlr --once --json   # one snapshot on stdout, then exit
py -3 -m ostlr --selftest      # headless PASS/SKIP lines
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
| **L1** | OSC 7501 over the PTY we own | PTY owned; the sequence is **not parsed yet** - that is the next step, not a mystery |
| **L2** | a JSON status file per pid | works, and is the only source that can say `blocked` |
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

`blocked` needs a producer. A stock `claude` or `codex` writes no status file and
we cannot parse its OSC 7501 yet, so its row reads `unknown` until L1 lands. The
panel states this on screen rather than showing a green row that means nothing.
`unknown` is never rendered as `idle`, and L3 (window titles) can never produce
`blocked`.

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
