# Producer recipe: make a third-party CLI report `blocked`

> **Status: unverified on this machine.** No third-party CLI with a hook surface
> is installed here, so nobody has fired this recipe end to end. The JSON and the
> command below are exact and are asserted by `tests/unit/test_docs.py`; what is
> unproven is that a given CLI's hook actually runs them. Treat the first run as
> an experiment, not a guarantee.

The panel reads one file per session:

```
%LOCALAPPDATA%\ostlr\status\<pid>.json
```

If your CLI writes that file, its row stops reading `unknown` and starts showing
the real state - including `blocked`, which is the only state that means "a human
is needed".

## 1. Get the pid

The file name must be the pid of the process the panel sees as the session root -
the CLI itself, not the shell that started it. Inside a hook, the hook's own
process is usually a child of the CLI, so ask the CLI for its pid, or use the
pid the hook receives. Two ways to get a concrete number:

```
py -3 -c "import os; print(os.getpid())"
```

or, from a shell, the pid of the running CLI:

```
powershell -NoProfile -Command "(Get-Process claude).Id"
```

Do not paste a literal `<pid>`: the panel keys the record by that number, and a
wrong one attaches the state to the wrong row.

## 2. Write the record

The exact JSON (this block is the contract, plan section 8.2):

```json
{"v": 1, "ts": "2026-10-30T20:11:03Z", "source": "claude-code", "pid": 12345,
 "cwd": "D:/ws/sop-test", "state": "blocked", "kind": "permission",
 "msg": "Approve rm -rf build?", "exit_code": 0}
```

Rules the reader enforces:

- `state` in `idle` / `working` / `blocked` / `done` / `error`.
- `blocked` requires `kind` in `permission` / `question` / `auth`.
- `ts` is ISO-8601 UTC; older than 90 s is stale and the row falls back to
  `unknown`.
- Write with a temp file plus `os.replace`, so the panel never reads a
  half-written file.

## 3. The command to run from the hook

```
py -3 -m ostlr.status --state blocked --kind permission --pid <pid> --msg "Approve rm -rf build?"
```

`ostlr.status` writes the record atomically for you, so a hook only has to call
it. The same command with `--state working` on tool start and `--state done` on
tool end gives the panel a live state for the whole turn.

## 4. What this buys you, and what it does not

| You get | You do not get |
|---|---|
| `blocked` with a reason (`kind`) and a message | the CLI's own approval UI - the panel only takes you to the window |
| `working` / `done` transitions | L1 (OSC 7501) - that needs our own PTY, which is deferred |
| the cwd, so the row shows the right project | any write into the CLI's config or session files - the panel is read-only |

## 5. If it does not work

Check, in this order:

1. `py -3 -m ostlr --once --json` - does the row exist at all? If not, the pid is
   wrong or the process is not a recognised agent.
2. Is the file where the panel looks? `py -3 -m ostlr --selftest` prints the
   resolved status directory on its `paths status` line.
3. Is `ts` in the future or more than 90 s old? Both read as stale.
4. Is `blocked` missing its `kind`? That record is rejected as absent, on
   purpose: a `blocked` row without a reason cannot tell you what to do.
