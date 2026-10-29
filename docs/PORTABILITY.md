<!-- liu bai (留白) · four-layer-memory · internal design note -->

# Portability as a testable claim（可迁移性：从形容词到验收）

> Internal note for paper v0.5. Source: 2026-10-26 讨论（老板 m10502/m10505）。
> Rule: 这里只写能进论文的东西；实验结论未跑之前一律标 **proposed**，不写成已证。

## 1. Claim（可直接放进论文 §1 的窄表述）

英文候选：

> Portability here is not "we have an export button". It is that **the audit trail travels with the data**:
> a memory bundle exported from one host can be re-imported into a blank deployment, re-verified offline
> (`audit` = structural / content / authorization / ledger), and resumed with the same resident budget,
> without trusting the exporting platform and without re-deriving provenance from scratch.

中文对照：

> 可迁移性不是"有导出按钮"，而是**账本随数据走**：从一个宿主导出的 bundle，能在空白部署里重新导入、
> 离线复查（结构/内容/授权/账本四项）、并按同样的常驻预算继续工作——不需要信任导出方，也不需要重建来源。

## 2. Mechanism（三面分离）

| 面 | 内容 | 迁移时做什么 |
|---|---|---|
| 数据面 | layer files、`quarantine/`、`ledger.jsonl`、`state.json`、签名 | 拷贝（bundle 内） |
| 工具面 | MCP 11 工具 / CLI | 在新宿主挂上（同一协议） |
| 契约面 | `prompts/LOADING.md`（加载/写入/整合规则）、四轴准入规则 | 随包分发，读它 |

- 迁移流程：`export --out <bundle>` → 空白机 `init --from-bundle <bundle>` → `audit` → `memory_load`。
- v0.7.4 起这条路径是**真的**：`init --from-bundle` 以 bundle 的 ledger records 为重放源（bullet 元数据不含 `source_event_id`），A 层由**导入方**重新走批准通道签发（旧签名绑定导出方的宿主键，故意不可携带），撤销闭包按 `source_event_id` 重建；见 `tests/test_portability.py`。
- 不依赖任何平台 SDK；宿主可以是 DSH、别的 agent 框架、或任一支 MCP client。

## 3. Evidence plan（写进附录，标 proposed）

E2E（可自动化，不调外部模型）：

1. 在根 A 建若干条目（含 1 条隔离区、1 条撤销），导出 bundle。
2. 空白目录导入；断言：`audit` 四项全过；条目数/层分布/ledger 尾哈希与源一致。
3. `memory_load` 输出与源部署**逐字节一致**（A+B 常驻部分）。
4. 篡改 bundle 内任一 ledger 行 → `init --from-bundle` 在导入阶段就拒绝（先重算链哈希再落盘，不靠导入后的 `audit` 兜底）（负例）。
5. 删除 bundle 内签名 → 导入仍可跑，`audit` 报 `signed: none`（降级路径）。


指标：bundle 大小、导入耗时、验证耗时；与"重新手工录入"作定性对照即可，不宣称更快。

## 4. 与相关工作的差别（写作口径）

- **Mem0 / Letta**：开源、可自托管，也能导出数据；差别不在"能搬"，在**授权与撤销的账本一起搬**（离线可验）。
- **Anthropic memory tool / Managed Agents memory**：平台侧审计、作用域、回滚、导出；差别在**验证位置**（我们本地离线可验）与**耦合度**（不绑平台）。
- **PROV-DM**：概念层来源模型；我们给它加两个运行期约束：写入前准入、撤销级联。

主张强度：**不声称"唯一可迁移"**，只声称"迁移后仍可审计、可撤销、常驻预算不变"。

## 5. 一个必须先修的技术缺口（否则主张打折）

默认签名为 **HMAC-SHA256**：验证需要密钥副本，本质上是**设备绑定**的完整性校验，跨平台时无法只凭公开信息验证。

修法（v0.5 工程项）：

1. 可迁移/共享场景默认 **Ed25519**：导出 `.signing_key.pub`，私钥永不进 bundle；
2. bundle manifest 里标注 `sig_alg` 与 `public_key`，导入方用公钥验证；
3. HMAC 保留为单机模式（低依赖）；`audit` 报告 `signed: hmac | ed25519 | none`。

> 一句话：**"可迁移的审计"要求签名可被第三方验证**，否则只是同机校验。

## 6. 论文 §9 骨架（v0.5 候选）

1. §9.1 Portability as a property (define: data / tool / contract plane)
2. §9.2 What travels: bundle = data + ledger + signature + contract
3. §9.3 Verification without the exporter (offline audit, Ed25519)
4. §9.4 Limits: keys, size, host-specific hooks, no benchmark yet
5. §9.5 Testable claim + e2e plan (proposed)

## 7. Open risks

- bundle 体积与隐私：导出包含隔离区与账本（含 source refs）→ 需 `--redact` 选项？
- 多宿主并发写：目前只有单库锁（本机）；跨设备并发未解决（论文 §7 已列）。
- 公钥分发与信任：Ed25519 公钥如何被导入方信任（TOFU / 指纹比对）需要一句说明。
