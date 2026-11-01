"""The Tk panel - the shell that draws a published snapshot.

Three rules shape this file:

  * **Every string comes from `view_model`.** The view owns no label, no number
    format, no receipt text and no `Ready`. A literal here would let the panel
    and `--once --json` disagree about the same field, which is the one thing
    the product cannot afford.
  * **The UI never probes.** It renders the last published snapshot and nothing
    else: no CIM, no git, no taskkill. The sampler thread publishes; the UI
    reads. That is why a wedged WMI repository cannot freeze the window.
  * **Selection never steals focus.** A single click or an arrow key only
    selects; Enter or a double-click is the only path to `SetForegroundWindow`.
    Reading a row's hint must never throw the user out of the panel.

The reminder (T16) is degraded on purpose: the standard library cannot draw a
tray icon, so a blocked session shows as a window-title prefix, a header
counter and one bell per blocked *transition* - never one per tick.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .. import config as config_mod
from .. import view_model
from . import glyphs, rows as ui_rows, theme, type as type_mod

# --- DPI ------------------------------------------------------------------

# Must run BEFORE Tk() is constructed, or every font renders blurry at 125%.
_DPI_DONE = False


def set_dpi_awareness() -> str:
    """Make the process DPI-aware. Returns what was applied, for the selftest.

    Tries the per-monitor API first (Win 8.1+), then the system-DPI one, then
    the user32 fallback. A failure is not fatal: the panel still runs, just
    blurrier, and the returned string says which path was taken.
    """
    global _DPI_DONE
    if _DPI_DONE:
        return "already"
    if not sys.platform.startswith("win"):
        _DPI_DONE = True
        return "skipped (not windows)"
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        _DPI_DONE = True
        return "shcore per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        _DPI_DONE = True
        return "user32 system"
    except Exception:
        _DPI_DONE = True
        return "unavailable"


# --- the reminder (T16) ---------------------------------------------------

class BlockedReminder:
    """One bell per blocked *transition*, plus the title prefix.

    The edge detector is the whole point: a session that stays blocked for ten
    minutes must ring once, not six hundred times. `update()` is pure - it takes
    the header and returns what the title should say - so the sequence is
    testable without a window.
    """

    def __init__(self) -> None:
        self._was_blocked = False
        self.bells = 0

    def update(self, header: Dict[str, Any]) -> Tuple[str, bool]:
        """Return (title prefix, rang_now) for this header.

        The prefix is `! N waiting` while N > 0 and empty once it resolves.
        `rang_now` is True only on the 0 -> N edge.
        """
        waiting = _int((header or {}).get("waiting"))
        blocked = waiting > 0
        rang = blocked and not self._was_blocked
        if rang:
            self.bells += 1
        self._was_blocked = blocked
        return (title_prefix(waiting), rang)

    def reset(self) -> None:
        self._was_blocked = False
        self.bells = 0


def title_prefix(waiting: int) -> str:
    """`! N waiting`, or "" when nothing is waiting."""
    count = _int(waiting)
    if count <= 0:
        return ""
    return "! %d waiting" % count


def compose_title(prefix: str, base: str) -> str:
    """The window title: the prefix, then the app name."""
    if prefix:
        return "%s - %s" % (prefix, base)
    return base


# --- helpers --------------------------------------------------------------

def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _measure(text: str) -> int:
    """A cheap width estimate for elision: one unit per character.

    The real pixel width needs a Tk font object, which does not exist headless;
    the estimate keeps `render_row` pure and is only used to decide where to cut.
    """
    return len(text or "")


def tk_available() -> Tuple[bool, str]:
    """Can a Tk window be created here? Returns (ok, reason)."""
    try:
        import tkinter as tk
    except Exception as exc:  # pragma: no cover - only on a broken install
        return False, "tkinter import failed: %s" % exc
    try:
        root = tk.Tk()
    except Exception as exc:
        return False, "Tk() failed: %s" % exc
    try:
        root.destroy()
    except Exception:
        pass
    return True, ""


def window_title() -> str:
    """Read the foreground window's title back (T16's live check).

    Uses GetWindowText through ctypes so the check does not depend on Tk's
    internal state. Returns "" when there is no display.
    """
    if not sys.platform.startswith("win"):
        return ""
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        buf = ctypes.create_unicode_buffer(512)
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        user32.GetWindowTextW(hwnd, buf, 512)
        return buf.value
    except Exception:
        return ""


# --- the app --------------------------------------------------------------

class PanelApp:
    """The panel window.

    Constructed with a snapshot provider so a test can drive it without a
    sampler thread: `provider()` returns the last published envelope.
    """

    def __init__(self, cfg: Optional[config_mod.Config] = None,
                 provider: Optional[Callable[[], Dict[str, Any]]] = None,
                 focus_fn: Optional[Callable[[int], Dict[str, Any]]] = None,
                 bell_fn: Optional[Callable[[], None]] = None,
                 root: Any = None,
                 clipboard: Any = None,
                 launch_fn: Optional[Callable[..., Dict[str, Any]]] = None) -> None:
        self.cfg = cfg or config_mod.Config()
        self._provider = provider or (lambda: {"v": 1, "header": {}, "rows": []})
        self._focus_fn = focus_fn
        self._bell_fn = bell_fn
        self._clipboard = clipboard
        self._launch_fn = launch_fn
        self._copy_gate = None
        self.reminder = BlockedReminder()
        self.snapshot: Dict[str, Any] = {"v": 1, "header": {}, "rows": []}
        self.selected_pid: Optional[int] = None
        self.receipt_text = ""
        self.receipt_at = 0
        self.starting_pids: List[int] = []
        self.last_launch_error = ""
        self.focus_calls: List[int] = []

        self._after_id: Optional[str] = None
        self._row_widgets: List[Any] = []
        self._closed = False

        self.root = root if root is not None else self._make_root()
        self._build()

    # --- construction -------------------------------------------------
    def _make_root(self):
        set_dpi_awareness()
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title(compose_title("", "Ostlr"))
        root.geometry("%dx%d" % ui_rows.DEFAULT_WINDOW)
        root.minsize(*ui_rows.MIN_WINDOW)
        root.configure(bg=theme.role("bg"))
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        return root

    def _build(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = self.root
        self.header_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value=view_model.READY)
        self.footer_var = tk.StringVar(value="")

        outer = tk.Frame(root, bg=theme.role("bg"))
        outer.pack(fill="both", expand=True)

        # --- the toolbar -------------------------------------------------
        # Every action the panel can perform has a visible control here. The
        # first build had none: the launcher existed and nothing opened it, so
        # the product could not start a session at all.
        self.toolbar = tk.Frame(outer, bg=theme.role("bg"))
        self.toolbar.pack(fill="x", padx=8, pady=(8, 2))
        self.buttons: Dict[str, Any] = {}
        for key, label, command in (
            ("start", "+  Start a session", self.open_launcher),
            ("refresh", "Refresh", self.refresh_now),
            ("copy", "Copy path", self.copy_selected_path),
            ("help", "Help", self.show_help),
        ):
            button = tk.Button(
                self.toolbar, text=label, command=command, relief="flat",
                bg=theme.role("bg_row"), fg=theme.role("fg"),
                activebackground=theme.role("border"),
                activeforeground=theme.role("fg"),
                highlightthickness=0, padx=10, pady=3, cursor="hand2",
                font=type_mod.as_tk(type_mod.META))
            button.pack(side="left", padx=(0, 6))
            self.buttons[key] = button

        self.header_label = tk.Label(
            outer, textvariable=self.header_var, anchor="w",
            bg=theme.role("bg"), fg=theme.role("fg"),
            font=type_mod.as_tk(type_mod.META))
        self.header_label.pack(fill="x", padx=8, pady=(6, 2))

        self.banner_label = tk.Label(
            outer, text="", anchor="w",
            bg=theme.role("bg"), fg=theme.role("warn"),
            font=type_mod.as_tk(type_mod.META))
        self.banner_label.pack(fill="x", padx=8)

        body = tk.Frame(outer, bg=theme.role("bg"))
        body.pack(fill="both", expand=True, padx=8, pady=4)

        self.canvas = tk.Canvas(body, bg=theme.role("bg"), highlightthickness=0,
                                width=ui_rows.LIST_COLUMN_PX)
        self.scroll = ttk.Scrollbar(body, orient="vertical",
                                    command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")

        self.list_frame = tk.Frame(self.canvas, bg=theme.role("bg"))
        self._list_window = self.canvas.create_window(
            (0, 0), window=self.list_frame, anchor="nw")

        # --- the detail column -------------------------------------------
        # The right half used to be empty black space. It now always answers
        # three questions: what a state means, what the selected row is, and
        # what the panel can do.
        self.detail = tk.Frame(body, bg=theme.role("bg"),
                               width=ui_rows.DETAIL_COLUMN_PX)
        self.detail.pack(side="right", fill="y", padx=(10, 0))
        self.detail.pack_propagate(False)
        self.legend_label = tk.Label(
            self.detail, text="\n".join(view_model.legend_lines()), anchor="nw",
            justify="left", bg=theme.role("bg"), fg=theme.role("fg_dim"),
            font=type_mod.as_tk(type_mod.META))
        self.legend_label.pack(fill="x", pady=(0, 8))
        self.detail_label = tk.Label(
            self.detail, text="", anchor="nw", justify="left",
            bg=theme.role("bg"), fg=theme.role("fg"),
            font=type_mod.as_tk(type_mod.MONO))
        self.detail_label.pack(fill="both", expand=True)

        self.empty_frame = tk.Frame(outer, bg=theme.role("bg"))
        self.empty_labels: List[Any] = []
        for index in range(3):
            spec = type_mod.TITLE if index == 0 else (
                type_mod.MONO if index == 2 else type_mod.META)
            role = "fg" if index == 0 else "fg_dim"
            label = tk.Label(self.empty_frame, text="", anchor="w",
                             bg=theme.role("bg"), fg=theme.role(role),
                             font=type_mod.as_tk(spec))
            label.pack(fill="x", padx=8, pady=1)
            self.empty_labels.append(label)

        # A user with no sessions gets one button, in place, not a sentence
        # telling them to run a command.
        self.empty_button = tk.Button(
            self.empty_frame, text="+  Start a session", command=self.open_launcher,
            relief="flat", bg=theme.role("bg_row"), fg=theme.role("fg"),
            activebackground=theme.role("border"), activeforeground=theme.role("fg"),
            highlightthickness=0, padx=10, pady=3, cursor="hand2",
            font=type_mod.as_tk(type_mod.META))
        self.empty_button.pack(anchor="w", padx=8, pady=(8, 2))

        self.footer_label = tk.Label(
            outer, textvariable=self.footer_var, anchor="w",
            bg=theme.role("bg"), fg=theme.role("fg_dim"),
            font=type_mod.as_tk(type_mod.META))
        self.footer_label.pack(fill="x", padx=8)

        self.status_label = tk.Label(
            outer, textvariable=self.status_var, anchor="w",
            bg=theme.role("bg_row"), fg=theme.role("fg_dim"),
            font=type_mod.as_tk(type_mod.META))
        self.status_label.pack(fill="x", padx=0, pady=(2, 0))

        # Gestures: select only. Focus is Enter / double-click, never a click.
        self.canvas.bind("<Button-1>", self._on_click)
        root.bind("<Up>", lambda _e: self.select_relative(-1))
        root.bind("<Down>", lambda _e: self.select_relative(1))
        root.bind("<Return>", lambda _e: self.focus_selected())
        root.bind("<Double-Button-1>", lambda _e: self.focus_selected())
        # The two chords (T14). Ctrl+Shift+C copies the selected row's path,
        # Ctrl+Shift+R copies its hint. Both go through the ONE ctypes clipboard
        # and both produce a view_model receipt - the view owns no string.
        root.bind("<Control-Shift-C>", lambda _e: self.copy_selected_path())
        root.bind("<Control-Shift-R>", lambda _e: self.copy_selected_hint())
    # --- rendering ----------------------------------------------------
    def render(self, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Draw one published snapshot. Returns what was drawn (for tests)."""
        if snapshot is not None:
            self.snapshot = snapshot
        else:
            self.snapshot = self._provider() or {"v": 1, "header": {}, "rows": []}

        header = self.snapshot.get("header") or {}
        rows = self.snapshot.get("rows") or []

        self.header_var.set(self.header_text(header))
        self._render_rows(rows)
        self.footer_var.set(self.snapshot.get("footer") or "")
        self._render_empty(rows)
        self._render_banner()
        self._render_status(rows)
        self._render_detail(rows)
        self._update_title(header)
        return {"header": self.header_var.get(), "footer": self.footer_var.get(),
                "status": self.status_var.get(), "rows": len(rows)}

    def header_text(self, header: Dict[str, Any]) -> str:
        """The header line: fixed slots first, then non-zero buckets, total last.

        `waiting`, `running` and `unknown` always occupy a slot (rendered at 0),
        so the numbers do not jump around once per second. The rest appear only
        when non-zero, in rank order, and the total is always last. One line,
        never wrapped.
        """
        parts: List[str] = []
        for key in ("waiting", "running", "unknown"):
            parts.append("%s %d" % (key, _int(header.get(key))))
        for key in ("error", "done", "idle", "starting"):
            value = _int(header.get(key))
            if value:
                parts.append("%s %d" % (key, value))
        parts.append("sessions %d" % _int(header.get("sessions")))
        return "  ".join(parts)

    def _render_rows(self, rows: Sequence[Dict[str, Any]]) -> None:
        import tkinter as tk

        for widget in self._row_widgets:
            widget.destroy()
        self._row_widgets = []

        dropped = set(ui_rows.drop_columns(self._width()))
        for row in rows:
            frame = tk.Frame(self.list_frame, bg=theme.role("bg_row"))
            frame.pack(fill="x", pady=1)
            rendered = ui_rows.render_row(row, ui_rows.LIST_COLUMN_PX,
                                          _measure, mono_linespace=16)
            role = rendered.get("glyph_role") or "fg"
            line1 = " ".join(rendered.get("line1") or [])
            line2_parts = list(rendered.get("line2") or [])
            if "mem" in dropped or "branch" in dropped:
                # Below the breakpoint the optional columns are dropped whole,
                # never squeezed: the state column keeps its width.
                line2_parts = line2_parts[:2]
            line2 = " ".join(line2_parts)
            label1 = tk.Label(frame, text=line1, anchor="w",
                              bg=theme.role("bg_row"), fg=theme.role(role),
                              font=type_mod.as_tk(type_mod.ROW))
            label1.pack(fill="x", padx=6)
            label2 = tk.Label(frame, text=line2, anchor="w",
                              bg=theme.role("bg_row"), fg=theme.role("fg_dim"),
                              font=type_mod.as_tk(type_mod.META))
            label2.pack(fill="x", padx=6)
            pid = _int(row.get("pid"))
            for widget in (frame, label1, label2):
                widget.bind("<Button-1>", lambda _e, p=pid: self.select(p))
                widget.bind("<Double-Button-1>", lambda _e, p=pid: self.focus(p))
            self._row_widgets.append(frame)

        self.list_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _render_empty(self, rows: Sequence[Dict[str, Any]]) -> None:
        if rows:
            self.empty_frame.pack_forget()
            return
        lines = view_model.empty_state_lines(0, 0)
        for label, text in zip(self.empty_labels, lines):
            label.configure(text=text)
        self.empty_frame.pack(fill="x", pady=8)

    def _render_banner(self) -> None:
        # The banner states a degraded source, never a guess. The sampler
        # reports a stale CIM sample through the snapshot's `stale` key; when
        # nothing has a state at all, say why instead of leaving a grey wall.
        stale = self.snapshot.get("stale") or ""
        if stale:
            self.banner_label.configure(text=stale)
            return
        header = self.snapshot.get("header") or {}
        rows = self.snapshot.get("rows") or []
        producers = sum(1 for row in rows if (row.get("source") or "") == "l2")
        self.banner_label.configure(
            text=view_model.unknown_banner(_int(header.get("sessions")), len(rows), producers))

    def _render_detail(self, rows: Sequence[Dict[str, Any]]) -> None:
        """The selected row, spelled out. Answers "what did I just click"."""
        selected = None
        for row in rows:
            if _int(row.get("pid")) == self.selected_pid:
                selected = row
                break
        self.detail_label.configure(text="\n".join(view_model.detail_lines(selected)))

    def refresh_now(self) -> Any:
        """Toolbar Refresh: publish a new snapshot now instead of waiting."""
        try:
            self.render()
        except Exception as exc:  # a probe failure must not kill the tick
            self._set_receipt("refresh failed: %s" % exc)
        return self.snapshot

    def show_help(self) -> Any:
        """Toolbar Help: what this window can do, in plain words."""
        import tkinter.messagebox as messagebox

        try:
            messagebox.showinfo("Ostlr", view_model.help_text(), parent=self.root)
        except Exception:
            return {"ok": False, "reason": "help_unavailable"}
        return {"ok": True}

    def _render_status(self, rows: Sequence[Dict[str, Any]]) -> None:
        hint = ""
        for row in rows:
            if _int(row.get("pid")) == self.selected_pid:
                hint = row.get("hint") or ""
                break
        age = None
        if self.receipt_at:
            age = max(0, int(time.time()) - self.receipt_at)
        self.status_var.set(view_model.status_bar(self.receipt_text, age, hint))

    def _update_title(self, header: Dict[str, Any]) -> None:
        prefix, rang = self.reminder.update(header)
        try:
            self.root.title(compose_title(prefix, "Ostlr"))
        except Exception:
            # No display / window already destroyed: the reminder degrades to
            # the header counter, it must never crash the tick.
            pass
        if rang and self._bell_fn is not None:
            try:
                self._bell_fn()
            except Exception:
                pass

    def _width(self) -> int:
        try:
            return int(self.root.winfo_width())
        except Exception:
            return ui_rows.MIN_WINDOW[0]

    # --- gestures -----------------------------------------------------
    def select(self, pid: int) -> None:
        """Select a row. Never touches Windows focus."""
        self.selected_pid = _int(pid)
        self._render_status(self.snapshot.get("rows") or [])

    def select_relative(self, delta: int) -> None:
        rows = self.snapshot.get("rows") or []
        if not rows:
            return
        pids = [_int(r.get("pid")) for r in rows]
        if self.selected_pid in pids:
            index = pids.index(self.selected_pid) + delta
        else:
            index = 0
        index = max(0, min(len(pids) - 1, index))
        self.select(pids[index])

    def focus(self, pid: int) -> Dict[str, Any]:
        """Focus a session's window. The ONLY path to SetForegroundWindow."""
        self.focus_calls.append(_int(pid))
        if self._focus_fn is None:
            return {"ok": False, "reason": "no focus backend"}
        return self._focus_fn(_int(pid))

    def focus_selected(self) -> Dict[str, Any]:
        if self.selected_pid is None:
            return {"ok": False, "reason": "no selection"}
        return self.focus(self.selected_pid)

    # --- the two chords (T14) -----------------------------------------
    def selected_row(self) -> Optional[Dict[str, Any]]:
        """The row the user has selected, or None."""
        for row in self.snapshot.get("rows") or []:
            if _int(row.get("pid")) == self.selected_pid:
                return row
        return None

    def _set_receipt(self, text: str) -> None:
        self.receipt_text = text
        self.receipt_at = int(time.time())
        self._render_status(self.snapshot.get("rows") or [])

    def copy_selected_path(self) -> Dict[str, Any]:
        """Ctrl+Shift+C: copy the selected row's absolute path.

        The receipt names the target and whether it is the same project, because
        pasting into the wrong window is the most expensive mistake here.
        """
        from .. import actions

        row = self.selected_row()
        if row is None:
            return {"ok": False, "reason": "no selection"}
        # "Same project" is judged against the project the panel is showing,
        # which is the selected row's own project - so a copy from the panel is
        # always same-project. A cross-project receipt comes from a caller that
        # passes a different current project (the launcher's target pane).
        current = row.get("project") or ""
        result = actions.copy_path(row.get("cwd") or "", row, current,
                                   clipboard=self._clipboard, gate=self._copy_gate)
        if result.get("receipt"):
            self._set_receipt(result["receipt"])
        return result

    def copy_selected_hint(self) -> Dict[str, Any]:
        """Ctrl+Shift+R: copy the selected row's hint.

        The hint is the user's route from `unknown` to a real state, so it must
        be copyable - otherwise the panel tells them to run a command they have
        to retype.

        It goes through the SAME gate and receipt machinery as the path copy:
        one mechanism, one dedupe window, one receipt shape. Writing the
        clipboard directly here would give the hint no dedupe and no
        `copied ... -> agent / project` receipt, which is exactly the
        inconsistency the verifier found.
        """
        from .. import actions

        row = self.selected_row()
        if row is None:
            return {"ok": False, "reason": "no selection"}
        hint = row.get("hint") or ""
        if not hint:
            return {"ok": False, "reason": "no hint"}
        result = actions.copy_text(hint, row, row.get("project") or "",
                                   clipboard=self._clipboard,
                                   gate=self._copy_gate, kind="copied hint")
        if result.get("receipt"):
            self._set_receipt(result["receipt"])
        return result
    def _on_click(self, event) -> None:
        # A click on the canvas background only clears the selection; it never
        # focuses anything.
        self.selected_pid = None
        self._render_status(self.snapshot.get("rows") or [])

    # --- the launcher (T14) -------------------------------------------
    def open_launcher(self, which: Any = None) -> Any:
        """Open the New-session dialog. Returns the dialog (or None).

        The dialog is a thin shell over `launcher.precheck` / `command_for` /
        `hint_for`; this method only wires its Start button to `launch_agent`.
        """
        from .. import launcher as launcher_mod

        def _on_start(agent_id: str, directory: str) -> Dict[str, Any]:
            return self.launch_agent(agent_id, directory)

        try:
            return launcher_mod.LauncherDialog(self.root, self.cfg,
                                               on_start=_on_start, which=which)
        except Exception as exc:
            # No display / Tk refused: a value, never a crash.
            return {"ok": False, "reason": "launcher_unavailable", "detail": str(exc)}

    def launch_agent(self, agent_id: str, directory: str) -> Dict[str, Any]:
        """Start an agent in `directory` and publish a `starting` row.

        A failure turns that row red with the reason inline - never a modal,
        never a silent no-op.
        """
        from .. import agents as agents_mod
        from .. import launcher as launcher_mod

        template = None
        for candidate in agents_mod.TEMPLATES:
            if candidate.id == agent_id:
                template = candidate
                break
        if template is None:
            return {"ok": False, "reason": "unknown_agent", "agent": agent_id}

        result = launcher_mod.start(template, directory, cfg=self.cfg,
                                    launch_fn=self._launch_fn)
        if result.get("ok"):
            pid = _int(result.get("pid"))
            self.starting_pids.append(pid)
            self._set_receipt(launcher_mod.hint_for(template))
        else:
            self.last_launch_error = result.get("reason") or "spawn_failed"
            self._set_receipt(self.last_launch_error)
        return result

    # --- the tick -----------------------------------------------------
    def tick(self) -> None:
        """One UI tick: re-render the last published snapshot."""
        if self._closed:
            return
        self.render()
        self._schedule()

    def _schedule(self) -> None:
        if self._closed:
            return
        try:
            self._after_id = self.root.after(1000, self.tick)
        except Exception:
            self._after_id = None

    def start(self) -> None:
        """Render once and start the 1 s tick. Does not enter mainloop."""
        self.render()
        self._schedule()

    def close(self) -> None:
        """Cancel the tick and destroy the window. Safe to call twice."""
        self._closed = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        """The real entry point: start the tick and enter the mainloop."""
        self.start()
        try:
            self.root.mainloop()
        finally:
            self.close()


def run_panel(cfg: Optional[config_mod.Config] = None) -> int:
    """`py -3 -m ostlr` with no arguments: open the panel and block on it."""
    from .. import __main__ as cli

    cfg = cfg or config_mod.Config()
    app = PanelApp(cfg, provider=lambda: cli.build_snapshot(cfg))
    app.run()
    return 0
