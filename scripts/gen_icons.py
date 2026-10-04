"""由內嵌 SVG 產生擴充功能圖示（PNG）與桌面程式圖示（Windows .ico、Linux .png）。
32px 以下使用簡化版圖示（見 divebird.gui.icons.app_svg_for）。"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from divebird.gui.icons import app_svg_for, render_svg  # noqa: E402


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841
    out = ROOT / "extension" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    for s in (16, 32, 48, 128):
        render_svg(app_svg_for(s), s).save(str(out / f"icon{s}.png"))
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    render_svg(app_svg_for(256), 256).save(str(assets / "divebird.png"))
    # Windows .ico（多尺寸）：Qt 的 ico 外掛只寫入單一尺寸，這裡手動組合
    sizes = (16, 24, 32, 48, 64, 128, 256)
    blobs = []
    for s in sizes:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        render_svg(app_svg_for(s), s).save(buf, "PNG")
        blobs.append(bytes(buf.data()))
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, b in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(b), offset)
        offset += len(b)
    (assets / "divebird.ico").write_bytes(header + entries + b"".join(blobs))
    print("icons generated")


if __name__ == "__main__":
    main()
