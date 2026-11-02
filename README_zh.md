# Ostlr

[English](README.md) | **中文**

一台机器上跑着的 AI agent 会话，一个窗口管住。

![Ostlr：agent 的会话跑在 pane 里，状态在列表里，目录在树里](docs/ostlr.gif)

## 三个核心价值

- **多个 TUI 的运行状态与资源占用，一目了然。** 每个会话一行：Harness 状态，加上 CPU / 内存 / GPU 占用。
- **多个 TUI 的启动、切换与关闭，一手掌握。** 最多 5 个 pane；TUI 进程在这里起、也在这里收，不会留下僵尸。
- **文件、图片与目录，右键就能送进会话。** 文件树跟着选中的会话走；右键把路径（`路径` 或 `@路径`）直接送进当前 pane。

`blocked` 需要有人报：proJV、pi、Claude Code、Crush、opencode 会自己报；其余的行写 `unknown`，不会画成 `idle`。

## 三步用起来

1. **装依赖**：
   ```
   py -3 -m pip install -r requirements.txt
   ```
   在国内，或 `PySide6` / `pywinpty` 报 "not found" 时，加一个镜像：
   `-i https://mirrors.aliyun.com/pypi/simple/`。
2. **开面板**：
   ```
   py -3 -m ostlr
   ```
3. **把 Harness CLI 加进面板**：`+ New session` -> `Browse...` -> 选 exe。会自动写进 `config.toml` 的一条 `[[agents]]`。

## 更多

| Bonus | 是什么 |
|---|---|
| **状态由 TUI/CLI 自己说** | OSC 7501（[Program Status Protocol](https://www.superlogical.com/rex/docs/build/program-status)，rev 0.3）**已通**：[pi](https://github.com/earendil-works/pi) 已验证支持。文件格式与包装器：[status-levels](docs/producers/status-levels.md) |
| **GUI 与原生体验一致** | Ostlr 里跑的 TUI 和原生渲染一致 —— [parity](docs/parity.md) |
| **脚本可用** | `py -3 -m ostlr.status --list` 看程序此刻在报什么（`--prune` 清掉已消失的进程）；`py -3 -m ostlr run -- <cmd>` 包住不方便在这里启动的会话；`--selftest` 跑无界面自检 |
| **也能打包成 exe** | 在干净 venv 里构建（见 `docs/dev-machine.md`）：一个目录、约 610 MB、约一秒启动；`--onefile` 出单文件 |

---

Powered by [proJV](https://github.com/MilesKHuang/proJV)
