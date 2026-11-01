# Ostlr

A local Windows panel that lists the AI agent sessions running on this machine
(Claude Code / Codex / proJV / any shell), shows which one is waiting for a
human, and lets you focus / kill / restart / start one. Self-use tool: no cloud,
no telemetry, no account.

## Requirements

- Windows 10/11, 64-bit.
- **Python 3.13, standard library only.** No `pip install`, no virtualenv, no
  `requirements.txt`. Every dependency is in the stdlib (`tkinter`, `ctypes`,
  `tomllib`, `unittest`).
- Run it as `py -3`, never `python`: on this machine `python` is the Microsoft
  Store stub and does not work.

## Run it

```
py -3 -m ostlr                 # the panel
py -3 -m ostlr --once --json   # one snapshot to stdout, then exit
py -3 -m ostlr --selftest      # headless PASS/FAIL/SKIP lines
```

The panel is the product; the commands above are for scripts and for
diagnosing a machine. You can do everything from the window: **Start a session**
opens the launcher, **Refresh** re-samples, **Copy path** puts the selected
row's path on the clipboard, **Help** explains the rest. A session started with
**Start** reports its own state, so the CLI is never required to see a real
state.

`--once --json` prints exactly one JSON object and nothing else on stdout, so it
can be piped straight into a parser. `--selftest` exits 0 unless a check really
fails; a `SKIP` line is not a failure.

## How a session reports its state

The panel reads three sources, best first:

| Level | Source | Status |
|---|---|---|
| L1 | OSC 7501 (Program Status Protocol) over our own PTY | **unsupported** - needs `pywinpty`, which cannot be installed here |
| L2 | a JSON status file per pid | works, and is the only source that can say `blocked` |
| L3 | the window title | weak evidence of liveness only; never `blocked`, never `idle` |

With no L2 producer every row reads `unknown`. That is deliberate: the panel
never guesses `idle`, because "waiting for you" shown as "nothing happening" is
the one error that ends trust.

### The L2 status file (the producer contract)

Path: `%LOCALAPPDATA%\ostlr\status\<pid>.json`, written with a temp file plus
`os.replace` so a reader never sees a half-written file.

```json
{"v": 1, "ts": "2026-10-30T20:11:03Z", "source": "claude-code", "pid": 12345,
 "cwd": "D:/ws/sop-test", "state": "blocked", "kind": "permission",
 "msg": "Approve rm -rf build?", "exit_code": 0}
```

- `state` is one of `idle`, `working`, `blocked`, `done`, `error`.
- `blocked` **requires** `kind` to be one of `permission`, `question`, `auth`.
- `ts` is ISO-8601 UTC. A record older than 90 s is stale and the row falls back
  to `unknown`.
- Anything malformed counts as absent and logs one line. It is never guessed at.

Write one by hand:

```
py -3 -m ostlr.status --state blocked --kind permission --pid <pid> --msg "Approve rm -rf build?"
```

### The wrapper (working / done / cwd / liveness)

```
py -3 -m ostlr run -- <cmd>
```

`ostlr run` starts the command in its own console, writes a status file for it,
keeps a heartbeat while it runs, and records the exit code when it ends. It
reaches `working`, `done`, `cwd` and liveness - **not** `blocked`: only the CLI
itself knows when it is waiting for a human.

## Agents the launcher can start

The launcher greys out an agent it cannot find and says
"install it or set an absolute path in config.toml". That path is real:

```toml
[[agents]]
id = "projv"
label = "projV"
command = "D:\\ws\\proJV\\build\\Release\\proJV_tui.exe"
args = []
```

`id` must match a built-in template (`claude`, `codex`, `projv`, `pwsh`) to
replace its command, or be a new id to add one. A missing `label` falls back to
the id; an entry without `id` or `command` is ignored rather than half-applied.

The launched agent always starts **in the directory you picked**, and projV takes
that directory as its workspace (`getWorkspaceDir()` is the launch directory),
so "start projV here" opens it exactly on that project.

## The honest limit

`blocked` needs a producer. A stock `claude` or `codex` writes no status file,
so its row reads `unknown` until you either wrap it with `ostlr run` (which
gives you working/done/cwd) or wire a hook that writes the JSON above. The
copy-paste recipe is in [`docs/producers/claude-hook.md`](docs/producers/claude-hook.md);
it is marked **unverified on this machine** because no third-party hook exists
here to fire it.

## Docs

- [`docs/m1-notes.md`](docs/m1-notes.md) - acceptance table, manual-only checks,
  deliberate deferrals, deviations from the plan.
- [`docs/producers/claude-hook.md`](docs/producers/claude-hook.md) - the L2
  producer recipe for a third-party CLI.
- [`docs/sop/steve-ostlr.md`](docs/sop/steve-ostlr.md) - the agreed M1 plan.
- [`AGENTS.md`](AGENTS.md) - the measured environment facts and the rules.

## Test

```
py -3 -m unittest discover -s tests
```

Pure logic is tested headless; the Tk smoke test reports `SKIP (no display)`
when there is no window.
