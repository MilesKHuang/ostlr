# Where a session's state comes from

The panel shows one word per session. That word has exactly three possible
sources, in this order of authority:

| Level | Source | What it can say |
|---|---|---|
| **L1** | OSC 7501, the [Program Status Protocol](https://www.superlogical.com/rex/docs/build/program-status) (revision 0.3), read out of the PTY the panel owns | anything the program reports: `working`, `blocked` with a `kind`, `done`, `error`, `idle` |
| **L2** | a JSON status file per pid: `%LOCALAPPDATA%\ostlr\status\<pid>.json` | the same five states; the only source that can say `blocked` from outside a pane |
| **none** | nothing reported | `unknown`, with the reason on screen. Never `idle` |

`unknown` is not a state a producer may report: it is what the panel says when
nobody spoke.

## L1 - the terminal side of OSC 7501

The panel owns the PTY, so it is the terminal side of the protocol and has two
obligations (`ostlr/probe/l1.py`):

1. **Answer the probe.** `ESC ] 7501 ; ? ST` means "do you understand this?".
   A producer that gets no reply may stay silent for the whole session, so the
   reply is written the moment the query is seen.
2. **Keep records.** One record per `id`; a report replaces its record, `clear`
   removes it and its subtree, and the row shows the most urgent of them. The
   spec's exit rule (`working` and `blocked` are dropped when the program exits)
   is applied as well.

A report that breaks the spec is rejected as a whole, not half-applied.

## L2 - the status file

Written with a temp file plus `os.replace`, so a reader never sees a
half-written file:

```json
{"v": 1, "ts": "2026-10-30T20:11:03Z", "source": "claude-code", "pid": 12345,
 "cwd": "D:/ws/sop-test", "state": "blocked", "kind": "permission",
 "msg": "Approve rm -rf build?", "exit_code": 0}
```

A record is accepted only when it is valid JSON, carries every required field,
names one of the five states, and - for `blocked` - a valid `kind`. Anything else
counts as absent: the row falls back to `unknown` and the file produces one log
line per read, not one per field.

A record older than `l2_stale_s` (90 s by default) is stale. The wrapper
heartbeats every 10 s, so a long-running agent never reads as stale while it is
fine.

See what exists right now:

```
py -3 -m ostlr.status --list       # who reports, from where, how old, what they said
py -3 -m ostlr.status --prune      # delete the records of pids that are gone
```

Write one by hand:

```
py -3 -m ostlr.status --state blocked --kind permission --pid <pid> --msg "Approve rm -rf build?"
```

## The wrapper, for a session the panel cannot start

```
py -3 -m ostlr run -- <cmd>
```

The wrapper launches the command in its OWN console (`CREATE_NEW_CONSOLE`, never
`DETACHED_PROCESS` - a detached console app gets no console at all) and reports
its life cycle into the same status file. It holds no pipe from the panel, so
closing the panel cannot kill the agent.

## A third-party CLI

The copy-paste hook recipe is in [claude-hook.md](claude-hook.md). It is marked
**unverified on this machine**, because no third-party hook exists here to fire.

## When a state is held, and when it is withdrawn

- A report from the program is **held**: the producer is the process in that
  pane and the panel can see it is alive, so its last word stands until it says
  otherwise or the process ends. A state machine reports on change, not on a
  heartbeat - an agent waiting for an approval has to stay `blocked` for as long
  as the human takes.
- The pane's own traffic guess (`source=ostlr-pane`, written while the child is
  producing output) is **withdrawn** after silence. Silence is not idleness: an
  agent waiting for an approval is silent too. That is why the row falls back to
  `unknown` instead of claiming `idle`, and why a guess never outlives its
  evidence.

## The honest limit

`blocked` needs a producer. A stock `claude` or `codex` writes no status file and
emits no sequence, so its row reads `unknown` until something reports. The panel
states this on screen rather than showing a green row that means nothing. No state
is ever inferred from a window title.
