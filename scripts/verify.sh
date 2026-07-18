#!/bin/bash
# 中文注释：本脚本只验证规则仓库自身，不猜测业务项目技术栈或测试框架。

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# 中文注释：缺少明确依赖时直接失败，避免降级检查产生假阳性。
if ! command -v rg >/dev/null 2>&1; then
  printf '%s\n' '验证失败：需要安装 ripgrep（rg）。'
  exit 1
fi

REQUIRED_FILES=(
  "AGENTS.md"
  "codex/入口.md"
  "codex/架构.md"
  "codex/模块/_模板.md"
  "codex/高风险.md"
  "codex/协作.md"
  "codex/任务池.md"
)

OBSOLETE_FILES=(
  "codex/工作流.md"
  "codex/工作流/只读.md"
  "codex/工作流/微改.md"
  "codex/工作流/轻量.md"
  "codex/工作流/完整.md"
  "codex/工作流/收尾.md"
  "codex/规范.md"
  "codex/影响分析.md"
  "codex/自查清单.md"
  "codex/子代理.md"
  "codex/坑点.md"
  "codex/模式.md"
)

RESULT=0
RULE_FILES=("${REQUIRED_FILES[@]}")
MODULE_ARCHIVES=("codex/模块/_模板.md")

# 中文注释：集中档案和源码目录内的局部 AGENTS 都属于真实模块档案，需要执行相同结构检查。
while IFS= read -r archive; do
  MODULE_ARCHIVES+=("$archive")
  RULE_FILES+=("$archive")
done < <(find codex/模块 -maxdepth 1 -type f -name '*.md' ! -name '_模板.md' | sort)

while IFS= read -r archive; do
  MODULE_ARCHIVES+=("$archive")
  RULE_FILES+=("$archive")
done < <(
  find . -type f -name 'AGENTS.md' \
    ! -path './AGENTS.md' \
    ! -path './.git/*' \
    ! -path './node_modules/*' \
    | sort
)

# 中文注释：必需文件和废弃文件同时检查，防止迁移后保留两套权威规则。
for file in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$file" ]; then
    printf '验证失败：缺少必需文件 %s\n' "$file"
    RESULT=1
  fi
done

for file in "${OBSOLETE_FILES[@]}"; do
  if [ -e "$file" ]; then
    printf '验证失败：废弃文件仍然存在 %s\n' "$file"
    RESULT=1
  fi
done

# 中文注释：AGENTS 的项目入口属于真实加载路径，必须确认引用存在。
while IFS= read -r include_path; do
  if [ ! -f "$include_path" ]; then
    printf '验证失败：AGENTS 引用不存在 %s\n' "$include_path"
    RESULT=1
  fi
done < <(sed -n 's/^@//p' AGENTS.md)

# 中文注释：旧目录前缀和旧流程名称不允许继续出现，不提供双路径兼容。
FORBIDDEN_PATTERN='ai_constraints/codex/|codex/工作流|codex/(规范|影响分析|自查清单|子代理|坑点|模式)\.md'
if rg -n "$FORBIDDEN_PATTERN" . --glob '*.md'; then
  printf '%s\n' '验证失败：发现旧路径或旧规则引用。'
  RESULT=1
fi

# 中文注释：检查规则里明确写出的本地文档引用；带占位符的模板路径不作为真实文件处理。
while IFS= read -r referenced_file; do
  if [[ "$referenced_file" == *'{'* ]]; then
    continue
  fi
  if [ ! -f "$referenced_file" ]; then
    printf '验证失败：规则引用不存在 %s\n' "$referenced_file"
    RESULT=1
  fi
done < <(
  rg --no-filename -o '`codex/[^` ]+\.md`' "${RULE_FILES[@]}" \
    | tr -d '`' \
    | sort -u
)

# 中文注释：限制固定入口和档案模板长度，防止规则再次无边界增长。
check_line_limit() {
  local file="$1"
  local limit="$2"
  local count
  count="$(wc -l < "$file" | tr -d ' ')"
  if [ "$count" -gt "$limit" ]; then
    printf '验证失败：%s 有 %s 行，超过上限 %s 行\n' "$file" "$count" "$limit"
    RESULT=1
  fi
}

check_line_limit "AGENTS.md" 15
check_line_limit "codex/入口.md" 60
check_line_limit "codex/模块/_模板.md" 80

# 中文注释：模块档案必须用精确标题保留理解逻辑和验证稳定性的关键章节。
for archive in "${MODULE_ARCHIVES[@]}"; do
  for heading in '## 目标与边界' '## 代码入口' '## 主流程' '## 核心不变量' '## 修改风险' '## 验证'; do
    if ! rg -F -x -q "$heading" "$archive"; then
      printf '验证失败：%s 缺少章节 %s\n' "$archive" "$heading"
      RESULT=1
    fi
  done
done

# 中文注释：直接检查全部权威规则，包含尚未加入 Git 的新文件。
if rg -n '[[:blank:]]+$' "${RULE_FILES[@]}" scripts/verify.sh; then
  printf '%s\n' '验证失败：规则文件存在行尾空白。'
  RESULT=1
fi

# 中文注释：同时检查未暂存和已暂存 diff，避免空白错误漏过提交边界。
if ! git diff --check; then
  RESULT=1
fi
if ! git diff --cached --check; then
  RESULT=1
fi

if [ "$RESULT" -ne 0 ]; then
  exit "$RESULT"
fi

printf '%s\n' '规则验证通过。'
