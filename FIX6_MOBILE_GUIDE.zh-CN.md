# fix6 手机端应用说明

这份补丁适用于只能使用 iPhone/iPad 上 Working Copy 的情况。

它分成两步：

1. 先把本 ZIP 解压覆盖到仓库根目录，提交并 Push；
2. 再在 GitHub Actions 手动运行 **Apply fix6 source patch**，由 GitHub 在新分支中修改真实源码并完成测试。

## 第一步：在 Working Copy 导入 ZIP

1. 先对 `main` 执行 Pull，并确认 Changes 为空；
2. 将 ZIP 分享到 Working Copy；
3. 选择现有 `ai-project-copilot` 仓库；
4. 选择解压到仓库根目录并允许覆盖；
5. Changes 中应只出现以下 5 个文件：

```text
.github/workflows/apply-fix5-mobile.yml
.github/workflows/apply-fix6-mobile.yml
.github/workflows/release.yml
tools/apply_fix6_mobile_release.py
FIX6_MOBILE_GUIDE.zh-CN.md
```

不要提交 `.git`、`__pycache__`、API Key、数据库或额外外层目录。

查看 Diff 时，`.github/workflows/release.yml` 应当只改一处文字：把 `signed/reviewed tag` 改为 `reviewed tag`。如果该文件出现大量其它变化，先不要提交。

建议提交信息：

```text
chore: install fix6 mobile patch
```

然后 Push 到 `main`。

## 第二步：运行 fix6

在 GitHub 网页进入：

```text
Actions → Apply fix6 source patch → Run workflow
```

填写：

```text
output_branch: fix6-ready
confirmation: APPLY_FIX6
```

工作流成功后会创建 `fix6-ready` 分支，并在运行摘要中给出创建 Pull Request 的链接。

若提示分支已存在，请重新运行并把分支改为：

```text
fix6-ready-2
```

若最后 Push 步骤出现 `403` 或 `permission denied`，进入仓库 `Settings → Actions → General → Workflow permissions`，选择 `Read and write permissions` 后保存，再换一个新分支名重新运行。不要 Force Push。

## 第三步：合并

进入新建的 Pull Request，确认真实修改至少包含：

```text
skills/ai-project-copilot/scripts/model_budget_gateway.py
tests/test_fix6_gateway_hardening.py
CHANGELOG.md
```

等待 PR 的全部 CI 变绿后再合并到 `main`。合并后在 Working Copy 切回 `main` 并 Pull。

## fix6 实际修复内容

- Provider HTTP error、SSE event、token-count response 的深层 JSON 解析保护；
- 本地 request、served-model map、quality-policy 的解析前深度保护；
- `json.dumps()` 和 CLI 最外层的递归异常边界；
- 直接导入真实 Gateway 的回归测试；
- 旧 fix5 写入工作流改为只读退役提示；
- fix6 只在新审核分支提交真实源码、测试和 Changelog，不尝试由 Actions 修改 workflow 文件；
- Release 输入说明不再声称已经验证 tag 的密码学签名。

## 失败保护

补丁器使用精确匹配并“失败关闭”：源码与预期不一致时会停止，不会猜测修改，也不会强制覆盖。不要在失败后 Force Push；保留失败日志即可。

## 暂未自动删除的历史文件

仓库根目录仍有旧 ZIP、旧 patch、上传说明和 `ai-project-copilot-patch/` 等历史交付物。fix6 不自动删除，避免误删；应在修复合并后单独建一个清理提交逐项审核。
