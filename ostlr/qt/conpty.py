"""ConPTY, owned directly with ctypes.

Why not pywinpty: its `read()` goes through an internal pump thread that does
`time.sleep(1e-3)` per chunk and hands the bytes over a **socket**, and its read
reassembles UTF-8 **one byte at a time**. A TUI that repaints continuously hits
that ceiling - the reported "refresh rate is very low". Its `setwinsize()` goes
through `pty.set_size()` as well, and the symptom of a resize that never reaches
the child is a frame drawn for the old grid.

So this module does the three things we need and nothing else:

    create pipes -> CreatePseudoConsole -> CreateProcess with the pseudo console
    attached as a thread attribute -> a reader thread doing blocking ReadFile

Blocking ReadFile releases the GIL, so a fast child is limited by the pipe, not by
Python. Resize is `ResizePseudoConsole`, the real call.
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
from ctypes import wintypes
from typing import List, Optional

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
STILL_ACTIVE = 259


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


kernel32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE),
                                ctypes.POINTER(wintypes.HANDLE),
                                ctypes.POINTER(SECURITY_ATTRIBUTES), wintypes.DWORD]
kernel32.CreatePseudoConsole.argtypes = [COORD, wintypes.HANDLE, wintypes.HANDLE,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
kernel32.CreatePseudoConsole.restype = ctypes.c_long
kernel32.ResizePseudoConsole.argtypes = [wintypes.HANDLE, COORD]
kernel32.ResizePseudoConsole.restype = ctypes.c_long
kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
kernel32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
    ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION)]
kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]


class ConPtyError(RuntimeError):
    pass


def _pipe():
    read_end = wintypes.HANDLE()
    write_end = wintypes.HANDLE()
    attrs = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    if not kernel32.CreatePipe(ctypes.byref(read_end), ctypes.byref(write_end),
                               ctypes.byref(attrs), 0):
        raise ConPtyError("CreatePipe failed: %s" % ctypes.get_last_error())
    return read_end, write_end


class ConPty:
    """One pseudo console with one child in it."""

    def __init__(self, argv: List[str], cwd: Optional[str] = None,
                 cols: int = 100, rows: int = 30) -> None:
        self.argv = [str(a) for a in argv]
        self.cwd = cwd
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.pid = 0
        self.exit_code: Optional[int] = None

        self._hpc = wintypes.HANDLE()
        self._process = wintypes.HANDLE()
        self._thread = wintypes.HANDLE()
        self._in_write = wintypes.HANDLE()
        self._out_read = wintypes.HANDLE()
        self._closed = False
        self._queue: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None

    # --- lifecycle ----------------------------------------------------
    def start(self) -> int:
        in_read, in_write = _pipe()
        out_read, out_write = _pipe()

        hr = kernel32.CreatePseudoConsole(COORD(self.cols, self.rows),
                                          in_read, out_write, 0,
                                          ctypes.byref(self._hpc))
        if hr != 0:
            raise ConPtyError("CreatePseudoConsole failed: 0x%08x" % (hr & 0xFFFFFFFF))
        kernel32.CloseHandle(in_read)    # the pseudo console owns these ends now
        kernel32.CloseHandle(out_write)

        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attrs = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
            raise ConPtyError("InitializeProcThreadAttributeList failed")

        hpc_value = ctypes.cast(self._hpc, ctypes.c_void_p)
        if not kernel32.UpdateProcThreadAttribute(
                attrs, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, hpc_value,
                ctypes.sizeof(wintypes.HANDLE), None, None):
            raise ConPtyError("UpdateProcThreadAttribute failed")

        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
        pi = PROCESS_INFORMATION()

        command_line = ctypes.create_unicode_buffer(" ".join(self.argv))
        work_dir = ctypes.c_wchar_p(self.cwd) if self.cwd else None
        created = kernel32.CreateProcessW(
            None, command_line, None, None, False, EXTENDED_STARTUPINFO_PRESENT,
            None, work_dir, ctypes.byref(si), ctypes.byref(pi))
        kernel32.DeleteProcThreadAttributeList(attrs)
        if not created:
            kernel32.CloseHandle(in_write)
            kernel32.CloseHandle(out_read)
            raise ConPtyError("CreateProcessW failed: %s" % ctypes.get_last_error())

        self._process = pi.hProcess
        self._thread = pi.hThread
        self.pid = int(pi.dwProcessId)
        self._in_write = in_write
        self._out_read = out_read

        self._reader = threading.Thread(target=self._pump, name="conpty-read",
                                        daemon=True)
        self._reader.start()
        return self.pid

    def _pump(self) -> None:
        """Blocking ReadFile; the GIL is released for the duration of the call."""
        buffer = ctypes.create_string_buffer(16384)
        read = wintypes.DWORD(0)
        while True:
            ok = kernel32.ReadFile(self._out_read, buffer, 16384,
                                   ctypes.byref(read), None)
            if not ok or read.value == 0:
                break
            self._queue.put(bytes(buffer.raw[:read.value]))
        self._queue.put(None)   # EOF marker

    # --- io -----------------------------------------------------------
    def read(self, timeout: float = 0.05) -> Optional[bytes]:
        """Data that arrived, b"" on timeout, None at EOF."""
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return b""
        return item

    def write(self, data) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not data or self._closed or not self._in_write:
            return
        written = wintypes.DWORD(0)
        kernel32.WriteFile(self._in_write, data, len(data), ctypes.byref(written), None)

    def resize(self, cols: int, rows: int) -> bool:
        """The real ResizePseudoConsole: this is what the child observes."""
        cols, rows = max(1, int(cols)), max(1, int(rows))
        if (cols, rows) == (self.cols, self.rows) or not self._hpc:
            return False
        hr = kernel32.ResizePseudoConsole(self._hpc, COORD(cols, rows))
        if hr != 0:
            return False
        self.cols, self.rows = cols, rows
        return True

    def alive(self) -> bool:
        if not self._process:
            return False
        code = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess(self._process, ctypes.byref(code))
        return int(code.value) == STILL_ACTIVE

    def wait_exit(self, timeout_ms: int = 5000) -> Optional[int]:
        if not self._process:
            return None
        kernel32.WaitForSingleObject(self._process, timeout_ms)
        code = wintypes.DWORD(0)
        if kernel32.GetExitCodeProcess(self._process, ctypes.byref(code)):
            self.exit_code = int(code.value)
        return self.exit_code

    def terminate(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process:
            kernel32.TerminateProcess(self._process, 1)
        self._close_handles()

    def _close_handles(self) -> None:
        for name in ("_in_write", "_out_read"):
            handle = getattr(self, name)
            if handle:
                kernel32.CloseHandle(handle)
                setattr(self, name, wintypes.HANDLE())
        if self._hpc:
            kernel32.ClosePseudoConsole(self._hpc)
            self._hpc = wintypes.HANDLE()
        for name in ("_process", "_thread"):
            handle = getattr(self, name)
            if handle:
                kernel32.CloseHandle(handle)
                setattr(self, name, wintypes.HANDLE())

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            self._close_handles()
        except Exception:
            pass


def available() -> bool:
    """True when kernel32 exposes the pseudo console API (Windows 10 1809+)."""
    return os.name == "nt" and hasattr(kernel32, "CreatePseudoConsole")
