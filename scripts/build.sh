#!/usr/bin/env bash
# 构建脚本：检查环境 → 跑测试 → 打包。
#
#   ./scripts/build.sh              # 检查 + 测试 + 打包
#   ./scripts/build.sh --no-package # 只检查 + 测试
#
# 产物：dist/asset_suggestion-<version>.tar.gz
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DO_PACKAGE=1
[[ "${1:-}" == "--no-package" ]] && DO_PACKAGE=0

echo "==> 环境检查"
python3 --version
if ! python3 -c "import sys; assert sys.version_info >= (3, 10)"; then
  echo "需要 Python >= 3.10" >&2
  exit 1
fi

echo "==> 语法检查（编译全部模块）"
python3 -m compileall -q src tests examples

echo "==> 单元测试"
export PYTHONPATH="$ROOT/src"
python3 -m asset_suggestion test

if [[ "$DO_PACKAGE" -eq 1 ]]; then
  VERSION="$(python3 - <<'PY'
import re, pathlib
text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
print(m.group(1) if m else "0.0.0")
PY
)"
  echo "==> 打包 v$VERSION"
  rm -rf dist
  mkdir -p dist
  tar -czf "dist/asset_suggestion-${VERSION}.tar.gz" \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='data/*.db' --exclude='dist' \
    src tests examples docs scripts config README.md LICENSE CHANGELOG.md \
    CONTRIBUTING.md requirements.txt pyproject.toml .gitignore
  echo "产物：dist/asset_suggestion-${VERSION}.tar.gz"
fi

echo "==> 构建完成"
