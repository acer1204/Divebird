# Divebird 開發環境建置（Windows）
# 所有東西都放在專案目錄內，不依賴、也不修改系統上的 Python：
#   .runtime\uv\       uv 套件管理器（自動下載）
#   .runtime\python\   獨立的 CPython 3.12（由 uv 下載）
#   .runtime\cache\    套件快取
#   .venv\             專案虛擬環境（PySide6、yt-dlp、ffmpeg、deno…）
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root ".runtime"
$UvDir = Join-Path $Runtime "uv"
$Uv = Join-Path $UvDir "uv.exe"

if (-not (Test-Path $Uv)) {
    Write-Host "==> 下載 uv（Python 環境管理器）..."
    New-Item -ItemType Directory -Force $UvDir | Out-Null
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64" } else { "x86_64" }
    $zip = Join-Path $UvDir "uv.zip"
    Invoke-WebRequest "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-pc-windows-msvc.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $UvDir -Force
    Remove-Item $zip
    $found = Get-ChildItem $UvDir -Recurse -Filter uv.exe | Select-Object -First 1
    if ($found.FullName -ne $Uv) { Move-Item $found.FullName $Uv -Force }
}

$env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtime "python"
$env:UV_CACHE_DIR = Join-Path $Runtime "cache"
$env:UV_PYTHON_PREFERENCE = "only-managed"
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Root ".venv"

Write-Host "==> 安裝獨立 Python 與相依套件（第一次約需數分鐘）..."
& $Uv sync --project $Root
if ($LASTEXITCODE -ne 0) { throw "uv sync 失敗" }

# 建立不會開主控台視窗的啟動程式 .venv\Scripts\divebird-gui.exe
& (Join-Path $Root ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "win_gui_launcher.py") | Out-Null

Write-Host ""
Write-Host "完成！雙擊專案根目錄的 Divebird.bat 即可啟動。"
