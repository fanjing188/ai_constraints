# ai_constraints v5 — Codex 专属编码范式

面向 Codex 的单人 AI 协作规范。核心目标不是“塞更多上下文”，而是让 Codex 按固定节奏工作：先探索事实，再补齐决策，随后做最小改动，最后拿出验证证据。

## 快速开始

把模板放进项目根的 `ai_constraints/` 目录后，执行：

```bash
bash ai_constraints/setup.sh
```

安装完成后优先填写这三个文件：
- `ai_constraints/codex/入口.md`：项目快照、会话加载顺序、硬规则
- `ai_constraints/codex/任务池.md`：当前里程碑、任务、明确不做的内容
- `ai_constraints/codex/架构.md`：模块地图、依赖关系、全局约定

## 设计原则

- Plan Mode 优先：实现前先探索代码和文档，能从仓库确认的事实不要先问用户
- 当前任务先落盘：动代码前先写 `ai_constraints/codex/当前任务.md`，把范围、边界和验证方式锁住
- 最小 diff：只改本次声明的内容，不顺手修、不夹带抽象、不悄悄扩范围
- 验证闭环：改完先跑 `bash ai_constraints/scripts/verify.sh [file ...]`，再做自查和文档同步
- 中文协作：新增或修改的代码逻辑带中文注释，提交说明用中文

## 安装后的结构

```text
project-root/
├── AGENTS.md
└── ai_constraints/
    ├── codex/
    │   ├── 入口.md
    │   ├── 工作流.md
    │   ├── 任务池.md
    │   ├── 当前任务.md
    │   ├── 架构.md
    │   ├── 规范.md
    │   ├── 模式.md
    │   ├── 坑点.md
    │   ├── 影响分析.md
    │   ├── 自查清单.md
    │   ├── 归档/
    │   └── 模块/
    │       └── _模板.md
    ├── scripts/
    │   └── verify.sh
    └── setup.sh
```

`AGENTS.md` 保持一行入口，固定引用 `@ai_constraints/codex/入口.md`。

## 日常工作流

1. 会话开始先读 `ai_constraints/codex/入口.md` 指定的两个文件：`任务池.md` 和 `当前任务.md`
2. 需要实现时，按 `ai_constraints/codex/工作流.md` 完成探索、定界、写入当前任务、执行与验证
3. 代码完成后执行 `bash ai_constraints/scripts/verify.sh [file ...]`
4. 对照 `codex/自查清单.md` 做 diff 自查，并同步 `架构.md`、`模块/*.md`、`模式.md`、`坑点.md`

## 适用场景

| 场景 | 说明 |
|------|------|
| 小到中型项目 | 默认目标，尤其适合 5 到 20 个模块的服务或应用 |
| 单人 + 单代理工作流 | 重点优化 Codex 在一个线程里持续推进任务 |
| 有测试基础的项目 | `scripts/verify.sh` 会优先帮助你跑到相关测试 |

## 不适用场景

- 多团队长期并行协作：需要更强的流程和仓库治理，不建议只靠这套模板
- 完全没有测试的项目：模板仍可用，但验证环节需要手工补足，收益会打折

## v4 -> v5 迁移

v5 把旧版的多工具兼容叙事收束成 Codex 单一路径，主要变化如下：

- `AI_START.md` 改为 `codex/入口.md`
- `状态/目标.md`、`状态/本次.md` 改为 `codex/任务池.md`、`codex/当前任务.md`
- `上下文/*`、`约束/*` 统一迁入 `codex/`
- `.claude/commands/*`、`hooks/post-tool-use.sh`、`CLAUDE.md` 这一套旧入口全部移除
- 原先依赖 `/plan`、`/review`、`PostToolUse` 的说明，统一改成 `codex/工作流.md` + `scripts/verify.sh`

迁移时不保留旧路径兼容层。把项目根 `AGENTS.md` 改成引用 `@ai_constraints/codex/入口.md` 后，按新目录补齐内容即可。
