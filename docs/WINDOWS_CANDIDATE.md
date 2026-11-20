# Windows 阶段 2 候选包与隔离验收

本包仅作为 PR 的测试 Artifact，不是正式 Release，也不允许用于真实用户数据库。构建脚本复制 Windows x64 Python 标准运行时，在独立目录安装锁定版本的 Pillow 与 imageio-ffmpeg，并以 C# 启动器运行现有脚本。未采用 PyInstaller：现有更新器及迁移重启依赖 `sys.executable` 是可调用的 Python 解释器，冻结后需要重写启动和回滚协议。程序文件与 `USERPROFILE` 下的 V1/V2 数据目录分离。

构建前检查发布清单各程序文件的 SHA-256；Artifact 包含 ZIP、ZIP SHA-256、源码和检出提交、构建时间、依赖版本以及独立测试脚本。候选 ZIP 内含 Python、Pillow、FFmpeg、启动器和程序文件。构建过程需要联网下载依赖；**使用候选包启动及解码不需要联网下载依赖**。

GitHub 托管 Windows CI 使用全新临时用户数据、中文及空格目录和受限 PATH，通过包内 Python 运行测试，核对 Pillow、imageio-ffmpeg 和 FFmpeg 均来自包内；真实解码图片和视频、导入并去重、启动 EXE 和 HTTP、关闭服务、执行包内更新器并验证 V2 数据库、视频和设置。此测试不等于全新操作系统：托管运行器预装了开发工具，也未实施操作系统级断网。

## 真正干净 Windows 虚拟机方案（尚待执行）

使用可回滚快照的新 Windows x64 虚拟机，断开网络适配器，以无 Python、Python Launcher 和 FFmpeg 的全新账号运行。传入已核对 SHA-256 的候选 Artifact，在虚拟机中解压到中文及空格路径；使用 Artifact 附带的 `windows_clean_vm_smoke.ps1` 和 `windows_candidate_smoke.py`，由包内 Python 完成同一组测试。脚本会检查 PATH 和 Python 注册表项，但这些检查本身不能证明机器完全干净；须同时记录虚拟机映像、快照、网络隔离和测试日志。不得接入真实资料或真实 API 密钥。

## 当前边界

升级器仅替换现有允许清单中的程序文件，不能自动升级候选包中的 Python、Pillow 或 FFmpeg。日后需要变更这些依赖时，必须先设计经过验证的运行时更新和回滚机制，不能以此候选包声称此问题已解决。历史绝对路径视频作为待重新定位记录保留，不自动读取或复制；用户需授权扫描目录并明确选择视频，原引用不会自动覆盖。候选包通过自动化不代表真实 Windows 桌面视觉体验已验收。
