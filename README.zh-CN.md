<p align="center">
  <img src="docs/hero.svg" alt="AI Project Copilot" width="100%" />
</p>

<h1 align="center">AI Project Copilot</h1>

<p align="center">
  把模糊想法或普通仓库，变成可信、可演示、适合开源发布的 AI 项目。<br />
  一个完整 Skill，24 个高吸引力项目蓝图，不做“套壳聊天框”。
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="skills/ai-project-copilot/SKILL.md">查看 SKILL.md</a> ·
  <a href="skills/ai-project-copilot/references/showcase-projects.md">查看全部 24 个项目</a> ·
  <a href="GITHUB_UPLOAD.zh-CN.md">上传 GitHub 说明</a>
</p>

## 它真正解决什么

很多所谓 AI 项目只是在原应用旁边加一个聊天框，功能多、演示花，但看不出用户为什么需要它。这个 Skill 会要求 Codex/ChatGPT：

- 先检查现有仓库与技术栈，不乱重写；
- 找到一个真正有价值、能在 60 秒内展示清楚的 AI 核心；
- 从 24 个项目蓝图中选择最合适的方向；
- 只加入能通过“需求、证据、依据、失败回退、数据边界、评测”六项检查的 AI 功能；
- 先完成一条端到端垂直链路，再扩展更多功能；
- 同时补齐测试、评测、隐私、安全、模型许可、README 和演示脚本；
- 不编造性能、兼容性、用户量、截图或测试结果。

## 已加入的高吸引力项目

项目库覆盖开发工具、本地 AI、研究、自动化、教育、无障碍、数据评测、语音和创意媒体。其中包括你此前重点推进的两个方向：

### Codex Build Visualizer

把 Agent 的 JSONL、工具调用、文件修改、构建、测试、授权与恢复过程，变成隐私安全的交互时间线和依赖图。核心演示是：拖入一次真实运行记录，按时间回放跨平台构建，并准确定位 Windows、macOS、Linux 的分叉点。

### Android Local Video Runtime

一个与模型解耦的安卓本地视频推理 Runtime。应用本身**不训练、不托管、不内置、不再分发模型权重**；用户导入合法获得的模型包，Runtime 负责模型清单、后端适配、内存/温度/耗时监控、生成流水线和 MP4 导出。

其余代表项目包括：AI 代码库地图、PR 维护助手、测试失败回放、Agent 记忆检查器、安全审查助手、多模态研究画布、带引用的文档助手、浏览器工作流工作室、模型评测竞技场、端侧多模态助手、无障碍 Copilot 等。

完整目录：[`showcase-projects.md`](skills/ai-project-copilot/references/showcase-projects.md)

## 安装

在 Codex 中运行：

```text
$skill-installer install https://github.com/sun461941-hub/ai-project-copilot/tree/main/skills/ai-project-copilot
```

也可以手动放入：

```text
用户全局：~/.agents/skills/ai-project-copilot
仓库范围：<仓库>/.agents/skills/ai-project-copilot
```

本仓库生成的 `ai-project-copilot.skill.zip` 只有一个顶层技能目录，可直接用于支持 Agent Skill ZIP 的导入位置。

## 使用示例

```text
$ai-project-copilot 把这个仓库改造成一个能吸引开源用户的 AI 项目。
保留当前技术栈，不要加普通聊天框；选择一个最强垂直切片，补齐测试、评测、
README、隐私边界和 60 秒演示脚本。
```

```text
使用 $ai-project-copilot 继续完善 Codex Build Visualizer：加入隐私安全的运行回放、
跨平台差异比较、可恢复状态、真实演示素材和 GitHub 发布页。
```

```text
使用 $ai-project-copilot 设计安卓本地视频生成 Runtime。应用只提供通用推理能力，
不训练、不托管、不内置、不再分发模型；用户自行导入合法模型。
```

## 内置脚本

按需求排序 24 个项目：

```bash
python skills/ai-project-copilot/scripts/rank_blueprints.py \
  --priorities local-first,visual-demo,developer-tools \
  --constraints privacy,android \
  --limit 5
```

把项目说明、架构决策和演示脚本模板安全复制到另一个仓库：

```bash
python skills/ai-project-copilot/scripts/init_project_docs.py --repo /path/to/project
```

检查仓库是否具备 README、快速启动、演示、测试、CI、评测、隐私/模型边界、示例和明显密钥泄漏等公开证据：

```bash
python skills/ai-project-copilot/scripts/audit_repo.py --repo /path/to/project
```

## 可复现示例

仓库内置了一个与你的安卓本地视频方向对应的确定性示例：

```bash
python skills/ai-project-copilot/scripts/rank_blueprints.py \
  --priorities local-first,video,android,visual-demo \
  --constraints privacy,mobile \
  --limit 3 \
  --json
```

预期第一名是 **Android Local Video Runtime**。完整请求位于 [`examples/sample-request.md`](examples/sample-request.md)，固定输出位于 [`examples/android-local-video-ranking.json`](examples/android-local-video-ranking.json)。这个示例不需要 API Key，也不联网。

## 本仓库已经带上的工程质量

- Skill 目录名、`SKILL.md` 和 frontmatter 校验；
- 引用文件与脚本语法检查；
- 单顶层目录、确定性 ZIP 打包；
- 默认禁止覆盖已有输出；
- 拒绝符号链接、特殊文件和危险归档路径；
- 正向与近似负向触发评测数据；
- Linux、Windows、macOS 跨平台 CI；
- Python 3.10 与 3.14 测试；
- 最小 GitHub Actions 权限；
- GitHub Actions Dependabot 更新。

## 本地验证与打包

```bash
python tools/validate_skill.py skills/ai-project-copilot
python -m unittest discover -s tests -v
python tools/package_skill.py skills/ai-project-copilot \
  --output dist/ai-project-copilot.skill.zip
```

## 许可证

MIT © 2026 `sun461941-hub`
