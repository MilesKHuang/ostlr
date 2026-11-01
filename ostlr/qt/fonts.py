"""Which font the terminal uses, and where a bundled one would come from.

The default stack is the OS terminal's own stack, read from Windows Terminal's
settings when there is one:

    Cascadia Mono   ships with Windows and is Windows Terminal's default
    Consolas        conhost's default, present on every Windows
    Microsoft YaHei UI  the CJK face Windows itself falls back to
    monospace       the last resort

Nothing is bundled any more. The panel used to ship two OFL faces (JetBrains Mono
for Latin and the frame, LXGW WenKai Mono for Chinese, 583 files / 29.5 MB) on the
theory that alignment needed them. It did not - alignment is the renderer's job
(`docs/parity.md`) - and a pane whose text does not look like the user's own
terminal is the "Ostlr changed my experience" complaint in font form. They are
gone, and `font_family` in `config.toml` is the only override.

Measured on this machine at 14 px (advance of `M` / advance of a CJK glyph):

    Consolas          7.70 / 14.00 -> 1.819
    Cascadia Mono     8.20 / 14.00 -> 1.707

Neither is 2.000, and neither needs to be: the WebGL renderer places every cell
itself, so a wide glyph narrower than its two-cell box just leaves a gap - exactly
what the OS terminal does.
"""

from __future__ import annotations

import glob
import json
import os
import re

# Windows Terminal keeps its settings here (stable, preview, and the unpackaged
# build). A profile font there is the most direct statement of "what my terminal
# looks like".
_WT_SETTINGS = (
    r"%LOCALAPPDATA%\Packages\Microsoft.WindowsTerminal_*\LocalState\settings.json",
    r"%LOCALAPPDATA%\Packages\Microsoft.WindowsTerminalPreview_*\LocalState\settings.json",
    r"%LOCALAPPDATA%\Microsoft\Windows Terminal\settings.json",
)

# Cascadia Mono is Windows Terminal's default and is installed system-wide on a
# current Windows; Consolas is conhost's and is always there.
_LATIN_FALLBACK = "Cascadia Mono"
_SECOND_FALLBACK = "Consolas"
_CJK_FACE = "Microsoft YaHei UI"


def _strip_comments(text: str) -> str:
    """Windows Terminal's settings.json is JSON with `//` comments."""
    return re.sub(r"//[^\n\"]*", "", text)


def os_terminal_face() -> str:
    """The font face Windows Terminal is configured to use, or "".

    Read, not guessed: the whole point is to match the terminal the TUI was
    written for. A missing or unreadable file is not an error - the caller falls
    back to the built-in candidates.
    """
    for pattern in _WT_SETTINGS:
        for path in glob.glob(os.path.expandvars(pattern)):
            try:
                with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
                    data = json.loads(_strip_comments(handle.read()))
            except (OSError, ValueError):
                continue
            profiles = data.get("profiles") or {}
            defaults = (profiles.get("defaults") or {}).get("font") or {}
            face = defaults.get("face")
            if not face:
                wanted = data.get("defaultProfile")
                for entry in profiles.get("list") or []:
                    if entry.get("guid") == wanted:
                        face = (entry.get("font") or {}).get("face")
                        break
            if isinstance(face, str) and face.strip():
                return face.strip()
    return ""


def system_family() -> str:
    """The CSS stack that reproduces the OS terminal's look."""
    face = os_terminal_face()
    parts = []
    if face:
        parts.append('"%s"' % face if " " in face else face)
    for candidate in (_LATIN_FALLBACK, _SECOND_FALLBACK):
        if candidate not in parts:
            parts.append(candidate)
    parts.append('"%s"' % _CJK_FACE)
    parts.append("monospace")
    return ", ".join(parts)


# Kept as a name for callers and tests; it is the OS stack now, not a bundle.
DEFAULT_FAMILY = system_family()


def family_for(cfg=None) -> str:
    """The CSS font stack for this configuration.

    One override, no bundle: `font_family` in config.toml wins over the OS stack.
    """
    configured = str(getattr(cfg, "font_family", "") or "").strip() if cfg else ""
    if configured:
        return configured
    return system_family()


def term_url(query_string: str = "") -> str:
    """Kept for callers that only need the resource name."""
    return "term.html" + (("?" + query_string) if query_string else "")
