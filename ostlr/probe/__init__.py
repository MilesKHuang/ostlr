"""Probes: the only modules that talk to the operating system.

`l1` (the OSC 7501 stream we read out of our own PTY), `l2` (status files).
Everything else in `ostlr` is pure and testable headless.

The process probe (`cim`), the window-title classifier (`l3`), the tree walker
(`procs`) and the git branch lookup are gone with the machine scan: the panel
only describes panes it owns, and `psutil` covers the little it still needs
(creation time, CPU seconds, memory).
"""
