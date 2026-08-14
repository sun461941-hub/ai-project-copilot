<p align="center">
  <img src="docs/hero.svg" alt="AI Project Copilot" width="100%" />
</p>

<h1 align="center">AI Project Copilot 2.1</h1>

<p align="center">
  一个同时覆盖 <b>AI 产品工程 + 开源维护者智能</b> 的可移植 Agent Skill。<br />
  先理解仓库，再路由任务；再做 PR 风险、发布、安全、评测与可验证交付。
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="skills/ai-project-copilot/SKILL.md">查看 Skill</a> ·
  <a href="ECOSYSTEM_BENCHMARK.md">生态对标</a> ·
  <a href="ROADMAP.md">路线图</a> ·
  <a href="CHANGELOG.md">更新日志</a>
</p>

> **v2.1 增加了可执行的 OpenAI 模型预算网关和确定性评测器。** 启发式报告仍不会冒充语义证明，发布、合并、权限等高后果操作继续由人决定。

## v2 九大能力通道

| 通道 | 能力 | 确定性工具 |
|---|---|---|
| **Discover** | 代码库/架构上下文、AI-ready 指令、Skill Stack 盘点与冲突 | `repo_context.py`、`ai_ready_bootstrap.py`、`skill_stack_audit.py` |
| **Launch** | 从模糊想法选择最强 AI 垂直切片 | 24 个蓝图 + `rank_blueprints.py` |
| **Retrofit** | 给现有项目增加真正有价值的 AI 能力 | 功能门禁 + 架构参考 |
| **Maintain** | Issue 预分类、贡献者 onboarding、good first issue | `maintainer_triage.py` |
| **Review** | PR/diff 风险、fix/decline/escalate、Review 收敛门禁 | `change_risk.py`、`review_convergence.py` |
| **Release** | SemVer、更新日志、破坏性变更与发布阻塞项 | `release_intel.py` |
| **Secure** | GitHub Actions、MCP 配置、权限、Action/包引用、供应链 | `supply_chain_guard.py`、`mcp_config_audit.py` |
| **Quality** | 校验评测数据、运行确定性案例、支持实测改进循环 | `run_skill_evals.py`、`evals/evals.json` + 质量流程 |
| **Showcase** | README、Demo、Release 证据和开源展示 | 演示/发布参考 |

## AIPC Context Accelerator：少读、少绕、关键验证不打折

v2.0 正式加入 Codex/编码 Agent 的上下文加速层。它**不能**提高 OpenAI 后端本身的 tokens/s、绕过额度或强制修改模型推理档位；它优化的是一次任务真正需要读取和处理的东西。

```text
任务 → FAST / BALANCED / DEEP
    → 变化文件 + AGENTS 指令链
    → 小型初始上下文包
    → 定向工具/测试
    → 压缩后的失败证据
    → 精确指纹的非关键证据复用
    → 关键/最终门禁重新执行
```

先判断任务预算：

```bash
python skills/ai-project-copilot/scripts/token_governor.py \
  --prompt "修复 README 里的错别字" \
  --changed-file README.md \
  --format markdown
```

然后一次生成任务相关的上下文包：

```bash
python skills/ai-project-copilot/scripts/context_accelerator.py \
  --repo /path/to/repo \
  --task "审查认证模块修改" \
  --git-status \
  --format markdown
```

测试输出太长时，先保留原始日志，再生成小型证据视图：

```bash
mkdir -p .aipc
pytest -v > .aipc/raw-test.log 2>&1
python skills/ai-project-copilot/scripts/tool_output_compactor.py \
  --input .aipc/raw-test.log \
  --max-lines 80
```

压缩结果保留失败/汇总附近的上下文、遗漏行数和原始标准化日志的 SHA-256；原始日志始终是最终证据来源。`evidence_cache.py` 只允许复用**通过且非关键**、并且“命令 + 声明输入文件内容哈希”完全一致的证据。安全、Release、部署、迁移、权限和最终集成门禁必须重跑。`.aipc/` 已加入 `.gitignore`。

核心 `SKILL.md` 保持在 **250 行以内**，更多细节按通道放入 references。仓库的 [`benchmarks/`](benchmarks/) 提供可复现的上下文效率测试；其中路径字符和日志字符是**上下文大小代理指标，不是实测 Codex Token**。要报告真实 input/cached/reasoning/output tokens，必须读取客户端/API 的 usage telemetry。

一次 Linux / Python 3.13.5 本地测试（每个上下文场景重复 15 次）得到：

| 场景 | 仓库文件 | 初始选择文件 | 相对全部路径的字符代理缩减 | Accelerator 与完整 Repo Map 耗时 |
|---|---:|---:|---:|---:|
| FAST 文档 | 1,400 | 3 | 99.8769% | 0.571 ms vs 29.544 ms（本地侦察约 **51.7×**） |
| BALANCED 功能 | 1,800 | 7 | 99.6854% | 34.966 ms vs 34.050 ms（约 **2.7% 额外开销**） |
| DEEP 安全/发布 | 2,400 | 9 | 99.6637% | 49.521 ms vs 46.851 ms（约 **5.7% 额外开销**） |

另一个 5,003 行的合成测试日志从 184,074 字符压到 949 字符（99.4844%），两个失败标记、最终汇总和原始标准化日志 SHA-256 均保留。以上只是确定性预处理数据，**不能冒充 Codex 模型生成速度或真实 Token 节省比例**。

## 模型预算自动驾驶

**给首选模型设置透明的费用目标。** 应用可以让每个用户预设普通任务使用首选模型的周期费用上限；触发占比控制线或分配上限后，普通任务自动进入经过审核的低成本模型梯队。

```text
每月 $20 模型组合
质量模型分配上限       <= 40%  ████████
共享余额目标           >= 60%  ████████████
```

这是普通任务的首选模型分配上限，不是隔离资金池：受保护任务和一次质量升级可以超过它，其他请求也可能先用完共享的周期准入额度。

`model_budget_autopilot.py` 使用不可变 SQLite 路由决策和原子费用预留，处理冷启动、并发请求、防抖恢复线、安全/发布/迁移任务保护、迟到 usage、用量超出预估、请求内容绑定、提供商响应去重、可续期租约，以及最多一次由质量证据触发的向上重试。只有本次预计费用不高于请求模型时才会选择回退模型。

v2.1 新增 `model_budget_gateway.py`，把这套控制面接到可实时调用的 OpenAI
Responses：它并行精确计数审核梯队中每个模型的输入，在同一原子路由中绑定
最终选中模型的请求字节，流式输出、续租、结算 Provider 报告的 usage，记录 TTFT/E2E，
运行可选的确定性质量命令，并且最多只执行一次预算允许的升级。

```bash
read -rsp "OpenAI API key: " OPENAI_API_KEY && export OPENAI_API_KEY
printf '\n'
cp skills/ai-project-copilot/assets/templates/openai-response-request.json request.json

python skills/ai-project-copilot/scripts/model_budget_gateway.py \
  --db .aipc/model-budget.sqlite3 \
  --user opaque-trusted-user \
  --request-id task-001-attempt-1 \
  --logical-request-id task-001 \
  --request request.json \
  --task-class routine \
  --format json
```

请先配置账本，并把模板里的模型与价卡替换为你实际审核过的 OpenAI 模型。
Key 只从环境变量读取，不进入账本、报告或质量子进程。v2.1 的执行器只接受文本输入并返回文本/JSON；
图像、音频、文件、Prompt Template、Tools 和 Background 请求会 fail-closed，直到它们的执行循环、可变附加费用和租约生命周期能被正确对账。
CI 里验证的是确定性传输与协议；只有用你自己的 Key 成功执行，才是当前环境中真实 Provider 路径已打通的证据。详见
[`openai-responses-gateway.md`](skills/ai-project-copilot/references/openai-responses-gateway.md)。

运行不联网的确定性证明场景：

```bash
python skills/ai-project-copilot/scripts/model_budget_autopilot.py \
  simulate --format json
```

这个合成输出会列出第一次降级、一个刻意构造的 incomplete 响应、唯一一次获准升级，以及最终通过质量门禁的模型；它是离线控制流测试，不是真实模型调用。这个功能控制的是**费用分配**；低成本模型不保证使用更少 Token，因此在对同一批真实任务做前后对照之前，Token 节省仍应标记为未知。集成、价格、生命周期和信任边界见 [`references/model-budget-autopilot.md`](skills/ai-project-copilot/references/model-budget-autopilot.md)。

同一批冻结任务分别跑完 baseline/candidate 后，再计算实测效果；失败和重试也会进入总数：

```bash
python skills/ai-project-copilot/scripts/compare_efficiency_runs.py \
  --baseline baseline.jsonl \
  --candidate candidate.jsonl \
  --require-improvement \
  --format markdown
```

报告会分别给出实测 Token 节省率、价卡成本节省率、TTFT、端到端延迟下降和
加速倍数。它会拒绝请求模板、质量策略配置或定价策略指纹不一致的对照。请求指纹还绑定请求模型与任务类别；定价指纹绑定经审核的模型梯队与价卡、受保护任务策略、served-model 映射、固定额外费用和默认 Service Tier。这些指纹仍不能证明外部 evaluator 可执行文件未变，也不能对账 Provider 发票。
单次请求仍保持 `token_savings=null`，因为它没有任务对齐的反事实。

## 对标主流 Skill 后加入了什么

我们对比了 Agent Skills 开放规范、Anthropic 官方 Skills、GitHub Copilot/awesome-copilot、Vercel Skills 生态和 Gemini CLI Extensions 中反复出现的成熟模式：

- Progressive Disclosure（渐进式加载）；
- 确定性脚本；
- 代码库地图和上下文选择；
- PR Review 循环；
- SemVer / Release Notes；
- 安全、权限、供应链；
- Skill Evals；
- 多角色/多 Agent 协作；
- JSON + Markdown 结构化输出；
- 跨 Agent 可移植性；
- 高风险写操作必须预览和确认。

具体对标见 [`ECOSYSTEM_BENCHMARK.md`](ECOSYSTEM_BENCHMARK.md)。

## 先理解仓库，再动手

```bash
python skills/ai-project-copilot/scripts/repo_context.py \
  --repo /path/to/repo \
  --task "修改登录流程但不能破坏移动端兼容性" \
  --format markdown
```

它会给出：语言、依赖清单文件名、入口候选、测试、CI、文档、治理文件、当前任务最相关的文件，以及明显证据缺口；它不会解析依赖图或源码语义。

如果要把仓库准备成更适合 Codex / Copilot / 其他 Agent 协作的结构，可以先生成**不覆盖现有规则**的指令草稿：

```bash
python skills/ai-project-copilot/scripts/ai_ready_bootstrap.py \
  --repo /path/to/repo \
  --target agents \
  --target copilot \
  --json
```

还可以只读扫描本地 Skill Stack：

```bash
python skills/ai-project-copilot/scripts/skill_stack_audit.py \
  --project /path/to/repo \
  --format markdown
```

它会发现重复 Skill 名称、过长/缺失描述、以及可能互相抢触发的高重叠 Skill；不会安装、更新或执行任何第三方 Skill。

## PR 风险引擎

```bash
python skills/ai-project-copilot/scripts/change_risk.py \
  --patch change.diff \
  --format markdown
```

重点识别：

- 登录/鉴权/权限；
- 数据库 Schema 与迁移；
- 公共 API/契约；
- GitHub Actions / 依赖供应链；
- 部署与配置；
- 大规模 diff；
- 改源码却没有明显测试变更。

然后进入三遍评审：**风险面 → 行为正确性 → 失败/对抗路径**。

每条 Review Thread 不再一律照改，而是分为：

- **fix**：确实应该修；
- **decline**：建议错误/无依据/超范围；
- **escalate**：需要维护者做架构、安全、迁移或产品判断。

多轮 Review 时，把线程状态保存成 JSON，再用确定性的收敛门禁检查是否还有 Agent 应处理却没处理的线程：

```bash
python skills/ai-project-copilot/scripts/review_convergence.py \
  --threads-json review-state.json \
  --format markdown
```

通过门禁只代表“Review 循环已收敛”，**不代表自动允许 Merge**。

## Release Intelligence

```bash
python skills/ai-project-copilot/scripts/release_intel.py \
  --repo /path/to/repo \
  --from-ref v1.1.0 \
  --current-version 1.1.0 \
  --format markdown
```

输出：

- 建议 SemVer；
- Breaking / Features / Fixes / Security 等分类；
- 迁移说明要求；
- 确定性阻塞项；
- Draft Release Notes。

**生成版本建议不等于发布。** Tag/Release 仍然属于需要确认的高后果写操作。

## GitHub Actions、MCP 与供应链 Guard

```bash
python skills/ai-project-copilot/scripts/supply_chain_guard.py \
  --repo /path/to/repo \
  --format markdown
```

会检查：

- 是否显式最小化 `permissions`；
- `pull_request_target` / `workflow_run` 等高权限触发器；
- 高权限触发器下是否 checkout 不可信代码；
- `${{ github.event... }}` 直接插入 shell；
- Action 是否使用可变 tag/branch；
- Skill 文件 SHA-256 完整性清单（只有显式传 `--manifest` 才写文件）。

如果仓库配置了 MCP Server，还可以只读检查：

```bash
python skills/ai-project-copilot/scripts/mcp_config_audit.py \
  --repo /path/to/repo \
  --format markdown
```

重点发现硬编码 secret、`http://` 远端、shell wrapper，以及 `npx/uvx/bunx` 等动态启动器未锁版本的问题；它不会执行 MCP Server。

## Quality Engine

v2 新增完整 Skill 级 `evals/evals.json`，覆盖：

- 代码库发现；
- 高风险 PR；
- Release 规划；
- GitHub Actions 安全；
- Issue/贡献者流程；
- Review Thread 决策；
- 可衡量的质量循环；
- 供应链清单与 MCP 配置边界；
- 多角色协作；
- AI-ready 指令生成与本地 Skill Stack 审计；
- Review 收敛状态；
- 不应该触发 Skill 的近似任务。

质量循环是：

**提炼行为要求 → 跑基线 → 做一次可验证修改 → 重跑同一组检查 → 用证据决定保留或回滚。**

运行 Skill 内置的结构校验和确定性案例：

```bash
python skills/ai-project-copilot/scripts/run_skill_evals.py \
  --format markdown
```

它会校验 25 条静态 Eval、20 条触发样本，并以 `shell=False` 运行 Skill 内置的
3 条确定性命令案例。报告明确写出 `semantic_grading_performed=false`：这些 Prompt
expectation 不是模型实答，也没有被语义评分。

## 可选多 Agent 协作

如果客户端支持子 Agent，可拆为：

- mapper/planner；
- implementer；
- reviewer；
- security；
- release/verifier。

只读分析可以并行；真正写文件要串行，最后仍只有一个统一证据门禁，避免多个 Agent 互相覆盖。

## AI-ready 与 Skill Stack Intelligence

v2 也把主流“元 Skill”能力并进来，但刻意不做自动安装器：

- 先做代码库 Context Map，再开始修改；
- 安全生成 `AGENTS.md` 与 Copilot 指令草稿，默认不覆盖；
- 扫描项目级/用户级常见 Skill 目录；
- 找重复名称、触发描述重叠和可移植性问题；
- 网络发现、安装、升级第三方 Skill 必须作为独立、显式授权动作。

## 原有 AI 产品工程能力全部保留

- 24 个高吸引力项目蓝图；
- Codex Build Visualizer；
- Android Local Video Runtime；
- RAG、Agent、Multimodal、Voice、Media Generation、Local Model；
- 隐私、模型许可证、工具权限、安全边界；
- README、Demo Script、评测和跨平台 CI；
- 确定性 Skill 验证与打包。

## 安装

```text
$skill-installer install https://github.com/sun461941-hub/ai-project-copilot/tree/main/skills/ai-project-copilot
```

手动仓库级安装：

```bash
mkdir -p .agents/skills
cp -R skills/ai-project-copilot .agents/skills/ai-project-copilot
```

## 验证

```bash
python tools/validate_skill.py skills/ai-project-copilot
python -m unittest discover -s tests -v
python tools/package_skill.py skills/ai-project-copilot \
  --output dist/ai-project-copilot.skill.zip
```

## 60 秒 Demo 路径

可以完全只读地展示一条维护者工作流：**仓库地图 → 高风险 PR → Release Intelligence → GitHub Actions 安全检查 → 人工确认门禁**。没有合适的真实仓库时，可以使用仓库内置 JSON 示例进行可复现演示。

## 限制与模型/Provider 边界

- 风险分数只是注意力优先级，不是安全/正确/生产可用证明；
- Skill 不内置、不重新分发第三方模型权重，模型来源、许可证和数据边界必须单独确认；
- Skill Stack 扫描不等于信任或安全认证，也不会自动安装/升级；
- 语义代码审查仍然必须读取真实代码和测试，确定性脚本只能提供证据和门禁；
- GitHub/MCP 写操作能否执行取决于客户端与权限，跨客户端的最低共同能力始终是只读分析。
- 字符/路径压缩率和本地运行耗时只是确定性代理指标，不能冒充真实 Codex Token 节省或后端速度提升。
- 可实时调用的网关只覆盖文本输入、文本/JSON 输出的 OpenAI Responses 和价卡结算，不覆盖多模态输入、Tools、后台任务、Provider 发票对账，也不是绝对费用上限。
- 真实 Token/成本/延迟百分比必须来自同一配置成功门禁、经审核价卡下、任务对齐的 baseline/candidate；比较器校验请求模型/任务类别、请求模板、质量策略配置、定价/保护策略和 served-model 映射，不校验外部 evaluator 二进制，也不对账 Provider 发票。项目不宣称一个适用于所有任务的固定节省率。

## v2 核心原则

1. **先检查，再修改。**
2. **证据优先于模型自信。**
3. **一个完整工作流优于一堆 Prompt。**
4. **重复分类交给确定性脚本，模糊判断交给模型。**
5. **后果越大，人类控制越强。**
6. **渐进式加载，能力变强但上下文不爆炸。**
7. **不伪造 benchmark、兼容性、安全性、维护活跃度、用户量或 Star。**

## License

MIT © 2026 `sun461941-hub`
