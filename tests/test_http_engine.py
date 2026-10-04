import hashlib
import threading
import time

import pytest
import requests

from divebird.engine.http_engine import HttpDownloader, MIN_SPLIT
from divebird.models import Task


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_task(server, name, tmp_path, **kw):
    return Task(url=server.url(name), save_dir=str(tmp_path), **kw)


def test_segmented_download(server, tmp_path):
    data = server.add("/big.bin", 6 * 1024 * 1024 + 123)
    task = make_task(server, "/big.bin", tmp_path)
    dl = HttpDownloader(task, connections=6)
    assert dl.run() is True
    out = tmp_path / "big.bin"
    assert sha(out.read_bytes()) == sha(data)
    assert task.downloaded == task.total == len(data)
    assert task.resumable
    # 探測 1 次 + 至少 6 條分段連線
    assert sum(1 for _, r in server.requests if r and r != "bytes=0-") >= 6
    assert not (tmp_path / "big.bin.part").exists()
    assert not (tmp_path / "big.bin.part.json").exists()


def test_segmented_download_completion_race(server, tmp_path):
    """迴歸測試：最後一條連線在主迴圈檢查完成狀態後才結束時，曾被誤判為「所有連線皆已中斷」（約 4% 機率）。"""
    data = server.add("/race.bin", 2 * 1024 * 1024)
    for i in range(40):
        task = make_task(server, "/race.bin", tmp_path / f"r{i}")
        assert HttpDownloader(task, connections=8).run() is True, f"第 {i} 次失敗"
        assert (tmp_path / f"r{i}" / "race.bin").stat().st_size == len(data)


def test_no_range_support_single_connection(server, tmp_path):
    server.supports_range = False
    data = server.add("/plain.bin", 1024 * 1024)
    task = make_task(server, "/plain.bin", tmp_path)
    assert HttpDownloader(task, connections=8).run() is True
    assert sha((tmp_path / "plain.bin").read_bytes()) == sha(data)
    assert task.resumable is False


def test_pause_and_resume(server, tmp_path):
    data = server.add("/movie.mp4", 8 * 1024 * 1024)
    server.throttle = 0.05
    task = make_task(server, "/movie.mp4", tmp_path)
    dl = HttpDownloader(task, connections=4)
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("ok", dl.run()))
    t.start()
    time.sleep(0.6)
    dl.stop()
    t.join(15)
    assert result["ok"] is False
    assert (tmp_path / "movie.mp4.part.json").exists()
    partial = task.downloaded
    assert 0 < partial < len(data)

    # 續傳：新的 downloader 從狀態檔接續
    server.throttle = 0
    server.requests.clear()
    dl2 = HttpDownloader(task, connections=4)
    assert dl2.run() is True
    assert sha((tmp_path / "movie.mp4").read_bytes()) == sha(data)
    starts = [int(r.split("=")[1].split("-")[0]) for _, r in server.requests if r and r != "bytes=0-"]
    assert starts and all(s > 0 for s in starts), "續傳不應從 0 重新下載"


def test_dynamic_segmentation_steals_slow_segment(server, tmp_path):
    size = 8 * 1024 * 1024
    data = server.add("/slow.bin", size)
    server.throttle = 0.002
    server.slow_ranges_from = 0          # 第一段特別慢
    task = make_task(server, "/slow.bin", tmp_path)
    dl = HttpDownloader(task, connections=4)
    assert dl.run() is True
    assert sha((tmp_path / "slow.bin").read_bytes()) == sha(data)
    assert len(dl.segments) > 4, "應該有分段被動態切分接手"
    assert all(s.done for s in dl.segments)
    assert MIN_SPLIT > 0


def test_404_fails_fast(server, tmp_path):
    task = make_task(server, "/missing.bin", tmp_path)
    with pytest.raises(requests.HTTPError):
        HttpDownloader(task, connections=4).run()


def test_content_disposition_utf8_filename(server, tmp_path):
    server.add("/dl", 300_000)
    server.content_disposition = "attachment; filename*=UTF-8''%E6%B8%AC%E8%A9%A6%E5%BD%B1%E7%89%87.mp4"
    task = make_task(server, "/dl", tmp_path)
    assert HttpDownloader(task).run() is True
    assert task.filename == "測試影片.mp4"
    assert (tmp_path / "測試影片.mp4").exists()


def test_existing_file_gets_unique_name(server, tmp_path):
    server.add("/a.zip", 100_000)
    (tmp_path / "a.zip").write_bytes(b"old")
    task = make_task(server, "/a.zip", tmp_path)
    assert HttpDownloader(task).run() is True
    assert task.filename == "a (1).zip"
    assert (tmp_path / "a.zip").read_bytes() == b"old"


def test_connection_limited_server_still_completes(server, tmp_path):
    """伺服器限制同時連線數（超過回 503）：多出來的連線放棄後不可把整個下載判為失敗。"""
    data = server.add("/limited.bin", 8 * 1024 * 1024)
    server.max_conns = 2
    server.throttle = 0.03
    task = make_task(server, "/limited.bin", tmp_path)
    dl = HttpDownloader(task, connections=8, max_retries=1)
    assert dl.run() is True
    assert sha((tmp_path / "limited.bin").read_bytes()) == sha(data)
    assert dl._conn_limit <= 3, "應自動下修同時連線數"


def test_stop_returns_immediately_on_stalled_connection(server, tmp_path):
    """連線卡住時，stop() 不可等到讀取逾時才返回（會凍結 GUI）。"""
    server.add("/stall.bin", 8 * 1024 * 1024)
    server.throttle = 5.0                 # 每 64KB 停 5 秒：模擬卡住的連線
    task = make_task(server, "/stall.bin", tmp_path)
    dl = HttpDownloader(task, connections=2)
    t = threading.Thread(target=dl.run, daemon=True)
    t.start()
    time.sleep(1.5)
    t0 = time.monotonic()
    dl.stop()
    assert time.monotonic() - t0 < 0.5
