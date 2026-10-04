"""以 ffmpeg 產生本機 HLS 串流，驗證 yt-dlp 下載流程與內附工具。"""
import subprocess
from pathlib import Path

import pytest

from divebird.config import Settings
from divebird.engine.media_engine import (
    MediaDownloader, extract_info, format_choices, is_manifest_url, is_media_site,
)
from divebird.engine.tools import deno_path, ffmpeg_path
from divebird.models import Kind, Task


def test_bundled_tools_found():
    assert ffmpeg_path() and Path(ffmpeg_path()).is_file()
    assert deno_path() and Path(deno_path()).is_file()


def test_ffmpeg_can_demux_mpegts(tmp_path):
    """HLS 片段是 MPEG-TS；Linux 上靜態 ffmpeg 解析 TS 曾因系統 gconv 模組不相容而 segfault。"""
    ff = ffmpeg_path()
    ts = tmp_path / "a.ts"
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1",
                    "-c:v", "libx264", "-f", "mpegts", str(ts)], check=True, timeout=60)
    r = subprocess.run([ff, "-hide_banner", "-i", str(ts), "-c", "copy", "-f", "mp4", "-y",
                        str(tmp_path / "a.mp4")], capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr[-500:]
    assert (tmp_path / "a.mp4").read_bytes()[4:8] == b"ftyp"


def test_site_detection():
    assert is_media_site("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_media_site("https://www.bilibili.com/video/BV1GJ411x7h7")
    assert not is_media_site("https://example.com/files/video.mp4")
    assert is_manifest_url("https://cdn.example.com/live/master.m3u8?token=1")
    assert is_manifest_url("https://cdn.example.com/a/manifest.mpd")
    assert not is_manifest_url("https://cdn.example.com/a/video.mp4")


@pytest.fixture
def hls_server(server, tmp_path):
    src = tmp_path / "hls"
    src.mkdir()
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=25",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
           "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod",
           "-master_pl_name", "master.m3u8", str(src / "index.m3u8")]
    subprocess.run(cmd, check=True, timeout=60)
    for f in src.iterdir():
        server.files[f"/hls/{f.name}"] = f.read_bytes()
    return server


def test_hls_download(hls_server, tmp_path):
    out = tmp_path / "out"
    settings = Settings(download_dir=str(out), connections=4)
    task = Task(url=hls_server.url("/hls/master.m3u8"), kind=Kind.MEDIA, save_dir=str(out),
                filename="測試串流.mp4")

    info = extract_info(task, settings)
    labels = [label for label, _ in format_choices(info)]
    assert any("240p" in label for label in labels)

    updates = []
    dl = MediaDownloader(task, settings, on_update=lambda t: updates.append(t.downloaded))
    assert dl.run() is True
    result = out / task.filename
    assert result.exists() and result.suffix == ".mp4"
    assert result.stat().st_size > 10_000
    assert task.filename == "測試串流.mp4"
    assert updates, "應回報進度"
    # 輸出為合法 mp4：ffmpeg 可讀取且含影片與音訊
    probe = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(result)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    assert "Video: h264" in probe.stderr and "Audio: aac" in probe.stderr
