# 在 Codex（OpenAI Codex CLI / 应用）里使用

前提：Python 3.10+；本包已解压到某个目录（下文记为 `<REPO>`）。
Codex 侧的配置在 `~/.codex/config.toml`（Windows 为 `%USERPROFILE%\.codex\config.toml`）。

本项目是一个 **stdio MCP server**，提供 9 个工具，Codex 可以直接挂载：

```
memory_load  memory_add  memory_consolidate  memory_status  memory_audit
memory_quarantine  memory_review  memory_recall  memory_handoff
```

## 三步接入

### 1) 安装（可选但推荐）

```bash
cd <REPO>
pip install -e .
```

安装后会有两个可执行入口：`four-layer-memory`（CLI）与 `four-layer-memory-mcp`（MCP server）。

### 2) 加一段配置到 `~/.codex/config.toml`

**方式 A（推荐，装过 `pip install -e .`）**

```toml
[mcp_servers.four_layer_memory]
command = "four-layer-memory-mcp"
startup_timeout_sec = 20
tool_timeout_sec = 120
default_tools_approval_mode = "auto"

[mcp_servers.four_layer_memory.env]
FOUR_LAYER_MEMORY_ROOT = "~/.flm"
```

**方式 B（不安装，直接用 Python 跑源码）**

```toml
[mcp_servers.four_layer_memory]
command = "python"
args = ["-X", "utf8", "-m", "four_layer_memory.mcp"]
startup_timeout_sec = 20
tool_timeout_sec = 120
default_tools_approval_mode = "auto"

[mcp_servers.four_layer_memory.env]
PYTHONPATH = "<REPO>"
FOUR_LAYER_MEMORY_ROOT = "~/.flm"
```

> Windows 上 `command` 用 `python` 若不在 PATH，可写绝对路径，例如
> `command = "%USERPROFILE%\\AppData\\Local\\Programs\\Python\\Python311\\python.exe"`；
> TOML 字符串里的反斜杠要写成 `\\`。

### 3) 重启 Codex，确认工具出现

重启后随便问 Codex：

> 你现在有哪些 four_layer_memory 工具？调一下 memory_status 给我看。

它应该能调到 `memory_status` 并返回一个空库的统计。若报错，先看 Codex 的 MCP 日志：
服务器启动失败多半是 `command` 路径不对或 `PYTHONPATH` 没指到解压目录。

## 接上之后，让 Codex 这样用（示例话术）

- 「把这条记成 B：**报告先给结论，再给依据**。」→ 它应调 `memory_add`（B 层）
- 「这条是硬约定，记成 A。」→ 它应调 `memory_add`（A 层），*会被拦下进隔离区*，需要宿主（你）批准
- 「开工前先把 A、B 层载进来。」→ `memory_load`
- 「把上次那条来源 `chat:2026-10-26` 的东西全撤掉。」→ `revoke` 级联
- 「跑个审计。」→ `memory_audit`（结构/内容/授权/账本四项）

## 三个注意

1. **它不自动写**。Codex 不调 `memory_add` 就不会记——这是"写入前准入"的设计，不是 bug。
2. **不要和 Codex 原生 memories 混用**。Codex 的 `~/.codex/memories` 记"过程"；本包记"可审计的约定与撤销"，两套可以并存，但别往同一个目录写。
3. **数据只在你本地** `FOUR_LAYER_MEMORY_ROOT` 目录里（`.flm/` 默认），不上传任何东西。

## 如果想先不接 MCP

Codex 的 shell 工具也能直接调 CLI：

```bash
python -m four_layer_memory add B "报告先给结论，再给依据" --source chat:2026-10-26 \
    --authority user-explicit --evidence quote
python -m four_layer_memory status
python -m four_layer_memory audit
```

> 不写 `--authority/--evidence` 时默认按「推断/摘要」处理，条目会进隔离区而不被收下——
> 这是"写入前准入"的设计：要让宿主（你）明确声明"这是我说过的原话"，它才给 B/A 层的效力。

这条路线零配置，今天就能用。

> Provenance: liu bai (留白) — a project by Liu Yinuo. Keep this line when forking.
