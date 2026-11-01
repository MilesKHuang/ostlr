# Bundled terminal font

Drop a font file here (`.ttf` / `.otf` / `.ttc`) and Ostlr will use it for the
terminal, ahead of every system font. One file is enough: the first one found is
loaded and registered as the family `OstlrMono`.

## Why bundle one at all

A terminal grid only stays aligned when **advance(CJK) == 2 * advance(ASCII)**.
Measured on this machine at 14 px:

| family | ASCII | CJK | ratio |
|---|---|---|---|
| Consolas | 7.70 px | 14.00 px | 1.819  (wrong) |
| Cascadia Mono | 8.20 px | 14.00 px | 1.707 (wrong) |
| MS Gothic / NSimSun / SimSun | 7.00 px | 14.00 px | 2.000 (right) |

So the default stack starts with `MS Gothic` / `NSimSun`, which are exact on
Windows. That works, but it depends on the machine: on Linux, or on a Windows
without those fonts, the stack silently falls back to a 1.8-ratio font and the
same misalignment returns.

Shipping one font removes the dependency. **Do not commit a Microsoft font**: the
Windows fonts may not be redistributed. Use an openly licensed CJK monospace,
for example:

- Sarasa Mono SC (SIL OFL) - designed 2:1, the best fit for a terminal
- Maple Mono NF CN (SIL OFL)
- Noto Sans Mono CJK SC (SIL OFL)

Then set `font_family` in `config.toml` if you want a specific fallback order:

```toml
font_family = "OstlrMono, Consolas, monospace"
```

The terminal measures the ratio at startup and writes the result to the status
bar (`font ok` / `font ratio 1.82`), so a wrong font is visible instead of
looking like a rendering bug.
