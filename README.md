# AI Constraints

将整个目录放到目标 Git 项目根的 `ai_constraints/`，从该目录启动 Codex，并显式调用 `$project-initialize`。初始化会扫描符合合同的第一方文本、生成项目档案，并在项目根安装两个相对 Skill 链接。

后续从目标项目根显式调用 `$project-update`。无业务变化时只检查状态并保持零 diff；契约或未知影响半径会扩大到直接消费者或全量重扫。

工作包只可修改根 `AGENTS.md` 的托管块、规定的 Skill 链接和 `ai_constraints/codex/` 项目档案。它不读取秘密内容，不修改业务代码、测试、依赖、CI 或生产配置，也不会提交、推送或部署。
