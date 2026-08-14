# 上传到 GitHub

## 最省事的方法

1. 解压 `ai-project-copilot-github-ready.zip`。
2. 在 GitHub 新建公开仓库，建议名称：`ai-project-copilot`。
3. 把解压后的 `ai-project-copilot` 文件夹内全部内容上传到仓库根目录。注意保留 `.github`、`.gitattributes`、`.gitignore` 等隐藏文件。
4. 提交到 `main` 分支；仓库中的 CI 会自动校验 Skill、运行测试并检查打包结果。
5. 打标签 `v1.0.0` 后，发布工作流会生成技能 ZIP 和 SHA-256 校验文件。

## 使用 Git 命令上传

```bash
git init
git add .
git commit -m "Launch AI Project Copilot v1.0.0"
git branch -M main
git remote add origin https://github.com/sun461941-hub/ai-project-copilot.git
git push -u origin main
```

## 上传后检查

- 首页封面、徽章、英文与中文 README 是否正常显示；
- Actions 中的 `Validate and test` 是否全部通过；
- `skills/ai-project-copilot/SKILL.md` 能否从网页直接打开；
- 仓库地址是否与 README 中的安装命令一致；
- 不要上传任何模型权重、真实 API Key、私有运行记录或用户数据。

## 安装命令

仓库公开后，可在 Codex 中使用：

```text
$skill-installer install https://github.com/sun461941-hub/ai-project-copilot/tree/main/skills/ai-project-copilot
```
