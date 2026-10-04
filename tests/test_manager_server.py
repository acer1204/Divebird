import os
import socket
import threading
import time

import requests

from divebird.config import Settings
from divebird.engine.manager import DownloadManager
from divebird.models import Status, Task
from divebird.server import ApiServer


class Recorder:
    def __init__(self):
        self.added, self.removed, self.finished, self.changed = [], [], [], 0
        self.done = threading.Event()

    def task_added(self, t): self.added.append(t.id)
    def task_removed(self, tid): self.removed.append(tid)
    def task_changed(self, t): self.changed += 1

    def task_finished(self, t):
        self.finished.append(t.id)
        self.done.set()


def wait_for(cond, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_manager_queue_and_persistence(server, tmp_path):
    for i in range(4):
        server.add(f"/f{i}.bin", 1024 * 1024)
    settings = Settings(download_dir=str(tmp_path / "dl"), connections=4, max_concurrent=2)
    rec = Recorder()
    store = tmp_path / "tasks.json"
    mgr = DownloadManager(settings, rec, store=store)
    tasks = [mgr.add(Task(url=server.url(f"/f{i}.bin"))) for i in range(4)]
    assert wait_for(lambda: all(t.status == Status.COMPLETED for t in tasks))
    assert sorted(rec.finished) == sorted(t.id for t in tasks)
    assert all((tmp_path / "dl" / f"f{i}.bin").stat().st_size == 1024 * 1024 for i in range(4))
    mgr.shutdown()

    mgr2 = DownloadManager(settings, Recorder(), store=store)
    assert len(mgr2.tasks) == 4
    assert all(t.status == Status.COMPLETED for t in mgr2.tasks.values())
    mgr2.shutdown()


def test_manager_pause_resume(server, tmp_path):
    server.add("/big.bin", 8 * 1024 * 1024)
    server.throttle = 0.03
    settings = Settings(download_dir=str(tmp_path), connections=4)
    mgr = DownloadManager(settings, Recorder(), store=tmp_path / "tasks.json")
    t = mgr.add(Task(url=server.url("/big.bin")))
    assert wait_for(lambda: t.downloaded > 0)
    mgr.pause(t.id)
    assert t.status == Status.PAUSED
    assert wait_for(lambda: t.id not in mgr._runners)
    server.throttle = 0
    mgr.start(t.id)
    assert wait_for(lambda: t.status == Status.COMPLETED)
    assert (tmp_path / "big.bin").stat().st_size == 8 * 1024 * 1024
    mgr.shutdown()


def test_manager_error_status(server, tmp_path):
    settings = Settings(download_dir=str(tmp_path))
    mgr = DownloadManager(settings, Recorder(), store=tmp_path / "tasks.json")
    t = mgr.add(Task(url=server.url("/nope")))
    assert wait_for(lambda: t.status == Status.ERROR)
    assert "404" in t.error
    mgr.shutdown()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_api_server_security():
    port = _free_port()
    got, shown = [], []
    api = ApiServer(port, on_download=got.append, on_show=lambda: shown.append(1))
    assert api.start()
    base = f"http://127.0.0.1:{port}"
    try:
        r = requests.get(base + "/api/ping", timeout=5)
        assert r.status_code == 200 and r.json()["app"] == "Divebird"

        ext = {"Origin": "chrome-extension://abcdefghijklmnop"}
        r = requests.post(base + "/api/download", json={"url": "https://example.com/a.mp4"}, headers=ext, timeout=5)
        assert r.status_code == 200
        assert r.headers["Access-Control-Allow-Origin"] == ext["Origin"]
        assert got[-1]["url"] == "https://example.com/a.mp4"

        # 一般網頁不能呼叫
        r = requests.post(base + "/api/download", json={"url": "https://example.com/b.mp4"},
                          headers={"Origin": "https://evil.example"}, timeout=5)
        assert r.status_code == 403
        # DNS rebinding：Host 不是 127.0.0.1 / localhost
        r = requests.post(base + "/api/download", json={"url": "https://example.com/c.mp4"},
                          headers={"Host": f"evil.example:{port}"}, timeout=5)
        assert r.status_code == 403
        # 只接受 http(s)
        r = requests.post(base + "/api/download", json={"url": "file:///etc/passwd"}, headers=ext, timeout=5)
        assert r.status_code == 400
        assert len(got) == 1

        r = requests.post(base + "/api/show", json={}, timeout=5)
        assert r.status_code == 200 and shown == [1]
    finally:
        api.stop()
    # 埠被佔用時 start() 回傳 False
    blocker = socket.socket()
    if os.name != "nt":
        # Linux：剛關閉的埠還在 TIME_WAIT，需要 SO_REUSEADDR 才能綁定（仍不允許兩個 listener 共用）
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen()
    try:
        assert ApiServer(port, got.append, lambda: None).start() is False
    finally:
        blocker.close()
