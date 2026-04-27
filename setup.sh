#!/bin/bash
# Codex 专属初始化脚本。
# 支持两种常见运行方式：
# 1. 在项目根执行 `bash ai_constraints/setup.sh`
# 2. 在模板目录执行 `bash setup.sh` 做本地自测

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

WORKDIR="$(pwd)"
SOURCE_DIR=""
DISPLAY_PREFIX="ai_constraints"
VERIFY_COMMAND="bash ai_constraints/scripts/verify.sh [file ...]"
ENTRY_LINE="@ai_constraints/codex/入口.md"

# 先识别当前是在项目根调用，还是在模板目录里直接调用。
if [ -d "$WORKDIR/ai_constraints/codex" ] && [ -f "$WORKDIR/ai_constraints/setup.sh" ]; then
    SOURCE_DIR="$WORKDIR/ai_constraints"
    DISPLAY_PREFIX="ai_constraints"
    ENTRY_LINE="@ai_constraints/codex/入口.md"
elif [ -d "$WORKDIR/codex" ] && [ -f "$WORKDIR/setup.sh" ]; then
    SOURCE_DIR="$WORKDIR"
    DISPLAY_PREFIX="."
    VERIFY_COMMAND="bash scripts/verify.sh [file ...]"
    # 中文注释：模板仓库自测时没有外层 ai_constraints 目录，入口应直接指向本仓库的 codex/入口.md。
    ENTRY_LINE="@codex/入口.md"
else
    echo -e "${RED}❌ 未找到可安装的 ai_constraints 模板${NC}"
    echo "   请在项目根执行：bash ai_constraints/setup.sh"
    echo "   或在模板目录执行：bash setup.sh"
    exit 1
fi

echo -e "${BLUE}=== ai_constraints Codex 初始化 ===${NC}"

# 确保核心目录存在，避免用户手工删掉占位目录后安装异常。
echo -e "${BLUE}1. 校准目录结构${NC}"
mkdir -p "$SOURCE_DIR/codex/模块"
mkdir -p "$SOURCE_DIR/codex/归档"
mkdir -p "$SOURCE_DIR/scripts"

# 统一脚本权限，保证验证脚本可以直接执行。
echo -e "${BLUE}2. 配置脚本权限${NC}"
chmod +x "$SOURCE_DIR/setup.sh"
if [ -f "$SOURCE_DIR/scripts/verify.sh" ]; then
    chmod +x "$SOURCE_DIR/scripts/verify.sh"
fi

# 当前工作目录入口只保留 AGENTS.md，并按运行位置引用 Codex 主入口。
echo -e "${BLUE}3. 生成项目根入口${NC}"
ENTRY_FILE="$WORKDIR/AGENTS.md"

if [ -f "$ENTRY_FILE" ]; then
    if grep -Fxq "$ENTRY_LINE" "$ENTRY_FILE"; then
        echo -e "${YELLOW}⚠️  AGENTS.md 已存在，且已指向 Codex 主入口${NC}"
    else
        echo -e "${YELLOW}⚠️  AGENTS.md 已存在，请手动确认是否需要改成：${ENTRY_LINE}${NC}"
    fi
else
    printf '%s\n' "$ENTRY_LINE" > "$ENTRY_FILE"
    echo -e "${GREEN}✅ AGENTS.md 已创建${NC}"
fi

echo ""
echo -e "${GREEN}✨ 初始化完成${NC}"
echo ""
echo "接下来手动完成："
echo -e "  1. 编辑 ${BLUE}${DISPLAY_PREFIX}/codex/入口.md${NC} — 填项目名、项目快照和会话规则"
echo -e "  2. 编辑 ${BLUE}${DISPLAY_PREFIX}/codex/任务池.md${NC} — 按表格看板填任务、Owner、Scope、验证和验收"
echo -e "  3. 编辑 ${BLUE}${DISPLAY_PREFIX}/codex/架构.md${NC} — 填业务模块地图和依赖"
echo -e "  4. 按需编辑 ${BLUE}${DISPLAY_PREFIX}/codex/子代理.md${NC} — 调整 Main Agent 派发决策规则和子代理权限"
echo -e "  5. 为每个业务模块补齐 ${BLUE}${DISPLAY_PREFIX}/codex/模块/${NC} 下的文档"
echo ""
echo "日常命令："
echo -e "  - 验证改动：${BLUE}${VERIFY_COMMAND}${NC}"
echo -e "  - 查工作流：${BLUE}${DISPLAY_PREFIX}/codex/工作流.md${NC}"
echo -e "  - 查子代理协议：${BLUE}${DISPLAY_PREFIX}/codex/子代理.md${NC}"
