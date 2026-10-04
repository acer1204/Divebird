#!/usr/bin/env bash
# 安裝 Divebird 到目前使用者（不需 root）：複製到 ~/.local/share/divebird，
# 並建立應用程式選單捷徑與登入時自動啟動（縮小到系統匣）。
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/divebird"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

mkdir -p "$DEST" "$APPS" "$AUTOSTART" "$HOME/.local/bin"
if [ "$SRC" != "$DEST" ]; then
  rm -rf "$DEST"
  cp -r "$SRC" "$DEST"
fi
ln -sf "$DEST/Divebird" "$HOME/.local/bin/divebird"

cat > "$APPS/divebird.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Divebird
Comment=下載管理員（多連線加速、影片下載）
Exec="$DEST/Divebird" %U
Icon=$DEST/divebird.png
Terminal=false
Categories=Network;FileTransfer;
StartupWMClass=Divebird
EOF

if [ "${1:-}" != "--no-autostart" ]; then
  sed 's|%U|--minimized|' "$APPS/divebird.desktop" > "$AUTOSTART/divebird.desktop"
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1 || true

echo "已安裝到 $DEST"
echo "可從應用程式選單開啟 Divebird，或在終端機執行：divebird"
echo "Chrome 擴充功能資料夾：$DEST/extension"
