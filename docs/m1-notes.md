# M1 notes - acceptance, manual checks, deferrals, deviations

Companion to `docs/sop/steve-ostlr.md` (the agreed plan). This file records what
a verifier runs, what only a human can judge, what was deliberately not built,
and where the implementation differs from the plan.

## 0. Defects found after the skill run (fixed)

**D1 - launching in any other directory did nothing (T12/T14).** The launcher
starts its wrapper as `python -m ostlr run -- <agent>` with the **chosen project
directory** as cwd. `-m ostlr` cannot be resolved from an arbitrary directory, so
the wrapper died in its new console before the agent started: an empty row, no
status file, no reason on screen - precisely the "clicked launch, nothing
happened" failure the plan forbids. It survived earlier smoke tests because they
launched with the repository as the cwd, the one directory where the import
works.

Fix: `actions.wrapper_env()` puts the package root first on `PYTHONPATH` and
names it in `OSTLR_WRAPPER_ROOT`; `launch()` passes that environment to `Popen`;
`status._child_env()` strips the injected root again so the launched agent never
inherits our source tree. `tests/unit/test_wrapper_env.py` proves the module runs
from a temp directory, proves it fails without the env (so nobody "simplifies"
the fix away later) and checks the child environment is cleaned.

Verified end to end rather than by inspection - a launch from
`%TEMP%\ostlr-cwd-proof` with a child that writes `os.getcwd()`:

```
launch ok: True reason: launched pid: 12420
child cwd: C:\Users\ADMINI~1\AppData\Local\Temp\ostlr-cwd-proof
expected : C:\Users\ADMINI~1\AppData\Local\Temp\ostlr-cwd-proof
RESULT: MATCH
```

**D2 - the fix text promised a config table that was never read.** The launcher
greys a missing agent with "install it or set an absolute path in config.toml",
but no `[[agents]]` table existed in `config.py`. Now it is parsed, written back,
and merged into the registry by `agents.apply_overrides()` before anything reads
it, so the promise is true; `tests/unit/test_agent_overrides.py` pins parsing,
merging, dedupe and the PATH pre-check that follows.

## 1. Acceptance per milestone (verbatim from plan section 5)

| Milestone | Days | Contains | Exit criterion (every line must hold) |
|---|---|---|---|
| MS1 - a runnable panel | 1-5 | T1-T11 | `py -3 -m unittest discover -s tests` exits 0; `--once --json --fixture tests/fixtures/snapshot_small.json` matches the golden byte for byte; on a live box `--once --json` also exits 0 with >= 1 row; `py -3 -m ostlr.status --state blocked --kind permission --pid <pid> --msg t` puts that pid at row 1 within 6 s with `source=l2`, and deleting the file returns it to `unknown` and never to `idle`; the empty state prints `L1: unsupported`, `producers found:` and `inferred rows:` |
| MS2 - you can act on it | 6-8 | T12-T15 | selecting never focuses and Enter does; kill on a 3-child tree goes pending -> `killed` with 0 survivors; `identity_unverified` visibly disables kill on a `(no identity)` row; restart keeps the slot and refuses `cwd_unknown`; a launch shows `starting...` -> a real state, its console window exists, and the session survives closing the panel (fresh CIM sample after exit); `--selftest --clipboard` equals the copied path |
| MS3 - it looks after you | 9-10 | T16-T17 | `--selftest` all PASS/SKIP with the tkinter import blocked; the title prefix appears within 6 s; the README producer command pasted into a fresh `cmd` makes a row appear; the recipe file exists and contains the exact JSON |

## Manual smoke (T12/T13 live)

Run on this machine with a harmless target (`cmd /c timeout /t 60 /nobreak`) and
`OSTLR_STATUS_DIR` pointed at a temp directory. The harness was a scratch script,
deleted after the run; the commands below are what it executed.

### Real launch

```
actions.launch({"command": "cmd", "args": ["/c", "timeout", "/t", "60", "/nobreak"]})
-> {"ok": true, "reason": "launched", "pid": 17072, "command": "cmd",
    "argv": ["...python.exe", "-m", "ostlr", "run", "--", "cmd", "/c",
             "timeout", "/t", "60", "/nobreak"]}
status file 17072.json -> {"v": 1, "source": "ostlr-run", "pid": 17072,
    "cwd": "D:/ws/ostlr", "state": "working", "msg": "starting"}
tasklist /FI "PID eq 17072" -> python.exe  17072  Console  1  18,760 K
```

The wrapper is the session root, so the status file is keyed to the wrapper's
pid, not the child's. `state=working` proves the producer wrote before the child
was even visible, which is what makes a launch show `starting...` instead of
nothing.

### Focus

```
actions.focus(17072) -> {"ok": false, "reason": "foreground_refused",
                         "pid": 17072, "hwnd": 113055438}
l3.enumerate_titles() -> no entry for 17072
```

The window exists (the backend found a real hwnd) but Windows refused the
foreground switch, which is the normal outcome for a background console. Focus
degrades to `foreground_refused` plus a flash - not a crash, and not a silent
no-op. The title-based window check cannot see an untitled console window, so
`enumerate_titles()` returning nothing here is expected, not a defect.

### Real kill

```
confirm line1: 1 process will be ended
confirm line2: 0 B of memory
kill -> {"ok": true, "reason": "kill requested (pending)", "pid": 17072,
         "receipt": "kill requested (pending)",
         "taskkill": ["taskkill", "/PID", "17072", "/T", "/F"], "returncode": 0}
fresh cim.probe_once() -> survivors: []
tasklist /FI "PID eq 17072" -> INFO: No tasks are running which match the criteria.
```

The confirmation shows what will die before anything runs, the receipt says
`pending` because the sampler judges on the next tick, and a fresh sample proves
the tree is gone rather than trusting taskkill's exit code.

### Identity guard

```
kill({"pid": 17072, "started_at": 1}) -> {"ok": false,
    "reason": "identity_unverified", "pid": 17072, "expected": 1, "actual": 0}
taskkill calls recorded by the injected runner: []
```

A stale identity is refused and `taskkill` is never invoked. This is the check
that stops a recycled pid from ending an unrelated process.

### Restart

```
restart -> {"ok": true, "reason": "launched", "pid": 19880, "new_pid": 13496,
            "cmdline": "cmd /c timeout /t 60 /nobreak", "cwd": "D:\\ws\\ostlr"}
new pid differs: True   old: 19880   new: 13496
tasklist /FI "PID eq 13496" -> cmd.exe  13496  Console  1  8,120 K
```

The raw command line is passed through unmodified (plan 7.3), the new pid
differs, and the new process is alive.

### Still unproven

- A real `claude` / `codex` launch: neither CLI is installed on this machine, so
  only a harmless `cmd` target was exercised.
- Focus against a third-party terminal on another desktop: the refusal path is
  proven, the success path is not.

## Known gaps

- `restart()` spawns the raw CIM `CommandLine` directly, so a restarted session
  has no producer writing its status file and the panel shows it as `unknown`
  until something else reports - plan-faithful (7.3 requires the raw command
  line) but a real gap; candidate fix for the next iteration is to wrap the raw
  string through `ostlr run` so the raw string is still passed unmodified.

## 2. Manual-only checks (never in a DoD, never in CI)

These are judgements a machine cannot make. They are listed so nobody mistakes
them for verified criteria.

| Check | How to judge it | Why it is manual |
|---|---|---|
| "three seconds to know who needs me" | open the panel with 3 agents running, look once | it is a human reaction time, not a measurable property |
| readable at 1440p / 125 % scaling | look at the panel on the target display | needs the physical display and the OS scaling setting |
| context-menu-only kill / restart | right-click a row, confirm the menu is the only route | a Tk context menu has no headless equivalent |
| the 1 px accent selection border | select a row and look | ttk's focus ring is invisible on a dark theme; the border is drawn, not asserted |

## 3. Deliberate deferrals (with the reason)

| Not built | Why |
|---|---|
| L1 OSC 7501 capture | needs our own PTY (`pywinpty`), which cannot be installed here; `probe/l1.py` returns `unsupported` |
| embedded terminal pane / xterm.js / path injection | baseline M2; needs the same PTY layer |
| real system tray icon | the stdlib cannot draw one; degrades to a title prefix, a header counter and one bell per blocked transition |
| file tree panel | `askdirectory` plus copy-path already deliver the same two jobs |
| PEB cwd read via `ReadProcessMemory` | undocumented offsets, same-privilege only; the L2 producer writes `cwd` anyway |
| SQLite / history / layout persistence | no M1 requirement needs history; a schema now is migration debt |
| 5000-row / 64 KB command-line scale test | not measured in M1; recorded here as a deliberate deferral, to be re-checked before M2 |

## 4. Deviations from the plan (what the code does differently, and why)

| Plan says | Implementation does | Reason |
|---|---|---|
| `ui/type.py` renders a font as a Tk string via `as_tk()` | the view passes the font **tuple** to `font=` | Tk parses a string font as a list, so `"Segoe UI 9 normal"` becomes family `Segoe`, size `UI` -> `TclError: expected integer but got "UI"`. The tuple is the only form Tk accepts unambiguously. |
| `schema.HEADER_KEYS` has no `done_inferred` | `view_model.header_for()` adds `done_inferred` at runtime | L3 can yield `done`, so an inferred `done` needs a sub-count or it would be counted as authoritative. The tuple stays as the plan wrote it; the extra key is added where it is used. |
| the store is "sampler-owned" | `Store.apply_intents()` publishes immediately after draining | a caller that queues `already_exited` and reads the row back must see the new state without waiting for the next `observe()` |
| `--once --json` prints the envelope | it also carries `footer` and `truncated` | the footer (`showing 60 of 75`) is part of the header/list invariant, so it must be in the same artifact the verifier diffs |
| the panel's empty state prints the capability line | it prints `L1: unsupported \| producers found: N \| inferred rows: N` from `view_model.capability_line()` | the plan's wording; the view owns no literal |

## 5. How to verify this file's claims

```
py -3 -m unittest discover -s tests
py -3 -m ostlr --selftest
py -3 -m ostlr --once --json --fixture tests/fixtures/snapshot_small.json
```

The first must exit 0, the second must print only PASS/SKIP lines and exit 0,
and the third must match `tests/golden/snapshot_small.golden.json`.

## 2. Defects the first real open revealed (fixed)

Everything below shipped green through 485 tests and six design votes, and was
still unusable the first time a human opened the window. They are recorded
because the pattern matters more than the individual bugs.

**D3 - the launcher had no entry point.** `open_launcher()` existed, was tested
indirectly, and nothing called it. The gesture list in the plan named select,
focus and two copy chords; "open the launcher" was in neither the plan nor the
code, so the product could not start a single session. The file-tree right-click
was supposed to be that entry, and it was cut without re-homing the entry point.
Fixed: a toolbar with Start / Refresh / Copy path / Help, plus the same Start
button inside the empty state.

**D4 - the list was every process, not every agent.** `sessions.py` kept
parentless processes "so we can still see them", which filled the panel with
msedge, Explorer, dllhost, wps and Code rows reading `unknown` - a wall with
nothing to act on. Fixed: a process is a session only when its executable names
a template, or a producer wrote a **fresh** status record for it. The freshness
rule also closes a slower bug: a leftover status file whose pid was reused used
to keep an unrelated process on screen for ever.

**D5 - a path in the arguments named the agent.** Template matching scanned the
whole command line as a substring, so every VS Code window opened on
`D:\ws\projV` was listed as a "projV session". Fixed: match the executable
basename, or - only for a known interpreter - the script argument's path
segments. A folder mentioned in the arguments never names an agent.

**D6 - the window explained nothing.** 900x560, a narrow list and empty black
space on the right. Fixed: 1180x720, a permanent detail column with the state
legend and the selected row (state, source, cwd, reason, hint), and a banner
that says why every row is `unknown` instead of leaving a grey wall.

All four are covered by tests now: sessions and template matching have explicit
cases for "a path in the args is not an agent", "a stale record is not a
session", "the toolbar buttons exist and are bound", and the detail/legend text
is generated in `view_model` so the view still owns no literal.

## 3. The pivot: stdlib-only was a wrong conclusion, not a constraint (fixed)

The M1 build used tkinter because "PyPI is dead here". That was measured against
the **default index**, which serves only pure-Python wheels. Switching to
`https://mirrors.aliyun.com/pypi/simple/` yields the wheels the baseline always
wanted:

```
psutil-7.2.2-cp37-abi3-win_amd64.whl
pywinpty-3.1.2-cp313-cp313-win_amd64.whl
pyside6-6.12.0-cp310-abi3-win_amd64.whl
pillow-13.0.0 / pystray-0.19.5
```

So the whole stack downgrade - tkinter instead of Qt, no PTY, no terminal, a
process list instead of a manager - was my error, recorded as an environment
fact instead of being investigated. The lesson is in `AGENTS.md` now: a package
that "does not exist" says something about the index, not about the machine.

### What replaced what

| Then | Now |
|---|---|
| tkinter panel, list + banner only | PySide6 shell: toolbar, session list, terminal tabs, split panes, tray |
| no PTY; sessions only observed | ConPTY via pywinpty; the pane writes its own status record (`source=ostlr-pane`) |
| no terminal | xterm.js in QWebEngineView, 277 KB vendored, no CDN |
| fixed template list + absolute paths in config | discovery: PATH scan + remembered picks + Browse, and "use the running copy" when the selected exe has no API key |
| no layout memory | `layout.json`: tabs, splits, sizes, commands, directories |
| no tray | pystray badge with the waiting count; absence stated |

### Defects found while packaging

`--collect-all winpty` was missing: the wheel ships `OpenConsole.exe` and
`winpty-agent.exe` next to the DLLs, and without them the PTY fails silently -
the pane stays blank with no message anywhere. The exe now carries them, and a
`OSTLR_DEBUG_JS=1` switch prints the page's own console for the next time a
packaged pane looks dead.

## 4. The two symptoms were one root cause: a placeholder grid

"Its refresh rate is very low" and "the TUI's vertical borders are broken" turned
out to be the same defect, and neither was Python being slow.

The page reports its grid three times before the layout settles: the first report
is the fit floor, `20 x 5`, because the container has no size yet. We spawned the
child at that grid and also forwarded the next `20 x 5` resize to it. projV then
drew a 20-column frame into a 188-column terminal: borders everywhere, nothing
looking filled, and very few bytes arriving - which read as "low refresh".

Fixes, each at the cause:

| Cause | Fix |
|---|---|
| a placeholder grid reached the child | a grid must be plausible (`>= 40 x >= 10`) and seen twice before it starts or resizes the child; the 1 s fallback keeps a broken page from leaving the pane dead |
| the old PTY wrapper put a socket and a 1 ms-per-chunk pump thread between conhost and us, and reassembled UTF-8 one byte at a time | `winpty.PTY`, the C++ object, is used directly: blocking read in our own thread, `set_size` for resize. Measured on the same child: 1 028 -> 13 550 bytes received once the grid stopped shrinking |
| xterm keeps the old cells when the grid changes, while conhost clears the screen | on a grid change the alternate screen is cleared (and only there: clearing the normal screen would eat shell history) |
| output went out one `runJavaScript` per read | coalesced on a 16 ms timer, one call per frame |

A negative result worth keeping: a ctypes ConPTY written for this (CreatePipe ->
CreatePseudoConsole -> CreateProcess with the pseudoconsole attribute) compiled,
started and *silently did not attach the child* - projV wrote into the parent's
console while the pipe stayed empty. The `mode con` probe appeared to confirm the
resize, but the text was coming from the wrong console. It is deleted rather than
kept as a fallback: a PTY wrapper that leaks output is worse than no wrapper.
