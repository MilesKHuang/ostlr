# Ostlr M1 - executable design plan

> Status: agreed (Steve, PM revision round 6), **executed (M1 shipped)**. Repo `D:\ws\ostlr`. Design baseline `docs/ostlr-implmentation.md` v0.6; measured environment facts `AGENTS.md`.
> Target: Windows 10/11, Python 3.13 via `py -3`, standard library only. This is the input for the execution stage (`musk`); the task table in section 4 is the contract.
>
> **The `standard library only` target was the plan's one wrong conclusion** - a
> wrong reading of the PyPI index, not a constraint. The shipped stack is
> PySide6 + ConPTY (pywinpty) + xterm.js; the deviations and the pivot are
> recorded in [`docs/m1-notes.md`](../m1-notes.md) (§3 and §4). This file stays
> as the historical contract and is not edited to match the code.

## 1. Core user value (one sentence)

In three seconds, from one window, I know which of my running AI agents is working, which is waiting on me, and which is dead - and I can act on it without leaving that window.

Four anchor tests that judge every later decision:

1. The header and the rows can never contradict each other.
2. No code path returns `idle` without a fresh L2 status file.
3. A state that was inferred is visibly marked as inferred.
4. A failure never renders in a success colour.

## 2. Scope and non-goals

**In M1**

- Session list: project, branch, agent, state, time-in-state, process-tree memory (P1, P2, P5).
- Focus, kill with confirmation, restart (P6).
- Launcher: directory + agent, PATH pre-check, `ostlr run` wrapper, last-3 memory (L3, P4).
- Copy absolute path with the three-part receipt; `Ctrl+Shift+R` copies the selected row's hint (U3).
- `blocked` reminder, degraded to a title prefix + header counter + one bell per transition (P2).
- L2 producer (`py -3 -m ostlr.status`) and the detached `run` wrapper, plus one copy-paste recipe for a third-party hook.

**Not in M1 (stated, with the reason)**

| Not built | Why |
|---|---|
| Embedded terminal pane, xterm.js, path injection into a PTY | baseline M2; needs a PTY layer that cannot be installed here |
| L1 OSC 7501 capture over our own ConPTY | `pywinpty` is unavailable on this machine; `probe/l1.py` is a stub returning `unsupported` |
| Answering another CLI's approval on the user's behalf | permission semantics belong to each CLI; Ostlr only takes you there |
| Writing other tools' configuration files | read-only by design (baseline section 7) |
| Real system tray icon | the stdlib cannot draw one; degrades to title prefix + counter + one bell |
| Cloud, remote, SSH | baseline non-goal |
| History, layout persistence, SQLite | no M1 requirement needs them; a schema now is migration debt |
| PEB command-line / cwd reads, a ctypes Toolhelp hot path | undocumented offsets, and one probe path means one failure mode |
| File tree panel | `askdirectory` plus copy-path already deliver P3/P4; first thing cut per UX |

## 3. Tech choices

| Choice | Reason (one line) |
|---|---|
| Python 3.13 stdlib + tkinter/ttk, single process | the only stack installable here: psutil / PySide6 / pywinpty / pywin32 / pillow all fail to install |
| `ctypes` user32: `EnumWindows`, `GetWindowThreadProcessId`, `SetForegroundWindow`, `FlashWindowEx`, `GetWindowText` | window facts and focus without pywin32 |
| `Get-CimInstance Win32_Process` -> JSON, one worker thread, 5 s TTL, 3 s timeout, last-good cache | the only source of command line, ppid and working set without PEB reads; off the UI thread so it cannot freeze the panel |
| `Popen(..., creationflags=CREATE_NEW_CONSOLE or CREATE_NEW_PROCESS_GROUP)` for every launch | the agent gets its own visible console and outlives the panel; it is never our child to reap |
| One ctypes clipboard (`OpenClipboard` with a bounded retry, `Set/GetClipboardData`) | the app and the tkinter-free selftest must use the same mechanism |
| `taskkill /PID n /T /F` + re-sample | recursive kill is built into Windows; survivors are reported honestly |
| JSON status files, temp + `os.replace` | a producer contract any CLI or shell can write in three lines |
| `shutil.which`, `tomllib`, `unittest` | PATH pre-check, config and headless tests with no install |

## 4. Task breakdown (one developer turn each) - the implementation contract

Dependencies run in table order. Milestones: **MS1 = T1-T11** (day 1-5, a runnable panel), **MS2 = T12-T15** (day 6-8, you can act on it), **MS3 = T16-T17** (day 9-10, it looks after you). Acceptance is keyed by these task IDs and this is the only keying that counts.

| # | MS | Task | File(s) | DoD / check |
|---|---|---|---|---|
| T1 | 1 | Skeleton: argparse (`panel`, `--once [--json] [--fixture <path>]`, `--selftest`, `run -- <cmd>`), config, paths, logging, schema constants | `ostlr/__main__.py`, `config.py`, `paths.py`, `schema.py` | `py -3 -m ostlr --once --json` prints `{"v":1,"header":{...},"rows":[]}` and exits 0; `--once --json --fixture tests/fixtures/snapshot_small.json` prints exactly that fixture's rows; `py -3 -m unittest` green on an empty suite |
| T2 | 1 | CIM probe: JSON shapes (list / single object / garbage), `CreationDate` in both real formats -> epoch, timeout, live-shape capture | `ostlr/probe/cim.py`, `tests/fixtures/cim_*.json` | 5 fixtures pass; a **live** sample from `powershell` (5.1) parses to `epoch > 0` for every row, or the task stops and reports the actual shape; the `pwsh` 7 sample is captured when `pwsh` exists, otherwise it reports `SKIP (no pwsh)`; a forced 3 s stall returns in < 3.2 s and sets `stale, last good HH:MM:SS` |
| T3 | 1 | Process tree + per-tree memory aggregation | `ostlr/probe/procs.py` | 4 fixtures (3 trees, a cycle, an orphan, a recycled pid, depth cap 32): exact sums, always terminates |
| T4 | 1 | Agent templates, session correlation, cwd from 3 sources, branch on the sampler thread | `ostlr/agents.py`, `sessions.py`, `probe/git.py` | cmdline -> template id; cwd from L2 else an absolute path in the command line else `?`; git absent -> `?`, not yet fetched -> `...`; cache misses equal the distinct cwds inside the first 20 ranked rows |
| T5 | 1 | L2 reader, `combine()`, rank + `state_since` ordering, unreadable status directory | `ostlr/probe/l2.py`, `ladder.py` | 89 s vs 91 s flip; half-written file, `state=zombie`, `blocked` without `kind` -> `unknown` + exactly one log line; denied directory -> `status_dir_unreadable(path)`; property test: no `idle` without a fresh L2 file; oldest `state_since` first inside a state |
| T6 | 1 | Store: sampler-owned state + UI intent queue + slot adoption | `ostlr/store.py` | 100 interleaved UI intents (mark-dirty, new slot, transient, already_exited) applied at sampler tick start -> 0 lost, 0 exceptions, the UI never mutates store state; retention 600 s; `already_exited`; a restart is adopted into its old slot by `(template, cwd)` on the next tick |
| T7 | 1 | L3 window enumeration + title classifier | `ostlr/probe/l3.py` | 8 title fixtures; classification limited to `working`/`done`/`error`; the live check lists >= 1 titled window, otherwise it reports `SKIP (no titled window in this session)` |
| T8 | 1 | Producer: atomic writer, `run` wrapper (`CREATE_NEW_CONSOLE`, no pipes, writes `working`, `exit_code`, a start-failure reason) | `ostlr/status.py` | 200 concurrent writes -> 0 parse errors, 0 `.tmp` left behind; a wrapped child stops being `unknown` within `tick_s + 1 s`; the wrapper writes `exit_code` for a zero and a non-zero child; the panel holds no handle and no stdio redirect exists |
| T9a | 1 | View model core: rows, seven buckets + inferred sub-counts + total, ordering, done highlight | `ostlr/view_model.py`, `tests/golden/` | one fixture golden containing a transient row, a retained `done` row and a fresh-L2 `idle` row: `sessions == waiting+running+error+done+idle+unknown+starting == visible + truncated`; oldest-first inside a state; the highlight expires at 3 s |
| T9b | 1 | View model formatting: `waiting 2m`, capability line, `(no identity)`, `exit=N`, markers, receipts, hint, status-bar string, 120-char clip, JSON v1, exit-aware colour role | `ostlr/view_model.py`, `tests/golden/` | goldens: `0s`-`59s` in seconds and `>= 60s` in minutes; `...` vs `?`; `(no identity)` row; `done` with `exit=1` renders the **error** role; `showing 60 of N` on a 75-row fixture and never `showing 60 of 60`; `msg` and receipts clipped to 120 chars; `hint` never rendered inside a row; markers never truncated |
| T10 | 1 | theme, type, glyphs, row widgets, `TRANSIENT_MAP`, elide, layout rules | `ostlr/ui/theme.py`, `ui/type.py`, `ui/glyphs.py`, `ui/rows.py` | no `#hex`, no Tk colour name and no inline font tuple outside these modules; `STATE_MAP` has 6 entries and `TRANSIENT_MAP` 1 (`starting` = `U+22EF`), sharing no codepoint and no colour role; a 300-char command line is elided with the leaf visible; row height is identical with and without a marker; the selection border is manual-only |
| T11 | 1 | App shell: DPI awareness before `Tk()`, gesture routing, widget wiring, header rules, 1 s tick with `after(0)` | `ostlr/ui/app.py` | every displayed string comes from `view_model` (no literals in the view); `waiting`/`running`/`unknown` always occupy fixed slots (rendered at `0`), the rest are hidden when zero, in rank order, total last, never wrapped; the select path never calls `SetForegroundWindow` (asserted double); the Tk smoke test reports `SKIP (no display)` with a reason |
| T12 | 2 | Focus (Enter, double-click) + launch (own console, no pipes) | `ostlr/actions.py` | double: `focused` only if `GetForegroundWindow()` matches, else `foreground_refused`; a launch uses `CREATE_NEW_CONSOLE`, a window exists for that pid (`EnumWindows`) and still exists after the panel is closed, and the pid is still alive in a **fresh** CIM sample taken after the panel exits; a failed start becomes `error` for `spawn_error_retain_s` (30 s) with the reason the wrapper wrote |
| T13 | 2 | Kill (confirmation, two lines) + restart, visible identity guard | `ostlr/actions.py` | `messagebox.askokcancel` shows pid count and aggregated tree memory; a `started_at` mismatch or `0` refuses with `identity_unverified`, kill and restart are disabled and the reason sits in the detail line, `taskkill` is never called; the receipt is `kill requested (pending)` then `killed` or survivors; restart re-uses the **raw** CIM `CommandLine` string unmodified (a fixture with a quoted path proves it), refuses `cwd_unknown` when cwd is `?`, and a second restart is refused |
| T14 | 2 | Launcher dialog + hint routing + `Ctrl+Shift+R` / `Ctrl+Shift+C` | `ostlr/launcher.py`, `ui/app.py` | a `which` miss greys the row and prints the literal fix; a hit produces a row within `tick_s + 1 s` in the chosen directory; last-3 memory persists in `config.toml`; the hint for a template with a recipe names it; both chords copy with exact receipts |
| T15 | 2 | Copy path through the ctypes clipboard (bounded retry), three receipts, `--selftest --clipboard` | `ostlr/view_model.py`, `actions.py`, `ui/app.py` | retry <= 10 x 20 ms then a typed failure; the clipboard-writer double counts calls, so two copies in < 100 ms produce exactly 1 write; `--selftest --clipboard` reads through the same ctypes path, passes with a foreign non-empty clipboard, and equals the copied path |
| T16 | 3 | Blocked reminder: title prefix, header counter, bell on the transition edge, `GetWindowText` probe | `ostlr/ui/app.py` | 2 transitions -> 1 bell; the title is `! 1 waiting` within `tick_s + 1 s` (else `SKIP (no display)`); the prefix disappears when the state resolves |
| T17 | 3 | Selftest, README, producer recipe, notes (acceptance table verbatim), hygiene, L1 stub | `ostlr/selftest.py`, `README.md`, `docs/m1-notes.md`, `docs/producers/claude-hook.md`, `probe/l1.py` | README contains the exact JSON block and the exact `py -3 -m ostlr.status ...` command, asserted as literal strings; `--selftest` reports PASS/SKIP on every line with the tkinter import blocked; 0 non-stdlib imports; the forced 3.2 s stall line exists; `docs/m1-notes.md` carries this acceptance table verbatim; `probe/l1.py` returns `unsupported` |

## 5. Milestones, dependencies, exit criteria

| Milestone | Days | Contains | Exit criterion (every line must hold) |
|---|---|---|---|
| MS1 - a runnable panel | 1-5 | T1-T11 | `py -3 -m unittest discover -s tests` exits 0; `--once --json --fixture tests/fixtures/snapshot_small.json` matches the golden byte for byte; on a live box `--once --json` also exits 0 with >= 1 row; `py -3 -m ostlr.status --state blocked --kind permission --pid <pid> --msg t` puts that pid at row 1 within 6 s with `source=l2`, and deleting the file returns it to `unknown` and never to `idle`; the empty state prints `L1: unsupported`, `producers found:` and `inferred rows:` |
| MS2 - you can act on it | 6-8 | T12-T15 | selecting never focuses and Enter does; kill on a 3-child tree goes pending -> `killed` with 0 survivors; `identity_unverified` visibly disables kill on a `(no identity)` row; restart keeps the slot and refuses `cwd_unknown`; a launch shows `starting...` -> a real state, its console window exists, and the session survives closing the panel (fresh CIM sample after exit); `--selftest --clipboard` equals the copied path |
| MS3 - it looks after you | 9-10 | T16-T17 | `--selftest` all PASS/SKIP with the tkinter import blocked; the title prefix appears within 6 s; the README producer command pasted into a fresh `cmd` makes a row appear; the recipe file exists and contains the exact JSON |

Dependencies beyond table order: T2 before T3/T4 (both need parsed CIM rows); T5 and T6 before T9a; T9a/T9b before T10/T11; T11 before T12-T16 (they wire into the shell); T8 before T14 (the launcher's default path is the wrapper); T17 last.

Slip lever, in this order: drop the branch column, then `Ctrl+Shift+R`. The 60-row cap and the `showing 60 of N` footer are **one unit** - if the footer is cut, every row renders. Never cut the state column, the rank order or the never-idle rule.

## 6. Module and file layout

```
D:\ws\ostlr\
  ostlr\
    __main__.py      argparse: panel (default) | --once [--json] [--fixture <path>] | --selftest | run -- <cmd>
    schema.py        v=1 field names and order, bucket names, clip length - the single source of the API
    config.py        tomllib defaults: tick_s=5, l2_stale_s=90, done_retain_s=600, spawn_error_retain_s=30,
                     cim_timeout_ms=3000, list_cap=60, clip_chars=120
    paths.py         %APPDATA%\ostlr\config.toml; %LOCALAPPDATA%\ostlr\{status,logs}
    probe\cim.py     PowerShell JSON probe: shapes, /Date(ms)/ and CIM_DATETIME -> epoch, timeout, last-good cache
    probe\procs.py   pure: pid/ppid map -> roots, DFS memory sum, depth 32, cycle-safe
    probe\l2.py      read + validate status files (a bad file counts as absent)
    probe\l3.py      EnumWindows -> pid -> title, then classify (working / done / error only)
    probe\git.py     branch via `git -C <cwd> branch --show-current`, sampler thread, 30 s cache, first 20 ranked rows
    probe\l1.py      documented stub: returns "unsupported" (L1 needs pywinpty)
    agents.py        default templates: claude, codex, projv, pwsh (match by exe name and command line)
    sessions.py      pure: (cim rows, templates, l2, titles, now) -> Session list; cwd from 3 sources
    ladder.py        pure: combine(l2, l3, alive, now) -> (state, source, reason); rank; state_since ordering
    store.py         SAMPLER-OWNED: incarnations, state_since, retention, already_exited, slots, UI intent queue
    view_model.py    pure: rows, seven buckets + inferred + total, waiting 2m, capability lines, markers,
                     receipts, hint, status-bar string, 120-char clip, exit-aware role, JSON v1
    actions.py       focus / kill / restart / launch (CREATE_NEW_CONSOLE) / ctypes clipboard; failures are values
    launcher.py      PATH pre-check, last-3 memory, hint text, recipe naming
    status.py        producer: atomic writer + the `run` wrapper (console owner, exit code, start-failure reason)
    selftest.py      headless PASS / FAIL / SKIP lines
    ui\theme.py      colour roles only (no hex anywhere else)
    ui\type.py       four named font specs (row 10 pt, meta 9 pt, title 11 pt bold, mono 9 pt Consolas)
    ui\glyphs.py     six state glyphs + one transient glyph, font family with an ASCII fallback
    ui\rows.py       row widget, elide, TRANSIENT_MAP, list cap, column drop below 1100 px
    ui\app.py        Tk shell: DPI first, gestures, header, empty state, banner, status bar, 1 s tick, worker post
  tests\             unit_*.py + fixtures\ + golden\ (pure, headless, no Tk required)
  docs\m1-notes.md   deviations, manual-only checks, this acceptance table verbatim
  docs\producers\    the copy-paste L2 producer recipe for a third-party CLI
```
Rule: `ui\*` imports nothing from `probe\*` or `actions`; it consumes a rendered snapshot plus a small action facade.

## 7. Data flow - the four baseline flows with concrete calls

**7.1 Discover and state (baseline 3.1/3.2).** Sampler thread every `tick_s`: `powershell -NoProfile -Command "Get-CimInstance Win32_Process | Select ProcessId,ParentProcessId,CreationDate,WorkingSetSize,CommandLine,ExecutablePath,Name | ConvertTo-Json -Compress"` (3 s timeout) -> `probe/cim.py` (shape + `CreationDate` -> epoch) -> `probe/procs.py` (tree, per-tree `WorkingSetSize`) -> `sessions.py` (template match by executable name and command line; cwd = L2 `cwd`, else an absolute path inside `CommandLine`, else `?`) -> `store.py` (incarnation `(root_pid, started_at epoch)`, `state_since` bumped only on a state change) -> `ladder.combine` (L2 fresh > L3 title > `unknown`) -> published snapshot -> UI renders it on its own 1 s tick with `after(0)` posts. The UI never calls CIM, git, or taskkill.

**7.2 Focus (baseline 3.5).** Enter or double-click -> `EnumWindows` + `GetWindowThreadProcessId` -> first visible top-level window of that pid -> `SetForegroundWindow` -> verify with `GetForegroundWindow`; on refusal `FlashWindowEx` and the receipt reads `foreground_refused`. Selection alone never calls any of these.

**7.3 Kill and restart (baseline 3.6).** Context menu -> `messagebox.askokcancel` with two lines (pid count from the tree, aggregated memory) -> identity guard: re-read `CreationDate` for the pid, compare to the stored epoch; mismatch or `0` -> `identity_unverified`, nothing runs -> `taskkill /PID <pid> /T /F` -> mark dirty; the sampler judges on the next tick and the receipt moves from `kill requested (pending)` to `killed` or a survivor list. Restart re-launches the stored raw `CommandLine` with the stored `cwd`, in its own console, refusing `cwd_unknown` and a second restart.

**7.4 Copy and launch (baseline 3.3/3.4).** Row selected -> `Ctrl+Shift+C` writes the absolute path through the ctypes clipboard and prints `copied <path> -> <agent> / <project> (same project | cross project | no target)`; `Ctrl+Shift+R` writes the selected row's hint instead. Launcher: `askdirectory` (or the last directory) -> `shutil.which` pre-check per template -> `Popen([sys.executable, "-m", "ostlr", "run", "--", *cmd], creationflags=CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP)` -> a `starting` row appears, the wrapper writes the child's status file, a start failure becomes `error` for `spawn_error_retain_s`. Quitting the panel ends nothing: the console window and the process belong to the user, not to us.

## 8. Contracts

### 8.1 Row schema v1 (the program's real API)

```json
{"v": 1,
 "header": {"waiting": 1, "running": 2, "running_inferred": 1, "error": 0, "error_inferred": 0,
            "done": 0, "idle": 0, "unknown": 3, "starting": 0, "sessions": 6},
 "rows": [{"pid": 1234, "started_at": 1767225600, "state_since": 1767225660,
           "agent": "claude", "project": "projV", "cwd": "D:/ws/projV", "branch": "main",
           "state": "blocked", "source": "l2", "transient": null, "kind": "permission",
           "msg": "Approve rm -rf build?", "age_s": 12, "mem_bytes": 212000000,
           "elapsed_s": 130, "rank": 0, "hint": "py -3 -m ostlr.status --state blocked ..."}]}
```

Invariants a golden must assert: every row sits in exactly one bucket and `sessions == waiting+running+error+done+idle+unknown+starting == rows rendered + rows truncated`; a transient row is counted in `starting` and in no state bucket; `*_inferred` exists for every bucket L3 can produce (`running`, `done`, `error`) and `waiting` has none because L3 never yields `blocked`; JSON `state` is never rewritten by the visual layer; `msg` and receipts are clipped to `clip_chars` (120).

### 8.2 L2 status-file contract

Path `%LOCALAPPDATA%\ostlr\status\<pid>.json`, written with a temp file plus `os.replace`.

```json
{"v": 1, "ts": "2026-10-30T20:11:03Z", "source": "claude-code", "pid": 12345,
 "cwd": "D:/ws/sop-test", "state": "blocked", "kind": "permission",
 "msg": "Approve rm -rf build?", "exit_code": 0}
```

`state` in `idle|working|blocked|done|error`; `blocked` requires `kind` in `permission|question|auth`; `exit_code` is written by the `run` wrapper when the child ends; anything malformed counts as absent plus exactly one log line, never a guess.

### 8.3 Ladder

`combine(l2, l3, alive, now) -> (state, source, reason)`: dead pid -> `done` (retained `done_retain_s` = 600 s, in memory only, lost when the panel restarts); L2 with age <= `l2_stale_s` (90 s) -> its state; else an L3 title match -> `working` / `done` / `error` only, rendered with the dim variant of its role plus a `(title)` marker and counted as `(N inferred)`; else `unknown` with a reason and an age. Ordering: `blocked > error > working > unknown > done > idle`, oldest `state_since` first inside a state, then project, then pid. `done` is a 3 s highlight, never a transition to `idle`; a `done` row with `exit != 0` renders the error role while its JSON `state` stays `done`.

### 8.4 Store and threading

`store.py` is owned by the sampler thread. The UI never mutates it: it queues intents (mark-dirty, new slot, transient, already_exited) that the sampler applies at the start of its next tick, then publishes an immutable snapshot the UI renders. That is why a launch landing mid-iteration cannot raise or silently drop a row; T6 proves it with 100 interleaved intents.

### 8.5 Launch contract

Every launch uses `CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP` so the agent owns a visible console window and its lifetime is independent of the panel. The panel holds **no** pipe and no stdio redirect: `ostlr run` owns the console, waits for its child, and writes `exit_code` and any start failure into the status file, which the panel reads like any other producer. `DETACHED_PROCESS` is reserved for genuinely headless runs and is never used by the launcher. Quitting the panel terminates nothing; `Kill` is the only way to end a session.

### 8.6 Clipboard

One implementation in ctypes (`OpenClipboard` with a bounded retry of 10 x 20 ms, then a typed failure; `SetClipboardData` / `GetClipboardData`). The app and `--selftest --clipboard` use the same path, so a verifier compares a write and a read of one mechanism, and the check must pass with a foreign, non-empty clipboard.

### 8.7 Text and visual discipline

`ui/theme.py` is the only file allowed to contain a colour (a role name, never a hex literal, never a Tk colour name); `ui/type.py` the only file allowed to contain a font tuple; `ui/glyphs.py` the only file allowed to contain a codepoint. `STATE_MAP` has exactly six entries (`U+25CF` blocked, `U+25D0` working, `U+25CB` idle, `U+2713` done, `U+2715` error, `U+25CC` unknown), `TRANSIENT_MAP` exactly one (`starting` = `U+22EF`), sharing no codepoint and no colour role; the family (`Segoe UI Symbol`) falls back to an ASCII set when missing, and all seven codepoints are covered by the fallback test. Row height is one line of the mono linespace plus 8 px; long values elide on one line, paths elide from the left so the leaf survives, and the markers `(title)`, `(no identity)` and `exit=N` are never truncated.

## 9. Acceptance per milestone - the exact commands

**MS1 (T1-T11)**

```
py -3 -m unittest discover -s tests                                        # exit 0
py -3 -m ostlr --once --json --fixture tests/fixtures/snapshot_small.json   # matches the golden exactly
py -3 -m ostlr --once --json                                               # live box: exit 0, >= 1 row
py -3 -m ostlr.status --state blocked --kind permission --pid <pid> --msg t # row 1 within tick_s + 1 s
del %LOCALAPPDATA%\ostlr\status\<pid>.json                                  # next tick: unknown, never idle
```

**MS2 (T12-T15)**: select a row and confirm through the double that no focus call happened; focus with Enter and read `focused` or `foreground_refused`; kill a 3-child tree and read pending -> `killed` with 0 survivors in `--once --json`; on a `(no identity)` row confirm kill is disabled; restart and confirm the same slot with a new pid, then `cwd_unknown` on a `?` row; launch pwsh, see `starting...` -> a real state, close the panel, take a fresh CIM sample and confirm the pid and its window still exist; `py -3 -m ostlr --selftest --clipboard` equals the copied path.

**MS3 (T16-T17)**: `py -3 -m ostlr --selftest` all PASS/SKIP with the tkinter import blocked by a `sitecustomize` stub; a blocked row makes the title `! 1 waiting` within `tick_s + 1 s`; the README producer command pasted into a fresh `cmd` makes a row appear; `docs/producers/claude-hook.md` contains the exact JSON.

## 10. Open risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | CIM wedged or denied by policy | worker thread, 3 s timeout, last-good cache, `stale, last good HH:MM:SS` banner, forced stall test in T2 |
| R2 | producer coverage: `ostlr run` reaches working/done/cwd/liveness only, and `blocked` needs a real hook | T17 ships the copy-paste recipe; every unknown row names the recipe for its template; the recipe is marked unverified (R6) |
| R3 | CIM duty cycle at 20-50 sessions | one probe per tick, at most one in flight, a skipped tick instead of a queue; if latency hurts, move command-line acquisition to PEB reads in M2 |
| R4 | identity depends on the `CreationDate` format | live-shape capture from PowerShell 5.1 and pwsh 7 in T2, plus `identity_unverified` refusing kill and restart when the epoch is 0 |
| R5 | no display (session 0, CI) | everything except the Tk smoke checks runs headless and reports SKIP with a reason |
| R6 | the third-party hook recipe cannot be verified here | shipped as a literal command plus raw JSON, labelled `unverified on this machine`; only the `ostlr run` path is verified end to end in M1 |
| R7 | untested at scale | 5000 CIM rows and a 64 KB command line are **not measured in M1**; recorded as a deliberate deferral, re-checked before M2 |

## 11. Residual concerns and their disposition

| Concern | Disposition |
|---|---|
| `DETACHED_PROCESS` would give a TUI no console at all (Steve, Backend, Algorithm, all `[high]`) | fixed: `CREATE_NEW_CONSOLE` + `CREATE_NEW_PROCESS_GROUP`, asserted by "a window exists for that pid and still exists after the panel closes" |
| the panel cannot read a detached child's stderr or exit code | fixed: no pipes; `ostlr run` owns the console and writes `exit_code` and the start-failure reason into the status file |
| `child ppid != our pid` is not a detachment test | fixed: the test is "close the panel, take a fresh CIM sample, the pid is still alive and still has a window" |
| a crashed agent rendered `done` in success green | fixed: the visual role is derived from the exit code (`done` + `exit != 0` -> error role) while JSON `state` stays `done`; asserted in the T9b golden |
| `done_inferred` was missing while L3 can yield `done` | fixed: `running_inferred`, `done_inferred`, `error_inferred` exist; `waiting` has none by construction |
| the `starting` bucket and `spawn_failed`'s bucket were undefined | fixed: a row sits in exactly one bucket, transients count in `starting`, a failed start becomes `error` within one tick, so `spawn_failed` never persists |
| the header reflowed every tick as zero buckets disappeared | fixed: `waiting`, `running`, `unknown` keep fixed slots at `0`; the rest hide in rank order, total last |
| the empty state had no hierarchy | fixed: title 11 pt bold, capability line 9 pt dim in user terms, command in mono 9 pt, buttons last |
| markers could be truncated at 320 px | fixed: markers never truncate; agent, project and cwd yield first |
| `U+2717` was confusable with `U+2715` | fixed: `spawn_failed` is no longer a transient, so the transient map holds one glyph |
| restart's argument form was unpinned | fixed: the raw CIM `CommandLine` string is passed through unmodified, with a quoted-path fixture |
| `spawn_failed` retention had no knob | fixed: `spawn_error_retain_s = 30` in `config.py` |
| T2 demanded a `pwsh` 7 sample never observed here | fixed: required for `powershell` 5.1, `SKIP (no pwsh)` otherwise |
| T7's live window check could fail a correct build | fixed: `SKIP (no titled window in this session)` |
| "two copies in < 100 ms -> one write" had no seam | fixed: a clipboard-writer double with a call counter |
| the recipe proves formatting, not function | accepted and recorded: marked unverified (R6), because no third-party hook can be exercised on this machine |
| context-menu-only kill/restart and header no-wrap cannot be checked headlessly | manual-only, listed in `docs/m1-notes.md`; the pure bucket-selection rule is separated from the wrap decision |
| "three seconds to know" and 1440p/125 % readability | manual-only, never in a DoD, recorded in `docs/m1-notes.md` |
