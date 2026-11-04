"""One order for the file tree: folders first, then natural name order.

Explorer's Details view, without the Windows API. `StrCmpLogicalW` was the
tempting answer and the wrong one: it does not exist off Windows, it cannot be
tested without the OS, and it hides the rule inside a system call. The rule here
is plain Python, so it runs anywhere and a test can state it:

    sub/   b2.txt   b10.txt   README.md

(the plain dictionary order puts b10 before b2, which is the defect this
function exists to remove.)

What it does NOT reproduce, stated instead of hidden: on a Chinese Windows,
Explorer sorts CJK names by pinyin and this sorts by code point; a few
punctuation weights also differ. "Stable, natural, folders first, on every
platform" is the promise - byte-identical to Explorer is not.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, List, Tuple

_DIGITS = re.compile(r"(\d+)")


def case_sensitive() -> bool:
    """Windows file names ignore case; Linux and macOS do not.

    This is the only platform difference in the rule, and it is a semantic one:
    on Linux `README` and `readme` are two files, and folding their case would
    interleave them in an order no shell would agree with.
    """
    return os.name != "nt"


def name_key(name: str, is_dir: bool = False) -> tuple:
    """The sort key: (folder first, then name split into text and number runs)."""
    text = str(name or "")
    if not case_sensitive():
        text = text.casefold()
    parts: List[Tuple[int, object]] = []
    for chunk in _DIGITS.split(text):
        if not chunk:
            continue
        # A digit run compares as a number, everything else as text; the tag
        # keeps the two kinds from being compared with each other.
        parts.append((1, int(chunk)) if chunk.isdigit() else (0, chunk))
    return (0 if is_dir else 1, tuple(parts))


def sorted_names(entries: Iterable[Tuple[str, bool]]) -> List[str]:
    """`(name, is_dir)` pairs in the order the tree draws them."""
    return [name for name, _is_dir in sorted(
        entries, key=lambda pair: name_key(pair[0], pair[1]))]
