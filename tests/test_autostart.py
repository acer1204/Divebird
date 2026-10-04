"""開機自動啟動與舊版（OpenDM）設定的搬移。Windows 部分用假的 winreg，不會碰到真正的登錄檔。"""
import sys

import pytest

from divebird import autostart

RUN, APPROVED = autostart.RUN_KEY, autostart.APPROVED_KEY
ENABLED, DISABLED = b"\x02" + b"\x00" * 11, b"\x03" + b"\x00" * 11


class _Key:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE, KEY_QUERY_VALUE, REG_SZ, REG_BINARY = 2, 1, 1, 3

    def __init__(self):
        self.keys = {RUN: {}, APPROVED: {}}

    def OpenKey(self, root, path, reserved=0, access=0):  # noqa: N802
        if path not in self.keys:
            raise FileNotFoundError(path)
        return _Key(self.keys[path])

    def CreateKeyEx(self, root, path, reserved=0, access=0):  # noqa: N802
        return _Key(self.keys.setdefault(path, {}))

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]

    def SetValueEx(self, key, name, reserved, typ, data):  # noqa: N802
        key.values[name] = (data, typ)

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]


@pytest.fixture
def winreg(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    return fake


def test_windows_legacy_enabled_is_migrated(winreg):
    winreg.keys[RUN]["OpenDM"] = ("old.exe --minimized", 1)
    autostart.migrate_legacy()
    assert "OpenDM" not in winreg.keys[RUN]
    assert "Divebird" in winreg.keys[RUN]
    assert autostart.is_enabled()


def test_windows_legacy_disabled_by_user_stays_off(winreg):
    winreg.keys[RUN]["OpenDM"] = ("old.exe --minimized", 1)
    winreg.keys[APPROVED]["OpenDM"] = (DISABLED, 3)
    autostart.migrate_legacy()
    assert "OpenDM" not in winreg.keys[RUN] and "OpenDM" not in winreg.keys[APPROVED]
    assert "Divebird" not in winreg.keys[RUN], "使用者已停用的自動啟動不可被重新打開"
    assert not autostart.is_enabled()


def test_windows_disabled_in_task_manager_reported_and_reenabled(winreg):
    winreg.keys[RUN]["Divebird"] = ("x.exe", 1)
    winreg.keys[APPROVED]["Divebird"] = (DISABLED, 3)
    assert not autostart.is_enabled(), "設定畫面要反映工作管理員中的停用狀態"
    autostart.set_enabled(True)
    assert autostart.is_enabled()
    assert "Divebird" not in winreg.keys[APPROVED]


def test_windows_no_legacy_does_nothing(winreg):
    autostart.migrate_legacy()
    assert winreg.keys[RUN] == {}


def test_desktop_disabled_parsing():
    assert autostart.desktop_disabled("[Desktop Entry]\nHidden=true\n")
    assert autostart.desktop_disabled("X-GNOME-Autostart-enabled = False")
    assert not autostart.desktop_disabled("[Desktop Entry]\nX-GNOME-Autostart-enabled=true\n")


@pytest.mark.parametrize("legacy_text, expect_new", [
    ("[Desktop Entry]\nExec=opendm --minimized\n", True),
    ("[Desktop Entry]\nExec=opendm --minimized\nHidden=true\n", False),
])
def test_linux_legacy_desktop_migration(monkeypatch, tmp_path, legacy_text, expect_new):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "autostart").mkdir()
    (tmp_path / "autostart" / "opendm.desktop").write_text(legacy_text, encoding="utf-8")
    autostart.migrate_legacy()
    assert not (tmp_path / "autostart" / "opendm.desktop").exists()
    assert (tmp_path / "autostart" / "divebird.desktop").exists() == expect_new


def test_source_checkout_autostarts_through_root_launcher(tmp_path, monkeypatch):
    # 從原始碼執行且已有 Divebird.exe：自動啟動也經過它（先檢查環境、需要時先更新）
    monkeypatch.setattr(sys, "platform", "win32")
    assert autostart.launch_command(tmp_path)[0] != str(tmp_path / "Divebird.exe")
    (tmp_path / "Divebird.exe").write_bytes(b"")
    (tmp_path / "Divebird.bat").write_bytes(b"")
    assert autostart.launch_command(tmp_path) == [str(tmp_path / "Divebird.exe"), "--minimized"]


def test_windows_refresh_updates_command_but_keeps_disabled_state(winreg, monkeypatch):
    monkeypatch.setattr(autostart, "launch_command", lambda: ["C:/new/Divebird.exe", "--minimized"])
    autostart.refresh()
    assert winreg.keys[RUN] == {}, "沒有登記自動啟動時不可自行加上"
    winreg.keys[RUN]["Divebird"] = ("C:/old/divebird-gui.exe -m divebird --minimized", 1)
    winreg.keys[APPROVED]["Divebird"] = (DISABLED, 3)
    autostart.refresh()
    assert winreg.keys[RUN]["Divebird"][0] == "C:/new/Divebird.exe --minimized"
    assert winreg.keys[APPROVED]["Divebird"] == (DISABLED, 3), "使用者在工作管理員的停用設定要保留"
    assert not autostart.is_enabled()
