# 先读我：10 分钟试用

这是一个**给长会话 AI 用的记忆底座**（Python，无第三方依赖）。
它不教你"怎么让模型记住"，它管的是：**哪些内容配被写进记忆、谁有权写、写错能不能撤、事后能不能验。**

- 版本：v0.7.4
- 包内不含任何作者本地路径、密钥或私人数据
- 需要：Python 3.10 及以上；`cryptography` 只在你想用 Ed25519 签名时才需要（默认 HMAC）

---

## 一、30 秒跑起来

解压后进入目录：

```bash
cd four-layer-memory

# 1) 看它有哪些命令
python -m four_layer_memory --help

# 2) 跑一个完整演示（写 A/B/C/D、批准、撤销、审计）
python -m four_layer_memory demo

# 3) 初始化一个自己的库
python -m four_layer_memory init
```

Windows 上若 `python` 不在 PATH，可用 `py -3` 代替。

> 也可以 `pip install -e .` 装成命令 `four-layer-memory`，但不是必须的。

---

## 二、值得你花 20 分钟试的五件事

1. **写一条偏好（B 层）**
   ```bash
   python -m four_layer_memory add B "报告先给结论，再给依据" --source chat:2026-10-26 \
       --authority user-explicit --evidence quote
   ```
   > 不带 `--authority/--evidence` 时，默认按「推断/摘要」处理，会被放进**隔离区**而不是直接收下——
   > 这是设计：你没说"这是我说过的原话"，它就不替你升格。反过来，带了这两个参数才是你认账。
2. **写一条硬性约定（A 层）——会发现它不让你直接写**
   ```bash
   python -m four_layer_memory add A "未经我确认，不得修改项目根目录" --source chat:2026-10-26
   ```
   它会拒绝并提示：A 层必须由「宿主」用签名记录批准（不是模型自报）。完整批准流程在
   `demo` 的输出里能看到，也可以看 `four_layer_memory/approval.py` 的 `mint/verify`。
3. **撤销一条来源**（会级联影响由它派生出来的条目）
   ```bash
   python -m four_layer_memory revoke <source_event_id>
   ```
4. **审计**（结构 / 内容 / 授权 / 账本四项分开）
   ```bash
   python -m four_layer_memory audit
   ```
5. **把它挂成 MCP 工具**（任意支持 MCP 的宿主：Claude Desktop、DSH、IDE 插件等）
   ```json
   {
     "mcpServers": {
       "four-layer-memory": {
         "command": "python",
         "args": ["-m", "four_layer_memory.mcp"]
       }
     }
   }
   ```
   共 11 个工具：`memory_load` / `memory_add` / `memory_consolidate` / `memory_status` / `memory_audit` / `memory_quarantine` / `memory_review` / `memory_handoff` / `memory_recall` / `memory_archive_search` / `memory_archive_verify`。

---

## 三、它和"向量库/记忆插件"的区别（一句话）

向量库解决"相似度召回"；它解决"**这条记忆凭什么被写、谁批的、错了怎么撤、换台机器还能不能验**"。

它故意**不做**的事：不做自动摘要、不做向量检索质量竞赛、不替你决定该记什么。

---

## 四、想让你反馈的三件事（写在 TRIAL_QUESTIONS.md 里也行，直接发我也行）

1. 十分钟内有没有成功跑通？卡在哪一步？
2. `--help` / 报错文案，有没有让你困惑的地方？
3. 你会不会真的把它接到自己的 agent 上？不接的原因是什么？

---

## 五、安全提醒

- 别把它指向含真实密钥的目录做 `add`（示例里用的是聊天文本）；
- `audit` 会扫 `sk-` 形态的串，但它不是 DLP；
- 试用请用临时目录或 `init --root <tmp>`，不要污染你自己的工作目录。

> Provenance: liu bai (留白) — a project by Liu Yinuo. Keep this line when forking.
