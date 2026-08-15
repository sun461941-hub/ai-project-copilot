# AI Project Copilot fix4.0 全方位加固补丁

本 ZIP 以当前 fix3.0 已存在的修复为基础，不重复覆盖已经加固的
`run_skill_evals.py`、`maintainer_triage.py`、`ai_ready_bootstrap.py`
和 `init_project_docs.py`。

## 本版本直接覆盖的文件

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `skills/ai-project-copilot/scripts/evidence_cache.py`
- `skills/ai-project-copilot/scripts/mcp_config_audit.py`
- `skills/ai-project-copilot/scripts/supply_chain_guard.py`
- `tests/test_fix4_comprehensive_hardening.py`
- `tools/apply_fix4_gateway_patch.py`

## 修复范围

### 1. Evidence cache
- 在解析前限制 JSON 嵌套深度与缓存文件大小。
- 字符串中的 `[`、`{` 不计为结构嵌套。
- 拒绝目标文件或任意父目录中的 dangling/regular symlink。
- 缓存写入改为同目录临时文件、flush/fsync、再次校验后 atomic replace。
- 输入文件改为流式 SHA-256，并限制单文件最大体积。
- 校验缓存 schema/version。

### 2. MCP config audit
- 在 `json.loads()` 前限制字节数和嵌套深度。
- 把递归 walker 改为带节点上限的迭代遍历。
- 明确拒绝显式 symlink 配置；默认路径遇到 symlink 时产生 high finding，
  不跟随读取。
- 保留 hardcoded secret、HTTP transport、shell wrapper、未固定包版本等检查。

### 3. Supply-chain guard
- 不跟随 `.github/workflows`、workflow 文件、Skill 目录或 Skill 文件 symlink。
- workflow、哈希文件、文件数量增加资源上限。
- 哈希改为流式读取。
- manifest 的所有路径组件都做 symlink/containment 检查。
- manifest 使用 atomic replace，避免半写文件和直接写穿链接。

### 4. CI
- 增加 concurrency/cancel-in-progress。
- 每个 job 增加 timeout。
- 增加 Python、平台、UTF-8 等运行时诊断。
- package artifact 增加保留期限。
- 保留三个系统 × Python 3.10/3.14 matrix 与固定 SHA 的 actions。

### 5. Release
- 从“推送任意 v* tag 即自动发布”改为 `workflow_dispatch` 手动输入 tag。
- 先用 read-only 权限进行验证、测试、编译和 deterministic rebuild。
- publish job 才获得 `contents: write`。
- publish job 绑定 GitHub Environment `release`。
- 必须在仓库 Settings → Environments → release 中配置 required reviewers，
  才能形成真正的第二次人工批准。
- tag 通过环境变量传入 shell，并进行格式与 commit 绑定校验。

### 6. Model Budget Gateway
Gateway 文件很大，而且 fix3.0 可能已有定制修改。为了避免用旧整文件覆盖新代码，
本 ZIP 使用“精确匹配、失败关闭、幂等”的补丁器：

```bash
python tools/apply_fix4_gateway_patch.py --repo .
python tools/apply_fix4_gateway_patch.py --repo . --check
```

可选备份：

```bash
python tools/apply_fix4_gateway_patch.py --repo . --backup
```

补丁器会统一保护：
- HTTP error JSON
- SSE event JSON
- input-token response JSON
- quality policy JSON
- request / served-model-map JSON

如果源码块与当前版本不一致，补丁器会拒绝修改，而不是猜测替换。

## Working Copy 应用顺序

1. 将 ZIP 解压到现有仓库根目录并允许覆盖。
2. 检查 Changes，确认上述直接覆盖文件。
3. 在可运行 Python 的 Git 环境执行 gateway 补丁器；也可以在 GitHub
   Codespaces/本地终端运行后再同步回 Working Copy。
4. 根据 `FIX4.0_DELETE_LIST.txt` 人工确认并删除旧交付物。
5. 执行：

```bash
python tools/apply_fix4_gateway_patch.py --repo . --check
python tools/validate_skill.py skills/ai-project-copilot
python skills/ai-project-copilot/scripts/run_skill_evals.py --format json
python -m unittest discover -s tests -v
python -m compileall -q tools tests skills/ai-project-copilot/scripts
```

6. Commit → Push，以完整 GitHub Actions 全绿作为最终验收。

## 重要边界

- 本补丁不声称 CI 能证明真实 OpenAI 网络调用；它只加固 provider 返回内容的
  本地解析边界。
- GitHub Environment 的 required reviewers 属于仓库设置，无法仅靠 ZIP 自动开启。
- 删除清单不会自动删除任何文件，避免误删仍有用途的历史资料。
