<p align="center"><img src="assets/divebird.png" width="112" alt="Divebird"></p>

<h1 align="center">Divebird</h1>
<p align="center"><b>看準、俯衝、下載到手。</b><br><i>See it. Dive. Download.</i></p>

Divebird — 開源、跨平台（Windows／Linux）的下載管理員：多連線下載、斷點續傳，網頁影片一鍵下載。

Divebird is an open-source download manager for Windows and Linux, with multi-connection downloads, resume support,
and a one-click button for web videos.

搭配專案內附的 Chrome 擴充功能：在網頁上播放影片時，影片右上角會出現 **「下載此影片」懸浮按鈕**，點一下就交給 Divebird 下載。

> 名字的由來：鰹鳥看準海裡的魚，收起翅膀俯衝，一次就叼走。Divebird 也是這樣——看準網頁上的影片，一鍵下載。

| 功能 | 說明 |
| --- | --- |
| 多連線加速 | 一個檔案切成多段、同時以多條連線下載（預設 8 條），並採用「動態分段」：先完成的連線會接手剩餘最多的分段，讓連線一直滿載 |
| 續傳 | 暫停、斷線、關閉程式後都能從中斷處繼續（`.part` + `.part.json` 進度檔） |
| 影片懸浮按鈕 | 自動偵測網頁中的 `<video>`，包含被播放器遮罩蓋住的、iframe 內嵌的、全螢幕中的影片 |
| 串流嗅探 | 監聽網路請求，偵測 HLS（`.m3u8`）、DASH（`.mpd`）與影音檔，下載後自動合併成 mp4 |
| 影音網站 | 內建 yt-dlp，支援 YouTube、Bilibili 等上千個網站，可選畫質或只下載音訊（M4A / MP3） |
| 攔截瀏覽器下載 | 一般檔案下載自動轉交 Divebird 多連線下載（按住 **Alt** 點連結可略過） |
| 其他 | 下載佇列與同時下載數、全域限速、分類、搜尋、拖放網址、右鍵選單、系統匣、完成通知、開機自動啟動、深色模式 |

## 程式自帶環境

Divebird **不需要使用者安裝 Python、ffmpeg 或其他任何東西**：

- **打包版（給一般使用者）**：`dist/Divebird/` 內含 Python 執行環境、Qt、yt-dlp，以及 `tools/` 裡的 ffmpeg（影音合併）與 deno（yt-dlp 解析 YouTube 需要的 JavaScript 執行環境）。解壓縮即可執行。
- **原始碼開發**：`scripts/setup.*` 會把 uv、獨立的 CPython 3.12、套件快取與虛擬環境全部放在專案目錄內（`.runtime/`、`.venv/`），不使用、也不修改系統上的 Python。刪掉專案資料夾就完全移除。

## 安裝與執行

先取得原始碼（或到 Releases 下載打包好的免安裝版）：

```bash
git clone https://github.com/acer1204/Divebird.git
cd Divebird
```

### Windows

| 方式 | 怎麼做 |
| --- | --- |
| **雙擊 `Divebird.bat`**（專案根目錄） | 最簡單。第一次會自動在專案內建立環境（需幾分鐘），之後秒開 |
| **桌面／開始功能表捷徑** | 執行一次 `powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1`，之後點有圖示的捷徑啟動，不會閃出黑色視窗 |
| **打包版 exe** | 解壓縮 `Divebird-<版本>-windows-x64.zip`，雙擊 `Divebird\Divebird.exe`（不需要任何安裝） |

`Divebird.bat` 的判斷順序：專案內已有環境（`.venv`）→ 執行最新原始碼；否則旁邊有打包版 → 執行 exe；都沒有 → 先建立環境。

打包成免安裝版：`powershell -ExecutionPolicy Bypass -File scripts\build.ps1` → `dist\`

> 從檔案總管雙擊 `.bat` 或用 `powershell -ExecutionPolicy Bypass -File …` 執行，
> 可避開 Windows 預設「禁止執行 .ps1 指令碼」的限制。

### Linux

| 方式 | 怎麼做 |
| --- | --- |
| **`./divebird.sh`**（專案根目錄） | 第一次會自動在專案內建立環境，之後直接啟動 |
| **應用程式選單捷徑** | 執行一次 `./scripts/create-shortcut.sh`，之後從選單點 Divebird |
| **打包版** | `tar -xzf Divebird-<版本>-linux-x86_64.tar.gz && ./Divebird/install.sh`（安裝到 `~/.local/share/divebird`，建立選單捷徑與登入自動啟動） |

打包成免安裝版：`./scripts/build.sh` → `dist/`

> Qt 圖形介面在 X11 下需要系統的 `libxcb-cursor0`（Ubuntu/Debian：`sudo apt install libxcb-cursor0`）。
> 用 Wayland 桌面則不需要。打包時若建置機器已安裝，會一併打包進去。
>
> GNOME 預設沒有系統匣，可安裝「AppIndicator and KStatusNotifierItem Support」擴充；
> 沒有系統匣時，關閉視窗會改為最小化繼續在背景執行（按 **Ctrl+Q** 結束程式）。

## 安裝 Chrome 擴充功能

1. 開啟 `chrome://extensions`（Edge 為 `edge://extensions`）
2. 開啟右上角「**開發人員模式**」
3. 按「**載入未封裝項目**」，選擇 `extension` 資料夾（打包版在 `Divebird/extension`）
4. 建議把 Divebird 圖示釘選到工具列

擴充功能圖示上的數字是目前分頁偵測到的影音數量；圖示旁的綠點代表已連上 Divebird。

## 使用方式

- **懸浮按鈕**：把滑鼠移到播放中的影片上 → 右上角出現「下載此影片」→ 點擊
  - 影片是一般檔案（mp4）或只偵測到一個串流 → 直接送出
  - 偵測到多個來源 → 跳出選單讓你挑（HLS / DASH / MP4 / 以 yt-dlp 解析網頁）
  - YouTube 這類網站 → 整頁交給 yt-dlp，在 Divebird 的視窗中選擇畫質
- **擴充功能彈出視窗**：列出目前分頁偵測到的所有影音、「解析此頁面的影片」、開關設定
- **右鍵選單**：在連結 / 影片 / 頁面上按右鍵 → 「用 Divebird 下載…」
- **桌面程式**：「新增網址」、或直接把連結拖曳進視窗

收到下載時，Divebird 會顯示「下載檔案資訊」視窗（檔名、大小、畫質、儲存位置），可勾選「不再顯示」改為直接下載。

## 架構

```
Chrome 擴充功能 (extension/)                 Divebird 桌面程式 (src/divebird/)
┌─────────────────────────────┐             ┌──────────────────────────────────────┐
│ content.js  懸浮按鈕、選單     │─┐           │ server.py   本機 API 127.0.0.1:17890   │
│ background.js               │ │ HTTP/JSON │ gui/        PySide6 介面、系統匣        │
│   webRequest 嗅探 m3u8/mpd   │ ├─────────▶│ engine/manager.py   佇列、排程、持久化   │
│   Cookie / Referer 收集      │ │           │ engine/http_engine.py 多連線動態分段     │
│   攔截 chrome.downloads      │─┘           │ engine/media_engine.py yt-dlp+ffmpeg    │
│ popup.html  偵測清單與設定     │             └──────────────────────────────────────┘
└─────────────────────────────┘
```

- 擴充功能把網址連同該網站的 Cookie、Referer、User-Agent 一起交給桌面程式，所以需要登入的影片也能下載。
- 一般檔案走自製的多連線引擎；HLS / DASH / 影音網站交給 yt-dlp（同樣多片段並行），再由 ffmpeg 合併。
- **安全性**：本機 API 只監聽 `127.0.0.1`；會檢查 `Host` 標頭防 DNS rebinding；帶 `Origin` 的請求只接受擴充功能來源，一般網頁無法偷偷呼叫。下載完成後即清除該任務保存的 Cookie。

## 設定與資料位置

| 項目 | Windows | Linux |
| --- | --- | --- |
| 設定、下載清單 | `%APPDATA%\Divebird\` | `~/.config/divebird/` |
| 預設下載資料夾 | `~/Downloads` | XDG 下載資料夾（如 `~/下載`） |

可攜模式：在 `Divebird.exe`（或 `Divebird`）旁放一個名為 `portable` 的空檔案，設定就會存在程式旁的 `data/` 資料夾。

若更改了 API 埠號，記得在擴充功能彈出視窗中改成相同的埠號。

從舊名稱 OpenDM 升級：第一次啟動 Divebird 時，會自動把 `%APPDATA%\OpenDM`（Linux 為 `~/.config/opendm`）的設定與下載清單複製過來，
舊資料夾保留不動，確認無誤後可自行刪除；舊的開機自動啟動設定也會換成新名稱（若你曾在系統中停用它，則維持停用）。

- 升級前請先結束舊版 OpenDM（系統匣圖示按右鍵 → 結束）；舊版仍在執行時，Divebird 會提示你先關閉它。
- 到 `chrome://extensions` 移除舊的 OpenDM 擴充功能，再重新「載入未封裝項目」選擇新的 `extension` 資料夾
  （若是同一個資料夾，按「重新載入」即可），否則舊擴充功能會一直顯示「未連線」。

## 開發與測試

```bash
./scripts/test.sh          # Windows：powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

單元 / 整合測試（`tests/`）涵蓋：多連線分段下載、動態分段、暫停續傳、不支援 Range 的伺服器、
Content-Disposition 中文檔名、佇列與持久化、本機 API 的安全檢查、以 ffmpeg 產生的 HLS 串流下載與合併。

擴充功能端對端測試（以 Playwright 啟動載入擴充功能的 Chromium，實際點擊懸浮按鈕）。
使用 `scripts/setup.*` 放在專案內的 uv（Windows 為 `.runtime\uv\uv.exe`）：

```bash
.runtime/uv/uv run --no-project --with playwright python -m playwright install chromium   # 第一次
.runtime/uv/uv run --no-project --with playwright python tests/e2e/run_extension_e2e.py
```

打包版煙霧測試（啟動打包好的程式，透過 API 實際下載一般檔案與 HLS 串流）：

```bash
.venv/bin/python tests/smoke_package.py dist/Divebird/Divebird
```

```powershell
.venv\Scripts\python.exe tests\smoke_package.py dist\Divebird\Divebird.exe
```

`.github/workflows/build.yml` 會在 GitHub Actions 上同時建置 Windows 與 Linux 版本。

> **Linux 打包注意**：PyInstaller 無法跨平台打包，而且會把建置機器上的系統函式庫一起打包，
> 所以產物只能在 **glibc 版本不低於建置機器** 的發行版上執行。要發佈給大多數使用者，
> 請在較舊的系統上建置（CI 使用 Ubuntu 22.04，可在 glibc 2.35 以上的發行版執行）。
>
> 內附的 Linux ffmpeg 是靜態連結 glibc 的版本，解析 HLS 的 MPEG-TS 片段時會載入系統的 gconv 模組，
> 在 glibc 較新的系統（如 Ubuntu 26.04）上會當掉。Divebird 會自動以包裝腳本將 `GCONV_PATH`
> 指向空目錄來避開（見 `src/divebird/engine/tools.py`），`tests/test_media_engine.py` 有對應的迴歸測試。

## 限制

- 使用 DRM（Widevine 等）加密的串流，例如 Netflix、Disney+、Spotify，無法下載。
- 直播串流目前不支援。
- 請遵守各網站的使用條款與著作權法規，只下載你有權保存的內容。
