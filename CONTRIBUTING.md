# Contributing

Self-use tool, but the rules are the same ones that made it work.

## Setup

```
py -3 -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
py -3 -m ostlr --selftest          # PASS/SKIP lines, exit 0
py -3 -m unittest discover -s tests -v
```

`python` on Windows is the Microsoft Store stub here - always `py -3`. The mirror
matters: the default index serves only pure-Python wheels, so PySide6 and pywinpty
look unavailable when they are not.

## Rules that are not negotiable

- **Never invent a state.** No data means `unknown`, never `idle`.
- **Never fake an affordance.** A missing tray or an unadoptable PTY is reported
  on screen, not silently degraded.
- **A session is what this panel owns.** It does not claim to manage a process
  whose console it cannot see; for an external one it offers focus or a shell in
  its directory.
- **Every number has a unit and a source.** `cpu` is percent of the whole machine,
  `mem` is percent of physical RAM, `gpu` sums the process's engine instances -
  and a value that cannot be measured prints `-`.
- **The terminal font is exact 2:1** (advance(CJK) == 2 * advance(ASCII)) and the
  box glyphs come from xterm, not the font. A font with wrong metrics makes every
  border drift; the comparison table is in `ostlr/qt/fonts/README.md`.
- **No dependency outside the declared list** (`schema.ALLOWED_THIRD_PARTY`). The
  import test fails on an undeclared one.

## Before a pull request

```
py -3 -m unittest discover -s tests       # must be green
py -3 -m ostlr --selftest                 # must exit 0
```

New behaviour needs a test. Pure logic (sessions, ladder, store, view model,
discovery, layout, fonts, procstat) must be testable without a window; the Qt
shell is exercised by the smoke tests, which SKIP with a reason on a headless box.

## Commit messages

`area: what changed`, then one line of why. If a fix replaces an earlier attempt,
say so - this repository has three commits that exist because an earlier claim was
measured and found wrong, and that history is the useful part.
