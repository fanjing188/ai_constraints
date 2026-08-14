#!/bin/bash
# 仅定位工作包并调用标准库验证器，所有规则只在 Python 中维护一份。
set -euo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec python3 "$PACKAGE_ROOT/scripts/verify.py" --package-root "$PACKAGE_ROOT"
