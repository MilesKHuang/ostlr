xterm.js and its addons - MIT, vendored, no CDN
================================================

Everything in this folder is copied verbatim from the xterm.js project
(https://github.com/xtermjs/xterm.js) and shipped with Ostlr so the panel works
with no network and no third-party download at start-up.

| File                     | Package                      | License |
|--------------------------|------------------------------|---------|
| `xterm.js`               | `xterm` 5.3.x (core)         | MIT     |
| `xterm.css`              | `xterm` 5.3.x (core)         | MIT     |
| `xterm-addon-fit.js`     | `xterm-addon-fit` 0.8.x      | MIT     |
| `xterm-addon-webgl.js`   | `xterm-addon-webgl` 0.16.x   | MIT     |

`LICENSE-xterm.txt` covers all four files: they are one project with one
copyright holder. The version numbers are the ones whose API matches the vendored
core; the WebGL addon refuses to attach to a mismatched core, which is how the
pairing stays honest when one of them is updated.

Why the WebGL addon is here at all
----------------------------------
The core alone renders with the DOM renderer, which lays a row out as ordinary
text. Then the *font* decides geometry: the box-drawing vertical in our Latin
face is 31 px tall inside a 35 px row box, so a table frame drawn by a TUI shows
a 4 px gap at every row boundary, and `U+2588` (a gauge block) covers 84 % of its
cell instead of 100 %.

The WebGL renderer draws each cell itself (`customGlyphs`), which is what the OS
terminal does, and both defects disappear. Measured on a real pane with the same
child process:

| metric                        | DOM renderer | WebGL renderer |
|-------------------------------|--------------|----------------|
| gaps in a table's left border | 8            | 0-1            |
| gauge block fill              | 84 %         | 100 %          |

Geometry must come from the terminal, never from the font. That is the rule this
folder exists to keep, and `term.html` falls back to the DOM renderer with a
visible receipt line when WebGL cannot be created (remote desktop, software
raster) - a silent downgrade would be a lie about how the pane is drawn.

Updating these files
--------------------
Copy the new build over the file, then run:

    py -3 -m unittest discover -s tests
    py -3 tools\parity_probe.py             # frame gaps and gauge fill, real pane

`tools/parity_probe.py` builds a real pane with a real ConPTY child and measures
the screenshot, so a renderer change is judged by pixels rather than by opinion.
