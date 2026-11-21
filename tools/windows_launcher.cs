using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

public static class PromptAtelierLauncher
{
    [STAThread]
    public static int Main()
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, "runtime", "python.exe");
            string script = Path.Combine(root, "ai_prompt_workbench.py");
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("程序运行文件缺失，请保留用户数据并重新安装。");
            var start = new ProcessStartInfo(python, "\"" + script + "\"");
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables.Remove("PYTHONHOME");
            start.EnvironmentVariables.Remove("PYTHONPATH");
            start.EnvironmentVariables.Remove("IMAGEIO_FFMPEG_EXE");
            start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            using (Process process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception)
        {
            MessageBox.Show("工作台无法启动。请保留用户数据并检查程序运行文件。", "Prompt Atelier");
            return 1;
        }
    }
}
