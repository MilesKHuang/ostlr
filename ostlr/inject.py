"""The text that leaves for a pane, and how a path is quoted.

Two readers exist for this string: the program inside the pane (a shell, or an
agent that hands the path to a tool) and the person watching the receipt. Both
need the smallest rule that survives both:

    D:\\ws\\projV\\src\\main.py      -> D:\\ws\\projV\\src\\main.py
    D:\\ws\\my project\\a b.md       -> "D:\\ws\\my project\\a b.md"
    D:\\                                  -> "D:\\\\"   (an unescaped last
                                            backslash would eat the quote)

The `@` form is the prefix Claude Code and friends use for "this file is
context"; it goes in front of the quoted text, never inside the quotes.

Limits, stated rather than hidden: this is not a shell parser. Windows forbids
`"` in a file name, so a quote inside a path is impossible there; a tab is
quoted like a space; and a trailing backslash is doubled, which is what a
C-runtime parser expects (cmd.exe is lenient either way).
"""

from __future__ import annotations

# Everything that makes a bare path ambiguous for a command line: whitespace,
# the shell's own punctuation, and the two quote characters.
UNSAFE = set(" \t\"'`&()[]{}^=;!,|<>$")

AT_PREFIX = "@"


def needs_quotes(path: str) -> bool:
    """Does this path survive being pasted without quotes?"""
    if not path:
        return False
    if path.endswith("\\"):
        # A directory root: the closing quote would be escaped by this backslash.
        return True
    return any(ch in UNSAFE for ch in path)


def quote_path(path: str) -> str:
    """The path as one argument, quoted only when it has to be."""
    text = str(path or "")
    if not text:
        return ""
    if not needs_quotes(text):
        return text
    body = text.replace('"', "")
    if body.endswith("\\"):
        body += "\\"
    return '"%s"' % body


def payload(path: str, at_prefix: bool = False) -> str:
    """What is written into the pane: `path`, `@path`, or nothing for no path."""
    quoted = quote_path(path)
    if not quoted:
        return ""
    return (AT_PREFIX + quoted) if at_prefix else quoted
