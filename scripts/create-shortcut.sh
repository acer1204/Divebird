#!/usr/bin/env bash
# 在 Linux 應用程式選單建立 Divebird 捷徑（指向專案根目錄的 divebird.sh）。
# 打包版請改用 dist/Divebird/install.sh（會一併安裝到 ~/.local/share/divebird）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
chmod +x "$ROOT/divebird.sh"

# .desktop 的 Exec 欄位：路徑含空白等字元時需以雙引號包住，並跳脫 " ` $ \
exec_path=$(printf '%s' "$ROOT/divebird.sh" | sed -e 's/[\\"`$]/\\&/g')
cat > "$APPS/divebird.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Divebird
Comment=下載管理員（多連線加速、影片下載）
Exec="$exec_path" %U
Icon=$ROOT/assets/divebird.png
Terminal=false
Categories=Network;FileTransfer;
StartupWMClass=Divebird
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1 || true
echo "已建立應用程式選單捷徑：$APPS/divebird.desktop"
