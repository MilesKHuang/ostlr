# Where a session's state comes from

The panel shows one word per session. That word has exactly three possible
sources, in this order of authority:

| Level | Source | What it can say |
|---|---|---|
| **L1** | OSC 7501, the [Program Status Protocol](https://www.superlogical.com/rex/docs/build/program-status) (revision 0.4), read out of the PTY the panel owns | anything the program reports: `working`, `blocked` with a `kind`, `done`, `error`, `idle` |
| **L2** | a JSON status file per pid: `%LOCALAPPDATA%\ostlr\status\<pid>.json` | the same five states; the only source that can say `blocked` from outside a pane |
| **none** | nothing reported | `unknown`, with the reason on screen. Never `idle` |

`unknown` is not a state a producer may report: it is what the panel says when
nobody spoke.

## L1 - the terminal side of OSC 7501

The panel owns the PTY, so it is the terminal side of the protocol and has three
obligations (`ostlr/probe/l1.py`):

1. **Answer the probe.** `ESC ] 7501 ; ? ST` means "do you understand this?".
   A producer that gets no reply may stay silent for the whole session, so the
   reply is written the moment the query is seen - and before the DA1 that a
   producer sends right behind it can win the race and settle the question the
   other way. Revision 0.4 lets a terminal say what it accepts, so ours answers
   `ESC ] 7501 ; ? : states=idle,working,blocked,done,error : kinds=permission,question,auth ST`
   (written without the spaces the grammar forbids). A producer written against
   0.3 must ignore pairs it does not know, and the bare `ESC ] 7501 ; ? ST` is
   still a legal reply for one that does not.
2. **Keep records.** One record per `id`; a report replaces its record, `clear`
   removes it and its subtree, and the row shows the most urgent of them. A
   record without `app` inherits it from the nearest ancestor id that has one,
   and past 256 records the least recently updated one is dropped.
3. **Apply the lifetime.** A process exit or a new shell prompt (OSC 133 A) drops
   `working` and `blocked`; `done` and `error` survive both. A full reset (RIS)
   removes every record; a soft reset (DECSTR) touches none.

A report is rejected **whole** when it breaks a limit, when `msg`/`title` is not
decodable base64, when the decoded text holds a control character (U+0000-U+001F,
U+007F, U+0080-U+009F, tab included), or when its `id` is outside the grammar.
The other failures are not fatal: an unknown `kind`, a `progress` that is not
0-100 and an `app` outside the character set are treated as **absent**, and the
state the program did report is kept. Discarding those would turn a program that
is waiting for a human into a row that says `unknown`.

Text we keep from a report is disarmed before it is shown: bidi overrides and
zero-width joiners are removed, which is what 0.4 asks for text shown outside the
terminal grid. The grid itself still receives the original bytes.

## L2 - the status file

Written with a temp file plus `os.replace`, so a reader never sees a
half-written file:

```json
{"v": 1, "ts": "2026-10-30T20:11:03Z", "source": "claude-code", "pid": 12345,
 "cwd": "D:/ws/sop-test", "state": "blocked", "kind": "permission",
 "msg": "Approve rm -rf build?", "exit_code": 0}
```

A record is accepted only when it is valid JSON, carries every required field,
names one of the five states, and - for `blocked` - a valid `kind` or none at all:
OSC 7501 makes `kind` optional, and a program that is blocked without saying why
is still blocked. Anything else
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
