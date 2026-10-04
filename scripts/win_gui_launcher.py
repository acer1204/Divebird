r"""Windows：建立不會開主控台視窗的 Divebird 啟動程式，並檢查執行環境是否可用。

1. .venv\Scripts\divebird-gui.exe
   uv 建立的 .venv\Scripts\pythonw.exe 其實和 python.exe 是同一個「主控台」轉接程式，
   用它啟動 Divebird 會多出一個一直開著的黑色主控台視窗。
   CPython 內附的 venv GUI 啟動器（Lib\venv\scripts\nt\pythonw.exe）會讀取 pyvenv.cfg，
   以無主控台的 pythonw.exe 執行，並保留 venv 環境；這裡把它複製成 divebird-gui.exe。

2. 專案根目錄的 Divebird.exe
   .bat 一定會先開主控台視窗。這裡用 Windows 內建的 .NET Framework 編譯器（csc.exe）
   把 scripts\launcher.cs 編譯成無主控台的小啟動程式，按兩下就直接出現 Divebird 視窗。
   只在原始碼、圖示或編譯參數改變時重新編譯（內容雜湊記在 .venv\divebird-launcher.sha256）。
   建不起來不影響使用：Divebird.bat 仍可啟動，只是會閃一下主控台。
   設定環境變數 DIVEBIRD_NO_LAUNCHER=1 可完全不編譯（例如受管理的公司電腦）。

3. 「環境已建好」的戳記 .venv\divebird-setup.sha256
   scripts\setup.ps1 全部成功後才寫入，內容是 SETUP_INPUTS 的雜湊（scripts\launcher.cs 也算同一套）。
   戳記不存在（setup 中途失敗或被關掉）或不相符（git pull 改了相依套件或啟動程式）時，
   Divebird.exe 與 Divebird.bat 會先重跑 setup，再啟動 Divebird。

用專案的 venv 執行：
    .venv\Scripts\python.exe scripts\win_gui_launcher.py               建立以上兩個啟動程式
    .venv\Scripts\python.exe scripts\win_gui_launcher.py --setup-done  同上，並寫入戳記（setup.ps1 用）
    .venv\Scripts\python.exe scripts\win_gui_launcher.py --check       Divebird.bat 每次啟動時呼叫：
        0 可以啟動；2 .venv 屬於別的資料夾（專案被複製）；3 需要重跑 setup
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

NAME = "divebird-gui.exe"
ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_SRC = ROOT / "scripts" / "launcher.cs"
LAUNCHER = ROOT / "Divebird.exe"
ICON = ROOT / "assets" / "divebird.ico"
CSC_OPTIONS = ["/nologo", "/target:winexe", "/optimize+", "/codepage:65001"]
LAUNCHER_STAMP = "divebird-launcher.sha256"
SETUP_STAMP = "divebird-setup.sha256"
# 這些檔案改變時要重跑 setup；scripts\launcher.cs 的 SetupDigest 必須用同一份清單與同一個算法
SETUP_INPUTS = ("pyproject.toml", "uv.lock", "scripts/launcher.cs", "assets/divebird.ico")
FOREIGN_ENV = 2
NEEDS_SETUP = 3


class LauncherUnavailable(RuntimeError):
    """這台電腦無法建立 Divebird.exe。"""


def _replace(src: Path, dst: Path, attempts: int = 20, delay: float = 0.1) -> None:
    """以 src 取代 dst；任何失敗都不會讓原本可用的 dst 消失。

    dst 正在執行時 Windows 會鎖住它（但允許改名），所以先把它改名移開；
    src 剛編譯好時可能正被防毒軟體掃描，所以取代失敗時稍等再試。
    """
    try:
        os.replace(src, dst)
        return
    except PermissionError:
        pass
    old = None
    if dst.exists():
        old = dst.with_name(f"{dst.name}.old-{os.getpid()}")
        dst.rename(old)  # 連改名都不行就直接丟出例外，dst 原封不動
    try:
        for attempt in range(attempts):
            try:
                os.replace(src, dst)
                break
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)
    except BaseException:
        # 包含新檔被防毒隔離（FileNotFoundError）與 Ctrl+C：把舊檔放回去
        if old is not None and not dst.exists():
            try:
                old.rename(dst)
            except OSError:
                pass  # _remove_stale 下次會把它救回來
        raise
    if old is not None:
        _discard(old)  # 還在執行就刪不掉，下次再清


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # 還被鎖住：_remove_stale 下次再清


def _remove_stale(dst: Path) -> None:
    """清掉替換時留下的暫存檔與舊檔；dst 不見時先用最新的舊檔救回來。"""
    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    old = sorted([*dst.parent.glob(f"{dst.name}.old-*"), *dst.parent.glob(f"{dst.stem}.old-*{dst.suffix}")],
                 key=mtime, reverse=True)
    if old and not dst.exists():
        try:
            old.pop(0).rename(dst)
        except OSError:
            pass
    for path in [*old, *dst.parent.glob(f"{dst.name}.new-*")]:
        _discard(path)


def ensure() -> Path | None:
    if sys.platform != "win32" or sys.prefix == sys.base_prefix:
        return None
    src = Path(venv.__file__).parent / "scripts" / "nt" / "pythonw.exe"
    dst = Path(sys.prefix) / "Scripts" / NAME
    if not src.is_file():
        return None
    _remove_stale(dst)
    if dst.is_file() and dst.read_bytes() == src.read_bytes():
        return dst
    tmp = dst.with_name(f"{dst.name}.new-{os.getpid()}")
    shutil.copy2(src, tmp)
    try:
        _replace(tmp, dst)
    finally:
        _discard(tmp)
    return dst


def csc_candidates() -> list[Path]:
    """Windows 內建的 .NET Framework 4.x C# 編譯器（Windows 10 / 11 都有），原生架構優先。"""
    windir = Path(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows")
    return [csc for framework in ("FrameworkArm64", "Framework64", "Framework")
            if (csc := windir / "Microsoft.NET" / framework / "v4.0.30319" / "csc.exe").is_file()]


def find_csc() -> Path | None:
    found = csc_candidates()
    return found[0] if found else None


def smart_app_control_on() -> bool:
    """Smart App Control 開啟時，本機編譯、未簽章的 exe 會被擋下。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as key:
            return winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")[0] == 1
    except (ImportError, OSError):
        return False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(parts: list[bytes]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(hashlib.sha256(part).digest())
    return h.hexdigest()


def _inputs_digest() -> str:
    return _digest([LAUNCHER_SRC.read_bytes(), ICON.read_bytes(), " ".join(CSC_OPTIONS).encode()])


def launcher_is_current(out: Path, stamp: Path) -> bool:
    try:
        inputs, exe = stamp.read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return False
    return out.is_file() and inputs == _inputs_digest() and exe == _sha256(out)


def _compile(csc: Path, out: Path) -> None:
    """編譯失敗（原始碼或參數的問題）丟出 LauncherUnavailable；無法執行或逾時丟出 OSError。"""
    try:
        r = subprocess.run([str(csc), *CSC_OPTIONS, f"/win32icon:{ICON}", f"/out:{out}", str(LAUNCHER_SRC)],
                           capture_output=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise TimeoutError(f"{csc} 逾時") from e
    if r.returncode != 0 or not out.is_file():
        # csc 以系統的 ANSI 字碼頁輸出（已本地化的）訊息
        msg = (r.stdout + r.stderr).decode("mbcs", errors="replace").strip()
        raise LauncherUnavailable(f"{csc} 編譯失敗（{r.returncode}）：{msg}")


def build_launcher(out: Path = LAUNCHER, stamp: Path | None = None, force: bool = False) -> Path:
    """建立（或更新）Divebird.exe。

    這台電腦建不起來時丟出 LauncherUnavailable；檔案暫時被鎖、逾時等可重試的問題丟出 OSError。
    """
    if sys.platform != "win32":
        raise LauncherUnavailable("只有 Windows 需要 Divebird.exe")
    stamp = stamp or out.with_name(f"{out.name}.sha256")
    _remove_stale(out)
    if not force and launcher_is_current(out, stamp):
        return out
    if smart_app_control_on():
        raise LauncherUnavailable("Smart App Control 已開啟，會擋下在本機編譯、未簽章的程式")
    candidates = csc_candidates()
    if not candidates:
        raise LauncherUnavailable("找不到 .NET Framework 的 csc.exe")
    tmp = out.with_name(f"{out.name}.new-{os.getpid()}")
    errors: list[Exception] = []
    try:
        for csc in candidates:  # 例如 Windows 10 ARM64 無法執行 x64 的 csc：換下一個
            try:
                _compile(csc, tmp)
                break
            except (LauncherUnavailable, OSError) as e:
                errors.append(e)
                _discard(tmp)
        else:
            message = "；".join(str(e) for e in errors)
            if any(isinstance(e, LauncherUnavailable) for e in errors):
                raise LauncherUnavailable(message)
            raise OSError(message)
        _replace(tmp, out)
    finally:
        _discard(tmp)
    stamp.write_text(f"{_inputs_digest()} {_sha256(out)}\n", encoding="utf-8")
    return out


def try_build_launcher(out: Path = LAUNCHER, stamp: Path | None = None) -> tuple[Path | None, bool]:
    """建立 Divebird.exe；失敗只提示，不算錯誤（Divebird.bat 仍可啟動）。

    回傳 (Divebird.exe 或 None, 是否該稍後重試)。暫時性的失敗（檔案被鎖、逾時）要重試：
    這時不寫 setup 戳記，下次啟動會再跑 setup；這台電腦建不起來或使用者不要時則不必重試。
    """
    if os.environ.get("DIVEBIRD_NO_LAUNCHER") == "1":
        return None, False
    try:
        return build_launcher(out, stamp), False
    except LauncherUnavailable as e:
        print(f"注意：無法建立 {out.name}（{e}）；請改用 Divebird.bat 啟動。", file=sys.stderr)
        return None, False
    except OSError as e:
        print(f"注意：暫時無法建立 {out.name}（{e}），下次啟動時會再試。", file=sys.stderr)
        return None, True


def setup_digest(root: Path = ROOT) -> str:
    parts = []
    for name in SETUP_INPUTS:
        try:
            parts.append((root / name).read_bytes())
        except OSError:
            parts.append(b"")  # 與 launcher.cs 相同：缺檔當成空檔
    return _digest(parts)


def setup_is_current(root: Path = ROOT, prefix: Path | None = None) -> bool:
    try:
        stamp = (Path(prefix or sys.prefix) / SETUP_STAMP).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stamp == setup_digest(root)


def gui_running(prefix: Path | None = None) -> bool:
    """divebird-gui.exe 在 Divebird 執行期間一直開著；Windows 不允許寫入執行中的 exe。"""
    try:
        with open(Path(prefix or sys.prefix) / "Scripts" / NAME, "ab"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def environment_problem(root: Path = ROOT, prefix: Path | None = None) -> str | None:
    """.venv 是否屬於這個資料夾：可編輯安裝的 divebird 套件要指向這裡的 src。

    .venv 內記錄的是絕對路徑，專案資料夾被複製後，副本的 .venv 會悄悄執行原資料夾的程式碼。
    （被搬移或改名時，.venv 連 Python 都找不到，這支程式根本跑不起來，Divebird.bat 也會發現。）
    """
    pth = Path(prefix or sys.prefix) / "Lib" / "site-packages" / "_editable_impl_divebird.pth"
    if not pth.is_file():
        return None
    lines = pth.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return None
    try:
        if os.path.samefile(root / "src" / "divebird" / "__init__.py",
                            Path(lines[0].strip()) / "divebird" / "__init__.py"):
            return None
    except OSError:
        pass
    return f"執行環境指向另一個資料夾的程式碼（{lines[0].strip()}）"


def check(root: Path = ROOT, prefix: Path | None = None) -> int:
    """Divebird.bat 每次啟動時呼叫；要快，而且除了下面兩種情況，任何問題都不能擋住啟動。"""
    try:
        problem = environment_problem(root, prefix)
        if problem:
            print(problem, file=sys.stderr)
            return FOREIGN_ENV
        # 環境沒建完，或 git pull 改了相依套件／啟動程式。Divebird 正在執行時先不更新，
        # 照常啟動（它會把已開著的視窗叫到前面），下次再更新。
        if not setup_is_current(root, prefix) and not gui_running(prefix):
            return NEEDS_SETUP
    except Exception as e:  # noqa: BLE001
        print(f"注意：{e}", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    # 專案路徑可能含有系統字碼頁無法表示的字元；輸出只是給人看的，不能因此中斷
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    if "--check" in argv:
        return check()
    try:
        path = ensure()
    except OSError as e:
        print(f"無法建立 {NAME}：{e}", file=sys.stderr)
        return 1
    if path is None:
        print("略過：不是 Windows 或不在專案 venv 中執行", file=sys.stderr)
        return 1
    launcher, retry_later = try_build_launcher(LAUNCHER, Path(sys.prefix) / LAUNCHER_STAMP)
    if "--setup-done" in argv and not retry_later:
        (Path(sys.prefix) / SETUP_STAMP).write_text(setup_digest() + "\n", encoding="utf-8")
    print(path)
    if launcher is not None:
        print(launcher)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
