#!/bin/bash
# Codex 手动验证脚本。
# 目标是根据改动文件尽量找到对应测试，帮助在实现后快速补第一轮验证。

set -uo pipefail

# 统一输出格式，方便在终端里快速扫结果。
print_line() {
  printf '%s\n' "$1"
}

# 判断文件是否值得参与测试匹配，文档和锁文件直接跳过。
is_code_candidate() {
  case "$1" in
    *.md|*.json|*.yaml|*.yml|*.lock|*.txt)
      return 1
      ;;
    ai_constraints/*)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

# 收集改动文件。无参数时使用 git diff 自动找本地改动。
collect_changed_files() {
  if [ "$#" -gt 0 ]; then
    printf '%s\n' "$@"
    return
  fi

  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    # 中文注释：关闭 quotePath 并排除删除文件，避免中文路径变转义串，也避免对已删除文件做无效测试匹配。
    git -c core.quotepath=off diff --name-only --diff-filter=d
  fi
}

# 把候选测试文件加入列表，避免重复。
append_unique() {
  local candidate="$1"

  [ -z "$candidate" ] && return
  [ ! -e "$candidate" ] && return

  if [[ " ${TEST_FILES[*]-} " != *" $candidate "* ]]; then
    TEST_FILES+=("$candidate")
  fi
}

# 根据源码文件路径推测相关测试文件。
collect_tests_for_file() {
  local file="$1"
  local dir=""
  local base=""
  local before_count=0

  before_count="${#TEST_FILES[@]}"

  if [[ "$file" =~ \.(test|spec)\. ]] || [[ "$file" =~ _test\. ]] || [[ "$file" == *"/test_"* ]]; then
    append_unique "$file"
    return
  fi

  dir="$(dirname "$file")"
  base="$(basename "$file")"
  base="${base%.*}"

  while IFS= read -r matched; do
    append_unique "$matched"
  done < <(
    find "$dir" -maxdepth 2 \
      \( -name "${base}.test.*" \
         -o -name "${base}.spec.*" \
         -o -name "test_${base}.*" \
         -o -name "${base}_test.*" \) \
      -not -path "*/node_modules/*" \
      2>/dev/null
  )

  if [ "${#TEST_FILES[@]}" -gt "$before_count" ]; then
    return
  fi

  while IFS= read -r matched; do
    append_unique "$matched"
  done < <(
    find "$dir/.." -maxdepth 3 \
      \( -path "*/__tests__/*${base}*" \
         -o -path "*/tests/*${base}*" \
         -o -path "*/test/*${base}*" \) \
      \( -name "*.test.*" -o -name "*.spec.*" -o -name "*_test.*" -o -name "test_*" \) \
      -not -path "*/node_modules/*" \
      2>/dev/null
  )
}

# 执行 Node 项目的测试命令。
run_node_tests() {
  if grep -q '"vitest"' package.json 2>/dev/null; then
    npx vitest run "${TEST_FILES[@]}"
    return $?
  fi

  if grep -q '"jest"' package.json 2>/dev/null; then
    npx jest "${TEST_FILES[@]}" --no-coverage
    return $?
  fi

  if grep -q '"mocha"' package.json 2>/dev/null; then
    npx mocha "${TEST_FILES[@]}"
    return $?
  fi

  print_line "⚠️  找到了 package.json，但未识别到支持的测试框架（vitest / jest / mocha）"
  return 0
}

# 执行 Go 项目的测试命令。
run_go_tests() {
  local test_dirs=()
  local file=""
  local dir=""

  for file in "${TEST_FILES[@]}"; do
    dir="./$(dirname "$file")"
    if [[ " ${test_dirs[*]} " != *" $dir "* ]]; then
      test_dirs+=("$dir")
    fi
  done

  go test "${test_dirs[@]}"
}

# 执行 Python 项目的测试命令。
run_python_tests() {
  python -m pytest "${TEST_FILES[@]}" -q
}

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$PROJECT_ROOT" || exit 1

CHANGED_FILES=()
TEST_FILES=()

while IFS= read -r file; do
  [ -z "$file" ] && continue
  if is_code_candidate "$file"; then
    CHANGED_FILES+=("$file")
  fi
done < <(collect_changed_files "$@")

print_line ""
print_line "─── Codex 验证 ───"

if [ "${#CHANGED_FILES[@]}" -eq 0 ]; then
  print_line "ℹ️  未检测到可验证的改动文件。"
  print_line "   可以手动传入文件路径：bash ai_constraints/scripts/verify.sh path/to/file"
  print_line "──────────────────"
  exit 0
fi

print_line "📁 改动文件："
for file in "${CHANGED_FILES[@]}"; do
  print_line "   $file"
  collect_tests_for_file "$file"
done

if [ "${#TEST_FILES[@]}" -eq 0 ]; then
  print_line "⚠️  未找到对应测试。"
  print_line "   如果这是新能力，请补测试；如果命名不规则，请手动跑一次相关测试。"
  print_line "──────────────────"
  exit 0
fi

print_line "🧪 命中的测试："
for file in "${TEST_FILES[@]}"; do
  print_line "   $file"
done

RESULT=0

if [ -f "package.json" ]; then
  run_node_tests || RESULT=$?
elif [ -f "go.mod" ]; then
  run_go_tests || RESULT=$?
elif [ -f "pyproject.toml" ] || [ -f "requirements.txt" ]; then
  run_python_tests || RESULT=$?
else
  print_line "⚠️  无法识别项目类型，目前仅支持 Node.js / Go / Python。"
  print_line "──────────────────"
  exit 0
fi

if [ "$RESULT" -eq 0 ]; then
  print_line "✅ 验证通过"
else
  print_line "❌ 验证失败，退出码：$RESULT"
  print_line "   先修测试或实现，再继续后续改动。"
fi

print_line "──────────────────"
exit "$RESULT"
