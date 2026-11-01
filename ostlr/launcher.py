"""The launcher - pick a directory, pick an agent, start it.

Three rules, all of them about not lying to the user:

  * **Pre-check before the click.** Every template is checked with
    `shutil.which` when the dialog opens, so a missing agent is a greyed row
    with the literal fix on the same line - not a button that does nothing.
  * **The default path is the wrapper.** A launch runs
    `py -3 -m ostlr run -- <cmd>`, so the new session gets a producer and its
    row can reach a real state instead of sitting at `unknown` forever.
  * **Remember the last three.** Directory and agent are remembered in
    `config.toml`, most recent first, so "start an agent here" is two clicks.

The pure parts (`precheck`, `command_for`, `hint_for`, `remember`) are testable
headless; `LauncherDialog` is the thin Tk shell around them.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import agents as agents_mod
from . import config as config_mod

# mirrors config.py (dev_1 owns the constant; hardcoded here on purpose)
LAST_MEMORY = 3

# The recipe files that exist under docs/producers/. A template with a recipe
# gets a hint that names it, because that is the only route from a grey row to
# a real `blocked` state.
RECIPES: Dict[str, str] = {
    "claude": "docs/producers/claude-hook.md",
}

# The literal fix text. It names the agent and the two ways out, because
# "not found" alone leaves the user guessing.
FIX_TEMPLATE = "not found: %s - install it or set an absolute path in config.toml"


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def precheck(templates: Optional[Sequence[Any]] = None,
             which: Optional[Callable[[str], Optional[str]]] = None) -> List[Dict[str, Any]]:
    """Check every template's command against PATH.

    Returns one dict per template: {id, label, command, available,
    resolved_path, fix_text}. A missing agent is `available: False` with the
    literal fix text; the dialog greys that row and prints the fix inline.
    """
    lookup = which or shutil.which
    out: List[Dict[str, Any]] = []
    for template in (templates if templates is not None else agents_mod.TEMPLATES):
        command = _as_text(getattr(template, "command", ""))
        template_id = _as_text(getattr(template, "id", ""))
        label = _as_text(getattr(template, "label", template_id))
        resolved = ""
        if command:
            try:
                resolved = lookup(command) or ""
            except Exception:
                resolved = ""
        available = bool(resolved)
        out.append({
            "id": template_id,
            "label": label,
            "command": command,
            "available": available,
            "resolved_path": resolved,
            "fix_text": "" if available else (FIX_TEMPLATE % (command or template_id)),
        })
    return out


def command_for(template: Any, extra_args: Optional[Sequence[str]] = None,
                python: Optional[str] = None) -> List[str]:
    """The argv the launcher runs: our wrapper, then the agent's command.

    The wrapper is the default path on purpose (plan 7.4): it writes the status
    file, so the launched session gets a producer and a real state instead of
    `unknown`.
    """
    if isinstance(template, dict):
        command = _as_text(template.get("command"))
    else:
        command = _as_text(getattr(template, "command", ""))
    exe = python or sys.executable
    argv = [exe, "-m", "ostlr", "run", "--"]
    if command:
        argv.append(command)
    if isinstance(template, dict):
        template_args = template.get("args") or ()
    else:
        template_args = getattr(template, "args", ()) or ()
    argv.extend(_as_text(a) for a in template_args)
    argv.extend(_as_text(a) for a in (extra_args or []))
    return argv


def hint_for(template: Any) -> str:
    """The row hint for a template: the literal command, plus the recipe.

    A template with a recipe names the file, because the recipe is the only way
    to reach `blocked` for a third-party CLI.
    """
    if isinstance(template, dict):
        template_id = _as_text(template.get("id"))
        command = _as_text(template.get("command"))
    else:
        template_id = _as_text(getattr(template, "id", ""))
        command = _as_text(getattr(template, "command", ""))
    base = "py -3 -m ostlr run -- %s" % (command or template_id)
    recipe = RECIPES.get(template_id)
    if recipe:
        return "%s (blocked needs the hook: %s)" % (base, recipe)
    return base


def remember(cfg: config_mod.Config, directory: Optional[str] = None,
             agent_id: Optional[str] = None) -> config_mod.Config:
    """Record a launcher choice, most recent first, capped at three.

    Pure with respect to the file: the caller decides whether to `save()`.
    """
    return config_mod.remember(cfg, directory=directory, agent=agent_id)


def remember_and_save(cfg: config_mod.Config, directory: Optional[str] = None,
                      agent_id: Optional[str] = None,
                      path: Optional[str] = None) -> bool:
    """Remember a choice and persist it. Returns False when the save failed."""
    remember(cfg, directory=directory, agent_id=agent_id)
    return config_mod.save(cfg, path)


def default_directory(cfg: config_mod.Config) -> str:
    """The directory the dialog pre-fills: the last one, else the cwd."""
    if cfg.last_dirs:
        return cfg.last_dirs[0]
    try:
        return os.getcwd()
    except OSError:
        return ""


def default_agent(cfg: config_mod.Config,
                  checked: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    """The agent the dialog pre-selects: the last one, else the first available."""
    if cfg.last_agents:
        return cfg.last_agents[0]
    for row in checked or []:
        if row.get("available"):
            return _as_text(row.get("id"))
    return ""


def start(template: Any, directory: str, cfg: Optional[config_mod.Config] = None,
          launch_fn: Optional[Callable[..., Dict[str, Any]]] = None,
          extra_args: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Start an agent in `directory` through the wrapper.

    `launch_fn` is the injection point: a test passes a double and asserts the
    cwd and the argv without spawning anything. The result is a value, never an
    exception - a failed start turns that row red with the reason inline.
    """
    from . import actions

    work_dir = _as_text(directory)
    if not work_dir or work_dir == actions.UNKNOWN_CWD:
        return actions.fail(actions.REASON_CWD_UNKNOWN)

    argv = command_for(template, extra_args)
    runner = launch_fn or actions.launch
    try:
        result = runner(template, work_dir, argv=argv)
    except TypeError:
        # A double with the simpler signature (template, cwd).
        result = runner(template, work_dir)
    except Exception as exc:  # a broken launcher must not crash the panel
        return actions.fail(actions.REASON_SPAWN_FAILED, detail=str(exc))

    if not isinstance(result, dict):
        return actions.fail(actions.REASON_SPAWN_FAILED,
                            detail="launcher returned no value")
    if result.get("ok") and cfg is not None:
        template_id = (_as_text(template.get("id")) if isinstance(template, dict)
                       else _as_text(getattr(template, "id", "")))
        remember(cfg, directory=work_dir, agent_id=template_id)
    return result


def row_appears_within(snapshot: Dict[str, Any], pid: int, tick_s: int) -> bool:
    """Did the launched pid reach the published snapshot inside the bound?

    The bound is `tick_s + 1 s` (plan: every latency check is derived from the
    tick). This is a value check, not a sleep: the caller publishes a snapshot
    and asks whether the row is there.
    """
    for row in (snapshot or {}).get("rows") or []:
        if int(row.get("pid") or 0) == int(pid):
            return True
    return False


def latency_bound_s(tick_s: int) -> int:
    """The one latency bound the plan allows: `tick_s + 1 s`."""
    try:
        return max(1, int(tick_s)) + 1
    except (TypeError, ValueError):
        return config_mod.DEFAULT_TICK_S + 1


# --- the dialog -----------------------------------------------------------

class LauncherDialog:
    """The Tk dialog: agent list with availability, directory, Start.

    Thin on purpose: every decision (availability, fix text, argv, hint) comes
    from the pure functions above, so the dialog only lays out widgets and
    forwards the click.
    """

    def __init__(self, root: Any, cfg: config_mod.Config,
                 on_start: Optional[Callable[[str, str], Dict[str, Any]]] = None,
                 which: Optional[Callable[[str], Optional[str]]] = None) -> None:
        import tkinter as tk

        from .ui import theme, type as type_mod

        self.root = root
        self.cfg = cfg
        self.on_start = on_start
        self.checked = precheck(which=which)
        self.result: Optional[Dict[str, Any]] = None

        self.window = tk.Toplevel(root)
        self.window.title("New session")
        self.window.configure(bg=theme.role("bg"))

        self.dir_var = tk.StringVar(value=default_directory(cfg))
        self.agent_var = tk.StringVar(value=default_agent(cfg, self.checked))

        tk.Label(self.window, text="Directory", anchor="w",
                 bg=theme.role("bg"), fg=theme.role("fg"),
                 font=type_mod.as_tk(type_mod.META)).pack(fill="x", padx=8, pady=(8, 0))
        row = tk.Frame(self.window, bg=theme.role("bg"))
        row.pack(fill="x", padx=8)
        tk.Entry(row, textvariable=self.dir_var,
                 bg=theme.role("bg_row"), fg=theme.role("fg"),
                 font=type_mod.as_tk(type_mod.MONO)).pack(side="left", fill="x",
                                                          expand=True)
        tk.Button(row, text="Browse...", command=self._browse).pack(side="left", padx=4)

        tk.Label(self.window, text="Start", anchor="w",
                 bg=theme.role("bg"), fg=theme.role("fg"),
                 font=type_mod.as_tk(type_mod.META)).pack(fill="x", padx=8, pady=(8, 0))

        self.agent_buttons: List[Any] = []
        for entry in self.checked:
            available = bool(entry["available"])
            text = entry["label"] if available else "%s  %s" % (entry["label"],
                                                                entry["fix_text"])
            button = tk.Radiobutton(
                self.window, text=text, value=entry["id"], variable=self.agent_var,
                anchor="w", justify="left",
                bg=theme.role("bg"), fg=theme.role("fg" if available else "fg_dim"),
                selectcolor=theme.role("bg_selected"),
                activebackground=theme.role("bg"),
                font=type_mod.as_tk(type_mod.META),
                state="normal" if available else "disabled")
            button.pack(fill="x", padx=8)
            self.agent_buttons.append(button)

        buttons = tk.Frame(self.window, bg=theme.role("bg"))
        buttons.pack(fill="x", padx=8, pady=8)
        tk.Button(buttons, text="Cancel", command=self.close).pack(side="right")
        self.start_button = tk.Button(buttons, text="Start", command=self._start)
        self.start_button.pack(side="right", padx=4)

    def _browse(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(initialdir=self.dir_var.get() or None)
        if chosen:
            self.dir_var.set(chosen)

    def _start(self) -> None:
        directory = self.dir_var.get().strip()
        agent_id = self.agent_var.get().strip()
        if self.on_start is not None:
            self.result = self.on_start(agent_id, directory)
        self.close()

    def close(self) -> None:
        try:
            self.window.destroy()
        except Exception:
            pass
