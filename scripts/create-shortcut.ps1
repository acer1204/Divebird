# 建立 Divebird 捷徑：有程式圖示、雙擊不會跳出主控台視窗。
#   預設同時建立在「桌面」與「開始功能表」；也可只選其一或指定資料夾：
#   powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1 [-Desktop] [-StartMenu] [-Dir <資料夾>]
param(
    [switch]$Desktop,
    [switch]$StartMenu,
    [string]$Dir
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Gui = Join-Path $Root ".venv\Scripts\divebird-gui.exe"
$Exe = Join-Path $Root "dist\Divebird\Divebird.exe"

# 與 Divebird.bat 相同的優先順序：專案內環境（最新原始碼）> 打包版 > 先建立環境
if (-not (Test-Path $Py) -and -not (Test-Path $Exe)) {
    & (Join-Path $PSScriptRoot "setup.ps1")
}
if (Test-Path $Py) {
    # divebird-gui.exe：CPython 的無視窗 venv 啟動器（uv 的 pythonw.exe 會多開一個主控台視窗）
    & $Py (Join-Path $PSScriptRoot "win_gui_launcher.py") | Out-Null
    $Target = $Gui; $Arguments = "-m divebird"
} else {
    $Target = $Exe; $Arguments = ""
}

$Dirs = @()
if ($Dir) { $Dirs += $Dir }
if ($Desktop) { $Dirs += [Environment]::GetFolderPath("Desktop") }
if ($StartMenu) { $Dirs += [Environment]::GetFolderPath("Programs") }
if ($Dirs.Count -eq 0) {
    $Dirs = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))
}

$Shell = New-Object -ComObject WScript.Shell
foreach ($d in $Dirs) {
    New-Item -ItemType Directory -Force $d | Out-Null
    $Path = Join-Path $d "Divebird.lnk"
    $Lnk = $Shell.CreateShortcut($Path)
    $Lnk.TargetPath = $Target
    $Lnk.Arguments = $Arguments
    $Lnk.WorkingDirectory = $Root
    $Lnk.IconLocation = (Join-Path $Root "assets\divebird.ico") + ",0"
    $Lnk.Description = "Divebird 下載管理員"
    $Lnk.Save()
    Write-Host "已建立捷徑：$Path"
}
