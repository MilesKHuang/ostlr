# Next round: A1-A3, the execution list

> **Decision (2026-11-04): follow OSC 7501 only.** No hook / producer-template
> work. A CLI that does not speak the protocol shows `unknown` - the same rule the
> panel already applies to everything else. This file is the checklist for that
> decision, with the acceptance command for each item so "done" is checkable.

## 0. Why this decision, and what it costs

**Evidence for the protocol (surveyed 2026-11):**

| Producer | Since | Note |
|---|---|---|
| Claude Code | 2.1.295 | probes `OSC 7501;?` + a DA1 sentinel, emits only after a reply |
| pi | 1.1.0 | same probe, `PI_PROGRAM_STATUS=0/1` override |
| Buildkite CLI | 3.64.0 | `bk build watch`, `bk auth login` (`blocked:kind=auth`) |
| Tuist | 2026-10 | working / done / error |
| OpenTUI, `osc7501` (PyPI), a shell one-liner | - | any program can report in ~a dozen lines |

Spec is at **revision 0.4** (2026-10-10). Hosts that read it: Rex, tuios, psmux
(opt-in), libghostty (parser only, the app does not answer the probe yet),
SwiftTerm (parser). Windows Terminal: not found.

**What hooks would have added, and we are giving up:**

- richer payload than the protocol carries (tool name, command, exit code, cost);
- coverage for CLIs that never implement it (aider, gemini, crush, old versions);
- independence from our own probe handshake.

**Why we still skip them:** per-CLI config files, event names that drift (Herdr
rewrote its Claude Code detection rules ten times in three months), per-machine
setup, and a PATH/interpreter question on every install. The protocol is the
shrinking set of those problems, not a new one.

**The cost, stated plainly:** coverage is limited to protocol speakers, and the
line A2 shows is one line the CLI chose to send - no tool names, no command text.

## A1 - make the protocol path work, and keep proving it works

### A1.0 The foundation (do this first): a fake producer

Nothing else is measurable until the handshake is proven, and the handshake can
be proven **without installing any CLI**. A script writes three things into a
real pane's PTY: `OSC 7501;?` (probe), a DA1 query right behind it, and one
`blocked` report.

- Assert 1: the probe is answered - our reply is written back before any DA1
  answer (`terminal.py` writes `l1_mod.PROBE` when the query is seen).
- Assert 2: DA1 never overtakes it (this is risk **R2** in the design: a CLI that
  sees DA1 first concludes "unsupported" and stays silent for the whole session).
- Assert 3: the row turns `blocked`, with `source=l1`.
- Assert 4: a report split across two PTY reads is still one report.

Acceptance: `py -3 _verify/l1_handshake.py` prints four PASS lines, and
`tests/unit/test_l1_handshake.py` covers the ordering without a window.

### A1.1 Align with spec revision 0.4

`ostlr/probe/l1.py` says revision 0.3 throughout. 0.4 (2026-10-10) mainly lets
the probe reply list the states and kinds a host supports, and tidied limits.

- Re-read 0.4 against `_decode`, the discard rules and the limits; change the
  constant text in `reason()` to name the revision we actually implement.
- Add one test for a 0.4-shaped probe reply (states/kinds listed).

Acceptance: existing `tests/unit/test_l1_stream.py` stays green, plus the new
probe-reply case.

### A1.2 A version matrix that only says what was verified

`docs/producers/status-levels.md` gets one table: CLI, first version that
reports, verified on this machine (`yes` / `unverified`), and the evidence.

- A row is `verified` only after a real pane showed the state change.
- A CLI that is not installed is `unverified`, never "supported".

Acceptance: the table exists and every `verified` row names the pane run that
proved it.

### A1.3 Silence must explain itself

A pane whose program never reports stays `unknown`, but the row should say **why**
on the receipt line, without inventing a state: "no report yet (probe answered;
the program may be older than 2.1.295)" versus "probe not answered".

Acceptance: a unit test on the message builder; no new state value.

## A2 - put the question on the row

Already half-done: `RowDelegate` paints `row["msg"]` beside the state word. What
remains is priority and space.

- When `state == blocked`, the message owns the row: state word + age first, then
  the message elided to the space left before the two action icons.
- When there is no message, the row shows the state word alone - never a blank.
- Accept that the text is whatever the CLI sent (one line, ≤ 2048 bytes in the
  spec); it is not a tool name.

Acceptance: `tests/unit/test_ui_geometry.py` gains a case that a long message is
elided without covering the icons; one screenshot with a real `blocked` row.

## A3 - escalation when a wait gets long

`blocked` for longer than a threshold raises a desktop reminder, once, and only
while the panel is not in front.

- `config.py`: `blocked_escalate_s: int = 300` (0 disables), plus a cooldown so a
  flapping agent cannot spam.
- The duration comes from the state record's timestamp (`age.record_age`), not
  from a second clock - one source of truth.
- Reminder = tray badge (already exists) **plus** `pystray.Icon.notify(title,
  message)`; `Icon.notify` exists in the installed pystray, so no new dependency.
- Never when the panel is the active window; never for `working`/`done`.

Acceptance: `should_escalate(now, blocked_since, cfg, last_notified)` as a pure
function with unit tests (first hit, cooldown, disabled, not-blocked); a manual
run showing the badge and the toast.

## Order, and roughly how long

| Step | Item | Estimate |
|---|---|---|
| 1 | A1.0 fake producer (foundation) | half a day |
| 2 | A1.1 rev 0.4 alignment | half a day |
| 3 | A2 row message polish | half a day |
| 4 | A3 escalation + config | one day |
| 5 | A1.2 version matrix with a real Claude Code run | depends on the install |
| 6 | A1.3 silence explanation | half a day |

## Risks this list already knows about

| Risk | Where it bites | Mitigation |
|---|---|---|
| DA1 overtakes the 7501 reply | a CLI stays silent for the whole session and the row says `unknown` | A1.0 locks the ordering in a test |
| The CLI probes but we answer with a shape it rejects | same symptom, different cause | A1.1 + the 0.4 reply test |
| A CLI releases a "reports states" feature behind a flag | looks like our bug | A1.2 records the flag per CLI |
| `msg` is absent or generic | A2 shows only `blocked` | A2 falls back to the state word; A1.3 explains |
| Spec moves again (0.5) | silent drift | the revision is named in `reason()` and in the matrix |
