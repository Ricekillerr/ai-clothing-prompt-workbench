@echo off
setlocal
chcp 65001 >nul
title Prompt Atelier - AI服装提示词工作台

cd /d "%~dp0"
if errorlevel 1 goto path_error

where py >nul 2>&1
if errorlevel 1 goto use_python

:use_py
py -3 "%~dp0ai_prompt_workbench.py"
goto finished

:use_python
python "%~dp0ai_prompt_workbench.py"
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
  pause
)

:end
endlocal
