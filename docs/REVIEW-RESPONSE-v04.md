<!--
liu bai (留白) - a project by liubai
Four-Layer Agent Memory: conventions, preferences, capabilities, background.
Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
-->

# 回应 v0.4 审阅：v0.7.0 修复清单

审阅对象：`four_layers_memory_v04_review_20261025.md` 与 `_evidence_20261025.zip`。
修复版本：`four-layer-memory-v0.7.0`；论文：`paper/four_layer_memory_anonymous_v05.md`。
结论：C01–C06 保持已修复；R01–R13 全部复现并关闭；规格冲突与实验叙述按审阅 §5 修订。
本文每条给出「审阅条目 → 原缺陷（文件:行）→ v0.7 变更 → 验收测试」。

## 1. P0 缺陷（F01–F04）

### F01 批准边界（R01、R02）

| 条目 | v0.6.1 缺陷 | v0.7.0 变更 |
|---|---|---|
| R01 | `mcp.tool_add` 把一次性 `approval_token` 回给同一个 agent（`mcp.py:155,164`），agent 可自行 `memory_review` 批准 | 工具结果不再含任何批准材料；`memory_review` 只接受拒绝，`accept=True` 返回 `StoreError`（"layer A approval is a host action"）；批准只能在宿主侧执行 |
| R02 | `store.review(accept=True)` 不重跑 `admission.decide()`，无 `source_event_id` 的候选也能进入 A（`store.py:552`） | 新增 `four_layer_memory/approval.py`：宿主用 HMAC 审批记录签名，绑定 `entry_id` + `text_sha256` + `source_event_id` + `scope="layer:A"` + `format`；`store.approve()` 验签、核对内容与源事件、检查撤销血缘，并**重跑** `admission.decide()`，失败即拒绝 |
| 验收 | — | `tests/test_review_probes_v07.py::test_r01_tool_results_never_approve`、`test_r01_library_cannot_accept`、`test_r02_approval_is_content_and_event_bound` |

### F02 撤销持久（R03–R07）

| 条目 | v0.6.1 缺陷 | v0.7.0 变更 |
|---|---|---|
| R03 | `revoke()` 只匹配当时 `write`/`approve` 记录的 `source_event_id`，多跳派生在第二跳断掉 | 新增 `revoked_sets()`：按 `source_event_id` 墓碑 + `entry_id` + `derived_from` 求不动点闭包；`revoke()` 把闭包写回账本与 state |
| R04 | 撤销后仍可批准同一来源 | `approve()` 检查 `source_event_id` 与血缘是否已撤销，直接拒绝 |
| R05 | 撤销后新增派生条目仍可加载 | `add()`/`add_many()` 在准入前检查 `entry_id`/`source_event_id`/`derived_from` 是否触碰已撤销集合，命中则隔离（"lineage was revoked"），不写入层文件 |
| R06 | 撤销日志已写但 state 未存 → 重开后又加载 | `revoked_ids()` 从**账本**重算，不依赖 `state.revoked`；`MemoryStore.__init__` 重开时把账本派生的撤销集写回 state |
| R07 | 旧 handle `consolidate()` 写回旧 state，撤销失效 | `consolidate()` 在锁内先 `_reload()` 再合并撤销集，不覆盖账本事实 |
| 验收 | — | `test_r03_revocation_is_transitive`、`test_r04_approval_after_revoke_refused`、`test_r05_derived_after_revoke_not_loaded`、`test_r06_revocation_survives_state_loss`、`test_r07_stale_handle_cannot_resurrect`（旧套件 `test_admission.py::test_revocation_covers_derived_entries` 仍覆盖双跳） |

### F03 事务恢复（R08、R09）

| 条目 | v0.6.1 缺陷 | v0.7.0 变更 |
|---|---|---|
| R08 | `write` 已入账本但层文件未写 → `audit=true` 且不修复 | `write` 记录携带 `layer_sha256_expected`（预计算目标字节）+ `tx`；`MemoryStore.__init__` 调 `recover()`：未 bind 的事务若层文件缺该条目，按预期字节写入并补 `bind`；若文件已含该条目（如账本被截断），**不**补 bind，交由 audit 报告 |
| R09 | 层文件已写但 `bind` 未入账本 → 重试 `add` 直接返回、不补 bind | `_check_idempotent()` 发现记录未 bind 时调用 `_recover_tx()` 补 `bind`；重试后 `audit` 通过 |
| 验收 | — | `test_r08_crash_after_ledger_is_repaired`、`test_r09_retry_repairs_missing_bind` |

### F04 审计覆盖（R10、R11）

| 条目 | v0.6.1 缺陷 | v0.7.0 变更 |
|---|---|---|
| R10 | 旧行被手改后普通 `add` 用改后整文件 hash 生成新 `bind` → audit 由 false 变 true | `expected_layers()` 从账本重建字节；`add()` 写前 `_guard_bytes()` 比对磁盘与账本预期，不一致直接 `StoreError`，拒绝把篡改"洗白"；`audit` 用同一重建结果做 authorization 比对 |
| R11 | 非 bullet 普通段落绕过覆盖检查，仍被 `memory_load` 返回 | `parse_layer_text()` 定义严格语法（bullet + 缩进元数据）；未知内容计入 `unknown_blocks`，`audit` 失败且 `read_layers()` 永不返回未知文本 |
| 验收 | — | `test_r10_hand_edit_is_not_absorbed`、`test_r11_unknown_paragraph_not_served` |

## 2. P1 缺陷（F05–F06）

| 条目 | v0.6.1 缺陷 | v0.7.0 变更 |
|---|---|---|
| F05/R12 | 审计只在 `signing.has_key(root)`（仅私钥）时验签；只有公钥时跳过验签，audit 仍 true | `signing.material()` 返回可用密钥材料；`verify_with_material()` 在「有签名但无材料」时返回失败（"signature present but no key material available"）；`ledger_integrity()` 把该失败计入，`audit` 随之失败 |
| F06/R13 | 敏感正则为子串匹配，`hiv` 误中 `archive` | 6 类正则统一加词边界 `(?<![a-z])…(?![a-z])`；`archive`、`shiver` 不隔离，"diagnosed with HIV" 隔离 |
| 验收 | — | `test_r12_unverifiable_ledger_fails_audit`、`test_r13_sensitive_word_boundaries` |

## 3. 规格与实现统一（审阅 §5）

| 冲突 | v0.6.1 实际 | v0.7.0 |
|---|---|---|
| fold 条件 | 正文 "at least one pending entry"，代码 `entries_since == 0`（`triggers.py:52`） | `allow_fold and entries_since > 0`；文档、论文、测试一致 |
| cooldown 默认值 | 文档 8、`store.py:291`/`mcp.py:175` 默认 50 | 统一 `DEFAULT_COOLDOWN_ENTRIES = 8`（store/cli/mcp 共用） |
| §3 "其余一律隔离" 的暗示 | A/B/C/D 行为不一致 | 文档只陈述实现：A 默认隔离、C/D 由宿主写入通道直接准入、任何敏感类别隔离 |
| authority 枚举 | 缺 `none` | `Authority.NONE = "none"`，CLI/MCP schema 同步 |
| lifecycle 语义 | 仅标签，无读取时强制 | 新增 `scope`/`expires_at`；`read_layers()` 过滤过期条目（坏日期 fail-closed），`status()` 报告过期数；清理惰性 |
| 版本号 | `__init__ 0.1.0` / `mcp 0.5.0` / pyproject 0.6.1 / 论文 0.6.0 | 全部 0.7.0；论文 `v05` 使用 0.7.0 |

## 4. R01–R13 复现与关闭对照

| id | 反例（v0.6.1） | v0.7.0 结果 | 验收测试 |
|---|---|---|---|
| R01 | agent 用自己拿到的 token 批准自己的 A 写入 | 工具结果无批准材料；MCP 不能接受 → 关闭 | `test_r01_*` ×2 |
| R02 | 无 source_event 的候选经 `review` 进入 A | `approve` 重跑准入并要求 source_event → 关闭 | `test_r02_*` |
| R03 | 撤销只命中第一跳，孙条目仍加载 | 闭包撤销 + 加载过滤 → 关闭 | `test_r03_*` |
| R04 | 撤销后仍可批准 | 批准检查撤销血缘 → 关闭 | `test_r04_*` |
| R05 | 撤销后新增派生仍加载 | 新增即隔离 → 关闭 | `test_r05_*` |
| R06 | `state.json` 清空后撤销丢失 | 撤销由账本重算 → 关闭 | `test_r06_*` |
| R07 | 旧 handle consolidate 复活撤销 | consolidate 前 reload + 合并 → 关闭 | `test_r07_*` |
| R08 | crash 后 audit=true 且不修复 | 启动时按 `layer_sha256_expected` 恢复 → 关闭 | `test_r08_*` |
| R09 | 缺 bind 的重试不修复 | 重试补 bind；截断账本保持 audit=false → 关闭 | `test_r09_*` |
| R10 | 手改后普通写把 audit 洗成 true | 写前拒绝 + 账本重建比对 → 关闭 | `test_r10_*` |
| R11 | 非 bullet 段落被加载 | 严格语法 + unknown_blocks + 加载过滤 → 关闭 | `test_r11_*` |
| R12 | 只有公钥时 audit 跳过验签 | 无材料即有签名 → audit 失败 → 关闭 | `test_r12_*` |
| R13 | `archive` 被误判敏感 | 词边界正则 → 关闭 | `test_r13_*` |

> 说明：R01/R02 的修复改变了公共接口：MCP 不再返回批准 token，`memory_review` 只拒绝；宿主改用 `four-layer-memory approve <entry_id>`。因此 v0.6.1 的 `verify_revision.py` 中依赖旧 token 的探针在 v0.7 会报 API 错误——这是接口收紧的预期结果，替代证据见 `scripts/verify_v07_probes.py` 与 75 条测试。

## 5. 论文 v0.5 对应修订

- 标题与摘要改为三项契约（独立准入 / 可持续撤销 / 可重放审计），删除 "What none of them makes checkable"。
- 撤回快照回放、任意派生深度与逐行审计的实现主张，新增 §7 "What we do not claim"（10 条）与附录 D non-claims。
- 实验部分：两条件实验只测契约整体、E1/E2 分列并预注册主终点、固定读取预算与排序、on 条件真执行、两名盲评、paired bootstrap 与非劣界；Status 明示旧协议未运行、新版为计划。
- 规格冲突按 §3 表全部对齐 v0.7.0（fold/cooldown/authority/lifecycle/版本）。
- 论文与本文的逐条对照见 `paper/V05-CHANGES.md`。

## 6. 复现方式

```bash
python -m pytest -q                          # 75 passed
python scripts/verify_v07_probes.py --source . --work-dir ./_v07_work --result ./_v07_work/result.json
python scripts/verify_release.py             # provenance / secret / structure
four-layer-memory --root ./demo init
four-layer-memory --root ./demo audit        # {"ok": true, ...}
```

## 7. 未做与边界

- 未声称物理掉电安全；`recover()` 覆盖的是「账本与层文件之间被中断」的进程级故障。
- 未实现快照式恢复、compaction、多写者租约（沿用 v0.6 的声明）。
- 批准边界解决的是「同一 agent 经 MCP 自查自批」；宿主进程完全被攻陷时密钥可读，仍超出边界（见 `docs/THREAT-MODEL.md` 的 T4/T11）。
