# Ostlr

A local panel for the AI agent sessions running on this machine: who is running,
who is **waiting for you**, who is eating memory - and one click to focus, kill,
restart or start one in a directory.

Self-use tool. No cloud, no telemetry, no dependency outside the Python
standard library.

## Run

```
py -3 -m ostlr              # the panel
py -3 -m ostlr --selftest   # headless self-check
py -3 -m unittest discover -s tests -v
```

`python` on Windows is the Microsoft Store stub on this machine; use `py -3`.

## Status legend

| Symbol | State | Meaning |
|---|---|---|
| `!` | blocked | a human is needed (permission / question / auth) |
| `>` | working | busy |
| `o` | idle | alive, nothing in flight |
| `+` | done | finished just now |
| `x` | error | failed, last line attached |
| `?` | unknown | not enough data - never guessed |

## Where it keeps things

```
%APPDATA%\ostlr\config.toml        agent templates, preferences, last selection
%LOCALAPPDATA%\ostlr\status\       status files written by cooperating CLIs (L2)
%LOCALAPPDATA%\ostlr\logs\         own log, one file per day
```

## Docs

- `docs/ostlr-implmentation.md` - design baseline (pain points, milestones, hard
  constraints, rejected stacks)
- `docs/sop/` - design plan and execution record produced by the `steve` and
  `musk` skills
- `AGENTS.md` - environment facts and the rules for anyone editing this repo
