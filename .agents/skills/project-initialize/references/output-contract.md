# 初始化输出合同

## 允许输出

- `ai_constraints/codex/架构.md`
- `ai_constraints/codex/模块/{稳定模块名}.md`
- `ai_constraints/codex/项目状态.json`
- 根 `AGENTS.md` 中唯一的 AI Constraints 托管块
- 根 `.agents/skills/project-initialize` 与 `project-update` 两个规定链接

模块名必须稳定、可读、唯一；模块档案不得重复。架构地图只保留目标、技术栈、真实入口、唯一模块索引、跨模块依赖、难以从代码发现的全局契约和已证实复发风险。

## Markdown 托管标记

架构和模块档案必须各包含一次且顺序固定：

```md
<!-- ai-constraints:generated:start -->
机器可更新的代码事实
<!-- ai-constraints:generated:end -->

<!-- ai-constraints:manual:start -->
人工确认内容
<!-- ai-constraints:manual:end -->
```

首次创建时写入两个区块；已有合法文件只替换 generated 区块。manual 区块及文件其它内容逐字保留。标记缺失、重复、嵌套或顺序异常时停止，不重写整文件。

每个模块档案必须包含“目标与边界”“核心不变量与验证”“公开契约与直接消费者”“修改风险”“验证命令”。纯内部模块的公开契约可写“无”；存在公开契约时消费者不能为空，不确定写 `UNKNOWN` 并将后续变化最低按 R3。

## 根接入与链接

根 `AGENTS.md` 的唯一托管块为：

```md
<!-- ai-constraints:start -->
@ai_constraints/codex/入口.md
<!-- ai-constraints:end -->
```

文件不存在时只写此块；已存在时保留块外字节。链接必须是：

```text
.agents/skills/project-initialize -> ../../ai_constraints/.agents/skills/project-initialize
.agents/skills/project-update -> ../../ai_constraints/.agents/skills/project-update
```

同名实体或不同目标链接存在时停止，不复制回退。

## 项目状态

使用 schema 版本 1 和工具包版本 1.0。至少保存：当前 `indexed_commit`、项目内容指纹、合格/排除计数、稳定文件指纹、模块 ID/根/唯一档案/测试候选、已确认契约路径与直接消费者、快速/完整命令和最近实际验证状态。

所有项目路径相对 Git 根；JSON 使用 UTF-8、两空格缩进、键排序和末尾换行。禁止绝对路径、用户名、文件内容、秘密、随机 ID 和无意义时间戳。验证未通过时不要前移基线或记录成功。
