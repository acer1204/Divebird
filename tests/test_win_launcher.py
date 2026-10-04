"""Windows 啟動程式：專案根目錄的 Divebird.exe（scripts/launcher.cs，由 scripts/win_gui_launcher.py 編譯）。

啟動程式都編譯到暫存資料夾，搭配假的 .venv、divebird-gui.exe 與 Divebird.bat：
不會啟動真正的 Divebird，也不會動到專案根目錄與 .venv。對話框以 DIVEBIRD_LAUNCHER_DIALOG_LOG 攔截。
"""
import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 使用")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import win_gui_launcher as wgl  # noqa: E402

# 假的 divebird-gui.exe：記下收到的命令列與工作目錄；DIVEBIRD_TEST_SLEEP=1 時之後繼續執行（模擬 Divebird 開著）
RECORDER = r"""
using System; using System.IO; using System.Text;
static class Recorder {
    static void Main() {
        File.WriteAllText(Environment.GetEnvironmentVariable("DIVEBIRD_TEST_RECORD"),
            Environment.CommandLine + "\n" + Environment.CurrentDirectory, new UTF8Encoding(false));
        if (Environment.GetEnvironmentVariable("DIVEBIRD_TEST_SLEEP") == "1") System.Threading.Thread.Sleep(30000);
    }
}
"""
# 執行中、會鎖住自己執行檔的程式（測試替換執行中的 Divebird.exe）
SLEEPER = r"""
static class Sleeper { static void Main() { System.Threading.Thread.Sleep(30000); } }
"""
# 假的 Divebird.bat：記下收到的參數
FAKE_BAT = b'@echo off\r\n>"%~dp0bat-ran.txt" echo args=%*\r\n'
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _compile(source: str, dst: Path) -> Path:
    src = dst.with_name(dst.stem + "-src.cs")
    src.write_text(source, encoding="utf-8")
    subprocess.run([str(wgl.find_csc()), "/nologo", "/target:winexe", f"/out:{dst}", str(src)],
                   check=True, capture_output=True, creationflags=NO_WINDOW)
    return dst


def _subsystem(exe: Path) -> int:
    data = exe.read_bytes()
    pe = int.from_bytes(data[0x3C:0x40], "little")
    return int.from_bytes(data[pe + 0x5C:pe + 0x5E], "little")


def _icon_groups(exe: Path) -> int:
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LoadLibraryExW.restype = wintypes.HMODULE
    k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p)
    k32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, callback_type, ctypes.c_void_p]
    k32.FreeLibrary.argtypes = [wintypes.HMODULE]
    module = k32.LoadLibraryExW(str(exe), None, 0x2 | 0x20)  # AS_DATAFILE | AS_IMAGE_RESOURCE
    assert module, ctypes.get_last_error()
    found = []
    callback = callback_type(lambda *_: found.append(1) or True)
    try:
        k32.EnumResourceNamesW(module, 14, callback, None)  # RT_GROUP_ICON
    finally:
        k32.FreeLibrary(module)
    return len(found)


def _wait_for(path: Path, timeout: float = 15) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            text = path.read_text(encoding="utf-8")
            if text:
                return text
        except OSError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"逾時：{path} 沒有出現")


def _never_appears(path: Path, wait: float = 1.5) -> bool:
    time.sleep(wait)
    return not path.exists()


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    if wgl.find_csc() is None:
        pytest.skip("此系統沒有 .NET Framework 的 csc.exe")
    if wgl.smart_app_control_on():
        pytest.skip("Smart App Control 已開啟：本機編譯、未簽章的程式無法執行")
    build = tmp_path_factory.mktemp("build")
    return wgl.build_launcher(build / "Divebird.exe", build / "stamp.sha256", force=True)


@pytest.fixture
def root(tmp_path, built, monkeypatch) -> Path:
    """仿專案根目錄；路徑含中文與空白（使用者的專案就在「桌面」底下）。"""
    folder = tmp_path / "專案 資料夾"
    (folder / "src" / "divebird").mkdir(parents=True)
    (folder / "src" / "divebird" / "__init__.py").write_text("", encoding="utf-8")
    # setup 戳記涵蓋的檔案（assets\divebird.ico 故意不放：缺檔兩邊都要當成空檔）
    (folder / "pyproject.toml").write_text('[project]\nname = "divebird"\n', encoding="utf-8")
    (folder / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (folder / "scripts").mkdir()
    shutil.copy2(wgl.LAUNCHER_SRC, folder / "scripts" / "launcher.cs")
    shutil.copy2(built, folder / "Divebird.exe")
    (folder / "Divebird.bat").write_bytes(FAKE_BAT)
    monkeypatch.setenv("DIVEBIRD_TEST_RECORD", str(folder / "record.txt"))
    monkeypatch.setenv("DIVEBIRD_LAUNCHER_DIALOG_LOG", str(folder / "dialog.txt"))
    for name in ("DIVEBIRD_LAUNCHER_DIALOG_ANSWER", "DIVEBIRD_TEST_SLEEP"):
        monkeypatch.delenv(name, raising=False)
    return folder


def _make_env(root: Path, home: Path | None = None, src: str | None = None, stamp: bool = True) -> Path:
    """建立假的 .venv：divebird-gui.exe（記錄器）、pyvenv.cfg、可編輯安裝的 .pth 與 setup 戳記。"""
    venv = root / ".venv"
    gui = venv / "Scripts" / "divebird-gui.exe"
    gui.parent.mkdir(parents=True)
    _compile(RECORDER, gui)
    home = home or Path(sys.base_prefix)
    assert (home / "pythonw.exe").is_file() or not home.exists()
    (venv / "pyvenv.cfg").write_text(f"home = {home}\nimplementation = CPython\n", encoding="utf-8")
    site = venv / "Lib" / "site-packages"
    site.mkdir(parents=True)
    (site / "_editable_impl_divebird.pth").write_text(src or str(root / "src"), encoding="utf-8")
    if stamp:  # Python 算的戳記必須被 C# 的啟動程式認可（兩邊算法一致）
        (venv / wgl.SETUP_STAMP).write_text(wgl.setup_digest(root) + "\n", encoding="utf-8")
    return gui


def _run(root: Path, args: str = "") -> int:
    # 以字串傳入：Windows 上原封不動當成命令列，模擬檔案總管或捷徑帶參數啟動
    return subprocess.run(f'"{root / "Divebird.exe"}" {args}'.strip(), cwd=str(Path.home()), timeout=30).returncode


@pytest.fixture
def running_gui(root):
    """讓 .venv 的 divebird-gui.exe 保持執行（模擬 Divebird 開著）。"""
    procs = []

    def start(gui: Path) -> None:
        env = dict(os.environ, DIVEBIRD_TEST_SLEEP="1", DIVEBIRD_TEST_RECORD=str(root / "first.txt"))
        procs.append(subprocess.Popen([str(gui)], env=env))
        _wait_for(root / "first.txt")

    yield start
    for proc in procs:
        proc.kill()
        proc.wait()


# ── Divebird.exe ─────────────────────────────────────────────────────────────

def test_launcher_is_windowless_and_has_icon(built):
    assert _subsystem(built) == 2  # IMAGE_SUBSYSTEM_WINDOWS_GUI：不會開主控台視窗
    assert _icon_groups(built) >= 1


def test_starts_gui_with_raw_arguments(root):
    gui = _make_env(root)
    args = r'https://example.com/v?a=1&b=2 "C:\some dir\影片" --minimized'
    assert _run(root, args) == 0
    command_line, cwd = _wait_for(root / "record.txt").split("\n")
    assert command_line == f'"{gui}" -m divebird {args}'
    assert Path(cwd) == root
    assert not (root / "dialog.txt").exists() and not (root / "bat-ran.txt").exists()


def test_without_arguments(root):
    gui = _make_env(root)
    assert _run(root) == 0
    assert _wait_for(root / "record.txt").split("\n")[0] == f'"{gui}" -m divebird'


def test_source_path_compared_by_file_identity(root):
    # .pth 的寫法（斜線方向、大小寫）不同但指向同一個資料夾：不算環境不符
    _make_env(root, src=str(root / "src").replace("\\", "/").upper())
    assert _run(root, "--minimized") == 0
    assert _wait_for(root / "record.txt").split("\n")[0].endswith("-m divebird --minimized")


def test_first_run_opens_bat_without_arguments(root):
    # 尚未建立執行環境：交給 Divebird.bat（不轉交參數，避免 cmd 誤解 & 等符號）
    assert _run(root, "https://example.com/?a=1&b=2 --minimized") == 0
    assert _wait_for(root / "bat-ran.txt").strip() == "args="


@pytest.mark.parametrize("change", ["unfinished", "pulled"])
def test_outdated_setup_runs_bat(root, change):
    # 上次 setup 沒完成（沒有戳記），或 git pull 改了相依套件（戳記不符）：先讓 Divebird.bat 跑 setup
    _make_env(root, stamp=change == "pulled")
    if change == "pulled":
        (root / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    assert _run(root, "--minimized") == 0
    assert _wait_for(root / "bat-ran.txt").strip() == "args="
    assert not (root / "record.txt").exists()


@pytest.mark.parametrize("args", ["", "--minimized"])
def test_outdated_setup_while_running_just_starts(root, running_gui, args):
    # Divebird 正在執行時不更新環境，照常啟動（已開著的 Divebird 會把視窗叫到前面），
    # 並提示先結束再開啟；登入自動啟動（--minimized）不提示
    gui = _make_env(root, stamp=False)
    running_gui(gui)
    assert _run(root, args) == 0
    assert _wait_for(root / "record.txt").split("\n")[0] == f'"{gui}" -m divebird {args}'.rstrip()
    assert not (root / "bat-ran.txt").exists()
    dialog = root / "dialog.txt"
    if args:
        assert not dialog.exists()
    else:
        assert "更新待套用" in dialog.read_text(encoding="utf-8")


def test_moved_folder_offers_repair(root, monkeypatch):
    _make_env(root, home=root.parent / "舊的位置" / ".runtime" / "python")
    monkeypatch.setenv("DIVEBIRD_LAUNCHER_DIALOG_ANSWER", "yes")
    assert _run(root, "--minimized") == 0
    assert "搬移" in (root / "dialog.txt").read_text(encoding="utf-8")
    assert _wait_for(root / "bat-ran.txt").strip() == "args=/repair"
    assert not (root / "record.txt").exists()


def test_copied_folder_asks_and_respects_no(root, tmp_path):
    original = tmp_path / "原本的資料夾" / "src"
    (original / "divebird").mkdir(parents=True)
    (original / "divebird" / "__init__.py").write_text("", encoding="utf-8")
    _make_env(root, src=str(original))
    assert _run(root) == 0
    assert "複製" in (root / "dialog.txt").read_text(encoding="utf-8")
    assert _never_appears(root / "bat-ran.txt") and not (root / "record.txt").exists()


def test_missing_bat_with_broken_environment(root):
    _make_env(root, home=root / "不存在")
    (root / "Divebird.bat").unlink()
    assert _run(root) == 1
    assert "找不到 Divebird.bat" in (root / "dialog.txt").read_text(encoding="utf-8")


# ── 建置與替換 ───────────────────────────────────────────────────────────────

def test_build_is_incremental(tmp_path, built):
    out, stamp = tmp_path / "Divebird.exe", tmp_path / "stamp.sha256"
    assert wgl.build_launcher(out, stamp) == out
    assert wgl.launcher_is_current(out, stamp)
    before = out.stat().st_mtime_ns
    wgl.build_launcher(out, stamp)  # 內容沒變：不重新編譯（雜湊不變，防毒信譽也不會每次歸零）
    assert out.stat().st_mtime_ns == before
    out.write_bytes(b"tampered")  # exe 被換掉：重新編譯
    assert not wgl.launcher_is_current(out, stamp)
    wgl.build_launcher(out, stamp)
    assert _subsystem(out) == 2 and wgl.launcher_is_current(out, stamp)


def test_replaces_running_exe(tmp_path, built):
    out = tmp_path / "Divebird.exe"
    _compile(SLEEPER, out)
    proc = subprocess.Popen([str(out)])
    try:
        time.sleep(0.5)
        new = tmp_path / "new.exe"
        shutil.copy2(built, new)
        wgl._replace(new, out)  # 執行中的檔案被鎖住：先改名再取代
        assert out.read_bytes() == built.read_bytes()
        assert list(tmp_path.glob("Divebird.exe.old-*"))  # 還在執行，刪不掉
    finally:
        proc.kill()
        proc.wait()
    wgl._remove_stale(out)
    assert not list(tmp_path.glob("Divebird.exe.old-*"))


def test_replace_keeps_working_exe_when_new_file_stays_locked(tmp_path):
    dst, src = tmp_path / "Divebird.exe", tmp_path / "Divebird.exe.new-1"
    dst.write_bytes(b"old")
    src.write_bytes(b"new")
    with open(src, "rb"):  # 例如防毒正在掃描剛編好的檔案（沒有 FILE_SHARE_DELETE）
        with pytest.raises(PermissionError):
            wgl._replace(src, dst, attempts=3, delay=0.01)
    assert dst.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.old-*"))


def test_replace_waits_for_brief_lock(tmp_path):
    dst, src = tmp_path / "Divebird.exe", tmp_path / "Divebird.exe.new-1"
    dst.write_bytes(b"old")
    src.write_bytes(b"new")
    handle = open(src, "rb")
    threading.Timer(0.3, handle.close).start()
    wgl._replace(src, dst)
    assert dst.read_bytes() == b"new"
    assert not src.exists() and not list(tmp_path.glob("*.old-*"))


def test_replace_restores_when_new_file_vanishes(tmp_path, monkeypatch):
    # 新檔先被掃描鎖住、接著被防毒隔離（消失）：原本可用的檔案要放回去
    dst, src = tmp_path / "Divebird.exe", tmp_path / "Divebird.exe.new-1"
    dst.write_bytes(b"old")
    src.write_bytes(b"new")
    calls = []

    def fake_replace(a, b):
        calls.append(a)
        raise PermissionError("scanning") if len(calls) == 1 else FileNotFoundError("quarantined")

    monkeypatch.setattr(wgl.os, "replace", fake_replace)
    with pytest.raises(FileNotFoundError):
        wgl._replace(src, dst, attempts=3, delay=0.01)
    monkeypatch.undo()
    assert dst.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.old-*"))


def test_remove_stale_recovers_missing_exe(tmp_path):
    # 程序在「舊檔改名」與「新檔就位」之間被中斷：下次先把舊檔救回來，而不是刪掉
    dst = tmp_path / "Divebird.exe"
    (tmp_path / "Divebird.exe.old-123").write_bytes(b"working")
    (tmp_path / "Divebird.exe.new-123").write_bytes(b"partial")
    wgl._remove_stale(dst)
    assert dst.read_bytes() == b"working"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Divebird.exe"]


def test_try_build_launcher(tmp_path, monkeypatch):
    out, stamp = tmp_path / "Divebird.exe", tmp_path / "stamp"
    monkeypatch.delenv("DIVEBIRD_NO_LAUNCHER", raising=False)
    monkeypatch.setattr(wgl, "smart_app_control_on", lambda: False)
    # 回傳 (Divebird.exe 或 None, 是否該稍後重試)；稍後重試時 setup 不寫戳記，下次啟動會再跑 setup
    with monkeypatch.context() as m:  # 這台電腦建不起來：只提示，不必重試
        m.setattr(wgl, "csc_candidates", lambda: [])
        assert wgl.try_build_launcher(out, stamp) == (None, False)
    with monkeypatch.context() as m:  # 暫時性失敗（檔案被鎖）：之後要重試
        m.setattr(wgl, "build_launcher", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
        assert wgl.try_build_launcher(out, stamp) == (None, True)
    with monkeypatch.context() as m:  # 使用者選擇不要啟動程式
        m.setenv("DIVEBIRD_NO_LAUNCHER", "1")
        assert wgl.try_build_launcher(out, stamp) == (None, False)
    assert not out.exists()
    if wgl.find_csc() is None:
        pytest.skip("此系統沒有 .NET Framework 的 csc.exe")
    assert wgl.try_build_launcher(out, stamp) == (out, False)


def test_compiler_that_cannot_run_is_transient(tmp_path, monkeypatch):
    # csc 無法執行或逾時不代表永遠建不起來：丟 OSError（可重試），不是 LauncherUnavailable
    monkeypatch.setattr(wgl, "smart_app_control_on", lambda: False)
    monkeypatch.setattr(wgl, "csc_candidates", lambda: [tmp_path / "a.exe", tmp_path / "b.exe"])
    monkeypatch.setattr(wgl, "_compile", lambda csc, out: (_ for _ in ()).throw(TimeoutError(f"{csc} 逾時")))
    with pytest.raises(OSError) as info:
        wgl.build_launcher(tmp_path / "Divebird.exe", tmp_path / "stamp", force=True)
    assert not isinstance(info.value, wgl.LauncherUnavailable)
    assert "a.exe" in str(info.value) and "b.exe" in str(info.value)  # 兩個候選都試過


def test_smart_app_control_skips_build(tmp_path, monkeypatch):
    monkeypatch.setattr(wgl, "smart_app_control_on", lambda: True)
    with pytest.raises(wgl.LauncherUnavailable, match="Smart App Control"):
        wgl.build_launcher(tmp_path / "Divebird.exe", tmp_path / "stamp", force=True)


# ── 環境檢查（Divebird.bat 的 --check） ──────────────────────────────────────

def test_environment_problem(tmp_path):
    root, prefix = tmp_path / "專案", tmp_path / "venv"
    (root / "src" / "divebird").mkdir(parents=True)
    (root / "src" / "divebird" / "__init__.py").write_text("", encoding="utf-8")
    site = prefix / "Lib" / "site-packages"
    site.mkdir(parents=True)
    assert wgl.environment_problem(root, prefix) is None  # 沒有 .pth：不檢查
    pth = site / "_editable_impl_divebird.pth"
    pth.write_text(str(root / "src").replace("\\", "/"), encoding="utf-8")
    assert wgl.environment_problem(root, prefix) is None
    other = tmp_path / "別的資料夾" / "src"
    (other / "divebird").mkdir(parents=True)
    (other / "divebird" / "__init__.py").write_text("", encoding="utf-8")
    pth.write_text(str(other), encoding="utf-8")
    assert "另一個資料夾" in wgl.environment_problem(root, prefix)
    pth.write_text(str(tmp_path / "不存在" / "src"), encoding="utf-8")
    assert wgl.environment_problem(root, prefix)


def test_check_exit_codes(root, running_gui):
    gui = _make_env(root, stamp=False)
    prefix = root / ".venv"
    assert wgl.check(root, prefix) == wgl.NEEDS_SETUP  # 沒有戳記：setup 沒完成
    (prefix / wgl.SETUP_STAMP).write_text(wgl.setup_digest(root), encoding="utf-8")
    assert wgl.check(root, prefix) == 0
    (root / "pyproject.toml").write_text("changed", encoding="utf-8")
    assert wgl.check(root, prefix) == wgl.NEEDS_SETUP  # git pull 改了相依套件
    running_gui(gui)
    assert wgl.check(root, prefix) == 0  # Divebird 開著：先照常啟動，下次再更新
    (prefix / "Lib" / "site-packages" / "_editable_impl_divebird.pth").write_text(
        str(root.parent / "別處" / "src"), encoding="utf-8")
    assert wgl.check(root, prefix) == wgl.FOREIGN_ENV
