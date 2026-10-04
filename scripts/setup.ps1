# Divebird 開發環境建置（Windows）
# 所有東西都放在專案目錄內，不依賴、也不修改系統上的 Python：
#   .runtime\uv\       uv 套件管理器（自動下載）
#   .runtime\python\   獨立的 CPython 3.12（由 uv 下載）
#   .runtime\cache\    套件快取
#   .venv\             專案虛擬環境（PySide6、yt-dlp、ffmpeg、deno…）
# 可重複執行：環境已完整時幾秒內結束。-Recreate 會重建 .venv（Divebird.bat /repair 使用）；
# 重建失敗時會把原本的 .venv 放回去。
# 結束碼：0 完成；3 另一個視窗正在建立環境；4 .venv 裡的程式正在執行；其他為失敗。
param([switch]$Recreate)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root ".runtime"
$UvDir = Join-Path $Runtime "uv"
$Uv = Join-Path $UvDir "uv.exe"
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$Stamp = Join-Path $Venv "divebird-setup.sha256"

# 同一個專案資料夾同時只能有一個 setup，避免兩個視窗互相破壞對方建到一半的 .venv。
# 鎖只是保護措施：系統不允許建立（例如受限的 PowerShell）時照常繼續。
$mutex = $null
try {
    $sha1 = [Security.Cryptography.SHA1]::Create()   # 不用 SHA1Managed：開啟 FIPS 原則的電腦會拒絕
    $id = [BitConverter]::ToString($sha1.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant()))).Replace("-", "")
    $mutex = New-Object Threading.Mutex($false, "Local\Divebird.Setup.$id")
    try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) {
        $mutex.Dispose()
        Write-Host "另一個視窗正在建立 Divebird 的執行環境，請等它完成後再試。"
        exit 3
    }
} catch {
    Write-Host "注意：無法建立同步鎖（$($_.Exception.Message)），繼續執行。"
    $mutex = $null
}

# 執行檔位於 .venv 裡的程序（Divebird 本身是 Scripts\divebird-gui.exe；也包括 python.exe、ffmpeg 等）
function Get-VenvProcesses {
    $prefix = $Venv.TrimEnd("\") + "\"
    foreach ($p in Get-Process) {
        try { $path = $p.Path } catch { continue }   # 系統或提權程序讀不到路徑
        if ($path -and $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { $p }
    }
}

# .venv 記錄的是絕對路徑：專案資料夾被搬移或改名後，它找不到 Python，只能重建
function Test-VenvBroken {
    try {
        $cfg = Join-Path $Venv "pyvenv.cfg"
        if (-not (Test-Path -LiteralPath $cfg)) { return $true }
        $line = Get-Content -LiteralPath $cfg -Encoding UTF8 | Where-Object { $_ -match "^\s*home\s*=" } | Select-Object -First 1
        if (-not $line) { return $true }
        $pythonHome = ($line -replace "^\s*home\s*=\s*", "").Trim().TrimEnd("\")
        # 不用 Join-Path：原本的磁碟機代號不存在時它會丟出例外
        return -not (Test-Path -LiteralPath ($pythonHome + "\python.exe"))
    } catch {
        return $true
    }
}

try {
    $old = $null
    if (Test-Path -LiteralPath $Venv) {
        $users = @(Get-VenvProcesses)
        if ($users.Count -gt 0) {
            if ($users | Where-Object { $_.ProcessName -eq "divebird-gui" }) {
                Write-Host "Divebird 正在執行（可能縮小在系統匣）。"
                Write-Host "請先結束 Divebird（系統匣圖示按右鍵 →「結束」），再重新執行。"
            } else {
                $names = ($users | ForEach-Object { "$($_.ProcessName).exe（PID $($_.Id)）" }) -join "、"
                Write-Host "下列程式正在使用 .venv：$names"
                Write-Host "可能是測試、終端機或編輯器，請先關閉它們再試。"
            }
            exit 4
        }
        # 動手之前先讓「環境已建好」失效：中途失敗或被關掉時，下次啟動會重跑 setup
        Remove-Item -LiteralPath $Stamp -Force -ErrorAction SilentlyContinue
        if ($Recreate -or (Test-VenvBroken)) {
            # 先整個移開再重建（失敗時放回去），不在原地刪：刪到一半會留下壞掉卻看似完整的 .venv
            Write-Host "==> 移開舊的執行環境（.venv）..."
            $old = "$Venv.old-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
            try {
                Rename-Item -LiteralPath $Venv -NewName (Split-Path -Leaf $old)
            } catch {
                Write-Host "無法移開 .venv：$($_.Exception.Message)"
                Write-Host "請關閉正在使用專案資料夾的程式（例如終端機、編輯器或防毒掃描）後再試。"
                exit 4
            }
        }
    }

    try {
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
        $env:UV_PROJECT_ENVIRONMENT = $Venv

        Write-Host "==> 安裝獨立 Python 與相依套件（第一次約需數分鐘）..."
        & $Uv sync --project $Root
        if ($LASTEXITCODE -ne 0) { throw "uv sync 失敗" }

        # 建立不會開主控台視窗的啟動程式（.venv\Scripts\divebird-gui.exe 與專案根目錄的 Divebird.exe），
        # 成功後寫入「環境已建好」的戳記（.venv\divebird-setup.sha256）
        & $VenvPy (Join-Path $PSScriptRoot "win_gui_launcher.py") --setup-done | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "無法建立 divebird-gui.exe" }
    } catch {
        if ($old) {
            Write-Host "==> 重建失敗，放回原本的執行環境..."
            Remove-Item -LiteralPath $Venv -Recurse -Force -ErrorAction SilentlyContinue
            if (-not (Test-Path -LiteralPath $Venv)) { Rename-Item -LiteralPath $old -NewName ".venv" }
        }
        throw
    }

    # 清掉移開的舊環境（含以前沒刪乾淨的）；刪不掉的下次再清
    Get-ChildItem -LiteralPath $Root -Directory -Filter ".venv.old-*" -Force -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

    Write-Host ""
    if (Test-Path (Join-Path $Root "Divebird.exe")) {
        Write-Host "完成！之後按兩下專案根目錄的 Divebird.exe 即可啟動（不會出現主控台視窗）。"
    } else {
        Write-Host "完成！按兩下專案根目錄的 Divebird.bat 即可啟動。"
    }
} finally {
    if ($mutex) { $mutex.ReleaseMutex(); $mutex.Dispose() }
}
