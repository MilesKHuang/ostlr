"""Agent templates - which process is which agent.

A template answers one question: "is this process a claude / codex / projV /
pwsh session?" It matches on the executable name and on the command line,
because the same agent arrives differently depending on how it was started:

    claude.exe                       (installed binary)
    node .../claude-code/cli.js      (npm shim)
    py -3 -m ostlr run -- claude     (our own wrapper)

Matching is deliberately conservative: a template matches when its *token*
appears as a whole word in the executable name or in the command line. A
substring match would make `claude-helper.exe` a claude session and put a
wrong row in the list, which is worse than an `unknown` row.

The registry is data, not code: adding an agent is adding an entry here, and
T14's launcher reads the same list so the two can never disagree about what
"claude" means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Template:
    """One launchable agent."""

    id: str
    label: str
    # Whole-word tokens that identify this agent in an exe name or command line.
    tokens: Tuple[str, ...]
    # The command the launcher runs (T14). Empty means "not launchable yet".
    command: str = ""
    # How a path is injected into this CLI: "path" or "at" (T14).
    inject: str = "path"
    inject_suffix: str = " "

    def matches(self, exe: str, cmdline: str) -> bool:
        return _matches_tokens(self.tokens, exe, cmdline)


# Order matters: the first match wins, so a more specific agent must come
# before a more general one (projv before pwsh, since projv runs under a shell).
TEMPLATES: Tuple[Template, ...] = (
    Template(
        id="claude",
        label="claude",
        tokens=("claude", "claude-code", "claude.exe"),
        command="claude",
        inject="at",
    ),
    Template(
        id="codex",
        label="codex",
        tokens=("codex", "codex.exe"),
        command="codex",
        inject="at",
    ),
    Template(
        id="projv",
        label="projV",
        tokens=("projv", "projv_tui", "projv_tui.exe"),
        command="projv_tui",
        inject="path",
    ),
    Template(
        id="pwsh",
        label="PowerShell",
        tokens=("pwsh", "pwsh.exe", "powershell", "powershell.exe"),
        command="pwsh",
        inject="path",
    ),
)

# The id used when nothing matches. Not a template: a row with this id is a
# session we can see but cannot name, and the panel says so.
UNKNOWN_ID = "unknown"

_BY_ID: Dict[str, Template] = {t.id: t for t in TEMPLATES}


def _word_re(token: str) -> "re.Pattern[str]":
    """A whole-word matcher for one token.

    Word boundaries are `[A-Za-z0-9_-]`-aware: `claude` must not match inside
    `claude-helper`, but `claude-code` must match `claude-code/cli.js`.
    """
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(token) + r"(?![A-Za-z0-9_-])",
                      re.IGNORECASE)


_TOKEN_RES: Dict[str, "re.Pattern[str]"] = {}


def _matches_tokens(tokens: Sequence[str], exe: str, cmdline: str) -> bool:
    haystacks = [h for h in (exe or "", cmdline or "") if h]
    if not haystacks:
        return False
    for token in tokens:
        pattern = _TOKEN_RES.get(token)
        if pattern is None:
            pattern = _word_re(token)
            _TOKEN_RES[token] = pattern
        for text in haystacks:
            if pattern.search(text):
                return True
    return False


def all_templates() -> Tuple[Template, ...]:
    return TEMPLATES


def get(template_id: str) -> Optional[Template]:
    return _BY_ID.get((template_id or "").strip().lower())


def match(exe: str, cmdline: str) -> Optional[Template]:
    """The first template that claims this process, or None."""
    for template in TEMPLATES:
        if template.matches(exe, cmdline):
            return template
    return None


def template_id_for(exe: str, cmdline: str) -> str:
    """The template id for a process, or UNKNOWN_ID.

    Returning a string (not None) keeps the session dict's `template_id` field
    always present, so a consumer never has to branch on None.
    """
    found = match(exe, cmdline)
    return found.id if found else UNKNOWN_ID


def label_for(template_id: str) -> str:
    """The display label for a template id (the id itself when unknown)."""
    template = get(template_id)
    return template.label if template else (template_id or UNKNOWN_ID)


def command_for(template_id: str) -> str:
    """The launch command for a template id, or "" when there is none."""
    template = get(template_id)
    return template.command if template else ""


def inject_for(template_id: str) -> str:
    """How a path is injected into this agent: "path" or "at"."""
    template = get(template_id)
    return template.inject if template else "path"
