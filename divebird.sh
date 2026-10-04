#!/usr/bin/env bash
# Divebird 啟動器（Linux）：./divebird.sh [網址...]
#   1. 專案內已有環境（.venv）→ 以原始碼執行最新程式
#   2. 旁邊有打包版（dist/Divebird/Divebird）→ 直接執行
#   3. 都沒有 → 先在專案內建立自帶環境（.runtime/、.venv/）再執行
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PY="$ROOT/.venv/bin/python"
EXE="$ROOT/dist/Divebird/Divebird"

if [ -x "$PY" ]; then
  exec "$PY" -m divebird "$@"
elif [ -x "$EXE" ]; then
  exec "$EXE" "$@"
fi
echo "[Divebird] 第一次執行：正在建立自帶環境，約需數分鐘…"
"$ROOT/scripts/setup.sh"
exec "$PY" -m divebird "$@"
