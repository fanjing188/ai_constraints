---
name: project-update
description: 按代码变化增量同步 AI Constraints 项目档案。仅在用户显式调用 $project-update，且目标项目已有项目状态、架构与模块档案时使用；无变化保持零 diff，命中升级条件时保留人工区并全量重扫。
---

# 项目更新

## 执行边界

只修改 `ai_constraints/codex/` 中合法 generated 区块和机器状态。除校正既有托管块外，不修改根 `AGENTS.md`；不修改 Skill 链接、业务源码、业务测试、依赖、CI、生产配置或其他文档，也不要提交或推送。

先完整读取 [references/update-contract.md](references/update-contract.md) 和 [references/escalation-rules.md](references/escalation-rules.md)。任何状态、manual 标记、模块映射或消费者证据异常都停止或按合同升级，不能猜测。

## 更新流程

1. 确认 Git 根与工作包路径，读取 `ai_constraints/codex/项目状态.json`、现有架构和模块档案；记录每个 manual 区块原始字节或哈希。
2. 运行 `python3 ai_constraints/scripts/project_inventory.py changed --project-root "$PROJECT_ROOT" --state ai_constraints/codex/项目状态.json`。状态缺失、损坏或 schema 不兼容时记录原始错误，并直接进入保留 manual 区块的全量重扫。
3. 结果为 `no-change` 时，只运行 `check` 与工作包轻量验证；输出“项目档案已是最新”，不写 JSON、Markdown或时间字段，不运行目标项目完整测试。
4. 结果为 `incremental` 时，只读取变化文件、唯一映射模块、直接消费者、相关测试和必要配置；按 R0–R3 最小闭包验证。
5. 结果要求 `full-rescan` 时，按初始化 Skill 的扫描合同重新枚举和读取全部合格文本，但保留所有现有 manual 区块。新状态仍须在实际验证成功后才替换旧状态。
6. 从当前代码、测试和配置重新确认 generated 事实。更新只替换 generated 区块；`UNKNOWN` 只有得到代码、测试或用户证据后才能消除。
7. 按真实变化处理模块新增、删除或重命名。证据不足、消费者仍存在或待删档案 manual 非空时停止删除并报告待处理项。
8. 运行所需最小验证闭包和 `bash ai_constraints/scripts/verify.sh`。只有全部实际验证成功后，重新扫描并刷新状态中的基线、指纹和最近验证状态。
9. 比对更新前后的 manual 字节、实际 diff 与允许 Scope。manual 有任何变化都视为失败，不用重写来“恢复”。
10. 报告变化文件、风险等级、受影响模块、消费者、实际命令、是否全量升级及残余风险。

核心闭环必须保持“核心不变量 → 实现入口 → 直接消费者 → 可执行验证命令”；无执行证据不得宣称防回归完成。
