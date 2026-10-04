#!/usr/bin/env bash
# 打包 Linux 免安裝版：dist/Divebird/Divebird ＋ dist/Divebird-<版本>-linux-<架構>.tar.gz
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || "$ROOT/scripts/setup.sh"
cd "$ROOT"

VERSION="$("$PY" -c 'import divebird; print(divebird.__version__)')"
QT_QPA_PLATFORM=offscreen "$PY" scripts/gen_icons.py
"$PY" -m PyInstaller --noconfirm --clean divebird.spec

DIST="$ROOT/dist/Divebird"
cp -r "$ROOT/extension" "$DIST/extension"
cp "$ROOT/README.md" "$DIST/"
cp "$ROOT/assets/divebird.png" "$DIST/divebird.png"
cp "$ROOT/scripts/linux-install.sh" "$DIST/install.sh"
chmod +x "$DIST/install.sh" "$DIST/Divebird" "$DIST/_internal/tools/"*

ARCH="$(uname -m)"
tar -C "$ROOT/dist" -czf "$ROOT/dist/Divebird-$VERSION-linux-$ARCH.tar.gz" Divebird
echo
echo "完成：$DIST/Divebird"
echo "壓縮檔：$ROOT/dist/Divebird-$VERSION-linux-$ARCH.tar.gz"
