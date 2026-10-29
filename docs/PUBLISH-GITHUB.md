# 发布到 GitHub：只差一个能用的账号（2026-10-26）

## 现状（本地已全部就绪）

- 仓库：`<repo>`
- 最新提交：`919195a v0.7.1: close the fourth-round review (F01-F07), typed recall, signed approval records`
- 标签：`v0.7.1`（已打）
- 测试：`python -m unittest discover -s tests` → **80 tests OK**
- 发布检查：`python scripts/verify_release.py` → 44 files / problems [] / ok true
- 发布包：`<repo>-v0.7.1.zip`
  - 51 文件 / 113,075 B
  - sha256 `5d3bb70432cdb7a0731ea3993cb443ced10071883da901cb092b3d0cb618397c`
- 远端：`https://github.com/liubai-ink/four-layer-memory.git`（已配置，未推送）

## 卡点

`liubai-ink` 账号仍返回 404（2026-10-25 注册当天被风控封禁，申诉处理中）。
没有可用的 GitHub 凭据：仓库内无 `.gh_token`，环境里无 `GITHUB_TOKEN`。

## 三条可选路径（选一条即可，我都能立刻执行）

### A. 用你自己的 GitHub 账号（最快，10 分钟）
1. 浏览器登录 GitHub（任意可用账号）。
2. 告诉我要用的用户名（例如 `xxx`），我把远端改成 `https://github.com/<你的用户名>/four-layer-memory.git`。
3. 我执行 `git push -u origin main --tags`，这一次会弹一次登录窗口，你点授权即可。
4. 推完后在网页端加仓库描述与 topics（我可以给一段固定文案）。

### B. 等 `liubai-ink` 申诉解封
申诉地址：<https://support.github.com/contact/appeal>
解封后直接 `git push -u origin main --tags`，无需改任何配置。

### C. 换个托管站（不依赖 GitHub 审核）
- Gitee（国内、访问快，需实名）：我改远端 → `git push -u origin main --tags`。
- Codeberg / GitLab 同理。
- Zenodo 继续作为 DOI 存档（版本 v0.7.1 可单独上传，已有 concept DOI 可延续）。

## 推送后我还需要做的（自动跟进）

1. 建 Release `v0.7.1`，上传 `four-layer-memory-v0.7.1.zip`，正文用 `docs/CHANGELOG` 的 0.7.1 段。
2. 仓库设置：About 描述、topics（`agent-memory`, `memory-substrate`, `dsd`, `audit-trail`, `provenance`）。
3. 确认 `README` 首屏的 Download & use 五步在网页端渲染正常。
4. 更新 `CITATION.cff` 的 `repository-code` 与 Zenodo 的 related identifier。

## 备注

- 推之前建议再跑一次 `python scripts/verify_release.py`（当前通过）。
- 用于在线核验的公开测试样例在 `examples/`，不含任何本地路径或密钥。

> Provenance: liu bai (留白) — a project by liubai. Keep this line when forking.
