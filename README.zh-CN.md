# AI Project Copilot

一个可移植的 Agent Skill，用于 AI 产品工程、开源维护、代码库理解、风险审查、发布准备、供应链检查和确定性质量验证。

> 当前主分支包含 fix3.0 系列加固。正式版本号应以 Git tag、GitHub Release 和 `CHANGELOG.md` 为准；不要把临时补丁 ZIP 或解压目录当成产品源码提交。

## 核心能力

| 能力通道 | 主要用途 | 对应实现 |
|---|---|---|
| Discover | 映射陌生仓库、生成受控的 Agent 指令草案、审计本地 Skill Stack | `repo_context.py`、`ai_ready_bootstrap.py`、`skill_stack_audit.py` |
| Launch / Retrofit | 从模糊想法选择可演示的 AI 垂直切片，或为现有产品增加高价值 AI 能力 | 24 个蓝图、`rank_blueprints.py`、架构参考 |
| Maintain | 对 Issue 做确定性预分诊，并改善维护者工作流 | `maintainer_triage.py` |
| Review | 对变更风险排序，并验证 review thread 是否收敛 | `change_risk.py`、`review_convergence.py` |
| Release | 给出 SemVer 建议、发布说明草案和迁移阻断项 | `release_intel.py` |
| Secure | 检查 GitHub Actions、MCP 配置、权限、依赖引用和 Skill 完整性 | `supply_chain_guard.py`、`mcp_config_audit.py` |
| Quality | 验证 eval 数据并运行确定性命令用例 | `run_skill_evals.py` |
| Budget | 使用 SQLite 做模型预算路由，并通过 OpenAI Responses Gateway 执行受控请求 | `model_budget_autopilot.py`、`model_budget_gateway.py` |

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

## 安全边界

- 启发式分数只能用于安排审查优先级，不是安全或生产就绪证书。
- 真实 OpenAI 网络路径需要使用你自己的 API Key 在目标环境中验证；CI 的注入式 transport 只能证明协议和控制流。
- Gateway 当前只覆盖文本输入和文本/JSON 输出，不支持多模态、tools、background jobs 或绝对费用保证。
- 发布、合并、部署、权限变更和删除等 consequential writes 必须保留人工确认。
- API Key 只应通过环境变量提供，禁止写入仓库、测试、日志或补丁文件。

## 版本与发布治理

`fix3.0` 可以作为修复批次名称，但正式公开版本建议统一使用 SemVer，例如 `v3.0.0`。发布前应同步：

- `CHANGELOG.md`
- README / 中文 README
- Skill 元数据
- Git tag
- GitHub Release
- 发布包与 SHA-256

GitHub Actions 中引用 `release` environment 并不自动产生审批；还需要在仓库 Settings → Environments → release 中配置 Required reviewers。
