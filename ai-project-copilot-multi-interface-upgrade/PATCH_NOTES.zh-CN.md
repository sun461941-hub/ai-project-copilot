# AI Project Copilot 多入口迭代补丁说明（2.2.0-preview.2）

## 目标

本补丁保留现有 **Agent Skill** 使用方式，同时新增统一的多入口调用层：

- Skill：原有自然语言/Agent Skill 入口继续保留；
- CLI：开发者和 CI 可直接调用；
- REST API：自研 Agent、SaaS、内部平台可通过 HTTP 调用；
- MCP stdio：Claude/Cursor/其他 MCP 客户端可把 Project Copilot 当作工具服务器；
- Core Engine：四个入口复用同一个固定能力注册表，不复制现有 repository/review/security/release/eval 业务逻辑。

补丁以 2026-08-15 可见的 `main` 分支、`AI Project Copilot 2.1` Skill 结构为兼容目标。安装器通过 `skills/ai-project-copilot/SKILL.md` 和 `## Capability lanes` 锚点验证，不要求仓库必须处于某一个精确 commit。

## 新增能力

统一能力名：

- `route`
- `analyze_repository`
- `review_changes`
- `scan_security`
- `release_readiness`
- `maintainer_triage`
- `run_evals`
- `copilot_run`

`copilot_run` 接收自然语言 `goal`，先调用现有 `workflow_router.py`，再执行当前能够安全自动化的只读确定性步骤。缺少 release 版本、issue fixture 等必要参数时会明确 `skipped`，不会猜测。

## 安全边界

这一版刻意 **不新增任意命令执行接口**，也不把仓库写操作暴露给 API/MCP。

- Core 只允许固定脚本白名单和固定 argv 模板；
- 所有 helper 都以 `shell=False` 启动；
- stdout/stderr 使用并发管道持续排空，只保留固定上限字节，超额内容即时丢弃，避免无限 RAM 或临时磁盘增长；
- 超时会尝试终止整个进程组/进程树；
- REST 默认只绑定 `127.0.0.1`；
- REST 非 loopback 绑定必须从环境变量读取 Bearer Token；
- REST 拒绝带 `Origin` 的浏览器请求，降低本地 DNS rebinding/网页跨边界调用风险；
- REST/MCP 默认只允许访问当前工作目录，使用 `--allow-root` 显式扩大；
- REST 增加连接超时与并发请求线程上限，降低 slow-client/线程耗尽风险；
- capability 参数按公开 JSON Schema 做严格键名/基础类型校验，未知参数不会被静默忽略；
- 通用 helper 子进程不会继承常见 OpenAI/GitHub/云凭据；
- merge / publish / deploy / delete / permission / repository write 继续由人类控制。

## MCP 兼容

`project_copilot_mcp.py` 支持：

- MCP `2026-07-28`：按最终规范校验每请求 `_meta` 中的 protocolVersion/clientCapabilities，所有成功结果包含 `resultType`，server identity 位于结果 `_meta`；
- 旧客户端：兼容 `2025-11-25` 与 `2025-06-18` 的 `initialize` + `notifications/initialized` 流程；
- 不支持的现代协议版本返回 MCP `-32022 UnsupportedProtocolVersion`，未知工具/非法工具参数返回 `-32602 Invalid params`。

当前补丁只提供 **stdio MCP**。远程 MCP Streamable HTTP 可作为下一阶段单独实现，不与 REST API 混为同一协议端点。

## 文件变化

新增：

```text
skills/ai-project-copilot/scripts/project_copilot_core.py
skills/ai-project-copilot/scripts/project_copilot.py
skills/ai-project-copilot/scripts/project_copilot_api.py
skills/ai-project-copilot/scripts/project_copilot_mcp.py
skills/ai-project-copilot/references/multi-interface-gateway.md
skills/ai-project-copilot/assets/templates/project-copilot-api-request.json
skills/ai-project-copilot/assets/templates/project-copilot-mcp-stdio.json
tests/test_multi_interface_gateway.py
```

修改：

```text
skills/ai-project-copilot/SKILL.md
```

只在 `## Capability lanes` 前插入 Multi-interface gateway 小节，不改变原 Skill frontmatter、触发方式或原有 lane。

## 推荐应用方式

### 方式 A：安装器（推荐）

在解压后的补丁目录执行：

```bash
python apply_multi_interface_patch.py /path/to/ai-project-copilot --dry-run
python apply_multi_interface_patch.py /path/to/ai-project-copilot --run-tests
```

如果目标文件已经存在且内容不同，安装器默认拒绝覆盖。只有你确认冲突后才使用；preview.2 会把被替换文件的原始内容保存在仓库 `.aipc/multi-interface-upgrade-2.2.0-preview.2/` 的本地回滚状态中：

```bash
python apply_multi_interface_patch.py /path/to/ai-project-copilot --force
```

回滚：

```bash
python apply_multi_interface_patch.py /path/to/ai-project-copilot --rollback
```

如果已对补丁新增文件做过本地修改，回滚也会拒绝删除/覆盖；确认后可加 `--force`。安装器只回滚自己拥有的变化：应用前已经存在且内容相同的文件或 gateway 小节不会在 rollback 时被误删；`--force` 替换的原文件会从本地 receipt backup 恢复。

### 方式 B：Git patch

```bash
git apply --check ai-project-copilot-multi-interface.patch
git apply ai-project-copilot-multi-interface.patch
python -m unittest -v tests/test_multi_interface_gateway.py
```

如果你的 `SKILL.md` 已经大幅改写，优先使用安装器；它只依赖 `## Capability lanes` 锚点。

## 使用示例

CLI：

```bash
python skills/ai-project-copilot/scripts/project_copilot.py \
  run "review this PR for security risk" \
  --repo /path/to/repo \
  --base main \
  --head HEAD
```

REST：

```bash
python skills/ai-project-copilot/scripts/project_copilot_api.py \
  --allow-root /path/to/repo
```

然后：

```bash
curl -sS http://127.0.0.1:8787/v1/run \
  -H 'Content-Type: application/json' \
  -d '{
    "goal":"review this PR for security risk",
    "repo":"/path/to/repo",
    "base":"main",
    "head":"HEAD"
  }'
```

MCP：

```bash
python skills/ai-project-copilot/scripts/project_copilot_mcp.py \
  --allow-root /path/to/repo
```

客户端配置模板见：

```text
skills/ai-project-copilot/assets/templates/project-copilot-mcp-stdio.json
```

## 本轮验证

preview.2 在 preview.1 的 9 项测试基础上扩展了协议与失败路径覆盖，重点新增：

- MCP 2026-07-28 最终 wire shape（required `_meta`、`resultType`、serverInfo result metadata）；
- `-32022` 不支持协议版本与 `-32602` 未知工具/非法参数；
- 2025-11-25 + 2025-06-18 legacy initialize；
- 10 MB helper 输出的边读边截断；
- capability unknown-argument 拒绝；
- orchestrator 缺少必要输入时返回 `partial`，REST 对这种成功但未完整执行的请求返回 HTTP 200；
- 安装器 payload manifest 完整性校验；
- apply/rollback 冲突预检与事务式恢复，防止半安装/半回滚；
- installer ownership receipt：不误删应用前已存在的 gateway 内容，`--force` 覆盖文件可恢复原始版本。

发布包生成时还会重新执行：Python compile、multi-interface 单测、installer package tests、`git apply --check`（2.1-compatible fixture）、安装/回滚回归与 SHA256 校验。

## 当前限制 / 下一步

本补丁是“多入口架构”的第一阶段，目标是先把接口边界做稳定，而不是一次性做成公网 SaaS。

暂不包含：

- GitHub URL 自动 clone / 托管仓库池；
- 多租户账号、数据库、配额、审计后台；
- MCP Streamable HTTP；
- 异步持久任务队列；
- API/MCP 写代码、push、merge、release 等写能力；
- SDK（Python/TypeScript）包装层。

建议先合并这一层并在本地 Skill + MCP + REST 三种入口做真实项目回归，再进行第二阶段远程服务化。
