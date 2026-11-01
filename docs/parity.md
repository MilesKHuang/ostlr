# Parity - the same TUI must look the same inside Ostlr as in the OS terminal

The one line this file exists for:

> **Geometry comes from the terminal, never from the font.**
> Ostlr may add capability; it may not be the reason a TUI looks worse.

A terminal grid is a rectangle of cells. Anything that makes a cell's position
depend on the font's metrics will eventually break: a different size, a different
DPI, another font version. The OS terminal does not work that way, so a pane that
does is a parity bug by definition - not a taste question, and not something a
font choice can fix.

## The measured failure (before)

A real pane: `TerminalPane` -> ConPTY child drawing an FTXUI-style table with
Chinese inside it. `tools/parity_probe.py` measures the screenshot, not the DOM,
because the DOM stops existing the moment a canvas renderer draws the grid.

| Metric | Value | Why it is wrong |
|---|---|---|
| cell box | 12 x 35 device px | correct |
| box-drawing `U+2502` ink | 4 x 31 device px | the font's glyph, not the cell |
| frame gap per row boundary | **4 device px** | the frame reads as dashed |
| `U+2588` (gauge block) ink | 14 x 27 device px | 84 % of its cell: a gauge has stripes |
| same-column bar, ASCII row vs Chinese row | **-0.06 device px** | the DOM renderer compensates wide glyphs with `letter-spacing`, rounded to 1/64 px |

Root cause: the vendored core (`xterm` 5.3.x) has only the DOM renderer, which
lays a row out as text. `customGlyphs: true` is honoured by the canvas/WebGL
renderers only, so it was inert and the font drew the frame.

## The fix, and the numbers after it

`ostlr/qt/vendor/xterm-addon-webgl.js` (MIT, same project as the core) is loaded
and attached in `term.html`; the page then reports the renderer it got. Nothing
changed about the font, the size or the grid.

| Metric | DOM renderer | WebGL renderer |
|---|---|---|
| frame gaps in a 7-row frame | 8 | **0-1** |
| gauge block fill | 84 % | **100 %** |
| cell box | 8.00 x 23.35 CSS | 8.00 x 23.33 CSS |

`0-1` rather than `0` is the scan's own edge effect at the top and bottom of the
sampled span; the border is continuous in the screenshot.

## The rules this leaves behind

1. **The frame and the block characters are the terminal's job.** Keep
   `customGlyphs: true` and keep a renderer that implements it. If the addon
   cannot attach (remote desktop, no GPU), the pane falls back to the DOM
   renderer **and says so in the receipt** - a silent downgrade is a lie about
   how the pane is drawn.
2. **`advance(CJK)` is not required to be 2 cells, but it may never be assumed.**
   The bundled stack measures 1.667 (JetBrains 0.6 em against WenKai 1.0 em),
   which is the same ratio the OS terminal has with Cascadia, and it is fine:
   the wide glyph is narrower than its two-cell box and leaves a gap, exactly as
   it does outside Ostlr. A font that *is* 2.000 buys nothing here and costs the
   look of a bigger Chinese glyph.
3. **No option that this core does not implement.** `rescaleOverlappingGlyphs`
   arrived in xterm 5.5; on 5.3 it was silently ignored while looking like a fix.
   A test now forbids it.
4. **The default font is the OS terminal's font, read from its settings.**
   `fonts.system_family()` reads Windows Terminal's `settings.json` (default
   profile font, falling back to Cascadia Mono / Consolas / Microsoft YaHei UI).
   The bundled OFL faces are opt-in through `font_family` in `config.toml`. This
   reversed the earlier default on purpose: a pane whose text does not look like
   the user's own terminal is the same complaint as a broken frame, in font form,
   and the earlier justification ("we need the bundled faces for alignment") was
   wrong - alignment is the renderer's job.

## The two defects that produced this file, and where they came from

| What the user saw | Cause | Fix |
|---|---|---|
| table borders broken into dashes, in proJV | the font drew the frame: `U+2502` ink is 31 px in a 35 px row box (DOM renderer, `customGlyphs` inert) | WebGL renderer draws every cell |
| progress bars striped | `U+2588` ink is 14 x 27 px in a 12 x 35 px cell | same |
| "the font is not my terminal's font" | the default stack was a bundled pair (JetBrains Mono + WenKai) | default is the OS stack; the bundle is opt-in |

Both were reproduced with the real `proJV_tui.exe` in a real pane, and the probe
writes the two screenshots that show it: `--webgl` gives continuous borders, and
the same session with the addon disposed gives dashes.

## How to re-measure

```
py -3 tools\parity_probe.py --png       # real pane, real PTY, screenshot + metrics
py -3 -m unittest discover -s tests     # the renderer contract lives in
                                        # tests/unit/test_renderer.py
```

The probe prints `frame gaps`, `border ink rows` and `gauge cell fill`, and it
returns a non-zero exit code when a bar lands on a different pixel in two rows,
so it can gate a future change instead of relying on someone's eye.
