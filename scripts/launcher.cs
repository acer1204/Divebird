// Divebird 啟動程式（Windows，從原始碼執行時使用）。
//
// 由 scripts/win_gui_launcher.py 以 Windows 內建的 .NET Framework 編譯器（csc.exe，C# 5）
// 編譯成專案根目錄的 Divebird.exe。它是無主控台的 GUI 程式，所以按兩下時不會像 Divebird.bat
// 那樣先跳出黑色的主控台視窗：
//   - 執行環境正常：直接以 .venv\Scripts\divebird-gui.exe -m divebird 啟動 Divebird，
//     命令列參數（網址、--minimized）原封不動轉交
//   - 尚未建立環境、上次沒建完，或 git pull 改了相依套件（.venv\divebird-setup.sha256 不相符）：
//     改開 Divebird.bat，執行 setup 並顯示進度
//   - 環境屬於別的資料夾（專案資料夾被搬移、改名或複製）：詢問後以 Divebird.bat /repair 重建
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32.SafeHandles;
using FILETIME = System.Runtime.InteropServices.ComTypes.FILETIME;

[assembly: AssemblyTitle("Divebird")]
[assembly: AssemblyProduct("Divebird")]
[assembly: AssemblyDescription("Divebird launcher for source checkouts")]

static class Launcher
{
    [STAThread]
    static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string venv = Path.Combine(root, ".venv");
        string gui = Path.Combine(venv, @"Scripts\divebird-gui.exe");
        string bat = Path.Combine(root, "Divebird.bat");
        try
        {
            bool installed = File.Exists(gui);
            string problem = installed ? EnvironmentProblem(root, venv) : null;
            // 環境建好且是最新的才直接啟動；Divebird 正在執行時也直接啟動（它會把已開著的視窗叫到前面），
            // 等下次再更新環境
            if (installed && problem == null)
            {
                bool current = SetupIsCurrent(root, venv);
                if (current || IsRunning(gui))
                {
                    string args = RawArguments();
                    Start(gui, Join("-m divebird", args), root, false);
                    if (!current && args.IndexOf("--minimized", StringComparison.Ordinal) < 0)
                        Dialog("Divebird 有更新待套用（相依套件或啟動程式已變更），但它正在執行，所以先沿用目前的環境。"
                               + "\n\n請從系統匣結束 Divebird（圖示按右鍵 →「結束」），再重新開啟，即可自動更新。",
                               MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return 0;
                }
            }
            if (!File.Exists(bat))
            {
                Fail("找不到 Divebird.bat。\n\nDivebird.exe 必須留在 Divebird 專案資料夾中；"
                     + "若想從桌面啟動，請建立捷徑，不要直接搬移或複製這個檔案。");
                return 1;
            }
            if (problem == null)
            {
                // 第一次執行、上次 setup 沒完成，或 git pull 改了相依套件：由 Divebird.bat 執行 setup
                // 並顯示進度，完成後啟動 Divebird（不轉交參數，避免 cmd 誤解 & 等符號）
                Start(bat, "", root, true);
                return 0;
            }
            DialogResult answer = Dialog(
                "Divebird 的執行環境需要重建。\n\n" + problem
                + "\n\n要現在重建嗎？會開啟一個視窗顯示進度，通常一分鐘內完成。",
                MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (answer == DialogResult.Yes) Start(bat, "/repair", root, true);
            return 0;
        }
        catch (Exception e)
        {
            Fail("無法啟動 Divebird：" + e.Message + "\n\n可改按兩下專案資料夾中的 Divebird.bat。");
            return 1;
        }
    }

    // .venv 內記錄的是絕對路徑：專案資料夾被搬移、改名或複製後，它會指向不存在或別處的檔案。
    static string EnvironmentProblem(string root, string venv)
    {
        string cfg = Path.Combine(venv, "pyvenv.cfg");
        if (!File.Exists(cfg))
            return "執行環境不完整（找不到 .venv\\pyvenv.cfg）。";
        string home = null;
        foreach (string line in File.ReadAllLines(cfg, Encoding.UTF8))
        {
            int eq = line.IndexOf('=');
            if (eq > 0 && line.Substring(0, eq).Trim().Equals("home", StringComparison.OrdinalIgnoreCase))
                home = line.Substring(eq + 1).Trim();
        }
        if (home == null || !File.Exists(Path.Combine(home, "pythonw.exe")))
            return "找不到執行環境使用的 Python，專案資料夾可能被搬移或改名過。";

        // 以可編輯模式安裝的 divebird 套件要指向這個資料夾的 src（用檔案身分比對，不受路徑寫法影響）
        string pth = Path.Combine(venv, @"Lib\site-packages\_editable_impl_divebird.pth");
        if (File.Exists(pth))
        {
            string src = File.ReadAllText(pth, Encoding.UTF8).Trim();
            int newline = src.IndexOfAny(new char[] { '\r', '\n' });
            if (newline >= 0) src = src.Substring(0, newline).Trim();
            string mine = Path.Combine(root, @"src\divebird\__init__.py");
            string theirs = Path.Combine(src, @"divebird\__init__.py");
            if (!SameFile(mine, theirs))
                return "執行環境指向另一個資料夾的程式碼，專案資料夾可能是複製來的或被搬移過。";
        }
        return null;
    }

    // 與 scripts/win_gui_launcher.py 的 SETUP_INPUTS／setup_digest 相同：
    // SHA-256( SHA-256(檔案1) ‖ SHA-256(檔案2) ‖ … )，缺檔當成空檔。setup.ps1 全部成功後才寫入戳記。
    static readonly string[] SetupInputs = { "pyproject.toml", "uv.lock", @"scripts\launcher.cs", @"assets\divebird.ico" };

    static bool SetupIsCurrent(string root, string venv)
    {
        string stamp = Path.Combine(venv, "divebird-setup.sha256");
        return File.Exists(stamp) && File.ReadAllText(stamp, Encoding.UTF8).Trim() == SetupDigest(root);
    }

    static string SetupDigest(string root)
    {
        // 不用 SHA256.Create()（= SHA256Managed）：開啟 FIPS 原則的電腦（例如符合政府組態基準的公務機）會拒絕
        using (SHA256 sha = new SHA256CryptoServiceProvider())
        {
            byte[] all = new byte[32 * SetupInputs.Length];
            for (int i = 0; i < SetupInputs.Length; i++)
            {
                string path = Path.Combine(root, SetupInputs[i]);
                byte[] data = File.Exists(path) ? File.ReadAllBytes(path) : new byte[0];
                Buffer.BlockCopy(sha.ComputeHash(data), 0, all, 32 * i, 32);
            }
            return BitConverter.ToString(sha.ComputeHash(all)).Replace("-", "").ToLowerInvariant();
        }
    }

    // divebird-gui.exe 在 Divebird 執行期間一直開著；Windows 不允許寫入執行中的 exe（開啟後不寫入任何內容）
    static bool IsRunning(string exe)
    {
        try
        {
            using (new FileStream(exe, FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete)) { }
            return false;
        }
        catch (IOException) { return true; }
        catch (UnauthorizedAccessException) { return false; }
    }

    [StructLayout(LayoutKind.Sequential)]
    struct FileInformation
    {
        public uint Attributes;
        public FILETIME CreationTime, LastAccessTime, LastWriteTime;
        public uint VolumeSerialNumber, SizeHigh, SizeLow, NumberOfLinks, IndexHigh, IndexLow;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation info);

    static bool SameFile(string a, string b)
    {
        try
        {
            using (FileStream fa = Open(a))
            using (FileStream fb = Open(b))
            {
                FileInformation ia, ib;
                if (!GetFileInformationByHandle(fa.SafeFileHandle, out ia)
                    || !GetFileInformationByHandle(fb.SafeFileHandle, out ib))
                    return true;  // 無法判斷時不要誤報
                return ia.VolumeSerialNumber == ib.VolumeSerialNumber
                    && ia.IndexHigh == ib.IndexHigh && ia.IndexLow == ib.IndexLow;
            }
        }
        catch (IOException) { return false; }  // 其中一個檔案不存在
        catch (UnauthorizedAccessException) { return true; }
    }

    static FileStream Open(string path)
    {
        return new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
    }

    // 取出原始命令列中程式名稱之後的部分，原封不動轉交（不重新拆解、不重新加引號）。
    // 程式名稱的切法與 Windows 的 CommandLineToArgvW 相同：以引號開頭時到下一個引號為止，
    // 否則到第一個空白為止。
    static string RawArguments()
    {
        string line = Environment.CommandLine;
        int end;
        if (line.StartsWith("\""))
        {
            end = line.IndexOf('"', 1);
            end = end < 0 ? line.Length : end + 1;
        }
        else
        {
            end = line.IndexOfAny(new char[] { ' ', '\t' });
            if (end < 0) end = line.Length;
        }
        return line.Substring(end).TrimStart(' ', '\t');
    }

    static string Join(string head, string tail)
    {
        return tail.Length == 0 ? head : head + " " + tail;
    }

    static void Start(string file, string arguments, string workingDirectory, bool shell)
    {
        ProcessStartInfo info = new ProcessStartInfo(file, arguments);
        info.WorkingDirectory = workingDirectory;
        info.UseShellExecute = shell;
        using (Process.Start(info)) { }
    }

    static void Fail(string message)
    {
        Dialog(message, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    // 測試用（tests/test_win_launcher.py）：設定 DIVEBIRD_LAUNCHER_DIALOG_LOG 時不顯示對話框，
    // 改把內容附加到該檔案，並以 DIVEBIRD_LAUNCHER_DIALOG_ANSWER（yes／no，預設 no）作答。
    static DialogResult Dialog(string message, MessageBoxButtons buttons, MessageBoxIcon icon)
    {
        string log = Environment.GetEnvironmentVariable("DIVEBIRD_LAUNCHER_DIALOG_LOG");
        if (string.IsNullOrEmpty(log))
            return MessageBox.Show(message, "Divebird", buttons, icon);
        File.AppendAllText(log, message + "\n", new UTF8Encoding(false));
        if (buttons != MessageBoxButtons.YesNo) return DialogResult.OK;
        string answer = Environment.GetEnvironmentVariable("DIVEBIRD_LAUNCHER_DIALOG_ANSWER") ?? "";
        return answer.Equals("yes", StringComparison.OrdinalIgnoreCase) ? DialogResult.Yes : DialogResult.No;
    }
}
