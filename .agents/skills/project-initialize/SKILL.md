---
name: project-initialize
description: 首次全仓扫描并生成 AI Constraints 项目档案。仅在用户显式调用 $project-initialize，且工作包位于目标 Git 项目根的 ai_constraints/ 时使用；已有有效初始化状态时停止并改用 $project-update。
---

# 项目初始化

## 执行边界

只修改项目根 `AGENTS.md` 的唯一托管块、项目根 `.agents/skills/` 中两个规定链接，以及 `ai_constraints/codex/` 下的架构、模块档案和状态。不要修改业务源码、业务测试、依赖、CI、生产配置、其他项目文档，也不要提交或推送。

先完整读取 [references/scan-contract.md](references/scan-contract.md) 和 [references/output-contract.md](references/output-contract.md)。任何前置条件、标记或链接冲突都立即停止，不猜测修复。

## 初始化流程

1. 用 `git rev-parse --show-toplevel` 确认项目根；确认工作包真实路径恰为 `$PROJECT_ROOT/ai_constraints`。非 Git 项目或路径不符时明确报错。
2. 检查 `ai_constraints/codex/项目状态.json`。有效状态存在时保持零写入并提示显式调用 `$project-update`；损坏状态也停止，不把初始化当修复入口。
3. 预检两个 Skill 链接和根 `AGENTS.md` 托管标记。目标存在且不是规定链接、标记缺失配对、重复或嵌套时停止。
4. 在临时目录运行：`python3 ai_constraints/scripts/project_inventory.py scan --project-root "$PROJECT_ROOT" --output "$TEMP_DIR/inventory.json"`。
5. 严格按扫描合同逐一读取 `eligible_files` 的当前内容；核对每个路径都已读取或出现在带原因的排除摘要中。
6. 先读治理文档、README、任务脚本、CI、构建与测试配置，再读第一方代码和测试。沿“入口/配置 → 调用/路由 → 领域逻辑 → 状态/数据 → 副作用 → 测试/构建”追踪主要能力。
7. 只从代码、测试和现有配置证据识别模块、不变量、公开契约、直接消费者及验证命令。证据不足写 `UNKNOWN`，不要把文件名或惯例写成事实。
8. 按输出合同生成或更新架构与每个核心模块的唯一档案。现有文件只替换合法 generated 区块，manual 区块逐字保留；无标记的人工文件停止处理。
9. 安装两个规定的相对 Skill 链接，并为根 `AGENTS.md` 创建或校正唯一托管块；保留块外全部字节。
10. 从真实任务脚本或 CI 选择安全聚合门禁并实际运行一次，同时运行 `bash ai_constraints/scripts/verify.sh`。命令不存在或无法执行时保留原始失败，不写成功状态。
11. 重新扫描最终项目状态，把已确认模块、契约、命令和实际验证状态合入稳定排序的 `ai_constraints/codex/项目状态.json`。禁止绝对路径、文件内容、秘密、随机 ID 或时间戳。
12. 再运行工作包验证，核对实际 diff 仅在允许范围，并报告读取/排除计数、模块、`UNKNOWN`、实际命令与残余风险。

不要为了让档案显得完整而补写未证实事实。核心闭环必须是“核心不变量 → 实现入口 → 直接消费者 → 可执行验证命令”。
