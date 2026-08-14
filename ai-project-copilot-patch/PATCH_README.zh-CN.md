# AI Project Copilot 2.1.0 完整修复补丁

适用基线：`main` 提交 `3e73757`（Release AI Project Copilot 2.1.0）

## 包内内容

- `ai-project-copilot-fixes.patch`：可直接使用 `git apply` 应用的完整补丁。
- `fixed-files/`：补丁涉及的 4 个源码文件和 4 个回归测试文件，便于人工检查。
- `SHA256SUMS.txt`：补丁文件的 SHA-256 校验值。

## 修复内容

1. 修复 `init_project_docs.py` 通过悬空符号链接向仓库外写入模板的问题。
2. 修复 `ai_ready_bootstrap.py` 通过悬空符号链接向仓库外写入指令文件的问题。
3. 修复 `supply_chain_guard.py` 通过悬空符号链接向仓库外写入完整性清单的问题。
4. 修复 `maintainer_triage.py` 遇到损坏、非 UTF-8 或过深嵌套 JSON 时输出 traceback 的问题。

## 应用方法

在项目仓库根目录运行：

```bash
git status --short
git apply --check ai-project-copilot-fixes.patch
git apply ai-project-copilot-fixes.patch
python -m unittest discover -s tests -v
```

建议先确保工作区没有与补丁涉及文件冲突的未提交修改。若 `git apply --check` 失败，请不要强制应用；先确认当前代码是否仍基于 `3e73757`，或手动移植对应修改。

如尚未提交且需要撤销：

```bash
git apply --reverse ai-project-copilot-fixes.patch
```

## 独立核查结果

- 在全新的 `3e73757` 仓库副本中：`git apply --check` 通过。
- 完整测试连续运行两次：每次均为 233/233 通过。
- 4 项新增安全与错误处理回归测试再次单独通过。
- `git diff --check` 通过，补丁反向检查通过。
- Skill 校验通过。
- 确定性 Skill evals 通过。
- Python 源码编译通过。
- 供应链检查为 100/100，无发现项。
- Skill 分发包构建通过。

这些结果确认补丁完整修复了本次识别并复现的 4 个问题，且在现有自动化覆盖范围内没有发现回归。它不构成对所有未来环境或未知缺陷的绝对保证。
