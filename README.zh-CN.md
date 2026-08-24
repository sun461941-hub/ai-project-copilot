# AI Project Copilot

一个可移植的 Agent Skill，用于 AI 产品工程、开源维护、代码库理解、风险审查、发布准备、供应链检查和确定性质量验证。

> 当前可安装 Skill 版本为 2.2.1；多接口 Gateway 仍是独立的预览兼容包，不代表主 Skill 的版本。正式版本号以 Git tag、GitHub Release 和 `CHANGELOG.md` 为准。

## 核心能力

| 能力通道 | 主要用途 | 对应实现 |
|---|---|---|
| Discover | 映射陌生仓库、生成受控的 Agent 指令草案、审计本地 Skill Stack | `repo_context.py`、`ai_ready_bootstrap.py`、`skill_stack_audit.py` |
| Launch / Retrofit | 从模糊想法选择可演示的 AI 垂直切片，或为现有产品增加高价值 AI 能力 | 24 个蓝图、`rank_blueprints.py`、架构参考 |
| Maintain | 对 Issue 做确定性预分诊、改善维护者工作流，并保留显式证据决策 | `maintainer_triage.py`、`github_evidence_sync.py`、`run_state_ledger.py` |
| Review | 对变更风险排序，并验证 review thread 是否收敛 | `change_risk.py`、`review_convergence.py` |
| Release | 给出 SemVer 建议、发布说明草案和迁移阻断项 | `release_intel.py` |
| Secure | 检查 GitHub Actions、MCP 配置、权限、依赖引用和 Skill 完整性 | `supply_chain_guard.py`、`mcp_config_audit.py` |
| Quality | 验证 eval 数据并运行确定性命令用例 | `run_skill_evals.py` |
| Budget | 使用 SQLite 做模型预算路由，并通过 OpenAI Responses Gateway 执行受控请求 | `model_budget_autopilot.py`、`model_budget_gateway.py` |

## 只读 GitHub 证据与维护台账

v2.2 可把已经由维护者授权导出的本地 GitHub JSON（Issue、PR、Workflow Run、Release）规范化为可复现的证据包，并按稳定 ID 保留 `fix`、`decline`、`escalate`、`observe` 决策。它**不会调用 GitHub API**，也不会自动合并、打标签、关闭 Issue 或发布版本。

```bash
python skills/ai-project-copilot/scripts/github_evidence_sync.py \
  --input-dir examples/github-export \
  --repo /path/to/repo \
  --output .aipc/github-evidence.json

python skills/ai-project-copilot/scripts/run_state_ledger.py init --repo /path/to/repo
python skills/ai-project-copilot/scripts/run_state_ledger.py sync \
  --repo /path/to/repo --bundle .aipc/github-evidence.json
```

`decline` 必须记录证据说明，`escalate` 必须指定人工负责人。静态看板默认写入已忽略的 `.aipc/` 目录，并会 HTML 转义所有导入字段；清空的看板只表示本地台账没有待处理项，不等同于合并、安全、部署或发布批准。详见[证据与台账说明](skills/ai-project-copilot/references/github-evidence-ledger.md)。

## 快速验证

```bash
python tools/validate_skill.py skills/ai-project-copilot
python skills/ai-project-copilot/scripts/run_skill_evals.py --format json
python -m unittest discover -s tests -v
python -m compileall -q tools tests skills/ai-project-copilot/scripts
```

## 打包

```bash
python tools/package_skill.py skills/ai-project-copilot \
  --output dist/ai-project-copilot.skill.zip
```

## 手机 Working Copy 用户

推荐流程：

1. 在 Working Copy 中 Pull 最新 `main`，确认 Changes 为空。
2. 创建独立分支，或先把引导补丁提交到 `main` 后立即运行一次手动工作流。
3. 导入补丁 ZIP 时选择 **Extract to existing repository**，目标为仓库根目录。
4. 检查 Changes，确保没有多余的外层目录、`.git`、密钥、缓存或大面积删除。
5. 推送后通过 GitHub Actions 运行完整验证。
6. 只在修复分支全绿后创建 Pull Request 并合并。

## 仓库维护地图

变更前请先阅读 [仓库地图与维护边界](docs/repository-map.md)。它说明了
可发布的 Skill 源码、仍在维护的多接口兼容包、自动化、校验清单和生成文件的边界；
已经淘汰的一次性补丁保留在 Git 历史中，而不是继续占用仓库根目录。

## 安全边界

- 启发式分数只能用于安排审查优先级，不是安全或生产就绪证书。
- 真实 OpenAI 网络路径需要使用你自己的 API Key 在目标环境中验证；CI 的注入式 transport 只能证明协议和控制流。
- Gateway 当前只覆盖文本输入和文本/JSON 输出，不支持多模态、tools、background jobs 或绝对费用保证。
- 发布、合并、部署、权限变更和删除等 consequential writes 必须保留人工确认。
- API Key 只应通过环境变量提供，禁止写入仓库、测试、日志或补丁文件。
- 导入的 GitHub JSON 一律按不可信展示数据处理；台账和看板只能帮助复核，不能代替人工批准。

## 版本与发布治理

内部修复批次名称不能代替公开版本。正式公开版本应统一使用 SemVer；发布前应同步：

- `CHANGELOG.md`
- README / 中文 README
- Skill 元数据
- Git tag
- GitHub Release
- 发布包与 SHA-256

GitHub Actions 中引用 `release` environment 并不自动产生审批；还需要在仓库 Settings → Environments → release 中配置 Required reviewers。
