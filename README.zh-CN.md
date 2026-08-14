# AI Project Copilot 完整修复包

适用仓库：`sun461941-hub/ai-project-copilot`

补丁基线：`245cefcc31baf72292addac4e2fdf58ead145e8e`（`fix`）

## 重要说明

上一次失败的直接原因是：解压后的 `ai-project-copilot-patch/` 目录被提交到了仓库，但其中的补丁没有应用到项目真实路径，所以 CI 仍然运行旧源码。

**不要把本修复包或解压后的修复包目录直接提交进仓库。**

## 推荐应用方式

在仓库根目录执行：

```bash
git checkout main
git pull --ff-only
git status --short
git apply --check /path/to/ai-project-copilot-repair-from-245cefc.patch
git apply /path/to/ai-project-copilot-repair-from-245cefc.patch
python -m unittest discover -s tests -v
git add -A
git commit -m "fix: apply complete CI repair"
git push
```

`git status --short` 在应用前应为空。如果 `git apply --check` 失败，请不要强行提交，应先确认当前 `main` 是否仍以 `245cefc` 为最新提交。

## 文件说明

- `ai-project-copilot-repair-from-245cefc.patch`：推荐给开发者使用的正式 Git 补丁；会修改真实源码，并删除上次误提交的 `ai-project-copilot-patch/` 目录。
- `ai-project-copilot-fixed-source.zip`：完整的已修复源码快照，不包含 `.git`；用于核对或在无法应用补丁时导入。
- `SHA256SUMS.txt`：上述两个文件的 SHA-256 校验值。

## 本次修复

- 在 JSON 解析前执行不依赖 Python 版本的安全嵌套深度检查，修复 Ubuntu / Python 3.14 的 `invalid_root` 与 `invalid_json` 行为差异。
- 正确忽略 JSON 字符串内部的方括号和转义引号，避免误报。
- 修复文档初始化、AI 配置引导和供应链清单写入中的悬空符号链接逃逸风险。
- 让维护者分诊工具对损坏、非 UTF-8 或过深的 JSON 返回干净的 CLI 错误，而不是 traceback。
- 删除提交 `245cefc` 中误加入仓库的旧补丁目录。
- 增加相应回归测试。

## 验证结果

- 定向 JSON 回归测试：通过。
- 完整单元测试：`234/234`，连续四轮通过（包括全新克隆应用补丁后，以及从最终交付包解压源码后运行）。
- Skill 验证：通过。
- 确定性 Skill evals：通过（25 个静态用例、20 个触发用例、3 个命令用例）。
- Python 源码编译：通过。
- Skill 打包与 ZIP 完整性校验：通过。
- `git diff --check` 与 `git apply --check`：通过。

当前执行环境提供 Python 3.12，没有本地 Python 3.14 可执行文件；针对 3.14 的失败路径已通过“解析前”深度检查改为版本无关逻辑，并由 300 层输入的确定性回归测试覆盖。
