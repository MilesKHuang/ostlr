# Musk execution board - Ostlr M1

> Job: build M1 of the Ostlr panel in `D:\ws\ostlr`. Contract: `docs/sop/steve-ostlr.md` section 4 (T1-T17, verbatim). Environment: `py -3` only, stdlib only, `cd /d D:\ws\ostlr` for every command.
> Judged on quoted command output only. A task is done when its DoD command ran and the real output matches.

## Waves

| Wave | Tasks | Why grouped |
|---|---|---|
| W1 | T1, T2, T3, T7, T10 | no shared files: CLI/schema/config/paths, cim, procs, l3, ui leaf modules |
| W2 | T4, T5, T6, T8 | agents/sessions/git, l2/ladder, store, status producer |
| W3 | T9a, T9b, T12, T13 | view model + fixture pipeline, then actions (focus/kill/restart) |
| W4 | T11, T14, T15 | app shell (needs view model), launcher, clipboard |
| W5 | T16, T17 | reminder, selftest/docs/hygiene, then full acceptance re-run |

## Shared contracts fixed up front (all devs code to these)

- CIM row dict, produced by `probe/cim.py`: `{"pid": int, "ppid": int, "started_at": int, "mem_bytes": int, "cmdline": str, "exe": str, "name": str}`.
- L2 record dict, produced by `probe/l2.py`: `{"pid": int, "ts_epoch": int, "source": str, "cwd": str, "state": str, "kind": str, "msg": str, "exit_code": int|None}`.
- Fixture file for `--fixture <path>` is INPUT, not a canned answer: `{"now": <epoch int>, "cim": [<cim row dict>...], "l2": [<l2 record dict>...], "titles": {"<pid>": "<window title>"}, "status_dir_error": "<path>"|null}`. The pipeline runs on it deterministically, so the golden is a real assertion.
- Row (schema v1) order and names are `schema.py`'s constants, exactly as plan 8.1.

## Board

| # | DoD (one line) | Owner | Verify | Status | Returns |
|---|---|---|---|---|---|
| T1 | `py -3 -m ostlr --once --json` prints `{"v":1,"header":{...},"rows":[]}` exit 0; `--fixture` path loads and runs | dev_1 | test_1 | todo | 0 |
| T2 | 5 cim fixtures pass; live `powershell` sample parses `started_at>0`; forced 3s stall returns <3.2s with `stale, last good` | dev_1 | test_1 | todo | 0 |
| T3 | 4 tree fixtures: exact rss sums, depth cap 32, cycle terminates, orphan/recycled pid safe | dev_1 | test_1 | todo | 0 |
| T4 | cmdline->template; cwd from 3 sources with `?` fallback; git absent `?` / not fetched `...`; cache = distinct cwds in first 20 ranked rows | dev_1 | test_2 | todo | 0 |
| T5 | 89s/91s flip; half-written/zombie/blocked-without-kind -> unknown + one log line; denied dir -> `status_dir_unreadable`; no idle without fresh L2 | dev_1 | test_2 | todo | 0 |
| T6 | 100 interleaved UI intents applied at tick start -> 0 lost/0 exceptions; 600s retention; `already_exited`; restart adopts slot by (template,cwd) | dev_2 | test_2 | todo | 0 |
| T7 | 8 title fixtures classified to working/done/error only; live check lists >=1 titled window else `SKIP (no titled window in this session)` | dev_2 | test_1 | todo | 0 |
| T8 | 200 concurrent writes -> 0 parse errors, 0 `.tmp`; wrapper writes `exit_code` for exit 0 and non-zero; no pipes on the panel side | dev_2 | test_2 | todo | 0 |
| T9a | fixture golden with transient+retained done+fresh-L2 idle: `sessions == 7 buckets == visible+truncated`; oldest-first in a state; highlight 3s | dev_1 | test_1 | todo | 0 |
| T9b | `0s..59s` seconds, `>=60s` minutes; `...` vs `?`; `(no identity)`; done+exit=1 -> error role; `showing 60 of N` on 75-row fixture, never `showing 60 of 60`; 120-char clip; hint not in a row | dev_1 | test_1 | todo | 0 |
| T10 | no hex/Tk colour/inline font tuple outside ui leaf modules; STATE_MAP 6, TRANSIENT_MAP 1 (U+22EF), disjoint; 300-char cmdline elided leaf-visible; row height stable with marker | dev_2 | test_1 | todo | 0 |
| T11 | all displayed strings come from view_model; fixed header slots at 0; select never calls SetForegroundWindow (asserted double); Tk smoke SKIPs with a reason | dev_1 | test_2 | todo | 0 |
| T12 | `focused` iff GetForegroundWindow matches else `foreground_refused`; launch uses CREATE_NEW_CONSOLE, window exists for pid; failed start -> error for `spawn_error_retain_s` | dev_2 | test_1 | todo | 0 |
| T13 | kill confirm shows pid count + tree memory; identity mismatch/0 -> `identity_unverified`, taskkill never called; pending -> killed/survivors; restart uses raw cmdline, refuses `cwd_unknown`, second restart refused | dev_2 | test_1 | todo | 0 |
| T14 | which-miss greys with literal fix; hit -> row within `tick_s+1s`; last-3 persists in config.toml; hint names recipe; both chords copy with exact receipts | dev_2 | test_2 | todo | 0 |
| T15 | clipboard bounded retry <=10x20ms then typed failure; counted writer double -> 2 copies <100ms = 1 write; `--selftest --clipboard` equals copied path with foreign clipboard | dev_2 | test_2 | todo | 0 |
| T16 | 2 transitions -> 1 bell; title `! 1 waiting` within `tick_s+1s` else SKIP; prefix gone on resolve | dev_1 | test_2 | todo | 0 |
| T17 | README exact JSON + exact `py -3 -m ostlr.status ...` literal; `--selftest` PASS/SKIP with tkinter blocked; 0 non-stdlib imports; 3.2s stall line; notes carry acceptance table; `probe/l1.py` -> `unsupported` | dev_1 | test_2 | todo | 0 |

## Deliberate omissions (kept current as the board moves)

- none yet.

## Wave log

**W1 dispatched**: dev_1 = T1, T2, T3; dev_2 = T7, T10.
- dev_1 attempt 1: BLOCKED (shell-output loop burned the turn before any code was written). Real facts recovered: `pwsh` absent -> T2 reports `SKIP (no pwsh)`; live CIM = array (single object with `-First 1`), `CreationDate` = `/Date(<ms>)/`, `CommandLine`/`ExecutablePath` null for System/Idle. Re-dispatched with those facts pasted in.
- dev_1 attempt 2: T1, T2, T3 done. Suite 143 tests OK. `--once --json` -> envelope with 10 header keys, exit 0. Live CIM 218 pids, all `started_at > 0`. Forced stall 3.01 s. Two defects found by its own tests and fixed (`bucket_for("working")` returned "working"; raw `\/Date(...)\/` form rejected).
- dev_2: T7, T10 done. Suite 72 tests OK, live 5 titled windows. Two self-caught defects (`starting` shared `fg_dim` with idle; a hex-ish example in a theme docstring).
- test_1 (independent): **T7 PASS, T10 PASS** - own grep found hex only in `theme.py` (10 lines), zero Tk colour names, zero code font tuples, zero raw glyph literals; `STATE_MAP` 6 / `TRANSIENT_MAP` 1 disjoint codepoints and roles; 300-char path elides leaf-visible; markers survive at a 60 px budget; `role_for_state('done',1)` -> danger, `('done',0)` -> ok.
- Open: T1/T2/T3 not yet independently verified -> test_2 in W2.
