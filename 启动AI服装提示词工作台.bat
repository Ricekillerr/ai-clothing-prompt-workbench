@echo off
setlocal
chcp 65001 >nul
title Prompt Atelier - AI服装提示词工作台

cd /d "%~dp0"
if errorlevel 1 goto path_error

if exist "_update_transaction.json" (
  echo 检测到未完成的更新。请保留程序与数据备份，查看 _update_transaction.json 和 _update_error.log 后恢复。
  exit /b 3
)
py.exe -3 -c "import sys; sys.exit(sys.version_info.major != 3)" >nul 2>&1
if not errorlevel 1 goto use_py
python.exe -c "import sys; sys.exit(sys.version_info.major != 3)" >nul 2>&1
if not errorlevel 1 goto use_python
echo 启动失败：未找到可运行的 Python 3。请检查 Python Launcher 或 PATH 中的 python.exe。
exit /b 2

:use_py
py.exe -3 "%~dp0ai_prompt_workbench.py"
goto finished

:use_python
python.exe "%~dp0ai_prompt_workbench.py"
goto finished

:path_error
echo.
echo 启动失败：无法进入工作台程序目录。
echo 请确认本 BAT 文件与 ai_prompt_workbench.py 位于同一文件夹。
pause
goto end

:finished
if errorlevel 1 (
  echo.
  echo 工作台启动或运行失败，错误代码：%errorlevel%
  exit /b 1
)

:end
endlocal
