# AI Project Copilot 多入口补丁复核报告

版本：`2.2.0-preview.2`  
复核日期：2026-08-15

## 结论

`preview.1` 不建议继续应用。复核发现的协议、安装事务和资源边界问题已经在 `preview.2` 修正，并新增对应回归测试。

## preview.1 复核发现并修正

1. **MCP 2026-07-28 最终协议结构不完全兼容**
   - `serverInfo` 不应继续放在 `server/discover` result 顶层；最终规范将服务端身份放入结果 `_meta`。
   - 2026-07-28 成功结果必须包含 `resultType`。
   - 每个现代请求需要携带协议版本和 client capabilities 的 `_meta`。
   - 未知工具/非法参数应返回 JSON-RPC `-32602`，不支持的现代协议版本使用 `-32022`。
   - preview.2 已按最终 2026-07-28 wire shape 调整，同时保留 2025-11-25 / 2025-06-18 legacy initialize。

2. **安装器可能半安装 / 半回滚**
   - preview.1 在复制/删除若干文件后才可能遇到后续冲突或缺失 SKILL anchor。
   - preview.2 改为全量 preflight 后再修改，并在异常时恢复事务前状态。

3. **输出上限只限制返回内容，没有限制临时磁盘增长**
   - preview.1 helper stdout/stderr 先完整写入临时文件，再截断读取。
   - preview.2 改为并发 drain 管道，仅保留固定字节，超额数据即时丢弃。

4. **Capability schema 与实际参数校验不一致**
   - preview.1 schema 声明 `additionalProperties: false`，但 Engine 会静默忽略未知键。
   - preview.2 在统一 registry 层严格拒绝未知键、缺少 required 字段和基础类型错误。

5. **Orchestrator 状态可能虚报 completed**
   - preview.1 对缺少 release 参数或尚未自动化的 lane 标记 skipped/planned，但顶层仍可为 `completed`。
   - preview.2 对这类成功但不完整的执行返回 `partial`；REST 将 `partial` 视为成功请求并返回 HTTP 200。

6. **REST slow-client / 线程增长边界不足**
   - preview.2 增加 per-connection socket timeout 和 simultaneous request thread 上限。
   - 仍定位为小型 adapter，不替代公网 TLS gateway、WAF、租户鉴权、配额和审计系统。

7. **Rollback ownership / `--force` 恢复不完整**
   - preview.1 无法区分安装前已存在的相同文件/Skill gateway 小节，也不会永久保存 `--force` 覆盖前内容。
   - preview.2 使用 `.aipc/multi-interface-upgrade-2.2.0-preview.2/receipt.json` 记录 installer ownership；仅回滚自己拥有的变化，并保存 replaced 文件备份。

## 自动验证

preview.2 发布包生成前执行：

- Python compile：installer、4 个 runtime 模块、gateway tests、installer tests；
- Multi-interface gateway 单元测试：14 项；
- Installer package tests：7 项；
- 10 MB helper 输出边读边截断；
- MCP 2026-07-28 modern metadata/resultType/serverInfo metadata；
- MCP unsupported protocol / unknown tool 错误码；
- MCP 2025-11-25 与 2025-06-18 legacy initialize；
- REST Bearer、partial response、并发/连接参数；
- allow-root 路径逃逸；
- apply dry-run、late-conflict preflight、missing-anchor preflight；
- apply → tests → rollback；
- rollback modified-file preflight；
- pre-existing gateway ownership；
- `--force` replaced-file backup/restore；
- `git apply --check`、实际 `git apply`、新增测试、`git diff --check`；
- ZIP 内 `SHA256SUMS.txt` 完整性校验。

## 当前 `main` 兼容性边界

复核时通过 GitHub 当前 `main` 页面确认：`skills/ai-project-copilot/SKILL.md` 仍为 2.1，并且补丁使用的 OpenAI gateway 段落后仍紧接 `## Capability lanes`，因此 Skill 插入上下文与当前主分支匹配。

当前执行沙箱无法直接取得该仓库完整 checkout/archive，因此没有在“真实 main 全量副本”上运行仓库原有的全部 CI 测试。发布前仍建议开发者在自己的 checkout 上执行：

```bash
python apply_multi_interface_patch.py /path/to/ai-project-copilot --dry-run
python apply_multi_interface_patch.py /path/to/ai-project-copilot --run-tests
# 再运行仓库已有的完整 CI / tests
```

## 仍然有意保留的限制

- REST 默认是本地/受控网络 adapter，不是生产多租户 SaaS edge；公网必须放在 TLS reverse proxy / gateway 后。
- MCP 仅提供 stdio；未在本补丁中实现 Streamable HTTP MCP transport。
- API/MCP 不提供任意 shell，也不提供 push / merge / publish / deploy / delete / 权限修改等写能力。
- `copilot_run` 的 model-driven lanes 仍可能返回 `planned`；它不会伪装成已经自动执行。
