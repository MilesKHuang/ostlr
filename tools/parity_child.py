"""The TUI the parity probe renders: an FTXUI-shaped table, Chinese inside it.

Every row is 28 CELLS wide (4 separators + three 8-cell columns), so separators
sit on columns 0, 9, 18 and 27 in every row. Cells are built from display width,
never from character count: 4 Chinese characters and 8 ASCII characters are both
8 cells, which is exactly the property that must survive the renderer.
"""
from __future__ import annotations

import sys
import time

BAR = "\u2502"      # box drawings light vertical
TEE = "\u251c"
CROSS = "\u253c"
RIGHT = "\u2524"

ASCII_CELL = "abcdefgh"                      # 8 cells
CJK_CELL = "\u4e2d\u6587\u4e2d\u6587"        # 4 chars, 8 cells
MIXED_CELL = "\u8fdb\u5ea6 50%"                 # 2 CJK + 4 ASCII, 8 cells
DATA_CELL = "\u4e2d\u6587\u6570\u636e"       # 4 chars, 8 cells
BLOCK_CELL = "\u2591" * 8                    # 8 cells
FULL_CELL = "\u2588" * 8                     # 8 cells
SHADE_CELL = "\u2593" * 8                    # 8 cells

TOP = ("\u250c" + "\u2500" * 8 + "\u252c" + "\u2500" * 8 + "\u252c"
       + "\u2500" * 8 + "\u2510")
RULE = TEE + "\u2500" * 8 + CROSS + "\u2500" * 8 + CROSS + "\u2500" * 8 + RIGHT
BOTTOM = ("\u2514" + "\u2500" * 8 + "\u2534" + "\u2500" * 8 + "\u2534"
          + "\u2500" * 8 + "\u2518")

ROWS = [
    (ASCII_CELL, ASCII_CELL, ASCII_CELL),      # pure ASCII
    (CJK_CELL, ASCII_CELL, ASCII_CELL),        # Chinese in the first column
    (ASCII_CELL, DATA_CELL, ASCII_CELL),       # Chinese in the middle
    (MIXED_CELL, ASCII_CELL, CJK_CELL),        # mixed inside one cell
    (BLOCK_CELL, FULL_CELL, SHADE_CELL),       # block gauge, the progress bar
]


def line(cells) -> str:
    return BAR + BAR.join(cells) + BAR


def frame() -> str:
    lines = [TOP, line((ASCII_CELL, ASCII_CELL, ASCII_CELL))]
    for index, cells in enumerate(ROWS):
        lines.append(line(cells))
        if index == 1:
            lines.append(RULE)
    lines.append(BOTTOM)
    return "\r\n".join(lines) + "\r\n"


def main() -> int:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write(frame())
    sys.stdout.flush()
    try:
        time.sleep(600)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
