# 打包 Windows 免安裝版：dist\Divebird\Divebird.exe ＋ dist\Divebird-<版本>-windows-x64.zip
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { & (Join-Path $PSScriptRoot "setup.ps1") }
Push-Location $Root
try {
    $Version = & $Py -c "import divebird; print(divebird.__version__)"
    & $Py scripts\gen_icons.py
    & $Py -m PyInstaller --noconfirm --clean divebird.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失敗" }

    $Dist = Join-Path $Root "dist\Divebird"
    Copy-Item -Recurse -Force (Join-Path $Root "extension") (Join-Path $Dist "extension")
    Copy-Item -Force (Join-Path $Root "README.md") $Dist

    $Zip = Join-Path $Root "dist\Divebird-$Version-windows-x64.zip"
    if (Test-Path $Zip) { Remove-Item $Zip }
    Compress-Archive -Path $Dist -DestinationPath $Zip
    Write-Host ""
    Write-Host "完成：$Dist\Divebird.exe"
    Write-Host "壓縮檔：$Zip"
} finally {
    Pop-Location
}
