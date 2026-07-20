# 智能体协作说明

> 目标读者：在本仓库中执行 git 提交、推送等操作的 AI 智能体或自动化脚本。
> 读完本文档后，应能正确地将代码同步到**两个远程仓库**。

---

## 双远程仓库策略（必读）

本仓库同时托管在两个平台，**每次推送代码时，必须同时推送到两个远程**，不能只推其中一个。

| 远程名 | 地址 | 平台 |
|--------|------|------|
| `origin` | `https://github.com/XMcbdggh/trans_model.git` | GitHub |
| `gitee` | `https://gitee.com/blade-ai/sim-blast.git` | Gitee |

默认分支：`main`

---

## 推送流程

完成 commit 后，按顺序执行：

```bash
git push origin main
git push gitee main
```

如果当前分支已设置上游跟踪，也可以分别执行：

```bash
git push origin
git push gitee
```

**不要**只执行 `git push` 然后结束——当前分支可能只跟踪其中一个远程，会导致另一个仓库落后。

---

## 首次配置远程（仅在新 clone 的仓库中需要）

```bash
git remote add origin https://github.com/XMcbdggh/trans_model.git
git remote add gitee  https://gitee.com/blade-ai/sim-blast.git
```

验证：

```bash
git remote -v
```

应看到 `origin` 和 `gitee` 各一行 fetch、一行 push。

---

## 提交与推送检查清单

智能体在完成代码变更后，推送前请确认：

1. `git status` 工作区干净，变更已 commit
2. `git push origin main` 成功
3. `git push gitee main` 成功
4. 两个推送均无报错后再结束任务

---

## 注意事项

- **禁止 force push** 到 `main`，除非用户明确要求
- 两个远程应保持相同的 commit 历史；若其中一个推送失败，应修复问题后补推，而不是忽略
- 认证失败时提示用户检查对应平台（GitHub / Gitee）的账号权限或 token，不要跳过失败的远程

---

## 相关文档

- 项目功能与架构：[`README.md`](README.md)
- 3D 建图管道详解：[`AGENT_3D_PIPELINE.md`](AGENT_3D_PIPELINE.md)
