@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0生成更新清单.py"
) else (
    python "%~dp0生成更新清单.py"
)
if errorlevel 1 (
    echo.
    echo 生成失败，请检查 Python 环境和文件是否齐全。
) else (
    echo.
    echo 清单生成完成。请再运行“运行工程质量检查.bat”。
)
pause
endlocal
