# Measured on the author's machine

Facts measured **here**, recorded so nobody re-measures them by accident and so a
future session does not turn a local observation into a global conclusion.
Nothing in this file is a requirement - those are in [AGENTS.md](../AGENTS.md).

| Fact | Value | Consequence, here |
|---|---|---|
| OS | Windows 11, build 26300, 64-bit, real desktop (no RDP, no CI) | Qt windows, ConPTY and the tray all work |
| Python | 3.13 via `py -3`; **`python` is the Microsoft Store stub** | every command in this repository is written `py -3` |
| Default PyPI index | serves **pure-Python wheels only** (`psutil`, `PySide6`, `pywinpty` all "not found") | never conclude a package is impossible from it - that mistake cost a whole design pivot; add `-i <mirror>` instead |
| Mirrors | `https://mirrors.aliyun.com/pypi/simple/` has the win_amd64 wheels; USTC works; Tsinghua times out **here** | the README offers the mirror as an option for readers who need it, never as a rule |
| WebView2 runtime | installed, 156.x | an alternative renderer path exists if Qt is ever dropped |
| Windows Terminal | `wt.exe` present | an external-window fallback is available |
| Build size | `dist\ostlr\` came out at 610 MB, ~1 s start (a clean-venv build) | the README rounds it to ~610 MB |

## Building the exe

The build must run in a **clean venv**, not the global interpreter. Measured
here the hard way: building from the shared global Python collected
`numpy`/`pytest`/`setuptools` hooks and shipped numpy inside a 621 MB bundle the
panel never imports. `tools/build_exe.py` now refuses to build from a polluted
interpreter (it detects venv and a few known-pollution packages) and prints the
fix; `--allow-dirty` is the explicit override.

```
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python tools\build_exe.py
```

## What this file is not

- Not a support matrix: a different Windows build, Python version or WebView2
  release is not a bug report.
- Not a reason to keep a mirror in `requirements.txt`: the file names the mirror
  in a comment only, so a reader outside China gets their own index.
