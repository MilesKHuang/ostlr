# Bundled terminal font

Drop a font file here (`.ttf` / `.otf` / `.ttc`) and Ostlr adds it to the terminal
font stack as `OstlrMono`. One file is enough.

**It is added as the CJK fallback, not as the primary face.** That is deliberate:
see below.

## Why a terminal needs two fonts, not one

A terminal gives every character one cell (two for wide characters), and it draws
box-drawing characters (`|`, `-`, corners) in **one** cell. Measured at 14 px on
this machine:

| family | `M` | `中` | `|` `-` corners | ratio CJK/M |
|---|---|---|---|---|
| Consolas | 7.70 | 14.00 | **7.70** | 1.819 |
| Cascadia Mono | 8.20 | 14.00 | 8.20 | 1.707 |
| NSimSun / SimSun / SimHei / MS Gothic | 7.00 | 14.00 | **14.00** | 2.000 |
| Microsoft YaHei (the font proJV ships) | 13.68 | 14.00 | 9.92 | 1.023 |
| DengXian | 12.11 | 14.00 | 14.00 | 1.156 |

Read the `|` column: in a Chinese or Japanese font the box-drawing characters are
**full width (14 px) although the terminal reserves one cell (7 px)**. Each border
character then bleeds into the next cell, and the frame looks shifted - which is
exactly "the separators break when Chinese appears", and why switching the whole
terminal to MS Gothic made it worse instead of better.

No single installed font satisfies both conditions, so the default stack mixes
them:

```
Consolas, "Cascadia Mono", "Microsoft YaHei", "NSimSun", "MS Gothic", monospace
```

- **Consolas first**: ASCII *and* box-drawing come from it, one cell wide, so the
  frame stays on the grid (this is also what Windows Terminal does, and why proJV
  looks right there).
- **"Microsoft YaHei" second**: Chinese is drawn with YaHei's glyph shapes - the
  same face proJV bundles - so the text looks like proJV's.
- The CJK glyph is 14 px inside a 15.4 px two-cell box: a small gap, not a shift.

Microsoft YaHei itself cannot be the terminal font: at 13.68 px per ASCII cell it
is proportional, so the grid would be meaningless.

## If you drop a file here

It becomes the first CJK fallback (after the western mono), so:

- a **CJK monospace** (Sarasa Mono SC, Maple Mono NF CN, Noto Sans Mono CJK SC -
  all SIL OFL) is what belongs here;
- a proportional font (YaHei, Segoe UI) will not fix anything and cannot be the
  primary face;
- a Microsoft font may not be redistributed, so none is committed to this repo.
  Using one locally is fine; `config.toml` can name it directly:

```toml
font_family = "Consolas, \"Microsoft YaHei\", monospace"
```
