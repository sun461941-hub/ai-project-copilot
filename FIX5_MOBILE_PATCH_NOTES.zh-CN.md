# fix5 手机综合补丁说明

## 目标

这份 ZIP 面向只有 iPhone/iPad + Working Copy 的维护方式，不要求你在手机本地执行 Python、Shell 或 `git apply`。

## 为什么需要一次手动 GitHub Actions

真实 `model_budget_gateway.py` 超过 1,400 行。为了避免用旧整文件覆盖你当前 fix3.0 的其它修改，本补丁使用**精确、失败关闭的源码替换器**。它只在当前已确认的源码块完全匹配时修改；一旦源码漂移，就会停止而不是模糊套用。

工作流只允许仓库所有者触发，并要求输入 `APPLY_FIX5`。它不会直接修改 `main`，而是创建新分支 `fix5-mobile-ready`，完成测试后再 Push，供你通过 Pull Request 审查。

## 实际修复

- Provider HTTP error JSON：解析前检查 UTF-8 和 256 层嵌套上限。
- SSE event JSON：解析前进行相同深度限制。
- Token-count response JSON：统一转换成受控 `ProviderError`。
- 本地 request、served-model map、quality-policy JSON：解析前深度保护。
- canonical JSON：补充 `RecursionError` 安全边界。
- 新测试直接加载真实 Gateway，不再只测试补丁器的模拟字符串。
- CI 增加 Gateway hardening 状态检查。
- Release workflow 将“signed/reviewed tag”改成“reviewed tag”，与实际校验能力一致。
- `CHANGELOG.md` 增加 Unreleased 记录。
- 将过时的中文“修复包说明”替换为真实项目中文介绍。
- 一次性 write-capable 工作流会在输出分支中自动删除，合并后不会长期留在仓库。

## 手机操作

1. Pull 最新 `main`，确认 Working Copy 的 Changes 为空。
2. 解压本 ZIP 到现有仓库根目录并允许覆盖。
3. 检查 Changes，预期只有：
   - `.github/workflows/apply-fix5-mobile.yml`
   - `tools/apply_fix5_mobile_release.py`
   - `README.zh-CN.md`
   - `FIX5_MOBILE_PATCH_NOTES.zh-CN.md`
   - `FIX5_CLEANUP_CANDIDATES.txt`
4. Commit 并 Push。
5. GitHub → Actions → `Apply fix5 mobile patch` → Run workflow。
6. `source_ref` 填 `main`，`output_branch` 填 `fix5-mobile-ready`，confirmation 填 `APPLY_FIX5`。
7. 工作流全绿后，创建 `fix5-mobile-ready` → `main` 的 Pull Request。
8. 检查 Files changed 后合并。
9. 回到 Working Copy，切换 `main` 并 Pull。

## 不自动删除的历史文件

仓库根目录存在若干旧 ZIP、patch、上传说明和补丁目录。它们不是运行时依赖，但自动删除可能破坏你希望保留的历史证据，因此本补丁只列出候选项，不自动删除。
